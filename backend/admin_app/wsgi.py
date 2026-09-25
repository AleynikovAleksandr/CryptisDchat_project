"""WSGI-точка входа Flask-админки: gunicorn -c gunicorn_admin.conf.py admin_app.wsgi:app"""
from admin_app import create_app

app = create_app()
