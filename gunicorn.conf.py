# gunicorn.conf.py
# Render 免費方案 RAM 512MB，限制 1 個 worker 避免 OOM SIGKILL
# 星曆表（de421.bsp 16MB）在模組載入時讀取一次，多 worker 會各自佔用記憶體

workers = 1
timeout = 120        # 查詢 14 個地點需要較長時間
worker_class = "sync"
