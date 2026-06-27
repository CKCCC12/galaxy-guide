# gunicorn.conf.py
# Render 免費方案 RAM 512MB：維持單一 worker 程序避免 OOM SIGKILL，
# 星曆表（de421.bsp 16MB）在模組載入時只讀取一次，多 worker 會各自佔用記憶體。
#
# worker_class 改用 gthread（gunicorn 內建，無需額外套件）：
# /recommend 大部分時間在「等外部 API 回應」（I/O 密集），執行緒在等待網路時
# 會釋放 GIL，因此單一程序開多條執行緒即可同時服務多位訪客，記憶體幾乎不變
# （星曆表仍只載入一次）。sync 則一次只能處理一個請求，一人查詢時其他訪客全部排隊。
#
# 執行緒安全：每個請求內部已用 ThreadPoolExecutor(3) 平行查 14 個地點，共用的
# 快取／速率限制狀態本來就被多執行緒存取（皆有鎖保護），故改為多請求並發不會
# 引入新的執行緒安全問題。Open-Meteo 的全域 0.5s 速率限制也會自動節流跨請求的呼叫。
#
# gthread 的另一好處：心跳由主執行緒維持，長查詢較不會被誤判逾時而 SIGKILL。

workers = 1
threads = 4          # 單一程序可同時處理 4 個請求（從 sync 的 1 個提升）
worker_class = "gthread"
timeout = 120        # 查詢 14 個地點需要較長時間
