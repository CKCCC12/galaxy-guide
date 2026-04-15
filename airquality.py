# airquality.py
# 台灣環保署空氣品質查詢模組
#
# 資料來源：環保署 Open Data API（免費，無需金鑰）
#   端點：https://data.epa.gov.tw/api/v2/aqx_p_432
#   回傳：全台所有測站的即時 AQI、PM2.5、PM10 等數值
#
# 重要限制：
#   環保署 API 只有「當下」的即時資料，沒有未來預報。
#   因此在查詢未來日期時，EPA 資料僅作為「地點背景參考值」，
#   並在報告中明確標示「今日鄰近測站」。
#
# AQI 等級對照（台灣標準）：
#   0-50   良好（Good）         空氣品質佳，適合戶外活動
#   51-100 普通（Moderate）     敏感族群需注意
#   101-150 對敏感族群不健康    一般人影響不大
#   151-200 對所有人不健康      避免長時間戶外活動
#   201+   非常不健康/危險      建議留在室內
#
# 計算距離的方式：
#   使用 Haversine 公式計算球面距離，找出最近的監測站。
#   台灣南北約 390km，東西約 140km，用球面公式比平面更準確。

import urllib.request
import json
import math
import os
import ssl
import time
import threading
from typing import Optional

# data.moenv.gov.tw 的 SSL 憑證與 CWA 同樣有已知相容性問題，
# 建立不驗證憑證的 context 繞過（僅用於此可信任的政府來源）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# MOENV API 回應快取：同一次查詢（約 2 分鐘內）避免重複下載全台測站資料
# 14 個地點各呼叫一次 = 14 次 HTTP 請求，快取後只需 1 次
_CACHE_TTL = 120  # 快取有效秒數
_cache_lock = threading.Lock()
_cache: dict = {
    "stations": None,
    "stations_ts": 0.0,
}

# 自動載入專案根目錄的 .env 檔案
# dotenv_values 只在 python-dotenv 有安裝時才生效；
# 若未安裝，os.environ 本來就有的環境變數仍可使用。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 環境部（前身環保署）Open Data API
#
# 需要免費申請 API 金鑰：
#   1. 前往 https://data.moenv.gov.tw
#   2. 右上角「登入/註冊」→ 申請帳號
#   3. 登入後至「會員專區」→「API 金鑰申請」
#   4. 取得金鑰後，設定環境變數：
#      export MOENV_API_KEY="你的金鑰"
#   5. 或在此檔案同層建立 .env 設定（不要 commit 進 git）
#
# 未設定金鑰時：空氣品質欄位將顯示「未設定金鑰」，
# 評分改為只用 Open-Meteo 的 AOD 資料計算。
EPA_AQI_URL = "https://data.moenv.gov.tw/api/v2/aqx_p_432"

def _get_api_key() -> Optional[str]:
    """從環境變數讀取環境部 API 金鑰"""
    return os.environ.get("MOENV_API_KEY") or None


def get_current_aqi(lat: float, lon: float) -> Optional[dict]:
    """
    查詢指定座標最近的環境部監測站即時 AQI

    Args:
        lat: 目標地點緯度
        lon: 目標地點經度

    Returns:
        {
            "station_name": str,        # 測站名稱
            "county": str,              # 縣市
            "distance_km": float,       # 距離（公里）
            "aqi": int,                 # AQI 數值
            "pm25": float,              # PM2.5（μg/m³）
            "pm10": float,              # PM10（μg/m³）
            "status": str,              # 狀態描述
            "publish_time": str,        # 資料更新時間
            "aqi_score": float,         # 換算為 0-1 的分數（供評分用）
        }
        未設定 API 金鑰時回傳 {"error": "no_key"}
        查詢失敗時回傳 None
    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "no_key"}

    try:
        stations = _fetch_all_stations(api_key)
        nearest = _find_nearest_station(lat, lon, stations)
        if nearest is None:
            return None
        return _parse_station(nearest)
    except Exception:
        return None


def _fetch_all_stations(api_key: str) -> list:
    """
    從環境部 API 取得所有測站資料（含快取，避免 14 個地點重複下載）

    API 回傳格式：
    {
        "records": [
            {
                "SiteName": "三重",
                "County": "新北市",
                "AQI": "45",
                "PM2.5": "12",
                "PM10": "20",
                "Status": "良好",
                "Latitude": "25.06",
                "Longitude": "121.49",
                "PublishTime": "2026-04-11 20:00"
            },
            ...
        ]
    }
    """
    with _cache_lock:
        if _cache["stations"] is not None and time.time() - _cache["stations_ts"] < _CACHE_TTL:
            return _cache["stations"]

    url = f"{EPA_AQI_URL}?format=json&limit=200&api_key={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": "galaxy-guide/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            # API 可能回傳陣列或 {"records": [...]}
            if isinstance(data, list):
                stations = data
            else:
                stations = data.get("records", [])
            with _cache_lock:
                _cache["stations"] = stations
                _cache["stations_ts"] = time.time()
            return stations
    except Exception:
        return []


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    用 Haversine 公式計算兩點之間的球面距離（公里）

    Haversine 公式是什麼？
      地球是球體，兩點間「直線距離」並非沿地表的距離。
      Haversine 公式透過球面三角計算地表弧線距離，
      對台灣這個範圍（~4 度緯度）來說，誤差 < 0.1%。

    R = 6371 是地球平均半徑（公里）
    """
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _find_nearest_station(lat: float, lon: float, stations: list) -> Optional[dict]:
    """找出距離目標座標最近的有效測站（必須有有效 AQI 數值）"""
    best = None
    best_dist = float("inf")

    for station in stations:
        try:
            # 欄位名稱為小寫
            slat = float(station.get("latitude", 0) or 0)
            slon = float(station.get("longitude", 0) or 0)
            aqi_str = station.get("aqi", "")
            if not aqi_str or not str(aqi_str).strip().isdigit():
                continue
            if slat == 0 or slon == 0:
                continue

            dist = _haversine_km(lat, lon, slat, slon)
            if dist < best_dist:
                best_dist = dist
                best = {**station, "_distance_km": dist}
        except (ValueError, TypeError):
            continue

    return best


def _safe_float(value, default=0.0) -> float:
    """安全地將字串轉為 float，失敗時回傳預設值"""
    try:
        return float(value) if value and str(value).strip() not in ("", "--", "N/A") else default
    except (ValueError, TypeError):
        return default


def _parse_station(station: dict) -> dict:
    """將測站原始資料整理為結構化格式（欄位為小寫）"""
    aqi = int(_safe_float(station.get("aqi", 0)))
    pm25 = _safe_float(station.get("pm2.5"))
    pm10 = _safe_float(station.get("pm10"))
    dist = round(station.get("_distance_km", 0), 1)

    # AQI → 0-1 分數（越低越好）
    aqi_score = max(0.0, 1.0 - aqi / 150.0)

    return {
        "station_name": station.get("sitename", "未知"),
        "county": station.get("county", "未知"),
        "distance_km": dist,
        "aqi": aqi,
        "pm25": pm25,
        "pm10": pm10,
        "status": station.get("status", "未知"),
        "publish_time": station.get("publishtime", "未知"),
        "aqi_score": round(aqi_score, 3),
    }


def get_aqi_level(aqi: int) -> str:
    """
    依 AQI 數值回傳台灣標準等級描述

    台灣 AQI 等級與國際一致，但對 PM2.5 的敏感度有在地化調整。
    """
    if aqi <= 50:
        return "良好"
    elif aqi <= 100:
        return "普通"
    elif aqi <= 150:
        return "對敏感族群不健康"
    elif aqi <= 200:
        return "對所有人不健康"
    elif aqi <= 300:
        return "非常不健康"
    else:
        return "危險"


def format_aqi_report(aqi_data: Optional[dict], is_future_date: bool = False) -> str:
    """
    格式化環境部 AQI 資料為報告字串

    Args:
        aqi_data: get_current_aqi() 的回傳值
        is_future_date: 若為未來日期，加上「參考值」標示
    """
    if aqi_data is None:
        return "空氣品質：無法取得（環境部 API 無回應）"
    if aqi_data.get("error") == "no_key":
        return "空氣品質：未設定 MOENV_API_KEY（詳見 airquality.py 說明）"

    label = "（今日參考值）" if is_future_date else ""
    level = get_aqi_level(aqi_data["aqi"])

    lines = [
        f"空氣品質{label}：AQI {aqi_data['aqi']}（{level}）",
        f"  測站：{aqi_data['county']} {aqi_data['station_name']}（距離 {aqi_data['distance_km']} km）",
        f"  PM2.5：{aqi_data['pm25']} μg/m³　PM10：{aqi_data['pm10']} μg/m³",
        f"  資料時間：{aqi_data['publish_time']}",
    ]
    return "\n".join(lines)


# ── 測試：直接執行此檔案 ─────────────────────────────────────
if __name__ == "__main__":
    test_locations = [
        ("台東三仙台", 23.1406, 121.4197),
        ("屏東龍磐公園", 21.9736, 120.8614),
        ("澎湖吉貝嶼", 23.6397, 119.5789),
    ]

    print("環保署 AQI 查詢測試\n")
    for name, lat, lon in test_locations:
        print(f"📍 {name}（{lat}°N, {lon}°E）")
        data = get_current_aqi(lat, lon)
        print(format_aqi_report(data))
        if data and "aqi_score" in data:
            print(f"  評分換算：{data['aqi_score']:.2f} / 1.00")
        print()
