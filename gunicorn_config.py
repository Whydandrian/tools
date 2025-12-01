# gunicorn_config.py
import multiprocessing

# Server socket
bind = "127.0.0.1:5001"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 300  # 5 menit untuk operasi berat (OCR, compress)
keepalive = 2

# Restart workers after this many requests (mencegah memory leak)
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = "/var/log/tools/gunicorn_access.log"
errorlog = "/var/log/tools/gunicorn_error.log"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "pdf_tools_api"

# Server mechanics
daemon = False
pidfile = "/var/run/gunicorn_pdf_tools.pid"
user = "www-data"
group = "www-data"
tmp_upload_dir = "/tmp"

# SSL (jika diperlukan langsung di Gunicorn, tapi Anda sudah pakai Apache)
# keyfile = None
# certfile = None