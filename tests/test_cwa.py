# tests/test_cwa.py
# 中央氣象署（CWA）模組的單元測試
#
# 用固定的 JSON 樣本（仿 CWA F-D0047-091 回應結構）測試解析邏輯，不碰網路。
# 聚焦：
#   1. _parse_cwa_time：CWA 的 +08:00 時區字串在 Python 3.7 需手動處理
#   2. _extract_night_pop：從全台縣市資料萃取「夜間」降雨機率，並正確處理無資料值

from datetime import datetime, date
from cwa import _parse_cwa_time, _extract_night_pop

ELEMENT_NAME = "12小時降雨機率"


# ── _parse_cwa_time：ISO 8601 帶時區字串 → naive datetime ─────

def test_parse_cwa_time_strips_timezone():
    """CWA 時間本身即台灣本地時間，去除 +08:00 後綴後回傳 naive datetime。"""
    got = _parse_cwa_time("2026-07-04T18:00:00+08:00")
    assert got == datetime(2026, 7, 4, 18, 0, 0)


# ── _extract_night_pop：萃取夜間降雨機率 ─────────────────────

def _make_location(time_blocks):
    """組一個仿 CWA 單一縣市的資料結構。"""
    return {
        "LocationName": "臺東縣",
        "_distance_km": 5.34,
        "WeatherElement": [
            {"ElementName": ELEMENT_NAME, "Time": time_blocks},
        ],
    }


def _block(start, end, pop):
    return {
        "StartTime": start,
        "EndTime": end,
        "ElementValue": [{"ProbabilityOfPrecipitation": pop}],
    }


def test_extract_night_pop_picks_night_block_only():
    """只納入與夜間(20:00–05:00)重疊的時段；白天時段應被排除。"""
    loc = _make_location([
        # 夜間 18:00–06:00 覆蓋觀測窗口 → 納入
        _block("2026-07-04T18:00:00+08:00", "2026-07-05T06:00:00+08:00", "30"),
        # 白天 06:00–18:00 → 無重疊，排除
        _block("2026-07-04T06:00:00+08:00", "2026-07-04T18:00:00+08:00", "10"),
    ])
    result = _extract_night_pop(loc, date(2026, 7, 4))
    assert result["county"] == "臺東縣"
    assert result["distance_km"] == 5.3          # round(5.34, 1)
    assert result["max_pop"] == 30
    assert len(result["pop_intervals"]) == 1     # 只有夜間那筆


def test_extract_night_pop_takes_max_across_intervals():
    """跨兩個夜間時段時，max_pop 取最大值。"""
    loc = _make_location([
        _block("2026-07-04T18:00:00+08:00", "2026-07-05T00:00:00+08:00", "20"),
        _block("2026-07-05T00:00:00+08:00", "2026-07-05T06:00:00+08:00", "70"),
    ])
    result = _extract_night_pop(loc, date(2026, 7, 4))
    assert result["max_pop"] == 70
    assert len(result["pop_intervals"]) == 2


def test_extract_night_pop_handles_dash_as_zero():
    """CWA 偶爾回傳 '-' 表示無資料 → 視為 0，不 crash。"""
    loc = _make_location([
        _block("2026-07-04T18:00:00+08:00", "2026-07-05T06:00:00+08:00", "-"),
    ])
    result = _extract_night_pop(loc, date(2026, 7, 4))
    assert result["max_pop"] == 0


def test_extract_night_pop_no_matching_element():
    """資料裡沒有降雨機率欄位時，回傳空的 pop_intervals、max_pop 為 0。"""
    loc = {"LocationName": "臺東縣", "_distance_km": 1.0, "WeatherElement": []}
    result = _extract_night_pop(loc, date(2026, 7, 4))
    assert result["max_pop"] == 0
    assert result["pop_intervals"] == []
