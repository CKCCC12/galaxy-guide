# astronomy.py
# 天文計算模組：月相、銀河核心可見窗口
#
# 核心概念：
#   銀河系中心（Galactic Center）位於射手座方向
#     赤經（RA）: 17h 45m 40s
#     赤緯（Dec）: -29° 00' 28"
#   台灣緯度約 22-25°N，銀河核心夏季最高仰角約 30-40°
#   拍攝門檻：核心仰角 > 15° 才有良好構圖
#
# 最佳拍攝條件（三者同時滿足）：
#   1. 月亮在地平線以下（或月照 < 20%，接近新月）
#   2. 銀河核心仰角 > 15°
#   3. 時間在天文黑夜（太陽低於地平線 18° 以下）

from skyfield.api import Star, Loader, wgs84
from skyfield import almanac
import os

# 從專案目錄讀取本地星曆表檔案（de421.bsp 需放在同一資料夾）
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()
load = Loader(_HERE)
from datetime import date, datetime, timedelta
import pytz

# 台灣時區
TW_TZ = pytz.timezone("Asia/Taipei")

# 銀河系中心座標（J2000）
GALACTIC_CENTER = Star(ra_hours=17.7611, dec_degrees=-29.0078)

# 星曆表在模組載入時只讀取一次，所有函式共用同一份實例
# 避免每次呼叫都重新讀取 16MB 檔案造成記憶體浪費
_ts = load.timescale()
_eph = load("de421.bsp")


def _load_ephemeris():
    """回傳已快取的星曆表實例（不重複讀檔）"""
    return _ts, _eph


# ── 月相計算 ─────────────────────────────────────────────────

def get_moon_illumination(target_date: date) -> dict:
    """
    計算指定日期（台灣時間午夜）的月亮照明比例與月相名稱

    月相週期約 29.5 天，依照明比例分為：
      新月(0%) → 眉月 → 上弦月(50%) → 盈凸月 → 滿月(100%) → 虧凸月 → 下弦月 → 殘月 → 新月

    Returns:
        {
            "illumination": 0.0~1.0,   # 0 = 全黑（新月）, 1 = 全亮（滿月）
            "phase_name": str,          # 中文月相名稱
            "is_suitable": bool,        # 照明 < 30% 才適合拍銀河
        }
    """
    ts, eph = _load_ephemeris()

    # 取台灣時間當天 21:00（晚間觀測代表時刻）
    dt_tw = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 21, 0))
    t = ts.from_datetime(dt_tw)

    # 計算月亮照明比例（skyfield 內建函式）
    moon = eph["moon"]
    sun = eph["sun"]
    earth = eph["earth"]

    # 計算地球-月亮-太陽的相位角，進而得到照明比例
    e = earth.at(t)
    sun_pos = e.observe(sun).apparent()
    moon_pos = e.observe(moon).apparent()

    # 相位角（0° = 新月, 180° = 滿月）
    phase_angle = moon_pos.separation_from(sun_pos).degrees

    # 照明比例公式：(1 - cos(phase_angle)) / 2
    import math
    illumination = (1 - math.cos(math.radians(phase_angle))) / 2

    # 判斷月相名稱（依照明比例與相位角）
    phase_name = _get_phase_name(illumination, phase_angle)

    return {
        "illumination": round(illumination, 3),
        "illumination_pct": round(illumination * 100, 1),
        "phase_name": phase_name,
        "is_suitable": illumination < 0.30,  # 照明 < 30% 才適合拍銀河
    }


def _get_phase_name(illumination: float, phase_angle: float) -> str:
    """依照明比例與相位角回傳中文月相名稱"""
    pct = illumination * 100
    # 用相位角判斷月亮是「漸圓」(waxing) 還是「漸缺」(waning)
    # 相位角 0→180 為漸圓，180→360 為漸缺
    waxing = phase_angle < 180

    if pct < 3:
        return "新月（幾乎全暗，最佳拍攝時機）"
    elif pct < 25:
        return "眉月（纖細月牙）" if waxing else "殘月（纖細月牙）"
    elif pct < 45:
        return "上弦月前（四分之一）" if waxing else "下弦月後（四分之一）"
    elif pct < 55:
        return "上弦月（半圓）" if waxing else "下弦月（半圓）"
    elif pct < 75:
        return "盈凸月（超過半圓）" if waxing else "虧凸月（超過半圓）"
    elif pct < 97:
        return "近滿月（光害嚴重）"
    else:
        return "滿月（不適合拍銀河）"


# ── 月升月落計算 ─────────────────────────────────────────────

def get_moon_schedule(lat: float, lon: float, target_date: date) -> dict:
    """
    計算指定地點、日期的月升與月落時間（台灣時間）

    Args:
        lat: 緯度（台灣約 22-25）
        lon: 經度（台灣約 120-122）
        target_date: 日期

    Returns:
        {
            "moonrise": datetime or None,   # 月升時間（台灣時間）
            "moonset": datetime or None,    # 月落時間（台灣時間）
            "dark_hours": [(start, end)],   # 月亮在地平線下的時段（台灣時間）
        }
    """
    ts, eph = _load_ephemeris()

    # 觀測位置
    observer = wgs84.latlon(lat, lon)

    # 搜尋範圍：當天 12:00 到隔天 14:00（26 小時）
    # 擴大窗口以捕捉月落發生在 06:00 以後的情況（例如月亮深夜升起、隔天上午才落）
    t0 = ts.from_datetime(TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 12, 0)))
    t1 = ts.from_datetime(TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day + 1, 14, 0)))

    # 找月升月落事件（0 = 落下, 1 = 升起）
    f = almanac.risings_and_settings(eph, eph["moon"], observer)
    times, events = almanac.find_discrete(t0, t1, f)

    moonrise = None
    moonset = None

    for t, event in zip(times, events):
        dt_tw = t.astimezone(TW_TZ)
        if event == 1 and moonrise is None:
            moonrise = dt_tw
        elif event == 0 and moonset is None:
            moonset = dt_tw

    # 計算月亮在地平線以下的「暗夜時段」
    dark_hours = _calculate_dark_hours(moonrise, moonset, target_date)

    return {
        "moonrise": moonrise,
        "moonset": moonset,
        "dark_hours": dark_hours,
    }


def _calculate_dark_hours(moonrise, moonset, target_date: date) -> list:
    """
    計算月亮在地平線以下的時段（暗夜窗口）
    觀測時段定義為當天 20:00 到隔天 05:00
    """
    obs_start = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 20, 0))
    obs_end = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day + 1, 5, 0))

    # 情境 1：整晚月亮都在地平線下（今晚無月升或月升在 05:00 後）
    if moonrise is None and moonset is None:
        return [(obs_start, obs_end)]  # 全夜皆暗

    # 情境 2：只有月升（月亮升起後就沒落下）
    if moonrise and not moonset:
        if moonrise > obs_start:
            return [(obs_start, moonrise)]  # 月升前有暗夜
        return []  # 月亮整晚都在地平線上

    # 情境 3：只有月落（月亮在觀測開始前已升起）
    if moonset and not moonrise:
        if moonset < obs_end:
            return [(moonset, obs_end)]  # 月落後有暗夜
        return []  # 月亮整晚都在地平線上

    # 情境 4：有月升也有月落
    dark = []
    if moonrise < moonset:
        # 月升在前、月落在後：月升前和月落後都是暗夜
        if moonrise > obs_start:
            dark.append((obs_start, moonrise))
        if moonset < obs_end:
            dark.append((moonset, obs_end))
    else:
        # 月落在前、月升在後：中間那段是暗夜
        if moonset > obs_start and moonrise < obs_end:
            dark.append((moonset, moonrise))

    return dark


# ── 銀河核心可見窗口 ─────────────────────────────────────────

def get_milkyway_window(lat: float, lon: float, target_date: date) -> dict:
    """
    計算銀河核心（銀河系中心方向）在指定地點的可見時段

    每 15 分鐘取樣一次，回傳整晚的仰角變化表
    仰角 > 15° 才算「可見」，> 25° 為「良好」，> 35° 為「最佳」

    Returns:
        {
            "samples": [{"time": datetime, "altitude": float, "azimuth": float}],
            "max_altitude": float,      # 整晚最高仰角
            "max_altitude_time": datetime,
            "visible_window": [(start, end)],  # 仰角 > 15° 的時段
            "best_window": [(start, end)],     # 仰角 > 25° 的時段
        }
    """
    ts, eph = _load_ephemeris()
    earth = eph["earth"]
    observer = wgs84.latlon(lat, lon)

    samples = []
    max_alt = -90.0
    max_alt_time = None

    # 從當天 20:00 到隔天 05:00，每 15 分鐘取樣
    start_dt = TW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, 20, 0))

    for i in range(37):  # 37 個取樣點 = 9 小時 × 4 次/小時 + 1
        current_dt = start_dt + timedelta(minutes=15 * i)
        t = ts.from_datetime(current_dt)

        # 計算銀河核心的仰角與方位角
        astrometric = (earth + observer).at(t).observe(GALACTIC_CENTER)
        alt, az, _ = astrometric.apparent().altaz()

        altitude = alt.degrees
        azimuth = az.degrees

        samples.append({
            "time": current_dt,
            "altitude": round(altitude, 1),
            "azimuth": round(azimuth, 1),
        })

        if altitude > max_alt:
            max_alt = altitude
            max_alt_time = current_dt

    # 從取樣點找出連續的可見時段（仰角 > 15° 和 > 25°）
    visible_window = _extract_windows(samples, threshold=15.0)
    best_window = _extract_windows(samples, threshold=25.0)

    return {
        "samples": samples,
        "max_altitude": round(max_alt, 1),
        "max_altitude_time": max_alt_time,
        "visible_window": visible_window,
        "best_window": best_window,
    }


def _extract_windows(samples: list, threshold: float) -> list:
    """從取樣點清單中，找出仰角連續超過門檻值的時段"""
    windows = []
    in_window = False
    window_start = None

    for s in samples:
        if s["altitude"] >= threshold and not in_window:
            in_window = True
            window_start = s["time"]
        elif s["altitude"] < threshold and in_window:
            in_window = False
            windows.append((window_start, s["time"]))

    # 如果取樣結束時仍在窗口中
    if in_window and window_start:
        windows.append((window_start, samples[-1]["time"]))

    return windows


# ── 綜合最佳拍攝窗口 ─────────────────────────────────────────

def get_best_shooting_window(lat: float, lon: float, target_date: date) -> dict:
    """
    綜合計算最佳拍攝窗口：

    月光照明 < 30%（新月/眉月）：月亮不造成明顯光害，整段銀河可見窗口皆可拍攝
    月光照明 ≥ 30%：需避開月亮在天上的時段，取暗夜 ∩ 銀河可見的交集

    Returns:
        {
            "moon": {...},          # 月相資訊
            "schedule": {...},      # 月升月落
            "milkyway": {...},      # 銀河核心窗口
            "golden_windows": [...],# 最終最佳時段
            "summary": str,         # 給人看的中文摘要
        }
    """
    moon_info = get_moon_illumination(target_date)
    moon_schedule = get_moon_schedule(lat, lon, target_date)
    mw_info = get_milkyway_window(lat, lon, target_date)

    if moon_info["illumination"] < 0.30:
        # 月光弱，不影響拍攝，直接用銀河可見窗口
        golden_windows = list(mw_info["visible_window"])
    else:
        # 月光強，取暗夜時段 ∩ 銀河可見時段
        golden_windows = _intersect_windows(
            moon_schedule["dark_hours"],
            mw_info["visible_window"],
        )

    # 產生中文摘要
    summary = _build_summary(moon_info, moon_schedule, mw_info, golden_windows)

    return {
        "moon": moon_info,
        "schedule": moon_schedule,
        "milkyway": mw_info,
        "golden_windows": golden_windows,
        "summary": summary,
    }


def _intersect_windows(dark_hours: list, mw_windows: list) -> list:
    """計算兩組時段清單的交集"""
    result = []
    for d_start, d_end in dark_hours:
        for m_start, m_end in mw_windows:
            # 找重疊部分
            overlap_start = max(d_start, m_start)
            overlap_end = min(d_end, m_end)
            if overlap_start < overlap_end:
                result.append((overlap_start, overlap_end))
    return result


def _fmt_time(dt) -> str:
    """格式化時間為 HH:MM"""
    if dt is None:
        return "—"
    return dt.strftime("%H:%M")


def _build_summary(moon_info, moon_schedule, mw_info, golden_windows) -> str:
    """組合人類可讀的中文摘要"""
    lines = []

    # 月相
    lines.append(f"月相：{moon_info['phase_name']}（照明 {moon_info['illumination_pct']}%）")

    # 月升月落
    rise = _fmt_time(moon_schedule["moonrise"])
    sset = _fmt_time(moon_schedule["moonset"])
    lines.append(f"月升：{rise}　月落：{sset}")

    # 銀河核心
    mw_peak_time = _fmt_time(mw_info["max_altitude_time"])
    lines.append(f"銀河核心最高仰角：{mw_info['max_altitude']}°（{mw_peak_time}）")

    if mw_info["max_altitude"] < 15:
        lines.append("⚠️  銀河核心整晚仰角偏低，本季不是最佳拍攝時間")
    elif mw_info["max_altitude"] < 25:
        lines.append("銀河核心勉強可見，建議在最高仰角時段拍攝")
    else:
        lines.append("銀河核心可見條件良好")

    # 最佳拍攝窗口
    if golden_windows:
        lines.append("最佳拍攝時段：")
        for start, end in golden_windows:
            duration_min = int((end - start).total_seconds() / 60)
            lines.append(f"  {_fmt_time(start)} — {_fmt_time(end)}（約 {duration_min} 分鐘）")
    else:
        if not mw_info["visible_window"]:
            lines.append("❌ 本日銀河核心未升至 15° 以上")
        elif not moon_info["is_suitable"]:
            lines.append("❌ 月光太強（照明 > 30%），月落前無拍攝機會")
        else:
            lines.append("❌ 月亮與銀河核心可見時段無交集，本夜不適合")

    return "\n".join(lines)


# ── 測試：直接執行此檔案 ─────────────────────────────────────
if __name__ == "__main__":
    # 以台東三仙台為範例，計算本週末的天文條件
    # 台東三仙台座標
    LAT = 23.1406
    LON = 121.4197

    # 取得本週六日期（測試用，直接用今天）
    from datetime import date
    today = date.today()
    # 找下一個週六
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)

    print(f"📅 計算日期：{saturday}（週六）")
    print(f"📍 地點：台東三仙台（{LAT}°N, {LON}°E）\n")

    result = get_best_shooting_window(LAT, LON, saturday)

    print("=" * 50)
    print(result["summary"])
    print("=" * 50)

    print("\n銀河核心仰角變化（每 30 分鐘）：")
    for s in result["milkyway"]["samples"]:
        if s == result["milkyway"]["samples"][0] or \
           result["milkyway"]["samples"].index(s) % 2 == 0:
            bar = "█" * max(0, int(s["altitude"] / 3))
            print(f"  {_fmt_time(s['time'])}  {s['altitude']:5.1f}°  {bar}")
