"""CLI Flask: `flask --app admin_app.wsgi create-admin <username>`, `flask --app admin_app.wsgi hash-password`."""
from __future__ import annotations

import getpass

import click
from flask import Flask
from sqlalchemy import select, update
from werkzeug.security import generate_password_hash

from admin_app import db
from app.models import AdminUser

HASH_METHODS = ("scrypt:", "pbkdf2:")


def is_password_hash(value: str) -> bool:
    """Строка в формате werkzeug: метод:параметры$соль$хеш."""
    return value.startswith(HASH_METHODS) and value.count("$") == 2


def upsert_admin(username: str, password: str | None = None, *, password_hash: str | None = None) -> AdminUser:
    """Создаёт администратора или меняет ему пароль: открытым текстом (12+ символов) или готовым хешем."""
    if password_hash is not None:
        if not is_password_hash(password_hash):
            raise ValueError("admin password hash must be a werkzeug scrypt/pbkdf2 hash")
        new_hash = password_hash
    else:
        if password is None or len(password) < 12:
            raise ValueError("admin password must be at least 12 characters")
        new_hash = generate_password_hash(password)
    admin = db.session.scalar(select(AdminUser).where(AdminUser.username == username))
    if admin is None:
        admin = AdminUser(username=username, password_hash="")
        db.session.add(admin)
    admin.password_hash = new_hash
    admin.is_active = True
    db.session.commit()
    return admin


def deactivate_other_admins(username: str) -> int:
    """Отключает всех администраторов, кроме указанного: после смены логина старый вход не должен работать."""
    result = db.session.execute(
        update(AdminUser).where(AdminUser.username != username, AdminUser.is_active.is_(True)).values(is_active=False)
    )
    db.session.commit()
    return result.rowcount


def register(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.argument("username")
    def create_admin(username: str) -> None:
        password = getpass.getpass("Password (min 12 chars): ")
        upsert_admin(username, password)
        click.echo(f"admin '{username}' saved")

    @app.cli.command("hash-password")
    def hash_password() -> None:
        """Печатает хеш пароля для ADMIN_PASSWORD_HASH в .env (сам пароль нигде не сохраняется)."""
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            raise click.ClickException("passwords do not match")
        click.echo(generate_password_hash(password))
