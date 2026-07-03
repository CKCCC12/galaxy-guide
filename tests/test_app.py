# tests/test_app.py
# Flask 端點的整合測試（用 app.test_client()，不需真的起伺服器、不碰網路）
#
# 涵蓋：
#   - 首頁與 /recommend 的 GET 導向
#   - 日期驗證（格式錯、超出預報範圍）—— 這些在呼叫 recommend() 前就攔下，不碰網路
#   - B5：查詢例外時不外洩內部錯誤訊息
#   - B4：除錯端點需 DEBUG_TOKEN
#   - B8：/ai-summary 的金鑰與簽章防護

from datetime import datetime, timedelta
import pytest

import app as app_module
from weather import TW_TZ


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _today_str():
    return datetime.now(TW_TZ).date().strftime("%Y-%m-%d")


# ── 基本路由 ─────────────────────────────────────────────────

def test_index_get_returns_form(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "台灣銀河拍攝地點推薦" in resp.get_data(as_text=True)


def test_recommend_get_redirects_to_index(client):
    """直接 GET /recommend（書籤、重新整理）應導回首頁，而非 405/500。"""
    resp = client.get("/recommend")
    assert resp.status_code == 302
    assert resp.headers["Location"].rstrip("/").endswith("") or resp.headers["Location"] in ("/", "http://localhost/")


# ── 日期驗證（不碰網路，validation 於 recommend() 前攔下）──────

def test_post_bad_date_format_shows_error(client):
    resp = client.post("/recommend", data={"date": "2026/07/04", "region": "全部", "top_n": "3"})
    assert resp.status_code == 200
    assert "參數格式錯誤" in resp.get_data(as_text=True)


def test_post_out_of_range_date_shows_error(client):
    """超出未來 7 天預報範圍的日期應被擋下並提示。"""
    far = (datetime.now(TW_TZ).date() + timedelta(days=30)).strftime("%Y-%m-%d")
    resp = client.post("/recommend", data={"date": far, "region": "全部", "top_n": "3"})
    assert resp.status_code == 200
    assert "日期需在" in resp.get_data(as_text=True)


# ── B5：查詢例外不外洩內部訊息 ───────────────────────────────

def test_post_exception_does_not_leak_internal_message(client, monkeypatch):
    """recommend() 拋出含敏感內容的例外時，回應只顯示通用訊息，不外洩細節。"""
    secret = "內部路徑 /etc/secret api_key=XYZ123"

    def boom(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(app_module, "recommend", boom)
    resp = client.post("/recommend", data={"date": _today_str(), "region": "全部", "top_n": "3"})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert secret not in body
    assert "RuntimeError" not in body
    assert "查詢時發生問題" in body


# ── B4：除錯端點需 DEBUG_TOKEN ───────────────────────────────

def test_version_without_token_is_404(client, monkeypatch):
    monkeypatch.delenv("DEBUG_TOKEN", raising=False)
    assert client.get("/version").status_code == 404
    assert client.get("/api-status").status_code == 404


def test_version_with_correct_token_ok(client, monkeypatch):
    monkeypatch.setenv("DEBUG_TOKEN", "tok123")
    assert client.get("/version?token=tok123").status_code == 200
    assert client.get("/version?token=wrong").status_code == 404
    assert client.get("/version").status_code == 404


# ── B8：/ai-summary 金鑰與簽章防護 ───────────────────────────

def test_ai_summary_empty_payload_400(client):
    resp = client.post("/ai-summary", json={})
    assert resp.status_code == 400


def test_ai_summary_no_gemini_key_returns_no_key(client, monkeypatch):
    """未設 GEMINI_API_KEY → 直接回 no_key（優雅降級，前端隱藏卡片）。"""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    resp = client.post("/ai-summary", json={"locations": [{"rank": 1}]})
    assert resp.get_json().get("error") == "no_key"


def test_ai_summary_bad_signature_rejected(client, monkeypatch):
    """設了金鑰與密鑰，但 payload 未帶正確簽章 → 回 bad_sig，不會真的呼叫 Gemini。"""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    monkeypatch.setenv("AI_SUMMARY_SECRET", "dummy-secret")
    # 沒有 sig 欄位的偽造 payload
    resp = client.post("/ai-summary", json={"locations": [{"rank": 1, "name": "X"}]})
    assert resp.get_json().get("error") == "bad_sig"
