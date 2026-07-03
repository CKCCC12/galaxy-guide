# tests/test_ai_signature.py
# /ai-summary HMAC 簽章的回歸測試
#
# 重點防守一個真實踩過的雷：前端讀 aiPayload 後 JSON.parse→JSON.stringify 再送回，
# JavaScript 會把「整數值的浮點數」（22.0、96.0）塌成整數（22、96），
# 導致簽章對不上、合法 payload 被誤判為偽造、AI 卡片靜默消失。
# _js_normalize 在簽章與驗章前先把這類浮點數轉成 int，讓兩端一致。

import ai_summary as m


def _collapse_int_floats(obj):
    """模擬瀏覽器 JSON round-trip：整數值浮點數塌成 int（22.0 → 22）。"""
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: _collapse_int_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_collapse_int_floats(v) for v in obj]
    return obj


def test_signature_survives_browser_integer_float_collapse(monkeypatch):
    """含整數值浮點數（avg_cloud 22.0）的 payload，經瀏覽器 round-trip 後仍驗證通過。"""
    monkeypatch.setenv("AI_SUMMARY_SECRET", "s3cr3t")
    data = {
        "date": "2026-07-03", "weekday": "五",
        "locations": [{"name": "三仙台", "avg_cloud": 22.0, "moon_illum": 96.0, "aod": 0.123, "score": 88}],
    }
    signed = {**data, "sig": m._sign_payload(data)}
    # 前端送回的版本：22.0 → 22、96.0 → 96（sig 是字串，不受影響）
    from_browser = _collapse_int_floats(signed)
    assert from_browser["locations"][0]["avg_cloud"] == 22  # 確認確實塌成 int
    assert m._verify_payload(from_browser) is True


def test_signature_non_integer_floats_unaffected(monkeypatch):
    """非整數浮點數（22.5、0.123）不受影響，仍正常驗證通過。"""
    monkeypatch.setenv("AI_SUMMARY_SECRET", "s3cr3t")
    data = {"a": 22.5, "b": 0.123, "locations": [{"x": 1}]}
    signed = {**data, "sig": m._sign_payload(data)}
    assert m._verify_payload(signed) is True


def test_signature_still_rejects_tampering(monkeypatch):
    """正規化不能削弱防護：竄改內容仍必須被擋下。"""
    monkeypatch.setenv("AI_SUMMARY_SECRET", "s3cr3t")
    data = {"date": "2026-07-03", "locations": [{"name": "X", "score": 88}]}
    signed = {**data, "sig": m._sign_payload(data)}
    signed["locations"][0]["score"] = 99  # 竄改分數
    assert m._verify_payload(signed) is False


def test_signature_rejects_missing_sig(monkeypatch):
    """完全沒有簽章的裸 payload 仍被擋。"""
    monkeypatch.setenv("AI_SUMMARY_SECRET", "s3cr3t")
    assert m._verify_payload({"date": "2026-07-03", "locations": [{"x": 1}]}) is False
