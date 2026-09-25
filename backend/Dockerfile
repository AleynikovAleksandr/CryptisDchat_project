# Образ backend: FastAPI (API + WebSocket + Login.html/cryptis.html) и Flask-админка.
# Собирается из корня проекта: docker build -f backend/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY backend/ /app/

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
 && mkdir -p /app/uploads /app/data_warehouses/gunicorn \
 && chown -R app:app /app/uploads /app/data_warehouses

EXPOSE 8000 8001
# в compose пользователь переопределяется на владельца каталогов хоста (HOST_UID/HOST_GID)
USER app
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
