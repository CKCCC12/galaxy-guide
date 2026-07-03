# tests/test_astronomy.py
# 天文模組的單元測試
#
# 聚焦 _calculate_dark_hours：從月升/月落時間推算「月亮在地平線下」的暗夜窗口。
# 這段有四種情境分支，邏輯繞（月升在前/月落在前、只有其中之一、都沒有），
# 最容易寫錯又不易一眼看出，最值得用測試釘住。
#
# 觀測窗口定義：當天 20:00 ~ 隔天 05:00。

from datetime import datetime, date
from astronomy import _calculate_dark_hours, TW_TZ

TARGET = date(2026, 7, 4)


def _t(h, m=0, next_day=False):
    """建 TW_TZ 帶時區的時間；next_day=True 表示隔天（7/5）。"""
    d = 5 if next_day else 4
    return TW_TZ.localize(datetime(2026, 7, d, h, m))


OBS_START = _t(20)             # 7/4 20:00
OBS_END = _t(5, next_day=True) # 7/5 05:00


# ── 情境 1：整晚無月（月升月落皆 None）→ 全夜皆暗 ─────────────

def test_no_moon_all_night_dark():
    dark = _calculate_dark_hours(None, None, TARGET)
    assert dark == [(OBS_START, OBS_END)]


# ── 情境 2：只有月升 ─────────────────────────────────────────

def test_only_moonrise_after_obs_start():
    """月亮 23:00 才升起 → 月升前（20:00–23:00）是暗夜。"""
    moonrise = _t(23)
    dark = _calculate_dark_hours(moonrise, None, TARGET)
    assert dark == [(OBS_START, moonrise)]


def test_only_moonrise_before_obs_start_no_dark():
    """月亮在觀測開始前（19:00）就升起且整晚未落 → 全程有月，無暗夜。"""
    dark = _calculate_dark_hours(_t(19), None, TARGET)
    assert dark == []


# ── 情境 3：只有月落 ─────────────────────────────────────────

def test_only_moonset_before_obs_end():
    """月亮 02:00 落下 → 月落後（02:00–05:00）是暗夜。"""
    moonset = _t(2, next_day=True)
    dark = _calculate_dark_hours(None, moonset, TARGET)
    assert dark == [(moonset, OBS_END)]


def test_only_moonset_after_obs_end_no_dark():
    """月亮到隔天 06:00 才落（超過觀測結束）→ 整晚有月，無暗夜。"""
    dark = _calculate_dark_hours(None, _t(6, next_day=True), TARGET)
    assert dark == []


# ── 情境 4：月升與月落都有 ───────────────────────────────────

def test_moonrise_before_moonset_two_dark_windows():
    """月升(22:00)在前、月落(隔天03:00)在後 → 月升前與月落後各一段暗夜。"""
    moonrise = _t(22)
    moonset = _t(3, next_day=True)
    dark = _calculate_dark_hours(moonrise, moonset, TARGET)
    assert dark == [(OBS_START, moonrise), (moonset, OBS_END)]


def test_moonset_before_moonrise_middle_dark_window():
    """月落(21:00)在前、月升(隔天04:00)在後 → 中間那段(21:00–04:00)是暗夜。"""
    moonset = _t(21)
    moonrise = _t(4, next_day=True)
    dark = _calculate_dark_hours(moonrise, moonset, TARGET)
    assert dark == [(moonset, moonrise)]
