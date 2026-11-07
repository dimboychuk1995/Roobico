"""
HTTP календаря: страница, справочники, настройки, CRUD записей.

Тонкие хендлеры — логика в services (events, statuses, work_orders).
Shop-база только через get_shop_db; каждый запрос по shop-коллекциям
фильтрует shop_id. Ошибки: {"error": "..."} с 400 (валидация) / 404 (нет
или чужое); 403 отдаёт permission_required; 500 на /api/ — JSON глобального
обработчика. Пути и имена полей ответов — контракт мобильного приложения.
"""
from __future__ import annotations

from flask import jsonify, request, session

from app.blueprints.calendar import calendar_bp
from app.blueprints.calendar.services import statuses as calendar_settings
from app.blueprints.calendar.services.events import (
    AUDIT_FIELD_LABELS,
    CUSTOMER_PROJECTION,
    UNIT_PROJECTION,
    CalendarError,
    customer_label,
    display_customer_label,
    event_label,
    load_events,
    naive_iso,
    prepare_event_updates,
    prepare_new_event,
    refresh_labels,
    serialize_event,
    unit_label,
)
from app.blueprints.calendar.services.work_orders import (
    WO_STATES,
    enrich_linked_appointments,
    link_event_to_work_order,
    snapshot_payload,
    unlink_work_order_events,
    work_order_snapshot_for_event,
)
from app.blueprints.main.routes import NAV_ITEMS
from app.blueprints.work_orders.services.lookups import get_assignable_mechanics
from app.extensions import get_master_db
from app.utils import audit_log
from app.utils.auth import SESSION_TENANT_ID, SESSION_USER_ID, login_required
from app.utils.display_datetime import get_active_shop_today_iso
from app.utils.layout import render_internal_page
from app.utils.permissions import filter_nav_items, has_permission, is_mechanic_mode, permission_required
from app.utils.tenant import get_shop_db, oid

# Хуки для других модулей исторически импортировались из routes — оставляем имена.
__all__ = ["link_event_to_work_order", "unlink_work_order_events"]


def _shop():
    return get_shop_db(get_master_db())


def _not_found(message: str = "Not found"):
    return jsonify({"error": message}), 404


def _json_body() -> dict:
    """Тело запроса как dict; не-объект (массив, строка, число, мусор) → {},
    чтобы валидация ответила 400, а не AttributeError."""
    data = request.get_json(force=True, silent=True)
    return data if isinstance(data, dict) else {}


def _present(shop_db, shop, rows: list[dict]) -> list[dict]:
    """Документы → payload API с живыми лейблами и данными WO."""
    refresh_labels(shop_db, shop, rows)
    items = [serialize_event(r) for r in rows]
    enrich_linked_appointments(shop_db, shop, items, rows, is_mechanic_mode())
    return items


# ── страница ─────────────────────────────────────────────────

@calendar_bp.get("/calendar")
@login_required
@permission_required("calendar.view")
def calendar_page():
    return render_internal_page(
        "public/calendar.html",
        filter_nav_items(NAV_ITEMS),
        "calendar",
        calendar_can_create=has_permission("calendar.create"),
        calendar_can_edit=has_permission("calendar.edit"),
        calendar_can_delete=has_permission("calendar.delete"),
        calendar_can_manage_settings=has_permission("calendar.manage_settings"),
        calendar_can_create_wo=not is_mechanic_mode(),
        calendar_today=get_active_shop_today_iso(),
    )


# ── справочники ──────────────────────────────────────────────

@calendar_bp.get("/calendar/api/customers")
@login_required
@permission_required("calendar.view")
def api_customers():
    shop_db, shop = _shop()
    if shop_db is None:
        return jsonify([])
    rows = shop_db.customers.find({"shop_id": shop["_id"], "is_active": True}, CUSTOMER_PROJECTION)
    items = [{"id": str(c["_id"]), "label": customer_label(c)} for c in rows]
    items.sort(key=lambda c: c["label"].lower())
    return jsonify(items)


@calendar_bp.get("/calendar/api/units/<customer_id>")
@login_required
@permission_required("calendar.view")
def api_units(customer_id):
    shop_db, shop = _shop()
    cid = oid(customer_id)
    if shop_db is None or not cid:
        return jsonify([])
    rows = shop_db.units.find(
        {"shop_id": shop["_id"], "customer_id": cid, "is_active": True}, UNIT_PROJECTION
    )
    items = [
        {"id": str(u["_id"]), "label": unit_label(u), "vin": str(u.get("vin") or "").strip()}
        for u in rows
    ]
    items.sort(key=lambda u: u["label"].lower())
    return jsonify(items)


@calendar_bp.get("/calendar/api/mechanics")
@login_required
@permission_required("calendar.view")
def api_mechanics():
    _, shop = _shop()
    return jsonify(get_assignable_mechanics(shop) if shop else [])


@calendar_bp.get("/calendar/api/presets")
@login_required
@permission_required("calendar.view")
def api_presets():
    shop_db, shop = _shop()
    if shop_db is None:
        return jsonify([])
    rows = shop_db.wo_presets.find({"shop_id": shop["_id"], "is_active": True}, {"name": 1})
    items = [{"id": str(r["_id"]), "name": str(r.get("name") or "")} for r in rows]
    items.sort(key=lambda p: p["name"].lower())
    return jsonify(items)


@calendar_bp.get("/calendar/api/statuses")
@login_required
@permission_required("calendar.view")
def api_statuses():
    shop_db, _ = _shop()
    return jsonify(calendar_settings.get_statuses(shop_db))


@calendar_bp.get("/calendar/api/wo_states")
@login_required
@permission_required("calendar.view")
def api_wo_states():
    """Состояния WO на календаре (фиксированные). В механик-режиме paid не
    существует — маскируется как completed."""
    states = [dict(s) for s in WO_STATES]
    if is_mechanic_mode():
        states = [s for s in states if s["key"] != "paid"]
    return jsonify(states)


@calendar_bp.get("/calendar/api/settings")
@login_required
@permission_required("calendar.view")
def api_settings():
    shop_db, _ = _shop()
    return jsonify(calendar_settings.get_settings(shop_db))


@calendar_bp.get("/calendar/api/work_orders/<work_order_id>/snapshot")
@login_required
@permission_required("calendar.view")
def api_work_order_snapshot(work_order_id):
    """Предзаполнение записи для существующего WO (ссылка «Schedule» со страницы WO)."""
    shop_db, shop = _shop()
    snapshot = work_order_snapshot_for_event(shop_db, shop, work_order_id) if shop_db is not None else None
    if not snapshot:
        return _not_found("Work order not found")
    return jsonify(snapshot_payload(snapshot, is_mechanic_mode()))


# ── настройки ────────────────────────────────────────────────

def _save_settings(payload: dict):
    """(settings, None) или (None, error). Пишет событие журнала."""
    shop_db, _ = _shop()
    if shop_db is None:
        return None, "Shop not configured"
    settings, error = calendar_settings.save_settings(shop_db, payload)
    if error:
        return None, error
    audit_log.log_event(
        "settings", "calendar_event", label="Calendar settings", summary="Calendar settings saved",
        details={
            "statuses": [s["key"] for s in settings["statuses"]],
            "start_hour": settings["start_hour"], "end_hour": settings["end_hour"],
        },
    )
    return settings, None


@calendar_bp.put("/calendar/api/settings")
@login_required
@permission_required("calendar.manage_settings")
def api_save_settings():
    settings, error = _save_settings(_json_body())
    if error:
        return jsonify({"error": error}), 400
    return jsonify(settings)


@calendar_bp.put("/calendar/api/statuses")
@login_required
@permission_required("calendar.manage_settings")
def api_save_statuses():
    """Тонкий алиас PUT /settings только для статусов; отдаёт их список."""
    settings, error = _save_settings({"statuses": _json_body().get("statuses")})
    if error:
        return jsonify({"error": error}), 400
    return jsonify(settings["statuses"])


# ── записи ───────────────────────────────────────────────────

@calendar_bp.get("/calendar/api/events")
@login_required
@permission_required("calendar.view")
def api_events():
    """Записи с start_time в [start, end) по возрастанию; mechanic_id=<id> |
    unassigned — фильтр по бригаде. include_work_orders принимается и игнорируется."""
    shop_db, shop = _shop()
    if shop_db is None:
        return jsonify([])
    rows = load_events(
        shop_db, shop, request.args.get("start"), request.args.get("end"), request.args.get("mechanic_id")
    )
    return jsonify(_present(shop_db, shop, rows))


@calendar_bp.post("/calendar/api/events")
@login_required
@permission_required("calendar.create")
def api_create_event():
    shop_db, shop = _shop()
    if shop_db is None:
        return jsonify({"error": "Shop not configured"}), 400
    try:
        doc = prepare_new_event(
            shop_db, shop, _json_body(),
            user_id=session.get(SESSION_USER_ID), tenant_id=session.get(SESSION_TENANT_ID),
        )
    except CalendarError as e:
        return jsonify({"error": e.message}), e.status

    # Статус — что выбрал диспетчер (или scheduled); за WO он следует только
    # по хукам work orders (таймер, Done, completed/paid), не при привязке.
    doc["_id"] = shop_db.calendar_events.insert_one(doc).inserted_id

    label = event_label(doc)
    audit_log.log_event(
        "create", "calendar_event", doc["_id"], label=label,
        summary=f"Appointment created: {label} on {doc['start_time']:%m/%d/%Y %H:%M}"
        + (f" (linked to WO #{doc['wo_number']})" if doc.get("wo_number") else ""),
        details={
            "start": naive_iso(doc["start_time"]), "end": naive_iso(doc["end_time"]),
            "mechanics": ", ".join(m["name"] for m in doc.get("mechanics") or []),
            "customer": display_customer_label(doc), "unit": doc.get("unit_label") or "",
            "work_order_id": str(doc.get("work_order_id") or ""),
        },
    )
    return jsonify(_present(shop_db, shop, [doc])[0]), 201


@calendar_bp.put("/calendar/api/events/<event_id>")
@login_required
@permission_required("calendar.edit")
def api_update_event(event_id):
    shop_db, shop = _shop()
    eid = oid(event_id)
    if shop_db is None or not eid:
        return _not_found()
    existing = shop_db.calendar_events.find_one({"_id": eid, "shop_id": shop["_id"]})
    if not existing:
        return _not_found()

    try:
        updates = prepare_event_updates(shop_db, shop, existing, _json_body(), user_id=session.get(SESSION_USER_ID))
    except CalendarError as e:
        return jsonify({"error": e.message}), e.status

    shop_db.calendar_events.update_one({"_id": eid, "shop_id": shop["_id"]}, {"$set": updates})
    updated = shop_db.calendar_events.find_one({"_id": eid, "shop_id": shop["_id"]}) or {**existing, **updates}

    audit_log.log_event(
        "update", "calendar_event", eid, label=event_label(updated),
        changes=audit_log.diff_fields(existing, updates, AUDIT_FIELD_LABELS),
    )
    return jsonify(_present(shop_db, shop, [updated])[0])


@calendar_bp.delete("/calendar/api/events/<event_id>")
@login_required
@permission_required("calendar.delete")
def api_delete_event(event_id):
    shop_db, shop = _shop()
    eid = oid(event_id)
    if shop_db is None or not eid:
        return jsonify({"ok": True})
    existing = shop_db.calendar_events.find_one_and_delete({"_id": eid, "shop_id": shop["_id"]})
    if existing:
        audit_log.log_event(
            "delete", "calendar_event", eid, label=event_label(existing),
            details={"start": naive_iso(existing.get("start_time")), "wo_number": existing.get("wo_number")},
        )
    return jsonify({"ok": True})
