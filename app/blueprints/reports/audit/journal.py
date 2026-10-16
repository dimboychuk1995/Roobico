"""
Технический слой журнала: каждый мутирующий HTTP-запрос (POST/PUT/PATCH/
DELETE) попадает в master_db.audit_journal.

Бизнес-события пишет app/utils/audit_log.log_event (с actor'ом, объектом и
изменениями). Если хендлер записал хотя бы одно такое событие и ответил
успешно, сырой запрос не дублируется. Если событий не было — пишется
строка kind="request" с человеческим названием эндпоинта
(audit_endpoint_labels), чтобы в журнале было абсолютно каждое действие.
Ошибки (5xx / исключения) и отказы в доступе (403) пишутся всегда.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from flask import current_app, g, has_request_context, request, session

from app.extensions import get_master_db
from app.utils.audit_endpoint_labels import describe_endpoint
from app.utils.auth import SESSION_SHOP_ID, SESSION_TENANT_ID, SESSION_USER_ID


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_KEYS = {
    "password",
    "pass",
    "passwd",
    "password_confirm",
    "new_password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "authorization",
    "csrf_token",
}
# Эндпоинты, чьи тела не стоит хранить даже в замаскированном виде
# (файлы/бинарные тела, большие AI-пейлоады).
SKIP_PAYLOAD_ENDPOINTS = {
    "attachments.api_upload",
    "work_orders.api_parse_handwritten_work_order",
    "parts.parts_api_orders_parse_invoice",
    "import_export.upload_headers",
    "import_export.run_import",
    "assistant.chat",
    "inbound_email.inbound_email_webhook",
    "billing.stripe_webhook",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_request_id() -> str:
    return uuid4().hex


def _safe_str(value, max_len: int = 1000) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _sanitize_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            lower_key = str(key or "").strip().lower()
            if lower_key in SENSITIVE_KEYS:
                sanitized[key] = "***"
            else:
                sanitized[key] = _sanitize_payload(item)
        return sanitized

    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]

    if isinstance(value, tuple):
        return [_sanitize_payload(item) for item in value]

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return _safe_str(value, 2000)


def _get_request_payload() -> dict:
    payload: dict = {}
    if str(request.endpoint or "") in SKIP_PAYLOAD_ENDPOINTS:
        return payload

    json_data = request.get_json(silent=True)
    if isinstance(json_data, dict):
        payload["json"] = _sanitize_payload(json_data)

    form_data = request.form.to_dict(flat=False)
    if form_data:
        flattened = {k: (v[0] if len(v) == 1 else v) for k, v in form_data.items()}
        payload["form"] = _sanitize_payload(flattened)

    args_data = request.args.to_dict(flat=False)
    if args_data:
        flattened_args = {k: (v[0] if len(v) == 1 else v) for k, v in args_data.items()}
        payload["query"] = _sanitize_payload(flattened_args)

    return payload


def _resolve_actor() -> dict:
    user = getattr(g, "user", None) if has_request_context() else None
    user_id = ""
    tenant_id = ""
    user_name = ""
    user_role = ""

    if user:
        user_id = str(user.get("_id") or "")
        user_name = str(user.get("name") or "").strip() or " ".join(
            p for p in (user.get("first_name"), user.get("last_name")) if p
        ).strip() or str(user.get("email") or "")
        user_role = str(user.get("role") or "").strip().lower()
    if getattr(g, "tenant", None):
        tenant_id = str(g.tenant.get("_id") or "")

    if not user_id:
        user_id = str(session.get(SESSION_USER_ID) or "")
    if not tenant_id:
        tenant_id = str(session.get(SESSION_TENANT_ID) or "")

    return {"user_id": user_id, "user_name": user_name, "user_role": user_role, "tenant_id": tenant_id}


def _resolve_shop_id(payload: dict) -> str:
    shop_id = str(session.get(SESSION_SHOP_ID) or "").strip()
    if shop_id:
        return shop_id

    for source_name in ("json", "form"):
        source = payload.get(source_name)
        if not isinstance(source, dict):
            continue

        for key in ("shop_id", "shopId", "location_id", "locationId"):
            value = source.get(key)
            if value:
                return str(value).strip()

    return ""


def _request_source() -> str:
    path = (request.path or "").lower()
    if path.startswith("/api/mobile"):
        return "mobile"
    if path.startswith("/portal") or path.startswith("/authorize"):
        return "portal"
    if path.startswith("/admin"):
        return "admin"
    return "web"


def should_log_current_request() -> bool:
    if not has_request_context():
        return False

    if request.method.upper() not in MUTATING_METHODS:
        return False

    if request.path.startswith("/static/"):
        return False

    return True


def write_audit_journal(response=None, error: Exception | None = None) -> None:
    if not should_log_current_request():
        return

    if getattr(g, "_audit_journal_written", False):
        return

    status_code = 500
    if response is not None:
        status_code = int(getattr(response, "status_code", 500) or 500)
    ok = error is None and 200 <= status_code < 400

    # Хендлер уже описал действие бизнес-событиями — сырой запрос не нужен.
    if ok and getattr(g, "_audit_events_written", False):
        g._audit_journal_written = True
        return

    payload = _get_request_payload()
    actor = _resolve_actor()
    shop_id = _resolve_shop_id(payload)
    endpoint = str(request.endpoint or "")
    action, entity_type, summary = describe_endpoint(endpoint, request.method, request.path)
    if error is not None or status_code >= 500:
        action = "error"
        summary = f"Failed: {summary}"
    elif status_code in (401, 403):
        action = "denied"
        summary = f"Access denied: {summary}"
    elif status_code >= 400:
        summary = f"Rejected ({status_code}): {summary}"

    entry = {
        "kind": "request",
        "request_id": str(getattr(g, "request_id", "") or build_request_id()),
        "created_at": utcnow(),
        "method": request.method.upper(),
        "path": request.path,
        "endpoint": endpoint,
        "blueprint": str(request.blueprint or ""),
        "status_code": status_code,
        "ok": ok,
        "action": action,
        "entity_type": entity_type,
        "entity_id": "",
        "entity_label": "",
        "entity_url": "",
        "summary": summary,
        "changes": [],
        "details": {},
        "source": _request_source(),
        "ip": _safe_str(request.headers.get("X-Forwarded-For") or request.remote_addr or "", 255),
        "user_agent": _safe_str(request.headers.get("User-Agent") or "", 1024),
        "referrer": _safe_str(request.referrer or "", 1024),
        "user_id": actor["user_id"],
        "user_name": actor["user_name"],
        "user_role": actor["user_role"],
        "tenant_id": actor["tenant_id"],
        "shop_id": shop_id,
        "payload": payload,
        "error": _safe_str(error, 4000) if error else "",
    }

    try:
        master = get_master_db()
        master.audit_journal.insert_one(entry)
        g._audit_journal_written = True
    except Exception:
        try:
            current_app.logger.exception("audit journal: failed to write request entry")
        except Exception:
            pass
