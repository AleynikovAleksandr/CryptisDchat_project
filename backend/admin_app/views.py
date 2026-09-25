"""Маршруты админ-панели (Interface_and_API.md, раздел 9). Каждое действие пишется в admin_audit_log."""
from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache, wraps

import redis
from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_, select, update
from werkzeug.security import check_password_hash

from admin_app import db
from admin_app.forms import ActionForm, LoginForm, ReportForm, SearchForm, SuspendForm
from app.models import (
    AdminAuditLog,
    AdminUser,
    ChainBatch,
    ChainEvent,
    Device,
    GroupMembershipEvent,
    Message,
    RefreshToken,
    Report,
    Thread,
    User,
    UserWallet,
    utcnow,
)
from app.services.cache import CHAIN_PENDING_KEY
from app.services.event_bus import channel, encode_event
from app.services.task_queue import make_producer

bp = Blueprint("admin", __name__)

MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)


def settings():
    return current_app.config["CRYPTIS_SETTINGS"]


@lru_cache
def _producer():
    return make_producer(settings())


def enqueue(task: str, *args) -> None:
    sender = current_app.config.get("TASK_SENDER")  # подменяется в тестах
    if sender is not None:
        sender(task, *args)
    else:
        _producer().send_task(task, args=args)


@lru_cache
def _redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


def _redis(db_index: int) -> redis.Redis:
    override = current_app.config.get("REDIS_CLIENT")
    if override is not None:
        return override
    return _redis_client(settings().redis_url(db_index))


def audit(action: str, target_type: str = "", target_id: str = "", **details) -> None:
    db.session.add(AdminAuditLog(
        admin_id=session.get("admin_id"), action=action, target_type=target_type, target_id=str(target_id),
        details=json.dumps(details, default=str), ip_address=(request.remote_addr or "")[:45],
    ))


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin.login", next=request.path))
        admin = db.session.get(AdminUser, session["admin_id"])
        if admin is None or not admin.is_active:
            session.clear()
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- вход
@bp.route("/admin/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        admin = db.session.scalar(select(AdminUser).where(AdminUser.username == form.username.data))
        now = utcnow()
        if admin and admin.locked_until and admin.locked_until > now:
            audit("login.locked", "admin", admin.id)
            db.session.commit()
            flash("Account is temporarily locked. Try again later.", "error")
            return render_template("admin/login.html", form=form), 429
        if admin and admin.is_active and check_password_hash(admin.password_hash, form.password.data):
            session.clear()
            session.permanent = True
            session["admin_id"] = admin.id
            admin.failed_logins = 0
            admin.locked_until = None
            admin.last_login_at = now
            audit("login.success", "admin", admin.id)
            db.session.commit()
            target = request.args.get("next", "")
            return redirect(target if target.startswith("/admin") else url_for("admin.dashboard"))
        if admin:
            admin.failed_logins += 1
            if admin.failed_logins >= MAX_FAILED_LOGINS:
                admin.locked_until = now + LOCKOUT
                admin.failed_logins = 0
        audit("login.failed", "admin", form.username.data or "")
        db.session.commit()
        flash("Invalid username or password.", "error")
    return render_template("admin/login.html", form=form)


@bp.post("/admin/logout")
@login_required
def logout():
    form = ActionForm()
    if form.validate_on_submit():
        audit("logout", "admin", session.get("admin_id"))
        db.session.commit()
        session.clear()
    return redirect(url_for("admin.login"))


# --------------------------------------------------------------------------- панель
def _metrics() -> dict:
    day_ago = utcnow() - timedelta(days=1)
    batches = dict(db.session.execute(select(ChainBatch.status, func.count()).group_by(ChainBatch.status)).all())
    try:
        pending = _redis(settings().redis_db_cache).llen(CHAIN_PENDING_KEY)
    except redis.RedisError:
        pending = -1
    return {
        "users_total": db.session.scalar(select(func.count()).select_from(User)) or 0,
        "users_suspended": db.session.scalar(select(func.count()).select_from(User).where(User.is_suspended)) or 0,
        "messages_24h": db.session.scalar(select(func.count()).select_from(Message)
                                          .where(Message.created_at >= day_ago)) or 0,
        "threads_total": db.session.scalar(select(func.count()).select_from(Thread)) or 0,
        "chain_events_queued": db.session.scalar(select(func.count()).select_from(ChainEvent)
                                                 .where(ChainEvent.status == "queued")) or 0,
        "chain_pending_redis": pending,
        "batches": {k: batches.get(k, 0) for k in ("pending", "submitted", "confirmed", "failed")},
        "membership_failed": db.session.scalar(select(func.count()).select_from(GroupMembershipEvent)
                                               .where(GroupMembershipEvent.chain_status == "failed")) or 0,
        "open_reports": db.session.scalar(select(func.count()).select_from(Report)
                                          .where(Report.status.in_(("open", "in_review")))) or 0,
    }


@bp.get("/admin/")
@login_required
def dashboard():
    recent = db.session.scalars(select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(15)).all()
    return render_template("admin/dashboard.html", m=_metrics(), audit_log=recent, logout_form=ActionForm())


@bp.get("/admin/metrics")
@login_required
def metrics():
    m = _metrics()
    lines = [
        "# TYPE cryptis_users_total gauge", f"cryptis_users_total {m['users_total']}",
        "# TYPE cryptis_users_suspended gauge", f"cryptis_users_suspended {m['users_suspended']}",
        "# TYPE cryptis_messages_24h gauge", f"cryptis_messages_24h {m['messages_24h']}",
        "# TYPE cryptis_threads_total gauge", f"cryptis_threads_total {m['threads_total']}",
        "# TYPE cryptis_chain_events_queued gauge", f"cryptis_chain_events_queued {m['chain_events_queued']}",
        "# TYPE cryptis_chain_pending_redis gauge", f"cryptis_chain_pending_redis {m['chain_pending_redis']}",
        "# TYPE cryptis_chain_batches gauge",
        *[f'cryptis_chain_batches{{status="{k}"}} {v}' for k, v in m["batches"].items()],
        "# TYPE cryptis_membership_failed gauge", f"cryptis_membership_failed {m['membership_failed']}",
        "# TYPE cryptis_open_reports gauge", f"cryptis_open_reports {m['open_reports']}",
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4")


# --------------------------------------------------------------------------- пользователи
@bp.get("/admin/users")
@login_required
def users():
    form = SearchForm(request.args)
    q = (form.q.data or "").strip()
    stmt = select(User).order_by(User.created_at.desc()).limit(100)
    if q:
        like = f"%{q.lstrip('@')}%"
        stmt = (select(User).outerjoin(UserWallet, UserWallet.user_id == User.id)
                .where(or_(User.id == q, User.username.ilike(like), User.display_name.ilike(like),
                           UserWallet.address.ilike(like), UserWallet.friendly_address.ilike(like)))
                .distinct().limit(100))
    rows = db.session.scalars(stmt).all()
    wallets = {w.user_id: w for w in db.session.scalars(
        select(UserWallet).where(UserWallet.user_id.in_([u.id for u in rows]), UserWallet.is_primary))}
    return render_template("admin/users.html", users=rows, wallets=wallets, form=form, logout_form=ActionForm())


@bp.get("/admin/users/<uuid>")
@login_required
def user_detail(uuid: str):
    user = db.session.get(User, uuid)
    if user is None:
        return render_template("admin/not_found.html", logout_form=ActionForm()), 404
    devices = db.session.scalars(select(Device).where(Device.user_id == uuid).order_by(Device.last_seen_at.desc())).all()
    tokens = db.session.scalars(select(RefreshToken).where(RefreshToken.user_id == uuid)
                                .order_by(RefreshToken.created_at.desc()).limit(50)).all()
    wallets = db.session.scalars(select(UserWallet).where(UserWallet.user_id == uuid)).all()
    audit("user.view", "user", uuid)
    db.session.commit()
    return render_template("admin/user_detail.html", user=user, devices=devices, tokens=tokens, wallets=wallets,
                           suspend_form=SuspendForm(), action_form=ActionForm(), logout_form=ActionForm(), now=utcnow())


def _revoke_all(user_id: str) -> None:
    now = utcnow()
    db.session.execute(update(User).where(User.id == user_id).values(sessions_revoked_at=now))
    db.session.execute(update(Device).where(Device.user_id == user_id, Device.revoked_at.is_(None)).values(revoked_at=now))
    db.session.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                       .values(revoked_at=now))


def _publish_revoked(user_id: str) -> None:
    try:
        _redis(settings().redis_db_pubsub).publish(channel(user_id), encode_event("session.revoked", {"device_id": "*"}))
    except redis.RedisError:
        current_app.logger.warning("could not publish session.revoked for %s", user_id)


@bp.post("/admin/users/<uuid>/suspend")
@login_required
def suspend(uuid: str):
    form = SuspendForm()
    user = db.session.get(User, uuid)
    if user is not None and form.validate_on_submit():
        user.is_suspended = True
        user.suspended_reason = form.reason.data
        _revoke_all(uuid)
        audit("user.suspend", "user", uuid, reason=form.reason.data)
        db.session.commit()
        _publish_revoked(uuid)
        flash("Account suspended and all sessions revoked.", "ok")
    return redirect(url_for("admin.user_detail", uuid=uuid))


@bp.post("/admin/users/<uuid>/unsuspend")
@login_required
def unsuspend(uuid: str):
    form = ActionForm()
    user = db.session.get(User, uuid)
    if user is not None and form.validate_on_submit():
        user.is_suspended = False
        user.suspended_reason = None
        audit("user.unsuspend", "user", uuid)
        db.session.commit()
        flash("Account restored.", "ok")
    return redirect(url_for("admin.user_detail", uuid=uuid))


@bp.post("/admin/users/<uuid>/revoke-sessions")
@login_required
def revoke_sessions(uuid: str):
    form = ActionForm()
    if db.session.get(User, uuid) is not None and form.validate_on_submit():
        _revoke_all(uuid)
        audit("user.revoke_sessions", "user", uuid)
        db.session.commit()
        _publish_revoked(uuid)
        flash("All refresh tokens revoked.", "ok")
    return redirect(url_for("admin.user_detail", uuid=uuid))


# --------------------------------------------------------------------------- жалобы
@bp.get("/admin/reports")
@login_required
def reports():
    status = request.args.get("status", "open")
    stmt = select(Report).order_by(Report.created_at.desc()).limit(200)
    if status != "all":
        stmt = stmt.where(Report.status == status)
    return render_template("admin/reports.html", reports=db.session.scalars(stmt).all(), status=status,
                           logout_form=ActionForm())


@bp.route("/admin/reports/<id>", methods=["GET", "POST"])
@login_required
def report_detail(id: str):  # noqa: A002 — имя параметра из маршрута ТЗ
    report = db.session.get(Report, id)
    if report is None:
        return render_template("admin/not_found.html", logout_form=ActionForm()), 404
    form = ReportForm(obj=report)
    if form.validate_on_submit():
        report.status = form.status.data
        report.resolution_note = form.resolution_note.data or ""
        report.handled_by = session.get("admin_id")
        audit("report.update", "report", id, status=report.status)
        db.session.commit()
        flash("Report updated.", "ok")
        return redirect(url_for("admin.report_detail", id=id))
    return render_template("admin/report_detail.html", report=report, form=form, logout_form=ActionForm())


# --------------------------------------------------------------------------- очередь блокчейна
@bp.get("/admin/blockchain-queue")
@login_required
def blockchain_queue():
    batches = db.session.scalars(select(ChainBatch).where(ChainBatch.status != "confirmed")
                                 .order_by(ChainBatch.created_at.desc()).limit(200)).all()
    confirmed = db.session.scalars(select(ChainBatch).where(ChainBatch.status == "confirmed")
                                   .order_by(ChainBatch.confirmed_at.desc()).limit(20)).all()
    membership = db.session.scalars(select(GroupMembershipEvent).where(GroupMembershipEvent.chain_status != "confirmed")
                                    .order_by(GroupMembershipEvent.created_at.desc()).limit(100)).all()
    return render_template("admin/blockchain_queue.html", batches=batches, confirmed=confirmed, membership=membership,
                           m=_metrics(), action_form=ActionForm(), logout_form=ActionForm())


@bp.post("/admin/blockchain-queue/retry/<batch_id>")
@login_required
def retry_batch(batch_id: str):
    form = ActionForm()
    batch = db.session.get(ChainBatch, batch_id)
    if batch is not None and form.validate_on_submit():
        enqueue("blockchain.retry_batch", batch_id)
        audit("chain.retry_batch", "batch", batch_id, previous_status=batch.status)
        db.session.commit()
        flash("Batch re-queued for submission.", "ok")
    return redirect(url_for("admin.blockchain_queue"))


@bp.post("/admin/blockchain-queue/retry-membership/<event_id>")
@login_required
def retry_membership(event_id: str):
    form = ActionForm()
    ev = db.session.get(GroupMembershipEvent, event_id)
    if ev is not None and form.validate_on_submit() and ev.chain_status == "failed":
        ev.chain_status = "pending"
        audit("chain.retry_membership", "membership_event", event_id)
        db.session.commit()
        enqueue("blockchain.anchor_membership", event_id)
        flash("Membership change re-queued.", "ok")
    return redirect(url_for("admin.blockchain_queue"))
