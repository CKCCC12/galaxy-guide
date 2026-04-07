# app.py
# Flask 網站主程式
#
# 把原本的 CLI 推薦系統包裝成網頁介面
# 使用者可以在手機瀏覽器上查詢銀河拍攝推薦

from flask import Flask, render_template, request
from datetime import date, timedelta, datetime
from recommender import recommend, build_report

app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    """顯示查詢表單（首頁）"""
    # 預設日期：下一個週六
    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    default_date = today + timedelta(days=days_until_saturday)

    return render_template(
        "index.html",
        default_date=default_date.strftime("%Y-%m-%d"),
        result=None,
        error=None,
    )


@app.route("/recommend", methods=["POST"])
def get_recommendation():
    """接收表單、執行推薦、回傳結果"""
    # 讀取表單參數
    date_str = request.form.get("date", "")
    region = request.form.get("region", "").strip()
    bortle = int(request.form.get("bortle", 4))
    top_n = int(request.form.get("top_n", 3))

    # 驗證日期格式
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return render_template(
            "index.html",
            default_date=date_str,
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

        return render_template(
            "index.html",
            default_date=date_str,
            result=result,
            error=None,
            selected_region=region,
            selected_bortle=bortle,
            selected_top_n=top_n,
        )

    except Exception as e:
        return render_template(
            "index.html",
            default_date=date_str,
            result=None,
            error=f"查詢失敗：{str(e)}",
        )


if __name__ == "__main__":
    # 本機測試用，port 5000
    app.run(debug=True, port=5001)
