# tests/test_recommender.py
# 評分公式 calculate_score 的單元測試
#
# 為什麼優先測這裡？綜合評分是整個系統的「裁判」，公式一改就可能悄悄影響
# 所有推薦排序，而且分支多（雲量壓制、CWA 覆蓋、EPA 平均、降雨懲罰）。
# 這些都是純函式：給定輸入就有固定輸出，不碰網路、不需 mock，最容易也最該測。

import pytest
from recommender import calculate_score


# ── 測試資料建構小工具 ──────────────────────────────────────
# 只塞 calculate_score 真正會讀到的欄位，其餘不填，讓每個測試的「變因」一目了然。

def make_weather(avg_cloud=0, avg_vis=25, avg_aod=0.0):
    return {"night_summary": {
        "avg_cloud": avg_cloud,
        "avg_visibility_km": avg_vis,
        "avg_aod": avg_aod,
    }}


def make_astro(illum=0.0, max_alt=30.0, golden=None):
    return {
        "moon": {"illumination": illum},
        "milkyway": {"max_altitude": max_alt},
        "golden_windows": golden if golden is not None else [],
    }


# ── 基準：完美條件應得滿分 ───────────────────────────────────

def test_perfect_conditions_scores_100():
    """晴空、能見度極佳、無氣膠、無 EPA/降雨資料 → 100 分。"""
    score, bd = calculate_score(make_weather(0, 25, 0.0), make_astro(), None)
    assert score == 100.0
    assert bd["cloud_source"] == "open-meteo"


# ── 雲量 > 70% 強制壓到 15 分（過去的重要規則）─────────────────

def test_heavy_cloud_caps_at_15():
    """整晚平均雲量 80%：即使其他條件完美，總分也被壓到 15 以下。"""
    score, _ = calculate_score(make_weather(80, 25, 0.0), make_astro(), None)
    assert score == 15.0


# ── CWA 雲量來源優先於 Open-Meteo ────────────────────────────

def test_cwa_cloud_overrides_openmeteo():
    """有 CWA 估計雲量時應改用它評分，並在 breakdown 標記來源為 cwa。"""
    # Open-Meteo 說雲量 10%，但 CWA 說 85% → 應以 CWA 的 85% 計算（且觸發 >70 壓制）
    score, bd = calculate_score(
        make_weather(10, 25, 0.0), make_astro(), None,
        cwa_cloud={"est_cloud": 85, "error": None},
    )
    assert bd["cloud_source"] == "cwa"
    assert bd["avg_cloud"] == 85
    assert score == 15.0  # 85 > 70 → 壓制


def test_cwa_cloud_with_error_falls_back_to_openmeteo():
    """CWA 查詢失敗（error 非 None）時退回 Open-Meteo 雲量。"""
    score, bd = calculate_score(
        make_weather(0, 25, 0.0), make_astro(), None,
        cwa_cloud={"error": "no_key"},
    )
    assert bd["cloud_source"] == "open-meteo"
    assert score == 100.0


# ── 降雨機率懲罰（方案 B）────────────────────────────────────

def test_pop_above_50_caps_at_20():
    """夜間最高降雨機率 > 50%：總分壓到 20 以下，並標記已套用懲罰。"""
    score, bd = calculate_score(
        make_weather(0, 25, 0.0), make_astro(), None,
        pop_data={"max_pop": 60, "error": None},
    )
    assert score == 20.0
    assert bd["pop_penalty_applied"] is True
    assert bd["max_pop"] == 60


def test_pop_between_30_and_50_progressive_penalty():
    """降雨機率 40%：漸進扣分，係數 = 1 - (40-30)/40 = 0.75，100 分 → 75 分。"""
    score, bd = calculate_score(
        make_weather(0, 25, 0.0), make_astro(), None,
        pop_data={"max_pop": 40, "error": None},
    )
    assert score == 75.0
    assert bd["pop_penalty_applied"] is True


def test_pop_below_30_no_penalty():
    """降雨機率 20%：低於門檻，不扣分。"""
    score, bd = calculate_score(
        make_weather(0, 25, 0.0), make_astro(), None,
        pop_data={"max_pop": 20, "error": None},
    )
    assert score == 100.0
    assert bd["pop_penalty_applied"] is False


# ── EPA AQI 與 AOD 平均 ──────────────────────────────────────

def test_epa_aqi_averaged_with_aod():
    """有 EPA AQI 時，空氣品質分數 = (AOD 分 + EPA 分) / 2。
    AOD 0（滿分 1.0）+ AQI 75（分數 0.5）→ 空氣品質 0.75。"""
    score, bd = calculate_score(
        make_weather(0, 25, 0.0), make_astro(),
        aqi_data={"aqi": 75, "error": None},
    )
    # total = (雲 1.0*0.4 + 空品 0.75*0.3 + 能見 1.0*0.3) * 100 = 92.5
    assert score == 92.5
    assert bd["epa_score"] == 50.0


def test_no_epa_key_uses_aod_only():
    """未設 EPA 金鑰（error=no_key）時，空氣品質只用 AOD。"""
    score, bd = calculate_score(
        make_weather(0, 25, 0.0), make_astro(),
        aqi_data={"error": "no_key"},
    )
    assert bd["epa_score"] is None
    assert score == 100.0


# ── 能見度分段計分 ───────────────────────────────────────────

@pytest.mark.parametrize("vis_km, expected", [
    (25, 100.0),  # ≥20km → 1.0 → 總分 (0.4+0.3+0.3)*100
    (15, 91.0),   # 10-20km → 0.7 → (0.4+0.3+0.21)*100
    (7,  79.0),   # 5-10km → 0.3 → (0.4+0.3+0.09)*100
    (3,  70.0),   # <5km → 0.0 → (0.4+0.3+0.0)*100
])
def test_visibility_tiers(vis_km, expected):
    """能見度分四段：≥20→100%、10-20→70%、5-10→30%、<5→0%。"""
    score, _ = calculate_score(make_weather(0, vis_km, 0.0), make_astro(), None)
    assert score == expected


# ── AOD 門檻：≥0.3 得 0 分 ───────────────────────────────────

def test_aod_at_threshold_scores_zero():
    """AOD 0.3（門檻）：氣膠分數歸零，空氣品質(僅 AOD)=0。"""
    score, bd = calculate_score(make_weather(0, 25, 0.3), make_astro(), None)
    # total = (雲 1.0*0.4 + 空品 0*0.3 + 能見 1.0*0.3) * 100 = 70
    assert score == 70.0
    assert bd["aod_score"] == 0.0


# ── 黃金窗口加分 ─────────────────────────────────────────────

def test_golden_window_adds_5():
    """有黃金拍攝窗口 → +5 分。雲量 50% 基準 80 分 + 5 = 85。"""
    score, _ = calculate_score(
        make_weather(50, 25, 0.0),
        make_astro(golden=[("dummy_start", "dummy_end")]),
        None,
    )
    assert score == 85.0
