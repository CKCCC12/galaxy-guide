# recommender.py
# 銀河拍攝地點推薦引擎
#
# 這是整個系統的「大腦」，負責：
#   1. 從地點資料庫篩選候選地點
#   2. 對每個地點查詢天氣（weather.py）
#   3. 對每個地點計算天文條件（astronomy.py）
#   4. 查詢環保署即時 AQI（airquality.py）
#   5. 用加權公式計算綜合分數
#   6. 輸出 Top 3 推薦 + 完整說明
#
# 評分公式（滿分 100）：
#   雲量     40%：有雲就看不到
#   空氣品質 30%：AOD + 環保署 AQI 綜合
#   能見度   30%：大氣透明度，有霾也看不清楚
#   月光       ：不計分，僅作參考（應避開月出後時段）
#   銀河仰角   ：不計分，僅作參考

from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from locations import get_locations_for_weekend
from weather import get_cloud_forecast
from astronomy import get_best_shooting_window
from airquality import get_current_aqi, get_aqi_level, format_aqi_report
from cwa import get_pop_forecast, format_pop_report, get_cloud_from_cwa
import pytz

TW_TZ = pytz.timezone("Asia/Taipei")


def recommend(target_date: date, max_bortle: int = 4, top_n: int = 3) -> dict:
    """
    主函式：計算指定日期的最佳銀河拍攝地點

    Args:
        target_date: 目標日期（通常是週六或週日）
        max_bortle:  接受的最高光害等級（預設 4）
        top_n:       回傳前幾名（預設 Top 3）

    Returns:
        {
            "date": date,
            "candidates": [...],
            "top": [...],
            "report": str,
        }
    """
    month = target_date.month
    is_future = target_date > date.today()

    candidates = get_locations_for_weekend(month=month, max_bortle=max_bortle)
    print(f"📍 共 {len(candidates)} 個候選地點，開始評估...\n")

    def _evaluate_location(loc):
        """單一地點的完整查詢與評分（供平行執行）"""
        weather = get_cloud_forecast(loc["lat"], loc["lon"], target_date)
        astro = get_best_shooting_window(loc["lat"], loc["lon"], target_date)
        aqi_data = get_current_aqi(loc["lat"], loc["lon"])
        pop_data = get_pop_forecast(loc["lat"], loc["lon"], target_date)
        cwa_cloud = get_cloud_from_cwa(loc["lat"], loc["lon"], target_date)
        _inject_pop_into_hours(weather["night_hours"], pop_data)
        score, breakdown = calculate_score(weather, astro, aqi_data, pop_data, cwa_cloud)
        return {
            "location": loc,
            "weather": weather,
            "astro": astro,
            "aqi": aqi_data,
            "pop": pop_data,
            "cwa_cloud": cwa_cloud,
            "score": score,
            "breakdown": breakdown,
            "is_future": is_future,
        }

    scored = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_evaluate_location, loc): loc for loc in candidates}
        for future in as_completed(futures):
            loc = futures[future]
            try:
                scored.append(future.result())
                print(f"  ✓ {loc['name']}")
            except Exception as e:
                print(f"  ⚠️  {loc['name']} 查詢失敗：{e}，略過")

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_n]
    report = build_report(target_date, top, scored)

    return {
        "date": target_date,
        "candidates": scored,
        "top": top,
        "report": report,
    }


def _inject_pop_into_hours(night_hours: list, pop_data) -> None:
    """
    將 CWA PoP6h 資料注入到每個逐小時天氣紀錄中

    PoP6h 是6小時一個值，找出每個小時所屬的6小時區間，
    複製對應的 PoP 數值到 h["pop"]。
    找不到對應區間時設為 None（前端顯示「—」）。
    """
    intervals = []
    if pop_data and "pop_intervals" in pop_data and pop_data.get("error") is None:
        intervals = pop_data["pop_intervals"]

    for h in night_hours:
        h["pop"] = None
        if intervals:
            # h["time"] 是有時區的 datetime（TW_TZ），iv 的時間是無時區的 naive datetime
            # 需去除時區資訊才能比較（CWA 時間本身即為台灣本地時間 UTC+8）
            h_naive = h["time"].replace(tzinfo=None)
            for iv in intervals:
                if iv["start"] <= h_naive < iv["end"]:
                    h["pop"] = iv["pop"]
                    break


def calculate_score(weather: dict, astro: dict, aqi_data, pop_data=None, cwa_cloud=None) -> tuple:
    """
    計算單一地點的綜合可見機率分數（0-100 分）

    三個計分維度（月光與銀河仰角僅顯示參考，不計入分數）：
    ┌─────────────┬──────┬──────────────────────────────────────────┐
    │ 維度        │ 權重 │ 計算方式                                  │
    ├─────────────┼──────┼──────────────────────────────────────────┤
    │ 雲量        │ 40%  │ (100 - 平均雲量) / 100                    │
    │ 空氣品質    │ 30%  │ (AOD 分數 + EPA AQI 分數) / 2            │
    │ 能見度      │ 30%  │ min(夜間平均能見度km / 20, 1.0)           │
    │ 月光        │ 參考 │ 月亮照明比例，僅顯示不計分               │
    │ 銀河仰角    │ 參考 │ 最高仰角 / 45°，僅顯示不計分             │
    └─────────────┴──────┴──────────────────────────────────────────┘

    雲量來源優先順序：CWA 天氣現象（有金鑰時）> Open-Meteo（無金鑰或失敗時）
    AOD 分數：max(0, 1 - aod / 0.3)   → AOD 0.3 以上 = 0 分
    AQI 分數：max(0, 1 - aqi / 150)   → AQI 150 以上 = 0 分

    額外獎懲：
    - 有黃金窗口（月暗 + 銀河高）：+5 分
    - 整晚雲量 > 70%：上限 15 分（完全不值得去）

    Returns:
        (總分 float, 各項分數 dict)
    """
    summary = weather["night_summary"]
    moon = astro["moon"]
    mw = astro["milkyway"]

    # ── 雲量分數 ─────────────────────────────────────────────
    # 優先使用 CWA 天氣現象轉換的估計值（解析度更符合台灣地形）
    # 若無 CWA 金鑰或查詢失敗，退回 Open-Meteo 資料
    cloud_source = "open-meteo"
    if cwa_cloud and cwa_cloud.get("error") is None and "est_cloud" in cwa_cloud:
        avg_cloud = cwa_cloud["est_cloud"]
        cloud_source = "cwa"
    else:
        avg_cloud = summary.get("avg_cloud", 100)
    cloud_score = max(0.0, (100 - avg_cloud) / 100)

    # ── 月光分數 ─────────────────────────────────────────────
    moon_score = 1 - moon["illumination"]

    # ── 能見度分數 ───────────────────────────────────────────
    # 分段計分：≥20km=100, 10-20km=70, 5-10km=30, <5km=0
    avg_vis_km = summary.get("avg_visibility_km", 10)
    if avg_vis_km >= 20:
        visibility_score = 1.0
    elif avg_vis_km >= 10:
        visibility_score = 0.7
    elif avg_vis_km >= 5:
        visibility_score = 0.3
    else:
        visibility_score = 0.0

    # ── 空氣品質分數 ─────────────────────────────────────────
    # AOD 部分（Open-Meteo 提供，有預報）
    avg_aod = summary.get("avg_aod", 0.3)
    aod_score = max(0.0, 1.0 - avg_aod / 0.3)

    # EPA AQI 部分（今日參考值）
    # aqi_data 可能為 None（API 失敗）、{"error": "no_key"}（未設金鑰）、或正常資料
    has_epa = (aqi_data and "aqi" in aqi_data and aqi_data.get("error") is None)
    if has_epa:
        epa_score = max(0.0, 1.0 - aqi_data["aqi"] / 150.0)
        air_quality_score = (aod_score + epa_score) / 2
    else:
        # 未設金鑰或 API 失敗時，只用 AOD
        air_quality_score = aod_score
        epa_score = None

    # ── 銀河仰角分數 ─────────────────────────────────────────
    max_alt = mw["max_altitude"]
    altitude_score = min(max_alt / 45.0, 1.0) if max_alt > 0 else 0

    # ── 加權合計 ─────────────────────────────────────────────
    # 月光與銀河仰角不計入分數，僅保留變數供 breakdown 參考顯示
    total = (
        cloud_score         * 0.40
        + air_quality_score * 0.30
        + visibility_score  * 0.30
    ) * 100

    # 黃金窗口加分
    if astro["golden_windows"]:
        total += 5

    # 雲太厚強制壓低
    if avg_cloud > 70:
        total = min(total, 15)

    # ── 方案 B：CWA 降雨機率懲罰 ──────────────────────────────
    # PoP > 50%：下雨機率高，直接壓制到 20 分
    # PoP 30–50%：漸進扣分（30% 無影響，50% 乘以 0.5）
    # 未設金鑰或 API 失敗時略過，不影響評分
    max_pop = 0
    pop_penalty_applied = False
    has_pop = pop_data and "max_pop" in pop_data and pop_data.get("error") is None
    if has_pop:
        max_pop = pop_data["max_pop"]
        if max_pop > 50:
            total = min(total, 20)
            pop_penalty_applied = True
        elif max_pop > 30:
            penalty = 1.0 - (max_pop - 30) / 40.0
            total *= penalty
            pop_penalty_applied = True

    breakdown = {
        "cloud_score":          round(cloud_score * 100, 1),
        "avg_cloud":            avg_cloud,
        "cloud_source":         cloud_source,   # "cwa" 或 "open-meteo"
        "moon_score":           round(moon_score * 100, 1),
        "visibility_score":     round(visibility_score * 100, 1),
        "aod_score":            round(aod_score * 100, 1),
        "epa_score":            round(epa_score * 100, 1) if epa_score is not None else None,
        "air_quality_score":    round(air_quality_score * 100, 1),
        "altitude_score":       round(altitude_score * 100, 1),
        "max_pop":              max_pop,
        "pop_penalty_applied":  pop_penalty_applied,
        "total":                round(total, 1),
    }

    return round(total, 1), breakdown


def build_report(target_date: date, top: list, all_scored: list) -> str:
    """組合完整的中文推薦報告（含能見度、空氣品質逐小時資訊）"""
    lines = []
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    weekday = weekday_names[target_date.weekday()]
    is_future = target_date > date.today()

    lines.append("=" * 60)
    lines.append(f"🌌  {target_date}（週{weekday}）銀河拍攝地點推薦")
    lines.append("=" * 60)

    if not top:
        lines.append("❌ 本週末天氣條件不佳，所有地點分數偏低，不建議出發。")
        return "\n".join(lines)

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    for rank, item in enumerate(top):
        loc = item["location"]
        score = item["score"]
        bd = item["breakdown"]
        moon = item["astro"]["moon"]
        mw = item["astro"]["milkyway"]
        summary = item["weather"]["night_summary"]
        golden = item["astro"]["golden_windows"]
        aqi_data = item.get("aqi")
        pop_data = item.get("pop")
        night_hours = item["weather"]["night_hours"]

        medal = medals[rank] if rank < len(medals) else f"{rank+1}."
        lines.append(f"\n{medal} 第 {rank+1} 名：{loc['name']}")
        lines.append(f"   綜合評分：{score:.0f} 分  │  區域：{loc['region']}  │  Bortle：{loc['bortle']}")
        lines.append(f"   海拔：{loc['altitude_m']}m")
        lines.append("")

        # ── 天氣 ────────────────────────────────────────────
        lines.append(f"   ☁️  天氣：{summary['overall_rating']}")
        lines.append(f"       平均雲量 {summary['avg_cloud']}%（分數 {bd['cloud_score']:.0f}/100）")

        # ── 月相 ────────────────────────────────────────────
        lines.append(f"   🌙  月相：{moon['phase_name']}")
        lines.append(f"       照明 {moon['illumination_pct']}%（分數 {bd['moon_score']:.0f}/100）")

        # ── 能見度 ──────────────────────────────────────────
        avg_vis = summary.get("avg_visibility_km", "?")
        min_vis = summary.get("min_visibility_km", "?")
        lines.append(f"   👁️  能見度：平均 {avg_vis} km，最低 {min_vis} km")
        lines.append(f"       （分數 {bd['visibility_score']:.0f}/100）")

        # ── 空氣品質 ────────────────────────────────────────
        avg_aod = summary.get("avg_aod", "?")
        lines.append(f"   🌫️  空氣品質：AOD {avg_aod}（分數 {bd['air_quality_score']:.0f}/100）")
        if aqi_data and aqi_data.get("error") == "no_key":
            lines.append("       環境部 AQI：未設定金鑰（export MOENV_API_KEY=你的金鑰）")
        elif aqi_data and "aqi" in aqi_data:
            epa_label = "今日參考值" if is_future else "即時"
            aqi_level = get_aqi_level(aqi_data["aqi"])
            lines.append(
                f"       {epa_label}｜{aqi_data['county']} {aqi_data['station_name']} "
                f"AQI {aqi_data['aqi']}（{aqi_level}）"
                f"　PM2.5 {aqi_data['pm25']} μg/m³"
            )
        else:
            lines.append("       環境部 AQI：無法取得")

        # ── 降雨機率 ────────────────────────────────────────────
        if pop_data and pop_data.get("error") == "no_key":
            lines.append("   🌧️  降雨機率：未設定 CWA_API_KEY（詳見 cwa.py 說明）")
        elif pop_data and "max_pop" in pop_data:
            max_pop = pop_data["max_pop"]
            county = pop_data.get("county", "未知")
            dist = pop_data.get("distance_km", "?")
            pop_label = "（預報）" if is_future else "（即時）"
            lines.append(f"   🌧️  降雨機率{pop_label}：夜間最高 {max_pop}%")
            lines.append(f"       縣市：{county}（距離 {dist} km）")
            for iv in pop_data.get("pop_intervals", []):
                lines.append(
                    f"       {iv['start'].strftime('%H:%M')}–{iv['end'].strftime('%H:%M')}：{iv['pop']}%"
                )
            if bd["pop_penalty_applied"]:
                if max_pop > 50:
                    lines.append("       ⛔ 降雨風險高，綜合評分已壓制")
                else:
                    lines.append(f"       ⚠️  降雨機率偏高，評分已扣減")
        else:
            lines.append("   🌧️  降雨機率：無法取得（CWA API 無回應）")

        # 沙塵、濕度警告
        max_dust = summary.get("max_dust", 0)
        max_hum = summary.get("max_humidity", 0)
        if max_dust >= 20:
            lines.append(f"       ⚠️  沙塵最高 {max_dust} μg/m³")
        if max_hum >= 85:
            lines.append(f"       💧 濕度最高 {max_hum}%，注意結露")

        # ── 銀河仰角 ────────────────────────────────────────
        lines.append(f"   🔭  銀河核心：最高仰角 {mw['max_altitude']}°")
        peak_time = mw["max_altitude_time"].strftime("%H:%M") if mw["max_altitude_time"] else "—"
        lines.append(f"       於 {peak_time} 達到最高點（分數 {bd['altitude_score']:.0f}/100）")

        # ── 黃金時段 ────────────────────────────────────────
        if golden:
            lines.append(f"   ⭐  黃金拍攝時段：")
            for start, end in golden:
                mins = int((end - start).total_seconds() / 60)
                lines.append(f"       {start.strftime('%H:%M')} — {end.strftime('%H:%M')}（{mins} 分鐘）")
        else:
            lines.append(f"   ⚠️  無黃金時段（月光與銀河窗口未重疊）")

        lines.append(f"   💡  {loc['notes'][:50]}...")

        # ── 逐小時詳情 ──────────────────────────────────────
        lines.append("")
        lines.append(f"   逐小時天氣（夜間 20:00 ~ 05:00）：")
        lines.append(f"   {'時間':5}  {'雲量':22}  {'能見度':>7}  {'AOD':>6}  {'濕度':>4}  {'降雨':>4}  評級")
        lines.append("   " + "-" * 76)
        for h in night_hours:
            bar_len = h["cloud_cover"] // 5
            bar = "█" * bar_len + "░" * (20 - bar_len)
            t = h["time"].strftime("%H:%M")
            warnings_str = " ".join(h["warnings"]) if h["warnings"] else ""
            pop_str = f"{h['pop']:3d}%" if h.get("pop") is not None else "  —"
            lines.append(
                f"   {t}  [{bar}] {h['cloud_cover']:3d}%"
                f"  {h['visibility_km']:>5.1f}km"
                f"  {h['aod']:>5.3f}"
                f"  {h['humidity']:>3d}%"
                f"  {pop_str}"
                f"  {h['rating']}"
                f"  {warnings_str}"
            )

    # ── 其他地點簡表 ────────────────────────────────────────
    if len(all_scored) > len(top):
        lines.append("\n── 其他地點評分 ──────────────────────────────────────")
        for item in all_scored[len(top):]:
            loc = item["location"]
            s = item["score"]
            avg_c = item["weather"]["night_summary"].get("avg_cloud", "?")
            avg_v = item["weather"]["night_summary"].get("avg_visibility_km", "?")
            avg_aod = item["weather"]["night_summary"].get("avg_aod", "?")
            lines.append(
                f"   {loc['name']:<18} {s:5.1f} 分  "
                f"雲量 {avg_c}%  能見度 {avg_v}km  AOD {avg_aod}"
            )

    lines.append("\n" + "=" * 60)
    lines.append("資料來源：Open-Meteo 天氣 / JPL DE421 星曆表 / 環保署 AQI")
    lines.append("=" * 60)

    return "\n".join(lines)


# ── 測試：直接執行此檔案 ─────────────────────────────────────
if __name__ == "__main__":
    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)

    print(f"🚀 銀河拍攝推薦系統啟動")
    print(f"📅 目標日期：{saturday}\n")

    result = recommend(saturday)
    print(result["report"])
