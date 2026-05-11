from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import request, session, jsonify

from app.blueprints.calendar import calendar_bp
from app.blueprints.main.routes import NAV_ITEMS
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.layout import render_internal_page
from app.utils.permissions import filter_nav_items, permission_required


# ── helpers ──────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


def _oid(v):
    if not v:
        return None
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _tenant_variants():
    raw = session.get(SESSION_TENANT_ID)
    if raw is None:
        return []
    out = {raw, str(raw)}
    o = _oid(raw)
    if o:
        out.add(o)
    return list(out)


def _get_shop_db():
    master = get_master_db()
    shop_id = _oid(session.get("shop_id"))
    if not shop_id:
        return None, None
    tv = _tenant_variants()
    if not tv:
        return None, None
    shop = master.shops.find_one({"_id": shop_id, "tenant_id": {"$in": tv}})
    if not shop:
        return None, None
    db_name = shop.get("db_name")
    if not db_name:
        return None, shop
    client = get_mongo_client()
    return client[str(db_name)], shop


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


def _customer_label(c):
    company = (c.get("company_name") or "").strip()
    if company:
        return company
    contacts = c.get("contacts") or []
    for ct in contacts:
        if ct.get("is_main"):
            fn = (ct.get("first_name") or "").strip()
            ln = (ct.get("last_name") or "").strip()
            name = f"{fn} {ln}".strip()
            if name:
                return name
    if contacts:
        fn = (contacts[0].get("first_name") or "").strip()
        ln = (contacts[0].get("last_name") or "").strip()
        return f"{fn} {ln}".strip() or "(no name)"
    return "(no name)"


def _unit_label(u):
    parts = []
    if u.get("unit_number"):
        parts.append(str(u["unit_number"]))
    if u.get("year"):
        parts.append(str(u["year"]))
    if u.get("make"):
        parts.append(str(u["make"]))
    if u.get("model"):
        parts.append(str(u["model"]))
    return " ".join(parts) or "(unit)"


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
        return jsonify({"error": str(e)}), 500


@calendar_bp.get("/calendar/api/statuses")
@login_required
@permission_required("calendar.view")
def api_statuses():
    db, _ = _get_shop_db()
    return jsonify(_get_statuses(db))


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
    db, shop = _get_shop_db()
    if db is None:
        return jsonify([])

    start = request.args.get("start", "")
    end = request.args.get("end", "")
    query = {"shop_id": shop["_id"]}

    if start:
        try:
            query["start_time"] = {"$gte": datetime.fromisoformat(start.replace("Z", "+00:00"))}
        except Exception:
            pass
    if end:
        try:
            query.setdefault("start_time", {})
            query["start_time"]["$lt"] = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except Exception:
            pass

    rows = list(db.calendar_events.find(query).sort("start_time", 1))

    events = []
    for r in rows:
        events.append({
            "id": str(r["_id"]),
            "title": r.get("title") or "",
            "start_time": r["start_time"].isoformat() if r.get("start_time") else "",
            "end_time": r["end_time"].isoformat() if r.get("end_time") else "",
            "status": r.get("status", "scheduled"),
            "customer_id": str(r["customer_id"]) if r.get("customer_id") else "",
            "customer_label": r.get("customer_label") or "",
            "unit_id": str(r["unit_id"]) if r.get("unit_id") else "",
            "unit_label": r.get("unit_label") or "",
            "mechanic_id": str(r["mechanic_id"]) if r.get("mechanic_id") else "",
            "mechanic_name": r.get("mechanic_name") or "",
            "presets": r.get("presets") or [],
        })

    return jsonify(events)


@calendar_bp.post("/calendar/api/events")
@login_required
@permission_required("calendar.create")
def api_create_event():
    db, shop = _get_shop_db()
    if db is None:
        return jsonify({"error": "Shop not configured"}), 400

    data = request.get_json(force=True, silent=True) or {}

    start_raw = (data.get("start_time") or "").strip()
    end_raw = (data.get("end_time") or "").strip()
    if not start_raw or not end_raw:
        return jsonify({"error": "Start and end time are required"}), 400

    try:
        start_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    except Exception:
        return jsonify({"error": "Invalid date format"}), 400

    if end_time <= start_time:
        return jsonify({"error": "End time must be after start time"}), 400

    customer_id_raw = (data.get("customer_id") or "").strip()
    customer_label_val = (data.get("customer_label") or "").strip() or "New Customer"
    unit_id_raw = (data.get("unit_id") or "").strip()
    unit_label_val = (data.get("unit_label") or "").strip() or ""
    mechanic_id_raw = (data.get("mechanic_id") or "").strip()
    mechanic_name_val = (data.get("mechanic_name") or "").strip()
    status = (data.get("status") or "scheduled").strip()
    title = (data.get("title") or "").strip()

    # presets (array of {id, name})
    raw_presets = data.get("presets") or []
    presets = []
    if isinstance(raw_presets, list):
        for rp in raw_presets:
            if not isinstance(rp, dict):
                continue
            pid = (rp.get("id") or "").strip()
            pname = (rp.get("name") or "").strip()
            if pid and pname:
                presets.append({"id": pid, "name": pname})

    valid_statuses = {s["key"] for s in _get_statuses(db)}
    if status not in valid_statuses:
        status = "scheduled"

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
        "shop_id": shop["_id"],
        "tenant_id": _oid(session.get(SESSION_TENANT_ID)),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }

    result = db.calendar_events.insert_one(doc)
    doc["_id"] = result.inserted_id

    return jsonify({
        "id": str(doc["_id"]),
        "title": doc["title"],
        "start_time": doc["start_time"].isoformat(),
        "end_time": doc["end_time"].isoformat(),
        "status": doc["status"],
        "customer_id": str(doc["customer_id"]) if doc["customer_id"] else "",
        "customer_label": doc["customer_label"],
        "unit_id": str(doc["unit_id"]) if doc["unit_id"] else "",
        "unit_label": doc["unit_label"],
        "mechanic_id": str(doc["mechanic_id"]) if doc["mechanic_id"] else "",
        "mechanic_name": doc["mechanic_name"],
        "presets": doc.get("presets") or [],
    }), 201


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
        raw_presets = data["presets"] or []
        presets = []
        if isinstance(raw_presets, list):
            for rp in raw_presets:
                if not isinstance(rp, dict):
                    continue
                pid = (rp.get("id") or "").strip()
                pname = (rp.get("name") or "").strip()
                if pid and pname:
                    presets.append({"id": pid, "name": pname})
        updates["presets"] = presets

    for field in ("start_time", "end_time"):
        if field in data and data[field]:
            try:
                updates[field] = datetime.fromisoformat(data[field].replace("Z", "+00:00"))
            except Exception:
                pass

    valid_statuses = {s["key"] for s in _get_statuses(db)}
    if "status" in updates and updates["status"] not in valid_statuses:
        updates["status"] = existing.get("status", "scheduled")

    db.calendar_events.update_one({"_id": eid}, {"$set": updates})

    updated = db.calendar_events.find_one({"_id": eid})
    return jsonify({
        "id": str(updated["_id"]),
        "title": updated.get("title") or "",
        "start_time": updated["start_time"].isoformat() if updated.get("start_time") else "",
        "end_time": updated["end_time"].isoformat() if updated.get("end_time") else "",
        "status": updated.get("status", "scheduled"),
        "customer_id": str(updated["customer_id"]) if updated.get("customer_id") else "",
        "customer_label": updated.get("customer_label") or "",
        "unit_id": str(updated["unit_id"]) if updated.get("unit_id") else "",
        "unit_label": updated.get("unit_label") or "",
        "mechanic_id": str(updated["mechanic_id"]) if updated.get("mechanic_id") else "",
        "mechanic_name": updated.get("mechanic_name") or "",
        "presets": updated.get("presets") or [],
    })


@calendar_bp.delete("/calendar/api/events/<event_id>")
@login_required
@permission_required("calendar.delete")
def api_delete_event(event_id):
    db, shop = _get_shop_db()
    eid = _oid(event_id)
    if db is None or not eid:
        return jsonify({"error": "Not found"}), 404

    db.calendar_events.delete_one({"_id": eid, "shop_id": shop["_id"]})
    return jsonify({"ok": True})
