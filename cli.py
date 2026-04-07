# cli.py
# 銀河拍攝推薦系統：命令列介面
#
# 使用方式：
#   python cli.py                        # 推薦下一個週六
#   python cli.py --date 2026-04-12      # 指定日期（週日）
#   python cli.py --both                 # 同時顯示週六與週日
#   python cli.py --region 台東          # 只看台東地點
#   python cli.py --bortle 3             # 只看 Bortle ≤ 3 的地點
#   python cli.py --top 5                # 顯示前 5 名（預設 3）

import argparse
import sys
from datetime import date, timedelta, datetime
from recommender import recommend


def parse_args():
    """定義並解析命令列參數"""
    parser = argparse.ArgumentParser(
        prog="galaxy-guide",
        description="🌌 台灣週末銀河拍攝地點推薦系統",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
範例：
  python cli.py                       下一個週六的推薦
  python cli.py --both                週六 + 週日都顯示
  python cli.py --date 2026-05-01     指定日期
  python cli.py --region 台東          只看台東地點
  python cli.py --bortle 2            只看極暗地點（Bortle ≤ 2）
  python cli.py --top 5               顯示前 5 名
        """,
    )

    parser.add_argument(
        "--date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="指定查詢日期（預設：下一個週六）",
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="同時顯示週六與週日的推薦",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="地區名稱",
        help="只篩選特定區域（如：台東、屏東、花蓮）",
    )
    parser.add_argument(
        "--bortle",
        type=int,
        default=4,
        metavar="1-9",
        help="接受的最高 Bortle 光害等級（預設：4）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        metavar="N",
        help="顯示前 N 名推薦（預設：3）",
    )

    return parser.parse_args()


def get_target_dates(args):
    """
    根據參數決定要查詢哪些日期

    邏輯：
    - 有指定 --date：用該日期
    - 有 --both：下一個週六 + 週日
    - 都沒有：下一個週六
    """
    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
            return [d]
        except ValueError:
            print(f"❌ 日期格式錯誤：{args.date}，請用 YYYY-MM-DD 格式")
            sys.exit(1)

    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)

    if args.both:
        return [saturday, saturday + timedelta(days=1)]

    return [saturday]


def filter_by_region(result: dict, region: str) -> dict:
    """
    從推薦結果中只保留指定區域的地點

    因為 recommend() 已跑完所有地點，這裡只是從結果中過濾，
    不需要重新查詢 API，效率較高。
    """
    filtered_candidates = [
        item for item in result["candidates"]
        if region in item["location"]["region"]
    ]

    if not filtered_candidates:
        print(f"⚠️  找不到區域「{region}」的地點，顯示全部結果")
        return result

    # 重新取 top N
    top_n = min(args_top, len(filtered_candidates))  # 用全域變數
    result["candidates"] = filtered_candidates
    result["top"] = filtered_candidates[:top_n]
    result["report"] = rebuild_report_for_region(result, region)
    return result


# 用來傳遞 top 數量給 filter_by_region
args_top = 3


def rebuild_report_for_region(result: dict, region: str) -> str:
    """重新組合只含特定區域的報告"""
    from recommender import build_report
    return build_report(result["date"], result["top"], result["candidates"])


def print_header():
    """印出系統標題"""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║       🌌  台灣銀河拍攝週末推薦系統  🌌               ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


def main():
    global args_top

    args = parse_args()
    args_top = args.top

    print_header()

    target_dates = get_target_dates(args)

    for i, target_date in enumerate(target_dates):
        if i > 0:
            print("\n" + "─" * 56 + "\n")

        print(f"⏳ 正在查詢 {target_date} 的天氣與天文資料...\n")

        result = recommend(
            target_date=target_date,
            max_bortle=args.bortle,
            top_n=args.top,
        )

        # 若有指定區域，過濾結果
        if args.region:
            result = filter_by_region(result, args.region)

        print()
        print(result["report"])

    print()


if __name__ == "__main__":
    main()
