# tests/test_weather.py
# 天氣模組的單元測試
#
# 聚焦三個過去出過 bug 或最容易出錯的地方：
#   1. _filter_night_hours：月末跨月（4/30 → 5/1）曾造成 crash（day+1 超出當月天數）
#   2. _match_responses_to_coords：批次回應依「座標就近」配對，曾因依順序 zip 而整批錯位
#   3. _parse_hourly：把 Open-Meteo 原始 JSON 解析成結構化逐小時資料

from datetime import datetime, date
from weather import (
    _filter_night_hours,
    _match_responses_to_coords,
    _parse_hourly,
    TW_TZ,
)


# ── _filter_night_hours：夜間時段篩選（含月末跨月）────────────

def _hour(y, mo, d, h):
    """建一筆只含 time 欄位的逐小時資料（篩選函式只看 time）。"""
    return {"time": TW_TZ.localize(datetime(y, mo, d, h, 0))}


def test_filter_night_hours_crosses_month_end():
    """4/30 查詢：夜間窗口跨到 5/1，不可因 4/31 不存在而 crash。"""
    target = date(2026, 4, 30)
    hours = [
        _hour(2026, 4, 30, 19),  # 19:00 夜間開始前 → 排除
        _hour(2026, 4, 30, 20),  # 20:00 夜間開始 → 納入
        _hour(2026, 4, 30, 23),  # 23:00 → 納入
        _hour(2026, 5, 1, 5),    # 隔天 05:00 夜間結束 → 納入
        _hour(2026, 5, 1, 6),    # 隔天 06:00 → 排除
    ]
    night = _filter_night_hours(hours, target)
    got_hours = [h["time"].hour for h in night]
    assert got_hours == [20, 23, 5]


def test_filter_night_hours_year_end():
    """12/31 查詢：夜間窗口跨到隔年 1/1，同樣不可 crash。"""
    target = date(2026, 12, 31)
    hours = [
        _hour(2026, 12, 31, 20),
        _hour(2027, 1, 1, 5),
        _hour(2027, 1, 1, 6),  # 排除
    ]
    night = _filter_night_hours(hours, target)
    assert len(night) == 2


# ── _match_responses_to_coords：批次回應就近配對 ─────────────

def test_match_responses_pairs_by_proximity_not_order():
    """回應順序與請求順序相反時，仍應依座標就近正確配對（防整批錯位）。"""
    coords = [(23.1, 121.4), (22.0, 120.8)]
    # 故意把回應順序反過來：第一筆其實對應第二個座標
    responses = [
        {"latitude": 22.05, "longitude": 120.82, "tag": "B"},
        {"latitude": 23.12, "longitude": 121.38, "tag": "A"},
    ]
    matched = _match_responses_to_coords(responses, coords)
    assert matched[(23.1, 121.4)]["tag"] == "A"
    assert matched[(22.0, 120.8)]["tag"] == "B"


def test_match_responses_drops_far_response():
    """回應座標與所有請求座標距離 > 0.5°（容差）→ 丟棄，不硬配。"""
    coords = [(23.1, 121.4)]
    responses = [{"latitude": 25.0, "longitude": 121.5}]  # 差約 1.9°
    matched = _match_responses_to_coords(responses, coords)
    assert matched == {}


def test_match_responses_drops_response_missing_coords():
    """回應缺 latitude/longitude 欄位 → 丟棄該筆，不 crash。"""
    coords = [(23.1, 121.4)]
    responses = [{"tag": "no-coords"}]
    matched = _match_responses_to_coords(responses, coords)
    assert matched == {}


def test_match_responses_within_tolerance_boundary():
    """恰在容差內（各軸偏 0.3°，距離約 0.42° < 0.5°）→ 應配對成功。"""
    coords = [(23.0, 121.0)]
    responses = [{"latitude": 23.3, "longitude": 121.3}]
    matched = _match_responses_to_coords(responses, coords)
    assert (23.0, 121.0) in matched


# ── _parse_hourly：原始 JSON → 結構化逐小時 ──────────────────

def test_parse_hourly_good_and_bad_hour():
    """一筆好天氣、一筆壞天氣，驗證評級、suitable、警告、逐小時分數。"""
    raw = {"hourly": {
        "time": ["2026-07-04T20:00", "2026-07-04T21:00"],
        "cloud_cover": [10, 90],
        "cloud_cover_low": [5, 80],
        "visibility": [24000, 3000],           # 公尺
        "aerosol_optical_depth": [0.05, 0.4],
        "dust": [2.0, 60.0],                   # μg/m³
        "relative_humidity_2m": [60, 95],
    }}
    parsed = _parse_hourly(raw, date(2026, 7, 4))
    assert len(parsed) == 2

    good = parsed[0]
    assert good["cloud_cover"] == 10
    assert good["visibility_km"] == 24.0
    assert good["rating"] == "良好"
    assert good["suitable"] is True
    assert good["warnings"] == []
    # 逐小時分數 = (雲 0.9*0.4 + 氣膠 0.833*0.3 + 能見 1.0*0.3)*100 ≈ 91
    assert good["hour_score"] == 91

    bad = parsed[1]
    assert bad["rating"] == "不建議"
    assert bad["suitable"] is False
    assert bad["hour_score"] == 4
    # 沙塵 60 與濕度 95 應各觸發一則警告
    assert any("沙塵" in w for w in bad["warnings"])
    assert any("結露" in w for w in bad["warnings"])


def test_parse_hourly_handles_missing_optional_fields():
    """缺 visibility/aod 等選用欄位時，套用安全預設（vis=0、aod=0.5），不 crash。"""
    raw = {"hourly": {
        "time": ["2026-07-04T20:00"],
        "cloud_cover": [30],
        "cloud_cover_low": [10],
        # 故意不給 visibility / aod / dust / humidity
    }}
    parsed = _parse_hourly(raw, date(2026, 7, 4))
    assert len(parsed) == 1
    h = parsed[0]
    assert h["visibility_m"] == 0
    assert h["aod"] == 0.5
    assert h["humidity"] == 0


def test_parse_hourly_handles_null_in_arrays():
    """陣列內含 null（Open-Meteo 偶爾如此）時，雲量以 100 代入（最保守）。"""
    raw = {"hourly": {
        "time": ["2026-07-04T20:00"],
        "cloud_cover": [None],
        "cloud_cover_low": [None],
        "visibility": [None],
    }}
    parsed = _parse_hourly(raw, date(2026, 7, 4))
    assert parsed[0]["cloud_cover"] == 100
