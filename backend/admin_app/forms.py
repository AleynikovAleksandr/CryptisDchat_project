"""Формы админ-панели (Flask-WTF + WTForms): серверная валидация и CSRF-токен в каждой форме."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=256)])
    submit = SubmitField("Sign in")


class ActionForm(FlaskForm):
    """Пустая форма-подтверждение: logout, revoke-sessions, retry — только CSRF-токен."""

    submit = SubmitField("Confirm")


class SuspendForm(FlaskForm):
    reason = StringField("Reason", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Suspend account")


class ReportForm(FlaskForm):
    status = SelectField("Status", choices=[("open", "Open"), ("in_review", "In review"),
                                            ("resolved", "Resolved"), ("rejected", "Rejected")])
    resolution_note = TextAreaField("Resolution note", validators=[Length(max=2000)])
    submit = SubmitField("Save")


class SearchForm(FlaskForm):
    class Meta:
        csrf = False  # GET-поиск ничего не меняет

    q = StringField("Search", validators=[Length(max=100)])
