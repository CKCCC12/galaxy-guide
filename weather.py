# weather.py
# 天氣查詢模組：串接 Open-Meteo 免費 API 取得雲量、能見度、空氣品質資料
#
# 為什麼用 Open-Meteo？
#   - 完全免費，無需 API 金鑰
#   - 提供逐小時雲量（cloud_cover），精度 1%
#   - 支援高/中/低雲層分別查詢（低雲最影響能見度）
#   - 提供能見度（visibility）、氣膠光學厚度（AOD）、沙塵、濕度預報
#   - 可查未來 7 天預報，適合週末規劃
#
# 雲量判斷標準（適合拍銀河）：
#   0-20%  ☆☆  良好，銀河清晰可見
#   21-40% ☆   普通，有薄雲干擾，仍可嘗試
#   41%+   ✗   不建議，雲層遮蔽銀河
#
# 能見度判斷標準（大氣透明度）：
#   > 20km  極佳，大氣非常透明
#   10-20km 良好，星光清晰
#   5-10km  普通，有霾，稍微影響
#   < 5km   不佳，霾/霧嚴重
#
# AOD（氣膠光學厚度）判斷標準：
#   < 0.1   極佳，幾乎無懸浮粒子
#   0.1-0.2 良好
#   0.2-0.3 普通，銀河明顯受影響
#   ≥ 0.3   0 分，不建議（計分門檻）

import urllib.request
import urllib.error
import json
import ssl
import time
import threading
from datetime import date, datetime, timedelta
import pytz

TW_TZ = pytz.timezone("Asia/Taipei")

# Render 上 Python 的 SSL 驗證對部分 API 會失敗，建立不驗證的 context
# （與 cwa.py、airquality.py 相同做法）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Open-Meteo 逐小時資料快取
# key: (lat, lon, date_str)，TTL 30 分鐘
# 同一次查詢 14 個地點各自需要資料，快取後使用者重新整理或
# 不同使用者查詢同一天，直接從記憶體取用，不再打 HTTP 請求
_CACHE_TTL = 1800  # 30 分鐘
_cache_lock = threading.Lock()
_cache: dict = {}

# 速率限制：Open-Meteo 免費版對每個 IP 有請求頻率上限。
# 14 個地點同時查詢（3 個 worker）會短時間大量打同一個 API，
# 導致 HTTP 429 Too Many Requests。
# 用全域鎖確保兩次 Open-Meteo 請求至少間隔 0.5 秒。
_rate_state: dict = {"last_ts": 0.0}
_rate_lock = threading.Lock()
_MIN_INTERVAL = 0.5  # 秒

# Open-Meteo API 端點
API_URL = "https://api.open-meteo.com/v1/forecast"


def get_cloud_forecast(lat: float, lon: float, target_date: date) -> dict:
    """
    查詢指定座標、日期的逐小時天氣預報
    包含：雲量、能見度、氣膠光學厚度、沙塵濃度、相對濕度

    Args:
        lat: 緯度
        lon: 經度
        target_date: 目標日期（需在今天起 7 天內）

    Returns:
        {
            "hourly": [...],            # 所有小時資料
            "night_hours": [...],       # 夜間時段（20:00~05:00）
            "night_summary": {...},     # 夜間統計摘要
            "best_hours": [...],        # 雲量 < 40% 的連續時段
        }
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "cloud_cover,cloud_cover_low,visibility,aerosol_optical_depth,dust,relative_humidity_2m",
        "timezone": "Asia/Taipei",
        "start_date": target_date.strftime("%Y-%m-%d"),
        "end_date": (target_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        "wind_speed_unit": "kmh",
    }

    raw_data = _fetch_api(params)
    hourly_data = _parse_hourly(raw_data, target_date)
    night_hours = _filter_night_hours(hourly_data, target_date)
    night_summary = _calc_night_summary(night_hours)
    best_hours = _find_best_windows(night_hours)

    return {
        "hourly": hourly_data,
        "night_hours": night_hours,
        "night_summary": night_summary,
        "best_hours": best_hours,
    }


def _fetch_api(params: dict) -> dict:
    """
    發送 GET 請求到 Open-Meteo API（含快取、SSL 降級、錯誤記錄）

    快取 key 由 lat / lon / start_date 組成，TTL 30 分鐘。

    嘗試策略（依序）：
    1. 自訂 SSL context（繞過憑證驗證），timeout 25s
       → 解決 Render 環境下 SSL CERTIFICATE_VERIFY_FAILED
    2. 標準 SSL（系統預設 CA），timeout 30s
       → 解決 1 的 context 本身引起的問題
    每次失敗都印出錯誤類型供 Render 日誌診斷。
    """
    cache_key = (params.get("latitude"), params.get("longitude"), params.get("start_date"))
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and time.time() - entry["ts"] < _CACHE_TTL:
            return entry["data"]

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_URL}?{query_string}"

    last_error = None
    strategies = [
        ("SSL-bypass",  _SSL_CTX, 25),
        ("SSL-default", None,     30),
    ]

    for label, ssl_ctx, timeout in strategies:
        raw = None

        for attempt in range(3):  # 每種 SSL 策略最多重試 3 次（主要應對 429）
            # ── 速率限制：確保兩次 Open-Meteo 請求至少間隔 0.5s ──
            # 「預訂時間槽」模式：lock 只做計算，sleep 在 lock 外，
            # 讓 3 個 worker 各自拿到不同的時間槽（t, t+0.5, t+1.0...），
            # 不會同時發送，也不會因為 lock 內 sleep 而排隊堆疊。
            with _rate_lock:
                now = time.time()
                next_ok = max(_rate_state["last_ts"] + _MIN_INTERVAL, now)
                _rate_state["last_ts"] = next_ok   # 預訂這個時間槽
            wait = next_ok - time.time()
            if wait > 0:
                time.sleep(wait)

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "galaxy-guide/1.0"})
                kw: dict = {"timeout": timeout}
                if ssl_ctx is not None:
                    kw["context"] = ssl_ctx
                with urllib.request.urlopen(req, **kw) as response:
                    raw = response.read().decode("utf-8")
                break  # HTTP 成功，跳出重試迴圈

            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    # 429：依指數退避等待，再試一次
                    wait = (attempt + 1) * 3  # 3s → 6s
                    print(f"[weather] {label} attempt={attempt+1} 429 限流，等待 {wait}s 後重試...")
                    time.sleep(wait)
                    # 繼續下一次 attempt
                else:
                    print(f"[weather] {label} attempt={attempt+1} HTTP {e.code}: {e.reason}")
                    last_error = e
                    break

            except Exception as e:
                print(f"[weather] {label} attempt={attempt+1} 失敗: {type(e).__name__}: {e}")
                last_error = e
                break

        if raw is None:
            continue  # 此 SSL 策略全部失敗，試下一個

        # ── JSON 解析（失敗直接往上拋，不換策略重試） ──
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[weather] JSON 解析失敗: {e}")
            raise ValueError(f"Open-Meteo 回傳資料格式錯誤: {e}")

        # ── API 應用層錯誤（如參數錯誤，重試無益） ──
        if isinstance(data, dict) and data.get("error"):
            reason = data.get("reason", "未知原因")
            print(f"[weather] Open-Meteo 拒絕請求: {reason}")
            raise ValueError(f"Open-Meteo API 拒絕請求：{reason}")

        with _cache_lock:
            _cache[cache_key] = {"data": data, "ts": time.time()}
        return data

    print(f"[weather] Open-Meteo 所有策略均失敗，最後錯誤: {type(last_error).__name__}: {last_error}")
    raise ConnectionError(f"無法連線到 Open-Meteo API：{last_error}")


def _parse_hourly(raw_data: dict, target_date: date) -> list:
    """
    將 API 回傳的時間序列資料解析為結構化清單

    Open-Meteo 回傳格式：
    {
        "hourly": {
            "time": ["2026-04-11T00:00", ...],
            "cloud_cover": [10, 25, ...],
            "cloud_cover_low": [5, 10, ...],
            "visibility": [24140, 18000, ...],      # 單位：公尺
            "aerosol_optical_depth": [0.08, 0.12, ...],
            "dust": [2.1, 3.5, ...],                # 單位：μg/m³
            "relative_humidity_2m": [65, 72, ...]   # 單位：%
        }
    }
    """
    if "hourly" not in raw_data:
        # 印出實際收到的欄位，方便診斷 API 回應結構
        print(f"[weather] Open-Meteo 回應缺少 hourly，收到欄位: {list(raw_data.keys())}")
        raise ValueError(f"Open-Meteo 回應缺少 hourly 資料")
    h = raw_data["hourly"]
    times = h["time"]
    cloud_cover = h["cloud_cover"]
    cloud_low = h["cloud_cover_low"]
    visibility = h.get("visibility", [None] * len(times))
    aod = h.get("aerosol_optical_depth", [None] * len(times))
    dust = h.get("dust", [None] * len(times))
    humidity = h.get("relative_humidity_2m", [None] * len(times))

    result = []
    for i, time_str in enumerate(times):
        dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
        dt = TW_TZ.localize(dt)

        cc = cloud_cover[i] if cloud_cover[i] is not None else 100
        cl = cloud_low[i] if cloud_low[i] is not None else 100
        vis = visibility[i] if visibility[i] is not None else 0
        aod_val = aod[i] if aod[i] is not None else 0.5
        dust_val = dust[i] if dust[i] is not None else 0.0
        hum = humidity[i] if humidity[i] is not None else 0

        # 逐小時銀河可見分數（與綜合評分同公式，不含月光/仰角）
        vis_km = vis / 1000
        _cloud_s = max(0.0, (100 - cc) / 100)
        _aod_s   = max(0.0, 1.0 - aod_val / 0.3)
        if vis_km >= 20:   _vis_s = 1.0
        elif vis_km >= 10: _vis_s = 0.7
        elif vis_km >= 5:  _vis_s = 0.3
        else:              _vis_s = 0.0
        hour_score = round((_cloud_s * 0.40 + _aod_s * 0.30 + _vis_s * 0.30) * 100)

        result.append({
            "time": dt,
            "cloud_cover": cc,
            "cloud_low": cl,
            "visibility_m": vis,
            "visibility_km": round(vis / 1000, 1),
            "aod": round(aod_val, 3),
            "dust": round(dust_val, 1),
            "humidity": hum,
            "rating": _get_rating(cc, cl, vis, aod_val),
            "suitable": cc <= 40,
            "warnings": _get_warnings(dust_val, hum),
            "hour_score": hour_score,
        })

    return result


def _get_rating(cloud_cover: int, cloud_low: int, visibility_m: float, aod: float) -> str:
    """
    依雲量、低雲、能見度、AOD 綜合給出逐小時評級

    優先判斷最嚴重的限制因素：
    - 低雲 > 能見度 > 雲量 > AOD
    """
    # 低雲嚴重時直接降級
    if cloud_low >= 30:
        return "不建議（低雲遮蔽）" if cloud_cover > 20 else "普通（低雲偏多）"

    # 能見度極差
    if visibility_m < 5000:
        return "不建議（能見度極差）"

    # 雲量基礎評級
    if cloud_cover <= 20:
        base = "良好"
    elif cloud_cover <= 40:
        base = "普通"
    else:
        return "不建議"

    # AOD 附加說明
    if aod >= 0.3:
        return f"{base}（AOD 偏高）"
    if visibility_m < 10000:
        return f"{base}（能見度偏低）"

    return base


def _get_warnings(dust: float, humidity: int) -> list:
    """回傳需特別注意的警告清單"""
    warnings = []
    if dust >= 50:
        warnings.append("⚠️ 沙塵偏高")
    elif dust >= 20:
        warnings.append("⚠️ 輕微沙塵")
    if humidity >= 90:
        warnings.append("💧 注意結露")
    elif humidity >= 85:
        warnings.append("💧 濕度偏高")
    return warnings


def _filter_night_hours(hourly_data: list, target_date: date) -> list:
    """篩選出夜間觀測時段：當天 20:00 到隔天 05:00"""
    # 用 timedelta 計算隔天，避免月末 day+1 超出當月天數（如 4/30+1=31 會 crash）
    next_day = target_date + timedelta(days=1)
    night_start = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 20, 0))
    night_end   = TW_TZ.localize(datetime(next_day.year, next_day.month, next_day.day, 5, 0))
    return [h for h in hourly_data if night_start <= h["time"] <= night_end]


def _calc_night_summary(night_hours: list) -> dict:
    """
    計算整晚夜間天氣統計摘要

    Returns:
        {
            "avg_cloud": float,
            "min_cloud": int,
            "max_cloud": int,
            "suitable_hours": int,
            "overall_rating": str,
            "avg_visibility_km": float,
            "min_visibility_km": float,
            "avg_aod": float,
            "max_humidity": int,
            "max_dust": float,
        }
    """
    if not night_hours:
        return {"avg_cloud": 100, "overall_rating": "無資料"}

    covers = [h["cloud_cover"] for h in night_hours]
    suitable = sum(1 for h in night_hours if h["suitable"])
    avg = sum(covers) / len(covers)

    vis_list = [h["visibility_km"] for h in night_hours]
    aod_list = [h["aod"] for h in night_hours]
    hum_list = [h["humidity"] for h in night_hours]
    dust_list = [h["dust"] for h in night_hours]

    overall = _get_overall_rating(avg, suitable, len(night_hours))

    return {
        "avg_cloud": round(avg, 1),
        "min_cloud": min(covers),
        "max_cloud": max(covers),
        "suitable_hours": suitable,
        "total_hours": len(night_hours),
        "overall_rating": overall,
        "avg_visibility_km": round(sum(vis_list) / len(vis_list), 1),
        "min_visibility_km": round(min(vis_list), 1),
        "avg_aod": round(sum(aod_list) / len(aod_list), 3),
        "max_humidity": max(hum_list),
        "max_dust": round(max(dust_list), 1),
    }


def _get_overall_rating(avg_cloud: float, suitable_hours: int, total_hours: int) -> str:
    """依整晚平均雲量給出整體評級"""
    suitable_ratio = suitable_hours / total_hours if total_hours > 0 else 0

    if avg_cloud <= 20 and suitable_ratio >= 0.7:
        return "良好（整晚多為晴空）"
    elif avg_cloud <= 40 and suitable_ratio >= 0.4:
        return "普通（部分時段有機會）"
    else:
        return "不建議（雲層厚重）"


def _find_best_windows(night_hours: list) -> list:
    """
    找出雲量 ≤ 40% 的連續時段（最佳拍攝窗口）

    Returns:
        [{"start": datetime, "end": datetime, "avg_cloud": float, "duration_hours": float}]
    """
    windows = []
    in_window = False
    window_start = None
    window_clouds = []

    for h in night_hours:
        if h["suitable"] and not in_window:
            in_window = True
            window_start = h["time"]
            window_clouds = [h["cloud_cover"]]
        elif h["suitable"] and in_window:
            window_clouds.append(h["cloud_cover"])
        elif not h["suitable"] and in_window:
            in_window = False
            duration = (h["time"] - window_start).total_seconds() / 3600
            windows.append({
                "start": window_start,
                "end": h["time"],
                "avg_cloud": round(sum(window_clouds) / len(window_clouds), 1),
                "duration_hours": round(duration, 1),
            })
            window_clouds = []

    if in_window and window_start and night_hours:
        last = night_hours[-1]
        duration = (last["time"] - window_start).total_seconds() / 3600 + 1
        windows.append({
            "start": window_start,
            "end": last["time"],
            "avg_cloud": round(sum(window_clouds) / len(window_clouds), 1),
            "duration_hours": round(duration, 1),
        })

    return windows


def format_weather_report(forecast: dict) -> str:
    """
    將天氣查詢結果格式化為人類可讀的中文報告（含能見度、AOD、濕度）
    """
    lines = []
    s = forecast["night_summary"]

    lines.append(f"整晚天氣評估：{s['overall_rating']}")
    lines.append(f"雲量範圍：{s['min_cloud']}% ~ {s['max_cloud']}%（平均 {s['avg_cloud']}%）")
    lines.append(f"適合拍攝時數：{s['suitable_hours']} / {s['total_hours']} 小時")
    lines.append(f"平均能見度：{s.get('avg_visibility_km', '?')} km（最低 {s.get('min_visibility_km', '?')} km）")
    lines.append(f"平均 AOD：{s.get('avg_aod', '?')}（氣膠光學厚度）")
    lines.append(f"最高濕度：{s.get('max_humidity', '?')}%")

    if s.get("max_dust", 0) >= 20:
        lines.append(f"⚠️  最高沙塵濃度：{s.get('max_dust')} μg/m³")

    if forecast["best_hours"]:
        lines.append("晴朗時段（雲量 ≤ 40%）：")
        for w in forecast["best_hours"]:
            start_str = w["start"].strftime("%H:%M")
            end_str = w["end"].strftime("%H:%M")
            lines.append(f"  {start_str} — {end_str}（{w['duration_hours']} 小時，平均雲量 {w['avg_cloud']}%）")
    else:
        lines.append("❌ 整晚雲量偏高，無適合拍攝時段")

    lines.append("\n逐小時天氣（夜間）：")
    lines.append(f"  {'時間':5}  {'雲量':22}  {'能見度':>7}  {'AOD':>6}  {'濕度':>4}  評級")
    lines.append("  " + "-" * 72)
    for h in forecast["night_hours"]:
        bar_len = h["cloud_cover"] // 5
        bar = "█" * bar_len + "░" * (20 - bar_len)
        time_str = h["time"].strftime("%H:%M")
        warnings = " ".join(h["warnings"]) if h["warnings"] else ""
        lines.append(
            f"  {time_str}  [{bar}] {h['cloud_cover']:3d}%"
            f"  {h['visibility_km']:>5.1f}km"
            f"  {h['aod']:>5.3f}"
            f"  {h['humidity']:>3d}%"
            f"  {h['rating']}"
            f"  {warnings}"
        )

    return "\n".join(lines)


# ── 測試：直接執行此檔案 ─────────────────────────────────────
if __name__ == "__main__":
    from datetime import date, timedelta

    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)

    LAT = 23.1406
    LON = 121.4197

    print(f"天氣查詢測試")
    print(f"日期：{saturday}（週六）")
    print(f"地點：台東三仙台（{LAT}°N, {LON}°E）\n")

    forecast = get_cloud_forecast(LAT, LON, saturday)

    print("=" * 75)
    print(format_weather_report(forecast))
    print("=" * 75)
