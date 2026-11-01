"""Вспомогательные функции, общие для FastAPI и Celery."""
from .logging_setup import setup_logging

__all__ = ["setup_logging"]
