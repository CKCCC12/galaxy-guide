# cwa.py
# 中央氣象署（CWA）降雨機率與天氣現象查詢模組
#
# 資料來源：CWA Open Data API
#   端點：F-D0047-091（全台22縣市，逐12小時，涵蓋7天）
#   欄位：12小時降雨機率（ProbabilityOfPrecipitation）、天氣現象
#
# 為何選 F-D0047-091？
#   - 一次請求取得全台所有縣市，無需知道目標縣市名稱
#   - 涵蓋7天，適合查詢週末
#   - 夜間時段（18:00–06:00）完整覆蓋我們定義的夜間窗口（20:00–05:00）
#   - CWA RWRF 模型，適合台灣地形（比 Open-Meteo 準確）
#
# 端點說明：
#   F-D0047-091：全台縣市，逐12小時（現行）
#   F-D0047-089：全台縣市，逐3小時（PoP/Wx 欄位名稱不同，需另處理）
#   F-D0047-001~054：各縣市鄉鎮市區（分縣請求，精度更高，未來可升級）
#
# 申請金鑰：
#   1. 前往 https://opendata.cwa.gov.tw
#   2. 右上角「登入/加入會員」→ 申請帳號
#   3. 登入後於「取得授權碼」申請
#   4. 設定環境變數：export CWA_API_KEY="CWA-XXXXXXXX-..."
#   5. 或在 .env 加入（不要 commit 進 git）
#
# 未設定金鑰時：降雨機率欄位顯示「未設定金鑰」，評分不套用方案 B 懲罰。

import urllib.request
import urllib.parse
import json
import math
import os
import ssl
import time
import threading
from datetime import date, datetime, timedelta
from typing import Optional

# CWA opendata.cwa.gov.tw 的憑證缺少 Subject Key Identifier 擴充欄位，
# 導致 Python 預設的 SSL 驗證失敗。此為台灣政府憑證的已知問題，
# 建立不驗證憑證的 context 繞過，僅用於此可信任的政府來源。
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# CWA API 回應快取：同一次查詢（約 2 分鐘內）避免重複下載全台縣市資料
# 每次 get_pop_forecast / get_cloud_from_cwa 都需要全台資料，14 個地點 × 2 個欄位 = 28 次呼叫
# 使用模組層級快取後只需呼叫 2 次（PoP 和 Wx 各一次）
_CACHE_TTL = 120  # 快取有效秒數
_cache_lock = threading.Lock()
_cache: dict = {
    "pop_locations": None,
    "wx_locations": None,
    "pop_ts": 0.0,
    "wx_ts": 0.0,
}

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CWA_FORECAST_URL = (
    "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-091"
)
# CWA API 的欄位名稱（中文，需 URL encode）
ELEMENT_NAME = "12小時降雨機率"
ELEMENT_WX   = "天氣現象"

# CWA 天氣代碼 → 估計雲量(%)
# 代碼 1–7：無降水，依文字描述對應雲量區間中位數
# 代碼 8+：有降水，雲層幾乎全覆蓋
CWA_CODE_TO_CLOUD = {
    1: 5,    # 晴
    2: 20,   # 晴時多雲
    3: 35,   # 多雲時晴
    4: 55,   # 多雲
    5: 70,   # 多雲時陰
    6: 85,   # 陰
    7: 75,   # 陰時多雲
}


def _get_api_key() -> Optional[str]:
    """從環境變數讀取 CWA API 金鑰"""
    return os.environ.get("CWA_API_KEY") or None


def get_pop_forecast(lat: float, lon: float, target_date: date) -> Optional[dict]:
    """
    查詢指定座標最近縣市的夜間降雨機率

    Args:
        lat:         目標地點緯度
        lon:         目標地點經度
        target_date: 目標日期

    Returns:
        {
            "county":        str,   # 縣市名稱
            "distance_km":   float, # 距離（公里）
            "max_pop":       int,   # 夜間最高降雨機率（%）
            "pop_intervals": list,  # 各時段 [{start, end, pop}, ...]
        }
        未設定 API 金鑰時回傳 {"error": "no_key"}
        查詢失敗時回傳 None
    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "no_key"}

    try:
        locations = _fetch_all_locations(api_key)
        if not locations:
            return None
        nearest = _find_nearest_location(lat, lon, locations)
        if nearest is None:
            return None
        return _extract_night_pop(nearest, target_date)
    except Exception:
        return None


def _fetch_all_locations(api_key: str) -> list:
    """
    從 CWA API 取得全台縣市的 12小時降雨機率資料（含快取，避免重複下載）

    F-D0047-091 回傳結構：
    {
        "records": {
            "Locations": [
                {
                    "LocationsName": "台灣",   ← 群組名
                    "Location": [
                        {
                            "LocationName": "臺東縣",   ← 縣市名
                            "Latitude":  "23.10",
                            "Longitude": "121.37",
                            "WeatherElement": [
                                {
                                    "ElementName": "12小時降雨機率",
                                    "Time": [
                                        {
                                            "StartTime": "2026-04-11T18:00:00+08:00",
                                            "EndTime":   "2026-04-12T06:00:00+08:00",
                                            "ElementValue": [
                                                {"ProbabilityOfPrecipitation": "10"}
                                            ]
                                        },
                                        ...
                                    ]
                                }
                            ]
                        },
                        ...  # 其他縣市
                    ]
                },
            ]
        }
    }
    """
    with _cache_lock:
        if _cache["pop_locations"] is not None and time.time() - _cache["pop_ts"] < _CACHE_TTL:
            return _cache["pop_locations"]

    params = urllib.parse.urlencode({
        "Authorization": api_key,
        "format": "JSON",
        "elementName": ELEMENT_NAME,
    })
    url = f"{CWA_FORECAST_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "galaxy-guide/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            locations = []
            for group in data.get("records", {}).get("Locations", []):
                locations.extend(group.get("Location", []))
            with _cache_lock:
                _cache["pop_locations"] = locations
                _cache["pop_ts"] = time.time()
            return locations
    except Exception:
        return []


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine 公式計算球面距離（公里）"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _find_nearest_location(lat: float, lon: float, locations: list) -> Optional[dict]:
    """找出距離目標座標最近的縣市"""
    best = None
    best_dist = float("inf")

    for loc in locations:
        try:
            slat = float(loc.get("Latitude", 0) or 0)
            slon = float(loc.get("Longitude", 0) or 0)
            if slat == 0 or slon == 0:
                continue
            dist = _haversine_km(lat, lon, slat, slon)
            if dist < best_dist:
                best_dist = dist
                best = {**loc, "_distance_km": dist}
        except (ValueError, TypeError):
            continue

    return best


def _parse_cwa_time(time_str: str) -> datetime:
    """
    解析 CWA 的 ISO 8601 時間字串，回傳不含時區的 datetime

    CWA 格式：'2026-04-11T18:00:00+08:00'
    Python 3.7 的 fromisoformat 不支援 +08:00，故手動去除時區後綴。
    CWA 所有時間均為 UTC+8，去除後即為台灣本地時間。
    """
    # 去除末尾的時區部分 (+08:00 或 Z)
    clean = time_str.split("+")[0].replace("T", " ")
    return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")


def _extract_night_pop(location: dict, target_date: date) -> dict:
    """
    從縣市資料中提取夜間（20:00 當日 ~ 05:00 隔日）的降雨機率

    PoP12h 的夜間時段固定為 18:00–06:00，完整涵蓋我們的夜間窗口：
        18:00–06:00  →  重疊 20:00–05:00，納入  ✓
        06:00–18:00  →  無重疊，略過
    """
    next_day = target_date + timedelta(days=1)
    night_start = datetime(target_date.year, target_date.month, target_date.day, 20, 0)
    night_end   = datetime(next_day.year,    next_day.month,    next_day.day,    5,  0)

    pop_intervals = []

    for element in location.get("WeatherElement", []):
        if element.get("ElementName") != ELEMENT_NAME:
            continue

        for time_block in element.get("Time", []):
            try:
                t_start = _parse_cwa_time(time_block.get("StartTime", ""))
                t_end   = _parse_cwa_time(time_block.get("EndTime", ""))

                # 時段與夜間有重疊就納入
                if t_end <= night_start or t_start >= night_end:
                    continue

                val_list = time_block.get("ElementValue", [])
                raw_val = val_list[0].get("ProbabilityOfPrecipitation", "0") if val_list else "0"
                # CWA 偶爾回傳 "-" 表示無資料
                try:
                    pop_val = int(raw_val)
                    if pop_val < 0:
                        pop_val = 0
                except (ValueError, TypeError):
                    pop_val = 0

                pop_intervals.append({
                    "start": t_start,
                    "end":   t_end,
                    "pop":   pop_val,
                })
            except (ValueError, KeyError):
                continue

    max_pop = max((p["pop"] for p in pop_intervals), default=0)

    return {
        "county":        location.get("LocationName", "未知"),
        "distance_km":   round(location.get("_distance_km", 0), 1),
        "max_pop":       max_pop,
        "pop_intervals": pop_intervals,
    }


def format_pop_report(pop_data: Optional[dict], is_future_date: bool = False) -> str:
    """格式化降雨機率資料為報告字串"""
    if pop_data is None:
        return "降雨機率：無法取得（CWA API 無回應）"
    if pop_data.get("error") == "no_key":
        return "降雨機率：未設定 CWA_API_KEY（詳見 cwa.py 說明）"

    county   = pop_data["county"]
    dist     = pop_data["distance_km"]
    max_pop  = pop_data["max_pop"]
    label    = "（預報）" if is_future_date else "（即時）"

    lines = [
        f"降雨機率{label}：夜間最高 {max_pop}%",
        f"  縣市：{county}（距離 {dist} km）",
    ]
    for iv in pop_data["pop_intervals"]:
        lines.append(
            f"  {iv['start'].strftime('%m/%d %H:%M')}–{iv['end'].strftime('%m/%d %H:%M')}：{iv['pop']}%"
        )
    return "\n".join(lines)


def get_cloud_from_cwa(lat: float, lon: float, target_date: date) -> Optional[dict]:
    """
    查詢 CWA 天氣現象，轉換為夜間估計雲量

    CWA 的 F-D0047-091 天氣現象為 12 小時一筆（夜間 18:00–06:00），
    比 Open-Meteo 全球模型更能反映台灣地形影響（如花東縱谷、山地對流）。

    Returns:
        {
            "county":       str,   # 縣市名稱
            "distance_km":  float,
            "wx_text":      str,   # 天氣描述，如「多雲」
            "wx_code":      int,   # 天氣代碼
            "est_cloud":    int,   # 估計雲量 %
        }
        未設定 API 金鑰時回傳 {"error": "no_key"}
        查詢失敗時回傳 None
    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "no_key"}

    try:
        locations = _fetch_locations_wx(api_key)
        if not locations:
            return None
        nearest = _find_nearest_location(lat, lon, locations)
        if nearest is None:
            return None
        return _extract_night_wx(nearest, target_date)
    except Exception:
        return None


def _fetch_locations_wx(api_key: str) -> list:
    """從 CWA API 取得全台縣市的天氣現象資料（含快取，避免重複下載）"""
    with _cache_lock:
        if _cache["wx_locations"] is not None and time.time() - _cache["wx_ts"] < _CACHE_TTL:
            return _cache["wx_locations"]

    params = urllib.parse.urlencode({
        "Authorization": api_key,
        "format": "JSON",
        "elementName": ELEMENT_WX,
    })
    url = f"{CWA_FORECAST_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "galaxy-guide/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            locations = []
            for group in data.get("records", {}).get("Locations", []):
                locations.extend(group.get("Location", []))
            with _cache_lock:
                _cache["wx_locations"] = locations
                _cache["wx_ts"] = time.time()
            return locations
    except Exception:
        return []


def _extract_night_wx(location: dict, target_date: date) -> Optional[dict]:
    """
    從縣市資料提取夜間天氣現象，轉換為估計雲量

    CWA 夜間時段為 18:00–06:00，與我們的 20:00–05:00 完整重疊。
    取目標夜間（18:00–06:00）對應的天氣代碼。
    """
    next_day = target_date + timedelta(days=1)
    # 目標夜間：當日 18:00 ~ 隔日 06:00
    night_start = datetime(target_date.year, target_date.month, target_date.day, 18, 0)
    night_end   = datetime(next_day.year, next_day.month, next_day.day, 6, 0)

    for element in location.get("WeatherElement", []):
        if element.get("ElementName") != ELEMENT_WX:
            continue

        for time_block in element.get("Time", []):
            try:
                t_start = _parse_cwa_time(time_block.get("StartTime", ""))
                t_end   = _parse_cwa_time(time_block.get("EndTime", ""))

                # 找夜間時段（18:00 當日 ~ 06:00 隔日）
                if t_start != night_start or t_end != night_end:
                    continue

                val_list = time_block.get("ElementValue", [])
                if not val_list:
                    continue

                wx_text = val_list[0].get("Weather", "未知")
                wx_code = int(val_list[0].get("WeatherCode", "0") or 0)

                # 代碼 8+ 代表有降水，雲量設為 90%
                est_cloud = CWA_CODE_TO_CLOUD.get(wx_code, 90)

                return {
                    "county":      location.get("LocationName", "未知"),
                    "distance_km": round(location.get("_distance_km", 0), 1),
                    "wx_text":     wx_text,
                    "wx_code":     wx_code,
                    "est_cloud":   est_cloud,
                }
            except (ValueError, KeyError, TypeError):
                continue

    return None


# ── 測試：直接執行此檔案 ─────────────────────────────────────
if __name__ == "__main__":
    from datetime import date

    test_locations = [
        ("台東三仙台",   23.1406, 121.4197),
        ("屏東龍磐公園", 21.9736, 120.8614),
        ("澎湖吉貝嶼",   23.6397, 119.5789),
    ]
    today = date.today()
    print("CWA 降雨機率查詢測試\n")
    for name, lat, lon in test_locations:
        print(f"📍 {name}（{lat}°N, {lon}°E）")
        data = get_pop_forecast(lat, lon, today)
        print(format_pop_report(data, is_future_date=False))
        print()
