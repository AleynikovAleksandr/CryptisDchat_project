"""Gunicorn — сервис FastAPI (API + WebSocket), Gunicorn_Celery.md 2.1.

Запуск: gunicorn -c gunicorn.conf.py app.main:app
"""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
# ASGI-приложению нужен асинхронный воркер: обычный sync не умеет async-эндпоинты и WebSocket
worker_class = "uvicorn.workers.UvicornWorker"
# каждый Uvicorn-воркер сам держит тысячи соединений, поэтому меньше классической формулы 2×CPU+1
workers = int(os.environ.get("GUNICORN_WORKERS", min(4, multiprocessing.cpu_count() * 2 + 1)))
worker_connections = 1500
timeout = 30               # запрос дольше — сигнал, что работа должна была уйти в Celery
graceful_timeout = 45      # время WebSocket-клиентам корректно переподключиться при деплое
keepalive = 5
# max_requests НЕ используется: перезапуск воркера оборвал бы все его WebSocket-сессии
preload_app = False        # соединения к БД и Redis открываются в каждом воркере после форка
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

log_dir = os.environ.get("GUNICORN_LOG_DIR", "/app/data_warehouses/gunicorn")
accesslog = os.path.join(log_dir, "access.log")
errorlog = os.path.join(log_dir, "error.log")
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
# токены не пишутся в access-лог: WebSocket авторизуется первым кадром, а не параметром URL
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'
