# recommender.py
# 銀河拍攝地點推薦引擎
#
# 這是整個系統的「大腦」，負責：
#   1. 從地點資料庫篩選候選地點
#   2. 對每個地點查詢天氣（weather.py）
#   3. 對每個地點計算天文條件（astronomy.py）
#   4. 用加權公式計算綜合分數
#   5. 輸出 Top 3 推薦 + 完整說明
#
# 評分公式（滿分 100）：
#   雲量分數（50%）：最重要，有雲就看不到
#   月光分數（30%）：月亮越暗越好
#   銀河仰角（20%）：核心越高越好拍

from datetime import date, timedelta
from locations import get_locations_for_weekend
from weather import get_cloud_forecast
from astronomy import get_best_shooting_window


def recommend(target_date: date, max_bortle: int = 4, top_n: int = 3) -> dict:
    """
    主函式：計算指定日期的最佳銀河拍攝地點

    Args:
        target_date: 目標日期（通常是週六或週日）
        max_bortle:  接受的最高光害等級（預設 4，過濾掉城市地點）
        top_n:       回傳前幾名（預設 Top 3）

    Returns:
        {
            "date": date,
            "candidates": [...],    # 所有評估過的地點（含分數）
            "top": [...],           # Top N 推薦
            "report": str,          # 完整中文報告
        }
    """
    month = target_date.month

    # Step 1：依月份與光害等級篩選候選地點
    candidates = get_locations_for_weekend(month=month, max_bortle=max_bortle)
    print(f"📍 共 {len(candidates)} 個候選地點，開始評估...\n")

    # Step 2：對每個地點查詢天氣 + 天文條件，並計算分數
    scored = []
    for i, loc in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] 評估：{loc['name']}...")

        try:
            weather = get_cloud_forecast(loc["lat"], loc["lon"], target_date)
            astro = get_best_shooting_window(loc["lat"], loc["lon"], target_date)
            score, breakdown = calculate_score(weather, astro)

            scored.append({
                "location": loc,
                "weather": weather,
                "astro": astro,
                "score": score,
                "breakdown": breakdown,
            })
        except Exception as e:
            print(f"    ⚠️  查詢失敗：{e}，略過此地點")

    # Step 3：依總分排序
    scored.sort(key=lambda x: x["score"], reverse=True)

    top = scored[:top_n]

    # Step 4：產生完整報告
    report = build_report(target_date, top, scored)

    return {
        "date": target_date,
        "candidates": scored,
        "top": top,
        "report": report,
    }


def calculate_score(weather: dict, astro: dict) -> tuple:
    """
    計算單一地點的綜合可見機率分數（0-100 分）

    三個維度：
    ┌─────────────┬──────┬──────────────────────────────┐
    │ 維度        │ 權重 │ 計算方式                      │
    ├─────────────┼──────┼──────────────────────────────┤
    │ 雲量        │ 50%  │ (100 - 平均雲量) / 100        │
    │ 月光        │ 30%  │ 1 - 月亮照明比例              │
    │ 銀河仰角    │ 20%  │ 最高仰角 / 45°（上限 100%）  │
    └─────────────┴──────┴──────────────────────────────┘

    額外獎懲：
    - 有黃金窗口（月暗 + 銀河高）：+5 分
    - 整晚雲量 > 70%：強制設為 0 分（完全不值得去）

    Returns:
        (總分 float, 各項分數 dict)
    """
    summary = weather["night_summary"]
    moon = astro["moon"]
    mw = astro["milkyway"]

    # 雲量分數：平均雲量越低越好
    avg_cloud = summary.get("avg_cloud", 100)
    cloud_score = max(0, (100 - avg_cloud) / 100)

    # 月光分數：月亮照明越低越好
    moon_score = 1 - moon["illumination"]

    # 銀河仰角分數：最高仰角 45° 為滿分（台灣緯度天花板約 40°）
    max_alt = mw["max_altitude"]
    altitude_score = min(max_alt / 45.0, 1.0) if max_alt > 0 else 0

    # 加權合計
    total = (cloud_score * 0.50 + moon_score * 0.30 + altitude_score * 0.20) * 100

    # 額外加分：有黃金窗口（暗夜 + 銀河同時可見）
    if astro["golden_windows"]:
        total += 5

    # 強制歸零：雲量太重根本沒意義去
    if avg_cloud > 70:
        total = min(total, 15)

    breakdown = {
        "cloud_score": round(cloud_score * 100, 1),
        "moon_score": round(moon_score * 100, 1),
        "altitude_score": round(altitude_score * 100, 1),
        "total": round(total, 1),
    }

    return round(total, 1), breakdown


def build_report(target_date: date, top: list, all_scored: list) -> str:
    """組合完整的中文推薦報告"""
    lines = []
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    weekday = weekday_names[target_date.weekday()]

    lines.append("=" * 55)
    lines.append(f"🌌  {target_date}（週{weekday}）銀河拍攝地點推薦")
    lines.append("=" * 55)

    if not top:
        lines.append("❌ 本週末天氣條件不佳，所有地點分數偏低，不建議出發。")
        return "\n".join(lines)

    # 各地點詳細資訊
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    for rank, item in enumerate(top):
        loc = item["location"]
        score = item["score"]
        bd = item["breakdown"]
        moon = item["astro"]["moon"]
        mw = item["astro"]["milkyway"]
        summary = item["weather"]["night_summary"]
        golden = item["astro"]["golden_windows"]

        lines.append(f"\n{medals[rank]} 第 {rank+1} 名：{loc['name']}")
        lines.append(f"   綜合評分：{score:.0f} 分  │  區域：{loc['region']}  │  Bortle：{loc['bortle']}")
        lines.append(f"   海拔：{loc['altitude_m']}m")
        lines.append("")

        # 分項說明
        lines.append(f"   ☁️  天氣：{summary['overall_rating']}")
        lines.append(f"       平均雲量 {summary['avg_cloud']}%（分數 {bd['cloud_score']:.0f}/100）")

        lines.append(f"   🌙  月相：{moon['phase_name']}")
        lines.append(f"       照明 {moon['illumination_pct']}%（分數 {bd['moon_score']:.0f}/100）")

        lines.append(f"   🔭  銀河核心：最高仰角 {mw['max_altitude']}°")
        peak_time = mw["max_altitude_time"].strftime("%H:%M") if mw["max_altitude_time"] else "—"
        lines.append(f"       於 {peak_time} 達到最高點（分數 {bd['altitude_score']:.0f}/100）")

        # 黃金拍攝窗口
        if golden:
            lines.append(f"   ⭐  黃金拍攝時段：")
            for start, end in golden:
                mins = int((end - start).total_seconds() / 60)
                lines.append(f"       {start.strftime('%H:%M')} — {end.strftime('%H:%M')}（{mins} 分鐘）")
        else:
            lines.append(f"   ⚠️  無黃金時段（月光與銀河窗口未重疊）")

        lines.append(f"   💡  {loc['notes'][:50]}...")

    # 其他地點簡表
    if len(all_scored) > len(top):
        lines.append("\n── 其他地點評分 ─────────────────────────────────")
        for item in all_scored[len(top):]:
            loc = item["location"]
            s = item["score"]
            avg_c = item["weather"]["night_summary"].get("avg_cloud", "?")
            lines.append(f"   {loc['name']:<18} {s:5.1f} 分  雲量 {avg_c}%")

    lines.append("\n" + "=" * 55)
    lines.append("資料來源：Open-Meteo 天氣 / JPL DE421 星曆表")
    lines.append("=" * 55)

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
