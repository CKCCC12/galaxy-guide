# app.py
# Flask 網站主程式
#
# 把原本的 CLI 推薦系統包裝成網頁介面
# 使用者可以在手機瀏覽器上查詢銀河拍攝推薦

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for,
    Response, stream_with_context,
)
from datetime import date, timedelta, datetime
import json
import queue
import threading
from recommender import recommend
from weather import TW_TZ

app = Flask(__name__)


@app.route("/version")
def version():
    """部署驗證端點：回傳當前運行版本標記與關鍵函式是否存在"""
    import weather
    return jsonify({
        "version": "batch-prefetch-v1",
        "has_prefetch_weather_batch": hasattr(weather, "prefetch_weather_batch"),
    })


@app.route("/api-status")
def api_status():
    """診斷端點：測試每個外部 API 的連線狀態，直接顯示錯誤訊息"""
    import urllib.request
    import ssl
    import json

    results = {}

    # 建立 SSL context（繞過驗證）
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # 測試 Open-Meteo
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=23.14&longitude=121.42&hourly=cloud_cover&timezone=Asia/Taipei&start_date=2026-04-17&end_date=2026-04-17"
        req = urllib.request.Request(url, headers={"User-Agent": "galaxy-guide/1.0"})
        with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as r:
            data = json.loads(r.read().decode())
            results["open_meteo"] = {"status": "ok", "hours": len(data.get("hourly", {}).get("time", []))}
    except Exception as e:
        results["open_meteo"] = {"status": "error", "error": str(e)}

    # 測試不加 SSL context 的 Open-Meteo（對比用）
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=23.14&longitude=121.42&hourly=cloud_cover&timezone=Asia/Taipei&start_date=2026-04-17&end_date=2026-04-17"
        with urllib.request.urlopen(url, timeout=10) as r:
            results["open_meteo_no_ssl_ctx"] = {"status": "ok"}
    except Exception as e:
        results["open_meteo_no_ssl_ctx"] = {"status": "error", "error": str(e)}

    return jsonify(results)


@app.route("/", methods=["GET"])
def index():
    """顯示查詢表單（首頁）"""
    today = datetime.now(TW_TZ).date()
    default_date = today
    max_date = today + timedelta(days=7)

    return render_template(
        "index.html",
        default_date=default_date.strftime("%Y-%m-%d"),
        max_date=max_date.strftime("%Y-%m-%d"),
        result=None,
        error=None,
    )


@app.route("/recommend", methods=["GET"])
def recommend_get():
    """GET 訪問 /recommend（直接輸入網址、重新整理、書籤）導回首頁，避免 405/500"""
    return redirect(url_for("index"))


@app.route("/recommend", methods=["POST"])
def get_recommendation():
    """接收表單、執行推薦、回傳結果"""
    # 讀取表單參數
    date_str = request.form.get("date", "")
    region = request.form.get("region", "").strip()
    bortle = 4

    today = datetime.now(TW_TZ).date()
    max_date = today + timedelta(days=7)

    # 驗證 top_n 與日期格式
    try:
        top_n = int(request.form.get("top_n", 3))
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return render_template(
            "index.html",
            default_date=date_str,
            max_date=max_date.strftime("%Y-%m-%d"),
            result=None,
            error=f"參數格式錯誤：日期 {date_str}，請使用 YYYY-MM-DD 格式",
        )

    # 驗證日期範圍：前端 input 的 max 屬性可被繞過（直接 POST），
    # 超出 Open-Meteo 預報範圍會導致所有地點查詢失敗、顯示一堆無意義的 0 分
    if not (today <= target_date <= max_date):
        return render_template(
            "index.html",
            default_date=today.strftime("%Y-%m-%d"),
            max_date=max_date.strftime("%Y-%m-%d"),
            result=None,
            error=f"日期需在 {today} 至 {max_date} 之間（天氣預報僅支援未來 7 天）",
        )

    # 執行推薦
    # 區域過濾在 recommend() 內、評分之前完成：選定區域時只評估該區地點，
    # 大幅減少外部 API 呼叫與等待時間（找不到該區域地點時自動退回全部）
    try:
        result = recommend(
            target_date=target_date, max_bortle=bortle, top_n=top_n, region=region
        )

        # 查詢今天才標記當前時段，查詢未來日期無意義
        now_tw = datetime.now(TW_TZ)
        current_hour = now_tw.hour if target_date == now_tw.date() else None

        return render_template(
            "index.html",
            default_date=date_str,
            max_date=max_date.strftime("%Y-%m-%d"),
            result=result,
            error=None,
            selected_region=region,
            selected_top_n=top_n,
            current_hour=current_hour,
        )

    except Exception as e:
        return render_template(
            "index.html",
            default_date=date_str,
            max_date=max_date.strftime("%Y-%m-%d"),
            result=None,
            error=f"查詢失敗：{str(e)}",
        )


@app.route("/recommend/stream")
def recommend_stream():
    """SSE 串流端點：一邊評估各地點、一邊回報真實進度，最後送出渲染好的結果 HTML。

    前端以 EventSource 連線，依序收到：
      event: progress  data: {"done": n, "total": N, "name": "剛完成的地點"}
      event: result    data: {"html": "<...>"}    ← 全部完成，內含伺服器渲染的結果片段
      event: failed    data: {"message": "..."}    ← 參數錯誤或查詢例外
    全程不換頁，進度條反映真實完成度。不支援 EventSource 的瀏覽器由前端走 POST 後備。
    """
    date_str = request.args.get("date", "")
    region = request.args.get("region", "").strip()
    try:
        top_n = int(request.args.get("top_n", 3))
    except (ValueError, TypeError):
        top_n = 3

    today = datetime.now(TW_TZ).date()
    max_date = today + timedelta(days=7)

    def sse(event, payload):
        """組一則 SSE 訊息；payload 以 JSON 編成單行，換行/引號交給 json 處理。"""
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # 日期驗證（與 POST 相同規則）：不合法直接送 failed 事件後結束
    err_msg = None
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        err_msg = f"參數格式錯誤：日期 {date_str}，請使用 YYYY-MM-DD 格式"
    else:
        if not (today <= target_date <= max_date):
            err_msg = f"日期需在 {today} 至 {max_date} 之間（天氣預報僅支援未來 7 天）"

    if err_msg is not None:
        return Response(sse("failed", {"message": err_msg}), mimetype="text/event-stream")

    now_tw = datetime.now(TW_TZ)
    current_hour = now_tw.hour if target_date == now_tw.date() else None

    # 工作執行緒負責耗時評估，把進度/結果丟進 queue；主請求執行緒負責 yield SSE。
    # （recommend 內部已用 ThreadPoolExecutor 平行查詢，這裡只是再外包一層，
    #   讓「等結果」與「推進度」能同時進行。）
    q = queue.Queue()

    def progress(done, total, name):
        q.put(("progress", {"done": done, "total": total, "name": name}))

    def worker():
        try:
            result = recommend(
                target_date=target_date, max_bortle=4, top_n=top_n,
                region=region, progress_callback=progress,
            )
            q.put(("result", result))
        except Exception:
            app.logger.exception("stream recommend failed")
            q.put(("failed", {"message": "查詢時發生問題，請稍後再試一次。"}))
        finally:
            q.put((None, None))  # 結束哨兵

    threading.Thread(target=worker, daemon=True).start()

    @stream_with_context
    def generate():
        while True:
            kind, payload = q.get()
            if kind is None:
                break
            if kind == "result":
                # render_template 需在請求情境內執行，stream_with_context 已保證
                html = render_template(
                    "_result.html", result=payload,
                    selected_region=region, selected_top_n=top_n,
                    current_hour=current_hour,
                )
                yield sse("result", {"html": html})
            else:
                yield sse(kind, payload)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    # 本機測試用，port 5000
    app.run(debug=True, port=5001)
