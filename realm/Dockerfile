# Realm — изолированный сервис хранения доли секрета (ТЗ 6.6). Три экземпляра: realm1..realm3.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 REALM_DATA_DIR=/data
WORKDIR /srv

COPY realm/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY realm/app.py realm/ecies.py ./
RUN useradd --uid 10001 --create-home realm && mkdir -p /data && chown realm /data
USER realm

EXPOSE 9000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
