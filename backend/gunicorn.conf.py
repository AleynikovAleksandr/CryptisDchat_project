"""Gunicorn — сервис FastAPI (API + WebSocket), Gunicorn_Celery.md 2.1.

Запуск: gunicorn -c gunicorn.conf.py app.main:app
"""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

def _cpus() -> int:
    """Ядра, доступные контейнеру (cpuset), а не все ядра хоста."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # macOS
        return multiprocessing.cpu_count()


# ASGI-приложению нужен асинхронный воркер: обычный sync не умеет async-эндпоинты и WebSocket.
# Свой подкласс UvicornWorker — чтобы worker_connections ниже действительно применялся.
worker_class = "app.gunicorn_worker.CryptisUvicornWorker"
# один асинхронный воркер (цикл событий) на ядро: больше процессов на ядро лишь делят тот же CPU,
# а каждый воркер и так держит тысячи соединений. Подпись и проверка сообщений нагружают CPU,
# поэтому воркеров не меньше, чем ядер.
workers = int(os.environ.get("GUNICORN_WORKERS", _cpus()))
# одновременных соединений (HTTP + WebSocket) на воркер; сверх — 503 вместо падения по памяти.
# Должно быть меньше ulimit nofile контейнера (docker-compose.yml, backend → ulimits).
worker_connections = int(os.environ.get("GUNICORN_WORKER_CONNECTIONS", 10000))
# очередь ещё не принятых соединений: всплеск подключений после рестарта не получает отказ
backlog = int(os.environ.get("GUNICORN_BACKLOG", 4096))
timeout = 30               # запрос дольше — сигнал, что работа должна была уйти в Celery
graceful_timeout = 45      # время WebSocket-клиентам корректно переподключиться при деплое
# дольше, чем Caddy держит простаивающее соединение к backend (keepalive 60s в Caddyfile):
# иначе воркер закрывает соединение, которое Caddy как раз переиспользует, — редкие 502
keepalive = 75
# max_requests НЕ используется: перезапуск воркера оборвал бы все его WebSocket-сессии
preload_app = False        # соединения к БД и Redis открываются в каждом воркере после форка
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

log_dir = os.environ.get("GUNICORN_LOG_DIR", "/app/data_warehouses/gunicorn")
accesslog = os.path.join(log_dir, "access.log")
errorlog = os.path.join(log_dir, "error.log")
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
# токены не пишутся в access-лог: WebSocket авторизуется первым кадром, а не параметром URL
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'
