# app.py
# Flask 網站主程式
#
# 把原本的 CLI 推薦系統包裝成網頁介面
# 使用者可以在手機瀏覽器上查詢銀河拍攝推薦

from flask import Flask, render_template, request, jsonify
from datetime import date, timedelta, datetime
from recommender import recommend, build_report
from weather import TW_TZ

app = Flask(__name__)


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


@app.route("/recommend", methods=["POST"])
def get_recommendation():
    """接收表單、執行推薦、回傳結果"""
    # 讀取表單參數
    date_str = request.form.get("date", "")
    region = request.form.get("region", "").strip()
    bortle = 4
    top_n = int(request.form.get("top_n", 3))

    max_date = datetime.now(TW_TZ).date() + timedelta(days=7)

    # 驗證日期格式
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return render_template(
            "index.html",
            default_date=date_str,
            max_date=max_date.strftime("%Y-%m-%d"),
            result=None,
            error=f"日期格式錯誤：{date_str}，請使用 YYYY-MM-DD 格式",
        )

    # 執行推薦
    try:
        result = recommend(target_date=target_date, max_bortle=bortle, top_n=top_n)

        # 如果有指定區域，過濾結果
        if region and region != "全部":
            filtered = [
                item for item in result["candidates"]
                if region in item["location"]["region"]
            ]
            if filtered:
                result["candidates"] = filtered
                result["top"] = filtered[:top_n]
                result["report"] = build_report(
                    target_date, result["top"], result["candidates"]
                )
            # 若找不到指定區域，顯示全部結果

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


if __name__ == "__main__":
    # 本機測試用，port 5000
    app.run(debug=True, port=5001)
