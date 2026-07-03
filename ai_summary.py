# ai_summary.py
# AI 智慧解讀模組
#
# 把推薦引擎（recommender.py）算出的 top 地點數據，丟給 Google Gemini，
# 生成一段給「攝影新手」看的人話拍攝建議。
#
# 設計原則（刻意與專案其他模組一致）：
#   1. 分數仍由 recommender.py 的確定性公式計算，AI 只負責「解讀」，絕不碰分數。
#      → 同樣的天氣資料永遠得到同樣的分數，可重現、可除錯。
#   2. 優雅降級：沒設金鑰、網路失敗、超時、回應格式錯 → 一律回傳含 error 的 dict，
#      前端據此隱藏卡片，完全不影響核心推薦功能。
#   3. 快取：免費版 Gemini 有每分鐘/每日額度限制，同一份 payload
#      （同日期、同地點、同分數）只生成一次，TTL 內直接回快取。
#   4. 只用標準庫 urllib（與 weather.py 一致），不新增任何第三方相依套件。
#
# 環境變數：
#   GEMINI_API_KEY  ── 必要。未設定時整個 AI 卡片不會出現。
#                      申請（免費）：https://aistudio.google.com/apikey
#   GEMINI_MODEL    ── 選用。預設 "gemini-2.0-flash"（免費、速度快）。

import os
import ssl
import json
import time
import hmac
import hashlib
import urllib.request
import urllib.error
import certifi

# 與 airquality.py / cwa.py 一致：若有安裝 python-dotenv，就從 .env 載入金鑰。
# 未安裝時 os.environ 本來就有的環境變數（如 Render 後台設定）仍可使用。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 與 weather.py 一致：Google 憑證正常，啟用 SSL 驗證以防中間人攻擊（MITM）。
# Render 環境曾因執行環境缺少 CA 憑證包出現 CERTIFICATE_VERIFY_FAILED，
# 改用 certifi 提供的 CA bundle 即可正常驗證並解決該錯誤（取代原本的 CERT_NONE 繞過）。
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

# 免費版 Gemini 模型；可用環境變數覆寫成其他型號。
# 註：gemini-2.0 系列在部分免費金鑰的額度為 0（會回 429 limit:0），
#     gemini-2.5-flash 則有免費額度且寫作品質佳，故設為預設。
_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

# 生成結果快取：key（payload 的 md5）-> (timestamp, result_without_cached_flag)
_cache = {}
_CACHE_TTL = 1800  # 30 分鐘，與 weather 快取一致

_TIMEOUT = 15  # 秒；超過就放棄，前端維持「無卡片」狀態，不拖累使用者


def _get_api_key():
    """讀取 Gemini API 金鑰，未設定回 None（觸發降級）。"""
    return os.environ.get("GEMINI_API_KEY") or None


# ── /ai-summary 濫用防護：HMAC 簽章 ──────────────────────────
# /ai-summary 是公開端點。若任何人都能 POST 任意 payload，等於把 Gemini
# 額度開放給全世界濫用（燒光免費額度、被 Google 限流），且 payload 內容會被
# 組進 prompt（prompt injection 風險）。
#
# 對策：payload 由伺服器 build_payload() 產生時附上 HMAC-SHA256 簽章；
# /ai-summary 收到後先驗章，不符即拒絕。前端只能原封轉交「伺服器自己算過」
# 的資料，無法偽造內容。
#
# 密鑰 AI_SUMMARY_SECRET 由環境變數提供。未設定時「不啟用驗證」——與專案
# 其他模組「沒金鑰就優雅降級」的一致做法，但正式部署務必於 Render 後台設定，
# 否則此防護等於關閉。
_SIG_FIELD = "sig"


def _secret_key():
    """讀取簽章密鑰，未設定回 None（不啟用驗證）。"""
    return os.environ.get("AI_SUMMARY_SECRET") or None


def _sign_payload(data: dict) -> str:
    """對 payload 的資料部分算 HMAC-SHA256，未設密鑰時回空字串。

    以 sort_keys 的 JSON 當簽章訊息，確保序列化順序穩定
    （與 _cache_key 相同做法，前後端算出來才會一致）。
    """
    secret = _secret_key()
    if not secret:
        return ""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _verify_payload(payload: dict) -> bool:
    """驗證 payload 簽章。未設密鑰時一律通過（降級不阻擋）。

    用 hmac.compare_digest 做定時比較，避免計時攻擊（timing attack）
    透過回應時間差反推正確簽章。
    """
    secret = _secret_key()
    if not secret:
        return True
    provided = payload.get(_SIG_FIELD, "")
    if not provided:
        return False
    data = {k: v for k, v in payload.items() if k != _SIG_FIELD}
    return hmac.compare_digest(provided, _sign_payload(data))


def build_payload(target_date, top: list) -> dict:
    """從 recommend() 的 top 清單，萃取出 AI 解讀需要的「精簡欄位」。

    刻意只取「人類看報告時會在意」的指標，不傳整包逐小時資料：
      - prompt 更短、省 token，免費版額度更耐用
      - 也避免把一堆雜訊餵給模型導致解讀失焦

    這份 payload 會嵌進結果頁，再由前端 POST 給 /ai-summary，
    所以它必須是可被 json 序列化的純資料（不含 datetime 物件）。
    """
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    locations = []

    for rank, item in enumerate(top, start=1):
        loc = item["location"]
        moon = item["astro"]["moon"]
        mw = item["astro"]["milkyway"]
        summary = item["weather"]["night_summary"]
        golden = item["astro"]["golden_windows"]
        aqi_data = item.get("aqi")
        pop_data = item.get("pop")

        # 黃金窗口是 (start, end) 的 list；取第一段格式化成 "21:00–04:30"
        golden_str = None
        if golden:
            s, e = golden[0]
            golden_str = f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}"

        mw_peak = mw.get("max_altitude_time")

        locations.append({
            "rank": rank,
            "name": loc["name"],
            "region": loc["region"],
            "bortle": loc["bortle"],
            "score": round(item["score"]),
            "avg_cloud": summary.get("avg_cloud"),
            "moon_phase": moon.get("phase_name"),
            "moon_illum": moon.get("illumination_pct"),
            "visibility_km": summary.get("avg_visibility_km"),
            "aod": summary.get("avg_aod"),
            # AQI / 降雨可能因未設金鑰或 API 失敗而缺值，用 None 表示「無資料」
            "aqi": aqi_data.get("aqi") if (aqi_data and "aqi" in aqi_data) else None,
            "max_pop": pop_data.get("max_pop") if (pop_data and "max_pop" in pop_data) else None,
            "mw_max_alt": mw.get("max_altitude"),
            "mw_peak_time": mw_peak.strftime("%H:%M") if mw_peak else None,
            "golden_window": golden_str,
        })

    payload = {
        "date": target_date.strftime("%Y-%m-%d"),
        "weekday": weekday_names[target_date.weekday()],
        "locations": locations,
    }
    # 附上 HMAC 簽章，供 /ai-summary 驗證此 payload 確實由本站產生（見 _verify_payload）
    payload[_SIG_FIELD] = _sign_payload(payload)
    return payload


def _build_prompt(payload: dict) -> str:
    """把精簡 payload 組成給 Gemini 的中文 prompt。"""
    lines = [
        f"你是台灣銀河攝影的資深嚮導。以下是 {payload['date']}（週{payload['weekday']}）"
        "系統評估後的推薦地點數據（分數已由氣象與天文公式算好，滿分 100，"
        "分數越高越適合拍銀河）。請用繁體中文、沉穩平實的口吻，"
        "寫一段精簡、易懂的「重點解讀」。",
        "",
        "數據如下：",
    ]

    for loc in payload["locations"]:
        parts = [
            f"第{loc['rank']}名 {loc['name']}（{loc['region']}，"
            f"Bortle {loc['bortle']}，綜合 {loc['score']} 分）",
            f"雲量 {loc['avg_cloud']}%",
            f"月相 {loc['moon_phase']} 照明 {loc['moon_illum']}%",
            f"能見度 {loc['visibility_km']}km",
            f"AOD {loc['aod']}",
        ]
        if loc["aqi"] is not None:
            parts.append(f"AQI {loc['aqi']}")
        if loc["max_pop"] is not None:
            parts.append(f"降雨機率 {loc['max_pop']}%")
        if loc["mw_max_alt"] is not None:
            peak = loc["mw_peak_time"] or "夜間"
            parts.append(f"銀河核心最高仰角 {loc['mw_max_alt']}° 於 {peak}")
        if loc["golden_window"]:
            parts.append(f"黃金拍攝時段 {loc['golden_window']}")
        lines.append("- " + "；".join(parts))

    lines += [
        "",
        "請輸出 JSON，包含兩個欄位：",
        "1. summary：100～140 字的整體解讀，務必精簡，勿超過 140 字。"
        "點出最佳選擇與原因、需要注意的風險（雲量太高、降雨機率高、月光太亮、"
        "空氣品質差都會影響拍攝）。語氣沉穩、客觀、實事求是，"
        "不要用驚嘆號，不要過度熱情或誇張的措辭（避免「衝一波」「超讚」這類用語）。"
        "不要逐項複述數字，要給出判斷與取捨建議。",
        "2. tips：2～4 條精簡的行動建議，每條 14 字以內，語氣平實、不用驚嘆號，"
        "像「21:00 前到定位卡機位」「月落後最暗最適合」這種可直接照做的提醒。",
        "注意：只能根據上面提供的數據解讀，不可杜撰沒有的資訊。"
        "另外，內容中不要出現或稱呼讀者為「新手」「攝影新手」等字眼。",
    ]
    return "\n".join(lines)


def _cache_key(payload: dict) -> str:
    """以 payload 內容（排序後）算 md5 當快取 key，內容相同就命中。"""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_ai_summary(payload: dict) -> dict:
    """產生 AI 解讀。任何失敗都回傳含 error 的 dict，讓前端靜默降級。

    回傳格式：
      成功    → {"summary": str, "tips": [str], "model": str, "cached": bool}
      無金鑰  → {"error": "no_key"}
      無資料  → {"error": "no_data"}
      其他    → {"error": "<原因代碼>"}
    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "no_key"}
    if not payload or not payload.get("locations"):
        return {"error": "no_data"}
    # 驗簽：擋掉偽造 payload 的濫用（未設 AI_SUMMARY_SECRET 時此檢查自動放行）
    if not _verify_payload(payload):
        return {"error": "bad_sig"}
    # 驗證後移除簽章欄位，讓後續的快取 key 與 prompt 只看純資料
    payload = {k: v for k, v in payload.items() if k != _SIG_FIELD}

    key = _cache_key(payload)
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0] < _CACHE_TTL):
        result = dict(cached[1])
        result["cached"] = True
        return result

    prompt = _build_prompt(payload)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            # 0.4 讓輸出更收斂、字數更穩定貼近上限（沉穩客觀的目標下，低溫反而加分）
            "temperature": 0.4,
            # 關閉「思考」：寫 180 字摘要不需要推理，開著只會讓回應從 2~4 秒
            # 暴增到 8~12 秒甚至逾時。2.5 系列吃 thinkingConfig.thinkingBudget=0。
            "thinkingConfig": {"thinkingBudget": 0},
            # 要求模型直接吐 JSON，並用 schema 約束結構，省去自己 parse 的麻煩
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "tips": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "tips"],
            },
        },
    }

    # 金鑰改放 header（x-goog-api-key），不放 URL query string：
    # URL 常被寫進代理紀錄、瀏覽器歷史、伺服器日誌，放 header 較不易外洩。
    url = _API_URL.format(model=_MODEL)
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "galaxy-guide/1.0",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 把 Google 回的錯誤訊息印出來（如金鑰錯、額度用盡），方便除錯
        detail = e.read().decode("utf-8", "ignore")[:300]
        print(f"⚠️  Gemini HTTP {e.code}：{detail}", flush=True)
        return {"error": f"http_{e.code}"}
    except Exception as e:
        print(f"⚠️  Gemini 呼叫失敗：{e}", flush=True)
        return {"error": "request_failed"}

    # 解析回應：Gemini 結構為 candidates[0].content.parts[0].text，text 是 JSON 字串
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        summary = parsed["summary"].strip()
        tips = [t.strip() for t in parsed.get("tips", []) if t and t.strip()]
    except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError) as e:
        print(f"⚠️  Gemini 回應格式非預期：{e}", flush=True)
        return {"error": "bad_response"}

    if not summary:
        return {"error": "empty"}

    stored = {"summary": summary, "tips": tips, "model": _MODEL}
    _cache[key] = (now, stored)

    result = dict(stored)
    result["cached"] = False
    return result
