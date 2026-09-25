"""Gunicorn — сервис Flask (админ-панель и вебхуки), Gunicorn_Celery.md 2.2.

Запуск: gunicorn -c gunicorn_admin.conf.py admin_app.wsgi:app
"""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_ADMIN_BIND", "0.0.0.0:8001")
worker_class = "gthread"   # немного параллелизма на блокирующих запросах к БД без усложнения модели
workers = int(os.environ.get("GUNICORN_ADMIN_WORKERS", min(3, multiprocessing.cpu_count() * 2 + 1)))
threads = 3
timeout = 30
# здесь периодический рестарт безопасен: долгих соединений нет
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
loglevel = "info"
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
