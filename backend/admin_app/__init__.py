"""Flask-приложение: административная панель, служебные вебхуки, мониторинг (ТЗ 3, 7).

Доступ к БД — Flask-SQLAlchemy поверх тех же ORM-моделей (backend/app/models),
формы — Flask-WTF/WTForms со встроенной CSRF-защитой.
Запуск: gunicorn -c gunicorn_admin.conf.py admin_app.wsgi:app
"""
from __future__ import annotations

import ipaddress
from datetime import timedelta

from flask import Flask, abort, request
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

from app.config import Settings, get_settings
from app.models import Base

db = SQLAlchemy(model_class=Base)
csrf = CSRFProtect()


def create_app(settings: Settings | None = None, **overrides) -> Flask:
    s = settings or get_settings()
    s.check_production_secrets()
    app = Flask(__name__, template_folder="templates")
    app.config.update(
        SECRET_KEY=s.admin_secret_key,
        SQLALCHEMY_DATABASE_URI=s.sync_db_url,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=not s.is_dev,
        SESSION_COOKIE_NAME="cryptis_admin",
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        WTF_CSRF_TIME_LIMIT=1800,
        CRYPTIS_SETTINGS=s,
    )
    app.config.update(overrides)
    db.init_app(app)
    csrf.init_app(app)

    allowed = [ipaddress.ip_network(n.strip()) for n in s.admin_allowed_ips.split(",") if n.strip()]

    @app.before_request
    def ip_allowlist() -> None:
        # отдельный контроль доступа к панели (ТЗ 7): вебхуки и healthz — без ограничения по IP
        if not allowed or not request.path.startswith("/admin"):
            return
        remote = ipaddress.ip_address(request.remote_addr or "0.0.0.0")
        if not any(remote in net for net in allowed):
            abort(403)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")
        if request.path.startswith("/admin"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    from admin_app import commands, views, webhooks

    app.register_blueprint(views.bp)
    app.register_blueprint(webhooks.bp)
    csrf.exempt(webhooks.bp)  # вебхуки аутентифицируются HMAC-подписью, а не CSRF-токеном
    commands.register(app)
    return app
