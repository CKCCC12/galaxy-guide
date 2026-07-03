# tests/test_map_pwa.py
# 地圖（P1）與 PWA（P3）功能的測試
#
# 涵蓋：地圖資料來源、分數上色資料組裝、Service Worker 路由、manifest、
# 以及首頁確實嵌入地圖與 PWA 所需的各要素。

import json
import app as app_module
from locations import get_map_locations, get_all_locations


# ── P1：地圖資料 ─────────────────────────────────────────────

def test_get_map_locations_has_all_and_slim_fields():
    """地圖資料應涵蓋全部地點，且只含精簡欄位（不含 notes/google_maps）。"""
    locs = get_map_locations()
    assert len(locs) == len(get_all_locations())
    for loc in locs:
        assert set(loc.keys()) == {"name", "lat", "lon", "region", "bortle"}


def test_build_map_scores_maps_candidates():
    """_build_map_scores 應把每個候選地點轉為 {名稱, 座標, 分數(整數)}。"""
    result = {
        "candidates": [
            {"location": {"name": "A", "lat": 23.1, "lon": 121.4}, "score": 87.6},
            {"location": {"name": "B", "lat": 22.0, "lon": 120.8}, "score": 40.2},
        ]
    }
    out = app_module._build_map_scores(result)
    assert out == [
        {"name": "A", "lat": 23.1, "lon": 121.4, "score": 88},
        {"name": "B", "lat": 22.0, "lon": 120.8, "score": 40},
    ]


def test_index_embeds_map_and_all_locations(client_fixture=None):
    """首頁應嵌入地圖容器與全部地點座標。"""
    c = app_module.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert '<div id="map">' in html
    assert 'id="allLocations"' in html
    # 14 個地點座標都在
    assert html.count('"lat"') >= len(get_all_locations())


# ── P3：PWA ─────────────────────────────────────────────────

def test_service_worker_served_from_root():
    """/sw.js 需從根路徑提供（scope 才涵蓋全站），且為 JS MIME。"""
    c = app_module.app.test_client()
    resp = c.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("Content-Type", "")
    body = resp.get_data(as_text=True)
    # 動態端點不應被 SW 快取（避免快取到查詢結果）
    assert "/recommend" in body and "/ai-summary" in body


def test_manifest_is_valid_and_has_pwa_fields():
    """manifest.json 需可解析且含 PWA 必要欄位與圖示。"""
    c = app_module.app.test_client()
    resp = c.get("/static/manifest.json")
    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    for key in ("name", "short_name", "start_url", "display", "icons"):
        assert key in data
    assert data["display"] == "standalone"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert "192x192" in sizes and "512x512" in sizes


def test_index_registers_service_worker_and_manifest():
    """首頁需連結 manifest 並註冊 Service Worker。"""
    c = app_module.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in html
    assert "serviceWorker.register('/sw.js')" in html
