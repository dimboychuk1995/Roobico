"""
Бизнес-журнал событий (Reports → Activity Journal).

Каждое действие пользователя записывается в master_db.audit_journal как
читаемое событие: кто (user_name/role), что (action + entity_type +
entity_label), когда, откуда (web / mobile / portal / system) и что именно
поменялось (changes: field → old → new). Это дополнение к техническому
логу HTTP-запросов (reports/audit/journal.py): если хендлер записал хотя бы
одно событие, сырой запрос в журнал уже не дублируется; если не записал —
журнал всё равно получает строку «технического» события с человеческим
названием эндпоинта (audit_endpoint_labels.py). Так в журнале есть
абсолютно каждое мутирующее действие, а описанные — ещё и с деталями.

Запись никогда не роняет исходный запрос: любые ошибки логируются и
глотаются ЗДЕСЬ намеренно (журнал — побочный эффект, а не бизнес-операция).

Использование в роуте:

    from app.utils.audit_log import log_event, diff_fields, wo_label

    log_event("update", "customer", cid, label=customer_label(before),
              changes=diff_fields(before, update_data, CUSTOMER_FIELDS))

Вне request-контекста (cron, скрипты) передавать tenant_id/shop и user:

    log_event("create", "parts_order", oid, label="PO #12", shop=shop,
              tenant_id=shop["tenant_id"], user={"name": "Email inbox"}, source="system")
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from bson import ObjectId
from flask import current_app, g, has_request_context, request, session

from app.extensions import get_master_db
from app.utils.auth import SESSION_SHOP_ID, SESSION_TENANT_ID, SESSION_USER_ID

# ── справочники для UI (лейблы фильтров и бэджей) ───────────────────────

ACTIONS: dict[str, str] = {
    "create": "Created",
    "update": "Updated",
    "delete": "Deleted",
    "status": "Status changed",
    "payment": "Payment",
    "payment_delete": "Payment removed",
    "send": "Sent",
    "receive": "Received",
    "return": "Returned",
    "activate": "Activated",
    "deactivate": "Deactivated",
    "login": "Signed in",
    "logout": "Signed out",
    "timer": "Timer",
    "done": "Marked done",
    "confirm": "Confirmed",
    "transfer": "Transferred",
    "import": "Imported",
    "export": "Exported",
    "upload": "Uploaded",
    "permissions": "Permissions changed",
    "settings": "Settings changed",
    "request": "Technical request",
    "error": "Failed request",
    "denied": "Access denied",
}

ENTITY_TYPES: dict[str, str] = {
    "work_order": "Work order",
    "estimate": "Estimate",
    "payment": "Payment",
    "authorization": "Authorization",
    "time_log": "Mechanic timer",
    "inspection": "Annual inspection",
    "customer": "Customer",
    "unit": "Unit",
    "vendor": "Vendor",
    "part": "Part",
    "parts_order": "Parts order",
    "core": "Core",
    "stocktake": "Stocktake",
    "calendar_event": "Appointment",
    "attachment": "Attachment",
    "user": "User",
    "role": "Role",
    "location": "Location",
    "preset": "WO preset",
    "labor_rate": "Labor rate",
    "settings": "Settings",
    "integration": "Integration",
    "billing": "Billing",
    "import": "Import / Export",
    "session": "Session",
    "request": "Request",
    "other": "Other",
}

SOURCES: dict[str, str] = {
    "web": "Web",
    "mobile": "Mobile app",
    "portal": "Customer portal",
    "system": "System",
    "email": "Email inbox",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── нормализация значений ───────────────────────────────────────────────

def _compact_record(item: dict) -> str:
    """Контакт/строка-словарь → короткая строка для журнала."""
    name = " ".join(str(item.get(k) or "").strip() for k in ("first_name", "last_name") if item.get(k)).strip()
    name = name or str(item.get("name") or item.get("description") or item.get("part_number") or "").strip()
    extras = [str(item.get(k)).strip() for k in ("email", "phone", "qty", "price", "amount") if item.get(k) not in (None, "")]
    text = name or ""
    if extras:
        text = (text + " " if text else "") + "(" + ", ".join(extras) + ")"
    return text or ", ".join(f"{k}={v}" for k, v in list(item.items())[:4] if v not in (None, ""))


def _plain(value, depth: int = 0):
    """Значение → JSON-дружелюбный вид для хранения в журнале."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, dict) for v in value):
        return "; ".join(_compact_record(v) for v in list(value)[:20])
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, dict):
        if depth > 2:
            return "{…}"
        return {str(k): _plain(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        if depth > 2:
            return "[…]"
        return [_plain(v, depth + 1) for v in list(value)[:40]]
    return str(value)[:500]


def _norm_for_compare(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.replace(microsecond=0, tzinfo=None).isoformat()
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return [_norm_for_compare(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _norm_for_compare(v) for k, v in value.items()}
    return str(value)


def _get_path(doc, path: str):
    cur = doc
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def diff_fields(before: dict | None, after: dict | None, fields: Iterable[str] | dict[str, str] | None = None) -> list[dict]:
    """
    Список изменений [{field, label, old, new}] по полям fields (ключ или
    {ключ: подпись}). Без fields — по всем ключам after. Поддерживает
    точечные пути вида "totals.grand_total". Поля, которых нет в after
    (не трогались этим апдейтом), и неизменившиеся поля опускаются.
    """
    before = before or {}
    after = after or {}
    if fields is None:
        fields = list(after.keys())
    labels = fields if isinstance(fields, dict) else {f: f for f in fields}

    changes = []
    for field, label in labels.items():
        new, present = _get_path(after, field)
        if not present:
            continue
        old, _ = _get_path(before, field)
        if _norm_for_compare(old) == _norm_for_compare(new):
            continue
        changes.append({
            "field": str(field),
            "label": str(label or field).replace("_", " "),
            "old": _plain(old),
            "new": _plain(new),
        })
    return changes


def change(field: str, old, new, label: str | None = None) -> dict:
    """Одно изменение, когда diff_fields не подходит (вычисляемые поля)."""
    return {"field": field, "label": (label or field).replace("_", " "), "old": _plain(old), "new": _plain(new)}


# ── лейблы сущностей ────────────────────────────────────────────────────

def wo_label(wo: dict | None, fallback_id=None) -> str:
    wo = wo or {}
    number = wo.get("wo_number")
    status = str(wo.get("status") or "").strip().lower()
    prefix = "Estimate" if status in ("estimate", "estimated", "quote", "quoted") else "WO"
    if number:
        return f"{prefix} #{number}"
    return f"{prefix} {str(fallback_id or wo.get('_id') or '')[-6:]}".strip()


def customer_label(c: dict | None) -> str:
    c = c or {}
    company = str(c.get("company_name") or "").strip()
    if company:
        return company
    try:
        from app.utils.contacts import get_main_contact_name

        name = get_main_contact_name(c, entity_type="customer")
    except Exception:
        name = ""
    if not name:
        name = f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
    return name or "(customer)"


def unit_label(u: dict | None) -> str:
    u = u or {}
    parts = [str(u[k]) for k in ("unit_number", "year", "make", "model") if u.get(k)]
    if not parts and u.get("vin"):
        parts.append(f"VIN {u['vin']}")
    return " ".join(parts) or "(unit)"


def part_label(p: dict | None) -> str:
    p = p or {}
    number = str(p.get("part_number") or "").strip()
    desc = str(p.get("description") or "").strip()
    if number and desc:
        return f"{number} — {desc[:60]}"
    return number or desc or "(part)"


def parts_order_label(o: dict | None) -> str:
    o = o or {}
    number = o.get("order_number")
    return f"PO #{number}" if number else "Parts order"


def vendor_label(v: dict | None) -> str:
    v = v or {}
    return str(v.get("name") or v.get("company_name") or "").strip() or "(vendor)"


def user_label(u: dict | None) -> str:
    u = u or {}
    name = str(u.get("name") or "").strip() or f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
    return name or str(u.get("email") or "").strip() or "(user)"


def entity_url(entity_type: str, entity_id, details: dict | None = None) -> str:
    """Ссылка на объект из журнала (если у объекта есть страница)."""
    eid = str(entity_id or "")
    details = details or {}
    if not eid:
        return ""
    if entity_type in ("work_order", "estimate"):
        return f"/work_orders/details?work_order_id={eid}"
    if entity_type == "customer":
        return f"/customers/{eid}"
    if entity_type == "unit" and details.get("customer_id"):
        return f"/customers/{details['customer_id']}/units/{eid}"
    if entity_type == "parts_order":
        return f"/parts/?tab=orders&open_order={eid}"
    if entity_type == "part" and details.get("part_number"):
        return f"/parts/?tab=parts&q={details['part_number']}"
    if entity_type == "stocktake":
        return f"/parts/stocktakes/{eid}"
    if entity_type == "calendar_event":
        return "/calendar"
    if entity_type == "user":
        return "/settings/users"
    if entity_type == "role":
        return "/settings/roles"
    if entity_type == "preset":
        return f"/settings/wo_presets/{eid}"
    if entity_type == "location":
        return "/settings/locations"
    return ""


# ── контекст запроса ────────────────────────────────────────────────────

def _request_source() -> str:
    if not has_request_context():
        return "system"
    path = (request.path or "").lower()
    if path.startswith("/api/mobile"):
        return "mobile"
    if path.startswith("/portal") or path.startswith("/authorize"):
        return "portal"
    return "web"


def _actor(user: dict | None = None) -> dict:
    """{user_id, user_name, user_role} — из явного user, g.user или сессии."""
    doc = user
    if doc is None and has_request_context():
        doc = getattr(g, "user", None)
    if doc is None and has_request_context() and session.get(SESSION_USER_ID):
        try:
            doc = get_master_db().users.find_one(
                {"_id": ObjectId(str(session.get(SESSION_USER_ID)))},
                {"name": 1, "first_name": 1, "last_name": 1, "email": 1, "role": 1},
            )
        except Exception:
            doc = None
    doc = doc or {}
    return {
        "user_id": str(doc.get("_id") or doc.get("user_id") or ""),
        "user_name": str(doc.get("user_name") or "").strip() or user_label(doc) if doc else "",
        "user_role": str(doc.get("role") or "").strip().lower(),
    }


def _tenant_and_shop(shop: dict | None, tenant_id) -> tuple[str, str, str]:
    shop = shop or {}
    tid = tenant_id or shop.get("tenant_id")
    if not tid and has_request_context():
        tid = session.get(SESSION_TENANT_ID)
    sid = shop.get("_id")
    if not sid and has_request_context():
        sid = session.get(SESSION_SHOP_ID)
    return str(tid or ""), str(sid or ""), str(shop.get("name") or "")


# ── запись события ──────────────────────────────────────────────────────

def log_event(
    action: str,
    entity_type: str,
    entity_id=None,
    *,
    label: str = "",
    summary: str = "",
    changes: list[dict] | None = None,
    details: dict | None = None,
    shop: dict | None = None,
    tenant_id=None,
    user: dict | None = None,
    source: str | None = None,
    url: str | None = None,
    ok: bool = True,
) -> None:
    """Записать бизнес-событие. Никогда не бросает исключений."""
    try:
        action = str(action or "update").strip().lower()
        entity_type = str(entity_type or "other").strip().lower()
        actor = _actor(user)
        tid, sid, shop_name = _tenant_and_shop(shop, tenant_id)
        details = _plain(details or {})
        label = str(label or "").strip()
        if not summary:
            verb = ACTIONS.get(action, action.replace("_", " ").capitalize())
            noun = ENTITY_TYPES.get(entity_type, entity_type.replace("_", " "))
            summary = f"{verb}: {noun} {label}".strip() if label else f"{verb}: {noun}"

        entry = {
            "kind": "event",
            "created_at": utcnow(),
            "request_id": str(getattr(g, "request_id", "") or "") if has_request_context() else "",
            "tenant_id": tid,
            "shop_id": sid,
            "shop_name": shop_name,
            "user_id": actor["user_id"],
            "user_name": actor["user_name"],
            "user_role": actor["user_role"],
            "source": source or _request_source(),
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id or ""),
            "entity_label": label,
            "entity_url": url if url is not None else entity_url(entity_type, entity_id, details if isinstance(details, dict) else None),
            "summary": str(summary)[:500],
            "changes": [_plain(c) for c in (changes or []) if isinstance(c, dict)][:60],
            "details": details,
            "ok": bool(ok),
        }
        if has_request_context():
            entry["method"] = request.method.upper()
            entry["path"] = request.path
            entry["endpoint"] = str(request.endpoint or "")
            entry["ip"] = str(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:255]
            g._audit_events_written = True

        get_master_db().audit_journal.insert_one(entry)
    except Exception:
        try:
            current_app.logger.exception("audit_log: failed to write event %s/%s", action, entity_type)
        except Exception:
            pass


# ── наборы полей для diff'ов по доменам ─────────────────────────────────

CUSTOMER_FIELDS = {
    "company_name": "Company", "contacts": "Contacts", "address": "Address", "taxable": "Taxable",
    "default_labor_rate": "Default labor rate", "pricing_rule_id": "Pricing matrix",
    "override_part_selling_price": "Override part selling price", "phone": "Phone", "email": "Email",
    "is_active": "Active",
}
UNIT_FIELDS = {
    "unit_number": "Unit #", "vin": "VIN", "make": "Make", "model": "Model", "year": "Year",
    "type": "Type", "mileage": "Mileage", "customer_id": "Customer", "is_active": "Active",
}
VENDOR_FIELDS = {
    "name": "Name", "website": "Website", "address": "Address", "notes": "Notes",
    "contacts": "Contacts", "is_active": "Active",
}
PART_FIELDS = {
    "part_number": "Part number", "description": "Description", "reference": "Reference",
    "vendor_id": "Vendor", "category_id": "Category", "location_id": "Location",
    "do_not_track_inventory": "Not tracked", "average_cost": "Average cost",
    "has_selling_price": "Has selling price", "selling_price": "Selling price",
    "core_has_charge": "Core charge", "core_cost": "Core cost", "misc_has_charge": "Misc charge",
    "misc_charges": "Misc charges", "in_stock": "In stock", "is_active": "Active",
}
PARTS_ORDER_FIELDS = {
    "vendor_id": "Vendor", "order_date": "Order date", "status": "Status", "vendor_bill": "Vendor bill",
    "remaining_balance": "Balance", "payment_status": "Payment status",
}
WO_FIELDS = {
    "status": "Status", "customer_id": "Customer", "unit_id": "Unit", "work_order_date": "Work order date",
    "totals.grand_total": "Grand total", "totals.labor_total": "Labor total", "totals.parts_total": "Parts total",
    "mechanic_done": "Mechanic done", "manager_confirmed": "Manager confirmed",
}


def labor_summary(wo: dict | None, limit: int = 6) -> list[str]:
    """Описания работ WO (для summary/diff)."""
    out: list[str] = []
    for block in (wo or {}).get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else block
        text = str(labor.get("description") or "").strip()
        if text:
            out.append(text[:80])
        if len(out) >= limit:
            break
    return out


def _labor_stats(labors) -> dict:
    hours = 0.0
    parts = 0
    for block in labors or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else block
        try:
            hours += float(labor.get("hours") or 0)
        except (TypeError, ValueError):
            pass
        parts += len([p for p in (block.get("parts") or []) if isinstance(p, dict)])
    return {"jobs": len([b for b in labors or [] if isinstance(b, dict)]), "hours": round(hours, 2), "parts": parts}


def wo_changes(before: dict | None, set_fields: dict | None) -> list[dict]:
    """Изменения WO по сохранению: поля верхнего уровня + состав работ."""
    before = before or {}
    set_fields = set_fields or {}
    changes = diff_fields(before, set_fields, WO_FIELDS)
    if "labors" in set_fields:
        old_jobs = labor_summary(before)
        new_jobs = labor_summary({"labors": set_fields.get("labors")})
        if old_jobs != new_jobs:
            changes.append(change("labors", ", ".join(old_jobs), ", ".join(new_jobs), "jobs"))
        old_stats = _labor_stats(before.get("labors"))
        new_stats = _labor_stats(set_fields.get("labors"))
        for key, label in (("hours", "labor hours"), ("parts", "parts lines")):
            if old_stats[key] != new_stats[key]:
                changes.append(change(key, old_stats[key], new_stats[key], label))
    return changes
