# locations.py
# 台灣銀河拍攝熱點資料庫
#
# Bortle 等級說明（天空黑暗程度，數字越小越好）：
#   1-2：極度黑暗，銀河清晰可見、有光影
#   3-4：農村天空，銀河明顯
#   5-6：郊區天空，銀河勉強可見
#   7+：城市天空，幾乎看不到銀河
#
# 每個地點包含：
#   name        - 地點名稱
#   lat / lon   - 緯度 / 經度（用於查詢天氣與計算銀河仰角）
#   altitude_m  - 海拔高度（公尺），越高通常雲層越少、視野越好
#   bortle      - 光害等級（1-9）
#   best_months - 適合拍攝銀河的月份（台灣銀河季約 3-10 月，核心 4-9 月）
#   region      - 所在區域
#   notes       - 補充說明（交通、注意事項）
#   google_maps - Google Maps 連結（可直接導航）

LOCATIONS = [
    # ── 東部・花蓮 ────────────────────────────────────────────
    {
        "name": "花蓮鹽寮漁港",
        "lat": 23.9372,
        "lon": 121.6461,
        "altitude_m": 5,
        "bortle": 4,
        "best_months": [3, 4, 5, 6, 7, 8, 9],
        "region": "花蓮",
        "notes": "面向太平洋，東側無光害，可拍銀河從海平面升起的構圖。"\
                 "漁港有防波堤可作前景，停車方便。花蓮市區光害偏北方，影響較小。",
        "google_maps": "https://maps.app.goo.gl/鹽寮漁港",
    },

    # ── 東部・台東 ────────────────────────────────────────────
    {
        "name": "台東池上大坡池",
        "lat": 23.1089,
        "lon": 121.2358,
        "altitude_m": 255,
        "bortle": 3,
        "best_months": [4, 5, 6, 7, 8, 9],
        "region": "台東",
        "notes": "濕地湖面可拍銀河倒影，四周視野開闊無遮蔽。"\
                 "池上市區在西側，東側面向海岸山脈方向光害極低。停車場免費。",
        "google_maps": "https://maps.app.goo.gl/大坡池",
    },
    {
        "name": "台東長濱金剛大道",
        "lat": 23.3278,
        "lon": 121.4419,
        "altitude_m": 30,
        "bortle": 3,
        "best_months": [4, 5, 6, 7, 8, 9],
        "region": "台東",
        "notes": "筆直大道兩側棕櫚樹成排，可拍銀河橫跨道路的壯觀構圖。"\
                 "長濱鄉人口稀少，光害極低。東側面向太平洋，視野無阻。",
        "google_maps": "https://maps.app.goo.gl/長濱金剛大道",
    },
    {
        "name": "台東三仙台",
        "lat": 23.1406,
        "lon": 121.4197,
        "altitude_m": 15,
        "bortle": 3,
        "best_months": [4, 5, 6, 7, 8, 9],
        "region": "台東",
        "notes": "八拱跨海步橋是絕佳前景，銀河配合跨橋構圖非常壯觀。"\
                 "有停車場與廁所。夜間園區內可能管制，建議事先確認開放時間。",
        "google_maps": "https://maps.app.goo.gl/三仙台",
    },

    # ── 南部・屏東（恆春半島）────────────────────────────────
    {
        "name": "屏東龍磐公園",
        "lat": 21.9736,
        "lon": 120.8614,
        "altitude_m": 190,
        "bortle": 3,
        "best_months": [3, 4, 5, 6, 7, 8, 9],
        "region": "屏東",
        "notes": "台地地形，南側懸崖直面太平洋，視野極開闊。"\
                 "是台灣拍攝銀河核心仰角最高的地點之一（緯度最南）。"\
                 "夜間強風，三腳架需壓重物。停車場免費，無遮蔽需備禦風衣物。",
        "google_maps": "https://maps.app.goo.gl/龍磐公園",
    },
    {
        "name": "屏東鵝鑾鼻燈塔外圍",
        "lat": 21.9019,
        "lon": 120.8511,
        "altitude_m": 18,
        "bortle": 3,
        "best_months": [3, 4, 5, 6, 7, 8, 9],
        "region": "屏東",
        "notes": "台灣本島最南端，銀河核心在此仰角最高，是夏季銀河核心拍攝聖地。"\
                 "燈塔本身夜間有旋轉燈光，建議在燈塔外圍草坪朝南拍攝避開燈光。"\
                 "停車場在燈塔公園入口，步行約 5 分鐘。",
        "google_maps": "https://maps.app.goo.gl/鵝鑾鼻燈塔",
    },
    {
        "name": "屏東九棚大沙漠",
        "lat": 22.0803,
        "lon": 120.8689,
        "altitude_m": 10,
        "bortle": 2,
        "best_months": [3, 4, 5, 6, 7, 8, 9],
        "region": "屏東",
        "notes": "台灣少數 Bortle 2 的平地地點，四周無任何聚落光害。"\
                 "大面積沙丘是獨特前景，銀河映照沙漠景觀震撼。"\
                 "需注意入場時間，部分區域夜間管制。道路偏遠，建議滿油出發。",
        "google_maps": "https://maps.app.goo.gl/九棚大沙漠",
    },
    {
        "name": "屏東滿洲八瑤灣",
        "lat": 22.0194,
        "lon": 120.8833,
        "altitude_m": 5,
        "bortle": 3,
        "best_months": [3, 4, 5, 6, 7, 8, 9],
        "region": "屏東",
        "notes": "弧形海灣，東側太平洋無光害，可拍銀河拱橋映照海面。"\
                 "滿洲鄉人口稀少，整體天空品質極佳。"\
                 "沿海灘停車，退潮時灘地廣闊，構圖選擇多。",
        "google_maps": "https://maps.app.goo.gl/八瑤灣",
    },

    # ── 離島 ──────────────────────────────────────────────────
    {
        "name": "澎湖吉貝嶼沙尾",
        "lat": 23.6397,
        "lon": 119.5789,
        "altitude_m": 5,
        "bortle": 3,
        "best_months": [4, 5, 6, 7, 8],
        "region": "澎湖",
        "notes": "離島光害極少，海天一線視野完整，可拍銀河入海構圖。"\
                 "需搭船前往，建議安排住宿。注意海風強，三腳架需加重。",
        "google_maps": "https://maps.app.goo.gl/吉貝嶼",
    },
    {
        "name": "小琉球（花瓶岩附近）",
        "lat": 22.3467,
        "lon": 120.3736,
        "altitude_m": 10,
        "bortle": 4,
        "best_months": [4, 5, 6, 7, 8],
        "region": "屏東（離島）",
        "notes": "適合結合浮潛旅遊的銀河拍攝。島上無大型光源，"\
                 "東側面向太平洋視野最好。從東港搭船約 30 分鐘。",
        "google_maps": "https://maps.app.goo.gl/小琉球",
    },
]


def get_all_locations():
    """回傳所有熱點"""
    return LOCATIONS


def get_locations_by_region(region: str):
    """依區域篩選地點"""
    return [loc for loc in LOCATIONS if loc["region"] == region]


def get_locations_by_month(month: int):
    """依月份篩選：只回傳該月份適合拍攝的地點"""
    return [loc for loc in LOCATIONS if month in loc["best_months"]]


def get_locations_by_bortle(max_bortle: int):
    """依光害等級篩選：回傳 Bortle 等級 ≤ max_bortle 的地點"""
    return [loc for loc in LOCATIONS if loc["bortle"] <= max_bortle]


def get_locations_for_weekend(month: int, max_bortle: int = 4):
    """
    綜合篩選：適合週末前往的地點
    - 該月份適合拍攝
    - 光害不超過指定等級
    """
    results = [
        loc for loc in LOCATIONS
        if month in loc["best_months"] and loc["bortle"] <= max_bortle
    ]
    # 依 Bortle 排序（越暗越前面）
    results.sort(key=lambda x: x["bortle"])
    return results


# ── 測試：直接執行此檔案可看到資料摘要 ──────────────────────────
if __name__ == "__main__":
    print(f"📍 資料庫共有 {len(LOCATIONS)} 個拍攝地點\n")

    print("=== 依區域分類 ===")
    regions = {}
    for loc in LOCATIONS:
        regions.setdefault(loc["region"], []).append(loc["name"])
    for region, names in regions.items():
        print(f"  {region}：{', '.join(names)}")

    print("\n=== 4月份適合拍攝的地點（Bortle ≤ 3）===")
    april_spots = get_locations_for_weekend(month=4, max_bortle=3)
    for loc in april_spots:
        print(f"  [{loc['bortle']}] {loc['name']} ({loc['region']}) — 海拔 {loc['altitude_m']}m")
