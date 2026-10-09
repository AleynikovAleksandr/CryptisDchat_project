# Образ backend: FastAPI (API + WebSocket + Login.html/cryptis.html) и Flask-админка.
# Собирается из корня проекта: docker build -f backend/Dockerfile .

# --- этап 1: клиент — все скрипты страницы в один минифицированный файл, без source map ---
FROM node:22-alpine AS frontend
WORKDIR /build
# esbuild ставится только здесь, на этапе сборки; версия закреплена, package.json в проекте не нужен
RUN npm install --no-save --no-audit --no-fund esbuild@0.24.2
COPY scripts/build_frontend.mjs scripts/
COPY backend/templates backend/templates
COPY backend/static/js backend/static/js
RUN node scripts/build_frontend.mjs --out /build/out

# --- этап 2: Python ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# зависимости из общего pyproject.toml (README.md и LICENSE нужны setuptools для сборки метаданных)
COPY pyproject.toml README.md LICENSE /tmp/project/
RUN pip install "/tmp/project[backend]" && rm -rf /tmp/project

COPY backend/ /app/
# исходники JS в образ не попадают: страницы подключают только static/dist/<страница>.<хеш>.js
RUN rm -rf /app/static/js
COPY --from=frontend /build/out/static/ /app/static/
COPY --from=frontend /build/out/templates/ /app/templates/

RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
 && mkdir -p /app/uploads /app/data_warehouses/gunicorn \
 && chown -R app:app /app/uploads /app/data_warehouses

EXPOSE 8000 8001
# в compose пользователь переопределяется на владельца каталогов хоста (HOST_UID/HOST_GID)
USER app
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
