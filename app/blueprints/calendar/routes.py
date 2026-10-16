from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, request, session, jsonify

from app.utils import audit_log
from app.blueprints.calendar import calendar_bp
from app.blueprints.calendar.services.work_orders import (
    WO_STATES,
    conflict_message,
    customer_label as _customer_label,
    enrich_linked_appointments,
    find_mechanic_conflict,
    load_work_order_events,
    mark_superseded_plans,
    parse_range_param,
    unit_label as _unit_label,
    work_order_snapshot_for_event,
    _shop_tz,
)
from app.blueprints.main.routes import NAV_ITEMS
from app.extensions import get_master_db
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.layout import render_internal_page
from app.utils.permissions import filter_nav_items, has_permission, is_mechanic_mode, permission_required


# ── helpers ──────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


from app.utils.tenant import (
    oid as _oid,
    tenant_id_variants as _tenant_variants,
    get_shop_db as _tenant_get_shop_db,
)


def _get_shop_db():
    return _tenant_get_shop_db(get_master_db())


def _get_assignable_mechanics(shop):
    shop_id = shop.get("_id")
    if not shop_id:
        return []
    tv = _tenant_variants()
    if not tv:
        return []
    master = get_master_db()
    rows = list(
        master.users.find(
            {
                "tenant_id": {"$in": tv},
                "is_active": True,
                "role": {"$in": ["senior_mechanic", "mechanic"]},
                "$or": [
                    {"shop_ids": {"$in": [shop_id, str(shop_id)]}},
                    {"shop_id": {"$in": [shop_id, str(shop_id)]}},
                ],
            },
            {"first_name": 1, "last_name": 1, "name": 1, "email": 1, "role": 1},
        ).sort([("first_name", 1), ("last_name", 1)])
    )
    out = []
    for u in rows:
        fn = (u.get("first_name") or "").strip()
        ln = (u.get("last_name") or "").strip()
        full = f"{fn} {ln}".strip() or (u.get("name") or "").strip() or (u.get("email") or "").strip()
        if not full:
            continue
        out.append({"id": str(u["_id"]), "name": full, "role": u.get("role", "")})
    return out


APPOINTMENT_STATUSES = [
    {"key": "scheduled", "label": "Scheduled", "color": "#1a73e8"},
    {"key": "confirmed", "label": "Confirmed", "color": "#0d904f"},
    {"key": "in_progress", "label": "In Progress", "color": "#e8710a"},
    {"key": "completed", "label": "Completed", "color": "#5f6368"},
    {"key": "cancelled", "label": "Cancelled", "color": "#d93025"},
]


def _get_statuses(db):
    """Return statuses from DB settings, or default list."""
    if db is None:
        return list(APPOINTMENT_STATUSES)
    doc = db.calendar_settings.find_one({"key": "statuses"})
    if doc and doc.get("statuses"):
        return doc["statuses"]
    return list(APPOINTMENT_STATUSES)


def _serialize_event(r: dict) -> dict:
    """Запись calendar_events → payload API (без данных WO — их добавляет
    enrich_linked_appointments)."""
    mechanic_id = str(r["mechanic_id"]) if r.get("mechanic_id") else ""
    mechanic_name = r.get("mechanic_name") or ""
    return {
        "id": str(r["_id"]),
        "kind": "appointment",
        "is_virtual": False,
        "editable": True,
        "title": r.get("title") or "",
        "start_time": r["start_time"].isoformat() if r.get("start_time") else "",
        "end_time": r["end_time"].isoformat() if r.get("end_time") else "",
        "status": r.get("status", "scheduled"),
        "customer_id": str(r["customer_id"]) if r.get("customer_id") else "",
        "customer_label": r.get("customer_label") or "",
        "unit_id": str(r["unit_id"]) if r.get("unit_id") else "",
        "unit_label": r.get("unit_label") or "",
        "mechanic_id": mechanic_id,
        "mechanic_name": mechanic_name,
        "mechanics": [{"id": mechanic_id, "name": mechanic_name}] if mechanic_id else [],
        "mechanic_ids": [mechanic_id] if mechanic_id else [],
        "presets": r.get("presets") or [],
        "work_order_id": str(r["work_order_id"]) if r.get("work_order_id") else "",
        "wo_number": r.get("wo_number"),
        "wo_state": "",
        "labor_summary": [],
    }


def _parse_presets(raw_presets) -> list[dict]:
    presets = []
    if isinstance(raw_presets, list):
        for rp in raw_presets:
            if not isinstance(rp, dict):
                continue
            pid = (rp.get("id") or "").strip()
            pname = (rp.get("name") or "").strip()
            if pid and pname:
                presets.append({"id": pid, "name": pname})
    return presets


def _parse_event_datetime(raw: str):
    """ISO без зоны — настенные часы магазина (так хранит календарь)."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_shop_tz()).replace(tzinfo=None)
    return parsed


# ── page ─────────────────────────────────────────────────────

@calendar_bp.get("/calendar")
@login_required
@permission_required("calendar.view")
def calendar_page():
    layout_nav = filter_nav_items(NAV_ITEMS)
    return render_internal_page(
        "public/calendar.html",
        layout_nav,
        "calendar",
        calendar_can_create_wo=not is_mechanic_mode(),
        calendar_can_create=has_permission("calendar.create"),
        calendar_can_edit=has_permission("calendar.edit"),
        calendar_can_delete=has_permission("calendar.delete"),
    )


# ── API: lookup data ─────────────────────────────────────────

@calendar_bp.get("/calendar/api/customers")
@login_required
@permission_required("calendar.view")
def api_customers():
    try:
        db, shop = _get_shop_db()
        if db is None:
            return jsonify([])
        rows = list(
            db.customers.find(
                {"is_active": True},
                {"company_name": 1, "contacts": 1},
            ).sort([("company_name", 1), ("last_name", 1)])
        )
        return jsonify([{"id": str(c["_id"]), "label": _customer_label(c)} for c in rows])
    except Exception as e:
        current_app.logger.exception("Calendar customers lookup failed")
        return jsonify({"error": str(e)}), 500


@calendar_bp.get("/calendar/api/units/<customer_id>")
@login_required
@permission_required("calendar.view")
def api_units(customer_id):
    try:
        db, shop = _get_shop_db()
        cid = _oid(customer_id)
        if db is None or not cid:
            return jsonify([])
        rows = list(
            db.units.find(
                {"customer_id": cid, "is_active": True},
                {"unit_number": 1, "year": 1, "make": 1, "model": 1},
            ).sort([("created_at", -1)])
        )
        return jsonify([{"id": str(u["_id"]), "label": _unit_label(u)} for u in rows])
    except Exception as e:
        current_app.logger.exception("Calendar units lookup failed")
        return jsonify({"error": str(e)}), 500


@calendar_bp.get("/calendar/api/mechanics")
@login_required
@permission_required("calendar.view")
def api_mechanics():
    try:
        _, shop = _get_shop_db()
        if not shop:
            return jsonify([])
        return jsonify(_get_assignable_mechanics(shop))
    except Exception as e:
        current_app.logger.exception("Calendar mechanics lookup failed")
        return jsonify({"error": str(e)}), 500


@calendar_bp.get("/calendar/api/statuses")
@login_required
@permission_required("calendar.view")
def api_statuses():
    db, _ = _get_shop_db()
    return jsonify(_get_statuses(db))


@calendar_bp.get("/calendar/api/wo_states")
@login_required
@permission_required("calendar.view")
def api_wo_states():
    """Статусы WO на календаре (фиксированные, не редактируются). В
    механик-режиме paid не существует — маскируется как completed."""
    states = [dict(s) for s in WO_STATES]
    if is_mechanic_mode():
        states = [s for s in states if s["key"] != "paid"]
    return jsonify(states)


@calendar_bp.get("/calendar/api/presets")
@login_required
@permission_required("calendar.view")
def api_presets():
    db, shop = _get_shop_db()
    if db is None:
        return jsonify([])
    rows = list(
        db.wo_presets.find(
            {"shop_id": shop["_id"], "is_active": True},
            {"name": 1, "description": 1},
        ).sort([("name", 1)])
    )
    return jsonify([{"id": str(r["_id"]), "name": r.get("name") or ""} for r in rows])


@calendar_bp.put("/calendar/api/statuses")
@login_required
@permission_required("calendar.manage_settings")
def api_save_statuses():
    import re
    db, _ = _get_shop_db()
    if db is None:
        return jsonify({"error": "Shop not configured"}), 400

    data = request.get_json(force=True, silent=True) or {}
    items = data.get("statuses")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "At least one status is required"}), 400

    clean = []
    seen_keys = set()
    for s in items:
        key = re.sub(r"[^a-z0-9_]", "_", (s.get("key") or "").strip().lower())
        label = (s.get("label") or "").strip()
        color = (s.get("color") or "#888888").strip()
        if not key or not label:
            continue
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if not re.match(r"^#[0-9a-fA-F]{6}$", color):
            color = "#888888"
        clean.append({"key": key, "label": label, "color": color})

    if not clean:
        return jsonify({"error": "At least one valid status is required"}), 400

    db.calendar_settings.update_one(
        {"key": "statuses"},
        {"$set": {"statuses": clean, "updated_at": _utcnow()}},
        upsert=True,
    )
    return jsonify(clean)


# ── API: events CRUD ─────────────────────────────────────────

@calendar_bp.get("/calendar/api/events")
@login_required
@permission_required("calendar.view")
def api_events():
    """
    События за диапазон [start, end).

    include_work_orders=1 — добавить виртуальные события WO (веб-календарь).
    Без флага — только записи calendar_events (так работает мобильное
    приложение, которому WO-события редактировать нельзя).
    """
    db, shop = _get_shop_db()
    if db is None:
        return jsonify([])

    tz = _shop_tz()
    start_local, _ = parse_range_param(request.args.get("start", ""), tz)
    end_local, _ = parse_range_param(request.args.get("end", ""), tz)
    include_wo = str(request.args.get("include_work_orders") or "").strip().lower() in ("1", "true", "yes")
    mechanic_mode = is_mechanic_mode()

    query = {"shop_id": shop["_id"]}
    if start_local:
        query["start_time"] = {"$gte": start_local}
    if end_local:
        query.setdefault("start_time", {})
        query["start_time"]["$lt"] = end_local

    rows = list(db.calendar_events.find(query).sort("start_time", 1))
    events = [_serialize_event(r) for r in rows]
    enrich_linked_appointments(db, shop, events, rows, mechanic_mode=mechanic_mode)

    if include_wo and start_local and end_local:
        try:
            events.extend(
                load_work_order_events(db, shop, start_local, end_local, mechanic_mode=mechanic_mode)
            )
            # Факты важнее плана: записи, пересёкшиеся с реальной работой по
            # тому же WO, помечаются, фронт убирает их с сетки.
            mark_superseded_plans(events)
        except Exception:
            current_app.logger.exception("Calendar: failed to build work order events")

    events.sort(key=lambda e: e.get("start_time") or "")
    return jsonify(events)


@calendar_bp.post("/calendar/api/events")
@login_required
@permission_required("calendar.create")
def api_create_event():
    db, shop = _get_shop_db()
    if db is None:
        return jsonify({"error": "Shop not configured"}), 400

    data = request.get_json(force=True, silent=True) or {}

    start_time = _parse_event_datetime(data.get("start_time"))
    end_time = _parse_event_datetime(data.get("end_time"))
    if not (data.get("start_time") or "").strip() or not (data.get("end_time") or "").strip():
        return jsonify({"error": "Start and end time are required"}), 400
    if start_time is None or end_time is None:
        return jsonify({"error": "Invalid date format"}), 400
    if end_time <= start_time:
        return jsonify({"error": "End time must be after start time"}), 400

    customer_id_raw = (data.get("customer_id") or "").strip()
    customer_label_val = (data.get("customer_label") or "").strip()
    unit_id_raw = (data.get("unit_id") or "").strip()
    unit_label_val = (data.get("unit_label") or "").strip() or ""
    mechanic_id_raw = (data.get("mechanic_id") or "").strip()
    mechanic_name_val = (data.get("mechanic_name") or "").strip()
    status = (data.get("status") or "scheduled").strip()
    title = (data.get("title") or "").strip()
    presets = _parse_presets(data.get("presets") or [])

    # Привязка к существующему WO (перетащили WO на сетку / «Schedule»):
    # клиент, юнит, механик и описание берём с WO, если не переданы.
    work_order_id = None
    wo_number = None
    wo_snapshot = None
    if (data.get("work_order_id") or "").strip():
        wo_snapshot = work_order_snapshot_for_event(db, shop, data.get("work_order_id"))
        if not wo_snapshot:
            return jsonify({"error": "Work order not found"}), 404
        work_order_id = wo_snapshot["work_order_id"]
        wo_number = wo_snapshot["wo_number"]
        if not customer_id_raw and wo_snapshot.get("customer_id"):
            customer_id_raw = str(wo_snapshot["customer_id"])
            customer_label_val = wo_snapshot.get("customer_label") or customer_label_val
        if not unit_id_raw and wo_snapshot.get("unit_id"):
            unit_id_raw = str(wo_snapshot["unit_id"])
            unit_label_val = wo_snapshot.get("unit_label") or unit_label_val
        # keep_unassigned — бросили в колонку «Unassigned»: механика с WO не брать.
        if not mechanic_id_raw and wo_snapshot.get("mechanic") and not data.get("keep_unassigned"):
            mechanic_id_raw = wo_snapshot["mechanic"]["id"]
            mechanic_name_val = wo_snapshot["mechanic"]["name"]
        if not title:
            title = wo_snapshot.get("title") or ""

    if not customer_label_val:
        customer_label_val = "New Customer"

    valid_statuses = {s["key"] for s in _get_statuses(db)}
    if status not in valid_statuses:
        status = "scheduled"

    # Один механик — одна работа в один момент времени.
    if mechanic_id_raw and status != "cancelled":
        conflict = find_mechanic_conflict(db, shop, mechanic_id_raw, start_time, end_time, exclude_wo_id=work_order_id)
        if conflict:
            return jsonify({"error": conflict_message(conflict, mechanic_name_val), "conflict_id": str(conflict.get("_id") or "")}), 409

    user_id = _oid(session.get(SESSION_USER_ID))
    now = _utcnow()

    doc = {
        "title": title or customer_label_val,
        "start_time": start_time,
        "end_time": end_time,
        "status": status,
        "customer_id": _oid(customer_id_raw),
        "customer_label": customer_label_val,
        "unit_id": _oid(unit_id_raw),
        "unit_label": unit_label_val,
        "mechanic_id": _oid(mechanic_id_raw),
        "mechanic_name": mechanic_name_val,
        "presets": presets,
        "work_order_id": work_order_id,
        "wo_number": wo_number,
        "shop_id": shop["_id"],
        "tenant_id": _oid(session.get(SESSION_TENANT_ID)),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }

    result = db.calendar_events.insert_one(doc)
    doc["_id"] = result.inserted_id
    audit_log.log_event("create", "calendar_event", doc["_id"], label=doc.get("title") or customer_label_val,
                        summary=f"Appointment created: {doc.get('title') or customer_label_val} on {start_time:%m/%d/%Y %H:%M}"
                        + (f" (linked to WO #{wo_number})" if wo_number else ""),
                        details={"start": start_time.isoformat(), "end": end_time.isoformat(), "mechanic": mechanic_name_val,
                                 "unit": unit_label_val, "work_order_id": str(work_order_id or "")})

    payload = _serialize_event(doc)
    enrich_linked_appointments(db, shop, [payload], [doc], mechanic_mode=is_mechanic_mode())
    return jsonify(payload), 201


@calendar_bp.put("/calendar/api/events/<event_id>")
@login_required
@permission_required("calendar.edit")
def api_update_event(event_id):
    db, shop = _get_shop_db()
    eid = _oid(event_id)
    if db is None or not eid:
        return jsonify({"error": "Not found"}), 404

    existing = db.calendar_events.find_one({"_id": eid, "shop_id": shop["_id"]})
    if not existing:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    updates = {"updated_at": _utcnow(), "updated_by": _oid(session.get(SESSION_USER_ID))}

    for field in ("title", "status", "customer_label", "unit_label", "mechanic_name"):
        if field in data:
            updates[field] = (data[field] or "").strip()

    for field in ("customer_id", "unit_id", "mechanic_id"):
        if field in data:
            updates[field] = _oid(data[field])

    if "presets" in data:
        updates["presets"] = _parse_presets(data["presets"] or [])

    for field in ("start_time", "end_time"):
        if field in data and data[field]:
            parsed = _parse_event_datetime(data[field])
            if parsed is not None:
                updates[field] = parsed

    new_start = updates.get("start_time", existing.get("start_time"))
    new_end = updates.get("end_time", existing.get("end_time"))
    if new_start and new_end and new_end <= new_start:
        return jsonify({"error": "End time must be after start time"}), 400

    # Привязка / отвязка WO: "" — отвязать.
    if "work_order_id" in data:
        raw_wo = (data.get("work_order_id") or "").strip()
        if not raw_wo:
            updates["work_order_id"] = None
            updates["wo_number"] = None
        else:
            snapshot = work_order_snapshot_for_event(db, shop, raw_wo)
            if not snapshot:
                return jsonify({"error": "Work order not found"}), 404
            updates["work_order_id"] = snapshot["work_order_id"]
            updates["wo_number"] = snapshot["wo_number"]
            if not (updates.get("customer_id") or existing.get("customer_id")) and snapshot.get("customer_id"):
                updates["customer_id"] = snapshot["customer_id"]
                updates["customer_label"] = snapshot.get("customer_label") or ""
            if not (updates.get("unit_id") or existing.get("unit_id")) and snapshot.get("unit_id"):
                updates["unit_id"] = snapshot["unit_id"]
                updates["unit_label"] = snapshot.get("unit_label") or ""

    valid_statuses = {s["key"] for s in _get_statuses(db)}
    if "status" in updates and updates["status"] not in valid_statuses:
        updates["status"] = existing.get("status", "scheduled")

    # Один механик — одна работа в один момент времени. Проверяем только
    # когда меняется сама бронь (время, механик, WO) или запись возвращается
    # из cancelled: правка заголовка/статуса старой записи с конфликтом
    # (помечена «!» на сетке) должна проходить.
    new_mechanic = updates.get("mechanic_id", existing.get("mechanic_id"))
    new_status = updates.get("status", existing.get("status"))
    new_wo = updates.get("work_order_id", existing.get("work_order_id"))
    booking_changed = (
        new_mechanic != existing.get("mechanic_id")
        or new_start != existing.get("start_time")
        or new_end != existing.get("end_time")
        or new_wo != existing.get("work_order_id")
        or (existing.get("status") == "cancelled" and new_status != "cancelled")
    )
    if booking_changed and new_mechanic and new_status != "cancelled" and new_start and new_end:
        conflict = find_mechanic_conflict(db, shop, new_mechanic, new_start, new_end, exclude_id=eid, exclude_wo_id=new_wo)
        if conflict:
            who = updates.get("mechanic_name") or existing.get("mechanic_name") or ""
            return jsonify({"error": conflict_message(conflict, who), "conflict_id": str(conflict.get("_id") or "")}), 409

    db.calendar_events.update_one({"_id": eid}, {"$set": updates})

    updated = db.calendar_events.find_one({"_id": eid})
    audit_log.log_event("update", "calendar_event", eid, label=updated.get("title") or updated.get("customer_label") or "Appointment",
                        changes=audit_log.diff_fields(existing, updates, {"start_time": "Start", "end_time": "End", "status": "Status",
                                                                          "mechanic_name": "Mechanic", "customer_label": "Customer",
                                                                          "unit_label": "Unit", "title": "Title", "wo_number": "Work order"}))
    payload = _serialize_event(updated)
    enrich_linked_appointments(db, shop, [payload], [updated], mechanic_mode=is_mechanic_mode())
    return jsonify(payload)


@calendar_bp.delete("/calendar/api/events/<event_id>")
@login_required
@permission_required("calendar.delete")
def api_delete_event(event_id):
    db, shop = _get_shop_db()
    eid = _oid(event_id)
    if db is None or not eid:
        return jsonify({"error": "Not found"}), 404

    _existing = db.calendar_events.find_one({"_id": eid, "shop_id": shop["_id"]}, {"title": 1, "customer_label": 1, "start_time": 1, "wo_number": 1})
    if _existing:
        audit_log.log_event("delete", "calendar_event", eid, label=_existing.get("title") or _existing.get("customer_label") or "Appointment",
                            details={"start": str(_existing.get("start_time") or ""), "wo_number": _existing.get("wo_number")})
    db.calendar_events.delete_one({"_id": eid, "shop_id": shop["_id"]})
    return jsonify({"ok": True})


# ── links from work orders ───────────────────────────────────

def link_event_to_work_order(shop_db, shop: dict, event_id, work_order_id, wo_number) -> bool:
    """
    Привязать запись календаря к только что созданному WO (кнопка «Create
    Work Order» в календаре передаёт calendar_event_id на страницу WO).
    Возвращает True, если запись найдена и обновлена.
    """
    eid = _oid(event_id)
    wo_id = _oid(work_order_id)
    if shop_db is None or not eid or not wo_id:
        return False
    res = shop_db.calendar_events.update_one(
        {"_id": eid, "shop_id": shop["_id"]},
        {"$set": {"work_order_id": wo_id, "wo_number": wo_number, "updated_at": _utcnow()}},
    )
    return res.modified_count > 0


def unlink_work_order_events(shop_db, shop: dict, work_order_id) -> int:
    """WO удалён — записи календаря остаются, но больше на него не ссылаются."""
    wo_id = _oid(work_order_id)
    if shop_db is None or not wo_id:
        return 0
    res = shop_db.calendar_events.update_many(
        {"shop_id": shop["_id"], "work_order_id": wo_id},
        {"$set": {"work_order_id": None, "updated_at": _utcnow()}},
    )
    return res.modified_count
