# Образ Celery worker / beat. Код моделей и сервисов общий с backend.
# Собирается из корня проекта: docker build -f worker/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# mariadb-client — для ежедневного дампа базы задачей maintenance.backup_database
RUN apt-get update \
 && apt-get install -y --no-install-recommends mariadb-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /tmp/req/backend/requirements.txt
COPY worker/requirements.txt /tmp/req/worker/requirements.txt
RUN pip install -r /tmp/req/worker/requirements.txt

# /app/app — модели и сервисы, /app/worker — задачи Celery
COPY backend/ /app/
COPY worker/ /app/worker/

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
 && mkdir -p /app/uploads /app/data_warehouses/celery /app/data_warehouses/backups \
 && chown -R app:app /app/uploads /app/data_warehouses

# в compose пользователь переопределяется на владельца каталогов хоста (HOST_UID/HOST_GID)
USER app
CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info"]
