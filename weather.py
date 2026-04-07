# weather.py
# 天氣查詢模組：串接 Open-Meteo 免費 API 取得雲量資料
#
# 為什麼用 Open-Meteo？
#   - 完全免費，無需 API 金鑰
#   - 提供逐小時雲量（cloud_cover），精度 1%
#   - 支援高/中/低雲層分別查詢（低雲最影響能見度）
#   - 可查未來 7 天預報，適合週末規劃
#
# 雲量判斷標準（適合拍銀河）：
#   0-20%  ☆☆☆ 絕佳，銀河清晰可見
#   21-40% ☆☆  良好，偶有薄雲不影響整體
#   41-60% ☆   普通，銀河模糊，慎重考慮
#   61%+   ✗   不建議，雲層遮蔽銀河

import urllib.request
import json
from datetime import date, datetime, timedelta
import pytz

TW_TZ = pytz.timezone("Asia/Taipei")

# Open-Meteo API 端點
API_URL = "https://api.open-meteo.com/v1/forecast"


def get_cloud_forecast(lat: float, lon: float, target_date: date) -> dict:
    """
    查詢指定座標、日期的逐小時雲量預報

    Open-Meteo 回傳的 cloud_cover 是 0-100 的整數，代表天空被雲覆蓋的百分比。
    另外查詢低雲（cloud_cover_low）—— 低雲比高雲更影響拍攝，因為又厚又密。

    Args:
        lat: 緯度
        lon: 經度
        target_date: 目標日期（需在今天起 7 天內）

    Returns:
        {
            "hourly": [
                {
                    "time": datetime,       # 台灣時間
                    "cloud_cover": int,     # 總雲量 0-100%
                    "cloud_low": int,       # 低雲量 0-100%
                    "rating": str,          # 評級：絕佳/良好/普通/不建議
                    "suitable": bool,       # 是否適合拍攝
                }
            ],
            "night_summary": {...},         # 20:00-05:00 的夜間摘要
            "best_hours": [...],            # 雲量 < 40% 的時段
        }
    """
    # 組合 API 查詢參數
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "cloud_cover,cloud_cover_low",
        "timezone": "Asia/Taipei",
        "start_date": target_date.strftime("%Y-%m-%d"),
        # 查隔天也一起帶進來（因為拍攝跨越午夜）
        "end_date": (target_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        "wind_speed_unit": "kmh",
    }

    # 發送 HTTP 請求
    raw_data = _fetch_api(params)

    # 解析回傳資料
    hourly_data = _parse_hourly(raw_data, target_date)

    # 篩選出夜間時段（當天 20:00 到隔天 05:00）
    night_hours = _filter_night_hours(hourly_data, target_date)

    # 計算夜間摘要統計
    night_summary = _calc_night_summary(night_hours)

    # 找出適合拍攝的連續時段
    best_hours = _find_best_windows(night_hours)

    return {
        "hourly": hourly_data,
        "night_hours": night_hours,
        "night_summary": night_summary,
        "best_hours": best_hours,
    }


def _fetch_api(params: dict) -> dict:
    """
    發送 GET 請求到 Open-Meteo API

    Python 內建的 urllib 不需要安裝額外套件。
    URL 格式範例：
      https://api.open-meteo.com/v1/forecast?latitude=23.14&longitude=121.42&...
    """
    # 把 dict 轉成 URL query string
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_URL}?{query_string}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.URLError as e:
        raise ConnectionError(f"無法連線到 Open-Meteo API：{e}")
    except json.JSONDecodeError:
        raise ValueError("Open-Meteo 回傳資料格式錯誤")


def _parse_hourly(raw_data: dict, target_date: date) -> list:
    """
    將 API 回傳的時間序列資料解析為結構化清單

    Open-Meteo 回傳格式：
    {
        "hourly": {
            "time": ["2026-04-11T00:00", "2026-04-11T01:00", ...],
            "cloud_cover": [10, 25, 40, ...],
            "cloud_cover_low": [5, 10, 20, ...]
        }
    }
    """
    times = raw_data["hourly"]["time"]
    cloud_cover = raw_data["hourly"]["cloud_cover"]
    cloud_low = raw_data["hourly"]["cloud_cover_low"]

    result = []
    for i, time_str in enumerate(times):
        # 解析時間字串 "2026-04-11T20:00" → datetime
        dt = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
        dt = TW_TZ.localize(dt)

        cc = cloud_cover[i] if cloud_cover[i] is not None else 100
        cl = cloud_low[i] if cloud_low[i] is not None else 100

        result.append({
            "time": dt,
            "cloud_cover": cc,
            "cloud_low": cl,
            "rating": _get_rating(cc, cl),
            "suitable": cc <= 40,
        })

    return result


def _get_rating(cloud_cover: int, cloud_low: int) -> str:
    """
    依總雲量與低雲量給出拍攝評級

    低雲的影響比高雲更大：
    - 高雲（卷雲）薄而透，有時仍可見到銀河
    - 低雲（積雲、層雲）又厚又不透明，基本上遮死銀河
    """
    # 低雲嚴重時直接降級
    if cloud_low >= 30:
        if cloud_cover <= 40:
            return "普通（低雲偏多）"
        return "不建議（低雲遮蔽）"

    if cloud_cover <= 20:
        return "絕佳"
    elif cloud_cover <= 40:
        return "良好"
    elif cloud_cover <= 60:
        return "普通"
    else:
        return "不建議"


def _filter_night_hours(hourly_data: list, target_date: date) -> list:
    """篩選出夜間觀測時段：當天 20:00 到隔天 05:00"""
    night_start = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 20, 0))
    night_end = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day + 1, 5, 0))

    return [h for h in hourly_data if night_start <= h["time"] <= night_end]


def _calc_night_summary(night_hours: list) -> dict:
    """
    計算整晚夜間天氣統計摘要

    Returns:
        {
            "avg_cloud": float,     # 平均雲量
            "min_cloud": int,       # 最低雲量
            "max_cloud": int,       # 最高雲量
            "suitable_hours": int,  # 雲量 ≤ 40% 的小時數
            "overall_rating": str,  # 整體評級
        }
    """
    if not night_hours:
        return {"avg_cloud": 100, "overall_rating": "無資料"}

    covers = [h["cloud_cover"] for h in night_hours]
    suitable = sum(1 for h in night_hours if h["suitable"])

    avg = sum(covers) / len(covers)
    overall = _get_overall_rating(avg, suitable, len(night_hours))

    return {
        "avg_cloud": round(avg, 1),
        "min_cloud": min(covers),
        "max_cloud": max(covers),
        "suitable_hours": suitable,
        "total_hours": len(night_hours),
        "overall_rating": overall,
    }


def _get_overall_rating(avg_cloud: float, suitable_hours: int, total_hours: int) -> str:
    """依整晚平均雲量給出整體評級"""
    suitable_ratio = suitable_hours / total_hours if total_hours > 0 else 0

    if avg_cloud <= 20 and suitable_ratio >= 0.8:
        return "絕佳（整晚幾乎無雲）"
    elif avg_cloud <= 35 and suitable_ratio >= 0.6:
        return "良好（多數時段晴朗）"
    elif suitable_ratio >= 0.4:
        return "普通（部分時段有機會）"
    elif suitable_ratio >= 0.2:
        return "偏差（僅少數時段有機會）"
    else:
        return "不建議（雲層厚重）"


def _find_best_windows(night_hours: list) -> list:
    """
    找出雲量 ≤ 40% 的連續時段（最佳拍攝窗口）

    邏輯與 astronomy.py 的 _extract_windows 類似：
    掃描每小時資料，找出連續適合的時段。

    Returns:
        [
            {
                "start": datetime,
                "end": datetime,
                "avg_cloud": float,
                "duration_hours": float,
            }
        ]
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

    # 如果掃到結尾仍在窗口內
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
    將天氣查詢結果格式化為人類可讀的中文報告

    這個函式供 Agent 在組合最終推薦時呼叫
    """
    lines = []
    s = forecast["night_summary"]

    lines.append(f"整晚天氣評估：{s['overall_rating']}")
    lines.append(f"雲量範圍：{s['min_cloud']}% ~ {s['max_cloud']}%（平均 {s['avg_cloud']}%）")
    lines.append(f"適合拍攝時數：{s['suitable_hours']} / {s['total_hours']} 小時")

    if forecast["best_hours"]:
        lines.append("晴朗時段（雲量 ≤ 40%）：")
        for w in forecast["best_hours"]:
            start_str = w["start"].strftime("%H:%M")
            end_str = w["end"].strftime("%H:%M")
            lines.append(f"  {start_str} — {end_str}（{w['duration_hours']} 小時，平均雲量 {w['avg_cloud']}%）")
    else:
        lines.append("❌ 整晚雲量偏高，無適合拍攝時段")

    lines.append("\n逐小時雲量（夜間）：")
    for h in forecast["night_hours"]:
        bar_len = h["cloud_cover"] // 5
        bar = "█" * bar_len + "░" * (20 - bar_len)
        time_str = h["time"].strftime("%H:%M")
        lines.append(f"  {time_str}  [{bar}] {h['cloud_cover']:3d}%  {h['rating']}")

    return "\n".join(lines)


# ── 測試：直接執行此檔案 ─────────────────────────────────────
if __name__ == "__main__":
    from datetime import date, timedelta

    # 找下一個週六
    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)

    # 以台東三仙台座標測試
    LAT = 23.1406
    LON = 121.4197

    print(f"🌤️  天氣查詢測試")
    print(f"📅 日期：{saturday}（週六）")
    print(f"📍 地點：台東三仙台（{LAT}°N, {LON}°E）\n")

    forecast = get_cloud_forecast(LAT, LON, saturday)

    print("=" * 55)
    print(format_weather_report(forecast))
    print("=" * 55)
