"""Заявки «Contact us» со страницы логина.

Хранятся в master.contact_requests (нет тенанта — пишет аноним). Статусы:
new → in_progress → closed. Админ видит их в admin.roobico.com → Contact
requests, меняет статус и оставляет заметку.

Антибот — та же схема, что у регистрации: honeypot-поле `website` и
`form_ts` (метка загрузки страницы, проставляется JS) + rate limit по IP.
Уведомление на почту — опционально: только если задан CONTACT_NOTIFY_EMAIL,
чтобы dev-окружение с реальными ключами Resend ничего не рассылало.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from html import escape

from bson import ObjectId
from flask import current_app, request

from app.extensions import get_master_db
from app.utils.email_sender import send_email

# Телефон поддержки — показывается на странице логина и в письме-уведомлении.
SUPPORT_PHONE_DISPLAY = "(773) 331-2795"
SUPPORT_PHONE_TEL = "+17733312795"

STATUSES = ("new", "in_progress", "closed")
STATUS_LABELS = {"new": "New", "in_progress": "In progress", "closed": "Closed"}

MIN_FORM_SECONDS = 4
MAX_FORM_SECONDS = 24 * 3600
MAX_MESSAGE_CHARS = 4000

_SPAM_CONTENT_RE = re.compile(r"[<>]|https?://|\bwww\.", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def spam_reason(form) -> str | None:
    """Причина, по которой сабмит похож на бота (None — похоже на человека)."""
    if (form.get("website") or "").strip():
        return "honeypot filled"
    ts_raw = (form.get("form_ts") or "").strip()
    try:
        ts = int(ts_raw)
    except ValueError:
        return f"bad form_ts {ts_raw!r}"
    age = time.time() - ts
    if age < MIN_FORM_SECONDS or age > MAX_FORM_SECONDS:
        return f"form_ts age {age:.1f}s"
    for field in ("name", "company", "phone"):
        value = form.get(field) or ""
        if _SPAM_CONTENT_RE.search(value):
            return f"spam content in {field}: {value[:80]!r}"
    return None


def validate(form) -> tuple[dict | None, list[str]]:
    """Нормализовать поля формы; вернуть (payload, errors)."""
    name = (form.get("name") or "").strip()[:120]
    email = (form.get("email") or "").strip().lower()[:200]
    phone = (form.get("phone") or "").strip()[:40]
    company = (form.get("company") or "").strip()[:160]
    message = (form.get("message") or "").strip()
    topic = (form.get("topic") or "general").strip().lower()[:40]

    errors: list[str] = []
    if len(name) < 2:
        errors.append("Please enter your name.")
    if not _EMAIL_RE.match(email):
        errors.append("Please enter a valid email so we can reply.")
    if len(message) < 10:
        errors.append("Please tell us a bit more — at least 10 characters.")
    if len(message) > MAX_MESSAGE_CHARS:
        errors.append(f"Message is too long (max {MAX_MESSAGE_CHARS} characters).")
    if topic not in ("general", "sales", "support", "demo", "billing"):
        topic = "general"
    if errors:
        return None, errors
    return {
        "name": name, "email": email, "phone": phone, "company": company,
        "message": message, "topic": topic,
    }, []


def _client_ip() -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or ""
    )


def create_request(payload: dict, *, source: str = "login_page") -> ObjectId:
    """Сохранить заявку; вернуть её _id."""
    now = utcnow()
    doc = {
        **payload,
        "status": "new",
        "source": source,
        "admin_note": "",
        "ip": _client_ip(),
        "user_agent": (request.headers.get("User-Agent") or "")[:300],
        "created_at": now,
        "updated_at": now,
    }
    res = get_master_db().contact_requests.insert_one(doc)
    _notify(doc)
    return res.inserted_id


def _notify(doc: dict) -> None:
    """Письмо на CONTACT_NOTIFY_EMAIL (если задан). Ошибка — только в лог."""
    to_addr = (os.environ.get("CONTACT_NOTIFY_EMAIL") or "").strip()
    if not to_addr:
        return
    rows = "".join(
        f"<tr><td style='color:#64748b;padding:4px 12px 4px 0'>{label}</td>"
        f"<td style='padding:4px 0'>{escape(str(doc.get(key) or '—'))}</td></tr>"
        for key, label in (
            ("name", "Name"), ("email", "Email"), ("phone", "Phone"),
            ("company", "Company"), ("topic", "Topic"),
        )
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:1.5rem;">
      <h2 style="color:#0f172a;margin-top:0;">New contact request</h2>
      <table style="font-size:14px;border-collapse:collapse;">{rows}</table>
      <p style="white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;
                border-radius:8px;padding:12px;font-size:14px;">{escape(doc.get('message') or '')}</p>
      <p style="color:#94a3b8;font-size:12px;">Open the admin panel → Contact requests to reply and track it.</p>
    </div>
    """
    try:
        send_email(
            to_address=to_addr,
            subject=f"Roobico contact request from {doc.get('name') or doc.get('email')}",
            html_body=html,
            reply_to=doc.get("email") or None,
            from_name="Roobico",
        )
    except Exception:
        current_app.logger.exception("Contact request notification email failed")


# ── Admin side ───────────────────────────────────────────────────────────


def list_requests(status: str | None = None, q: str = "", limit: int = 200) -> list[dict]:
    query: dict = {}
    if status in STATUSES:
        query["status"] = status
    q = (q or "").strip()
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"name": rx}, {"email": rx}, {"company": rx}, {"phone": rx}, {"message": rx}]
    return list(
        get_master_db().contact_requests.find(query).sort("created_at", -1).limit(limit)
    )


def count_new() -> int:
    return get_master_db().contact_requests.count_documents({"status": "new"})


def set_status(request_id: ObjectId, status: str, note: str | None, admin: dict) -> dict | None:
    """Сменить статус/заметку; вернуть (before, after) документа или None если не найден."""
    if status not in STATUSES:
        raise ValueError("invalid status")
    master = get_master_db()
    before = master.contact_requests.find_one({"_id": request_id})
    if not before:
        return None
    update = {
        "status": status,
        "updated_at": utcnow(),
        "handled_by": (admin or {}).get("email"),
    }
    if note is not None:
        update["admin_note"] = note.strip()[:2000]
    if status == "closed":
        update["closed_at"] = utcnow()
    master.contact_requests.update_one({"_id": request_id}, {"$set": update})
    return before
