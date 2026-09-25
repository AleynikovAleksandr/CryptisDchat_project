"""CLI Flask: `flask --app admin_app.wsgi create-admin <username>`."""
from __future__ import annotations

import getpass

import click
from flask import Flask
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from admin_app import db
from app.models import AdminUser


def upsert_admin(username: str, password: str) -> AdminUser:
    if len(password) < 12:
        raise ValueError("admin password must be at least 12 characters")
    admin = db.session.scalar(select(AdminUser).where(AdminUser.username == username))
    if admin is None:
        admin = AdminUser(username=username, password_hash="")
        db.session.add(admin)
    admin.password_hash = generate_password_hash(password)
    admin.is_active = True
    db.session.commit()
    return admin


def register(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.argument("username")
    def create_admin(username: str) -> None:
        password = getpass.getpass("Password (min 12 chars): ")
        upsert_admin(username, password)
        click.echo(f"admin '{username}' saved")
