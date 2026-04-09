from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import render_template, redirect, url_for, flash, session, request, jsonify
from bson import ObjectId

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_USER_ID, SESSION_TENANT_ID, SESSION_SHOP_ID
from app.utils.permissions import permission_required, filter_nav_items
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context


# ───────────────────────── helpers ──────────────────────────

def _maybe_oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _utcnow():
    return datetime.now(timezone.utc)


def _load_current_user(master):
    uid = _maybe_oid(session.get(SESSION_USER_ID))
    if not uid:
        return None
    return master.users.find_one({"_id": uid, "is_active": True})


def _load_current_tenant(master):
    tid = _maybe_oid(session.get(SESSION_TENANT_ID))
    if not tid:
        return None
    return master.tenants.find_one({"_id": tid, "status": "active"})


def _get_shop_db(master):
    client = get_mongo_client()
    shop_id = _maybe_oid(session.get(SESSION_SHOP_ID))
    if not shop_id:
        return None, None
    shop = master.shops.find_one({"_id": shop_id})
    if not shop:
        return None, None
    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None, None
    return client[str(db_name)], shop_id


def _render_page(template: str, **ctx):
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")
    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))
    layout.update(ctx)
    return render_template(template, **layout)


def _load_labor_rates(sdb, shop_oid):
    rows = list(
        sdb.labor_rates.find(
            {"shop_id": shop_oid, "is_active": True},
            {"code": 1, "name": 1, "hourly_rate": 1},
        ).sort([("name", 1)])
    )
    out = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        out.append({
            "id": str(r["_id"]),
            "code": code,
            "name": str(r.get("name") or code).strip() or code,
            "hourly_rate": float(r.get("hourly_rate") or 0),
        })
    return out


def _load_pricing_rules(sdb, shop_oid):
    doc = sdb.parts_pricing_rules.find_one({"shop_id": shop_oid, "is_active": True})
    if not doc:
        return None
    mode = (doc.get("mode") or "margin").strip().lower()
    rules = []
    for r in (doc.get("rules") or []):
        frm = r.get("from")
        to = r.get("to")
        vp = r.get("value_percent")
        try:
            frm_f = float(frm)
        except Exception:
            continue
        to_f = None
        if to is not None:
            try:
                to_f = float(to)
            except Exception:
                pass
        try:
            vp_f = float(vp)
        except Exception:
            continue
        rules.append({"from": frm_f, "to": to_f, "value_percent": vp_f})
    return {"mode": mode, "rules": rules}


def _f64(v):
    try:
        return float(v)
    except Exception:
        return None


def _parse_preset_form(form):
    """Parse the preset form and return (data_dict, error_string)."""
    name = str(form.get("name") or "").strip()
    if not name:
        return None, "Preset name is required."

    description = str(form.get("description") or "").strip()

    # labor
    labor_hours_raw = str(form.get("labor_hours") or "").strip()
    labor_hours = _f64(labor_hours_raw) if labor_hours_raw else None
    if labor_hours is not None and labor_hours < 0:
        return None, "Labor hours cannot be negative."

    labor_rate_code = str(form.get("labor_rate_code") or "").strip() or None

    # discount override
    allow_discount = bool(form.get("allow_discount"))

    # pricing mode
    fixed_labor_price = _f64(form.get("fixed_labor_price"))
    fixed_parts_total = _f64(form.get("fixed_parts_total"))
    fixed_total_price = _f64(form.get("fixed_total_price"))

    if fixed_labor_price is not None and fixed_labor_price < 0:
        return None, "Fixed labor price cannot be negative."
    if fixed_parts_total is not None and fixed_parts_total < 0:
        return None, "Fixed parts total cannot be negative."
    if fixed_total_price is not None and fixed_total_price < 0:
        return None, "Fixed total price cannot be negative."

    # parts
    parts = []
    parts_json = str(form.get("parts_json") or "").strip()
    if parts_json:
        try:
            parts_list = json.loads(parts_json)
        except Exception:
            return None, "Invalid parts data."
        if not isinstance(parts_list, list):
            return None, "Parts must be a list."
        for p in parts_list:
            if not isinstance(p, dict):
                continue
            part_id = str(p.get("part_id") or "").strip() or None
            part_number = str(p.get("part_number") or "").strip()
            desc = str(p.get("description") or "").strip()
            qty = _f64(p.get("qty"))
            if qty is None or qty <= 0:
                qty = 1
            cost = _f64(p.get("cost"))
            price = _f64(p.get("price"))

            parts.append({
                "part_id": part_id,
                "part_number": part_number,
                "description": desc,
                "qty": qty,
                "cost": cost,
                "price": price,
            })

    data = {
        "name": name,
        "description": description,
        "labor_hours": labor_hours,
        "labor_rate_code": labor_rate_code,
        "allow_discount": allow_discount,
        "fixed_labor_price": fixed_labor_price,
        "fixed_parts_total": fixed_parts_total,
        "fixed_total_price": fixed_total_price,
        "parts": parts,
    }
    return data, None


# ───────────────────────── routes ───────────────────────────

@settings_bp.route("/wo_presets", methods=["GET"])
@login_required
@permission_required("settings.manage_org")
def wo_presets_index():
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db(master)
    if sdb is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    presets = list(
        sdb.wo_presets.find(
            {"shop_id": shop_oid, "is_active": True},
        ).sort([("name", 1)])
    )
    for p in presets:
        p["id"] = str(p["_id"])

    labor_rates = _load_labor_rates(sdb, shop_oid)
    pricing_rules = _load_pricing_rules(sdb, shop_oid)

    return _render_page(
        "public/settings/wo_presets.html",
        presets=presets,
        labor_rates=labor_rates,
        pricing_rules=pricing_rules,
    )


@settings_bp.route("/wo_presets/create", methods=["POST"])
@login_required
@permission_required("settings.manage_org")
def wo_presets_create():
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db(master)
    if sdb is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    data, err = _parse_preset_form(request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("settings.wo_presets_index"))

    now = _utcnow()
    doc = {
        "shop_id": shop_oid,
        **data,
        "is_active": True,
        "created_at": now,
        "created_by": user["_id"],
        "updated_at": now,
        "updated_by": user["_id"],
    }
    sdb.wo_presets.insert_one(doc)

    flash(f"Preset \"{data['name']}\" created.", "success")
    return redirect(url_for("settings.wo_presets_index"))


@settings_bp.route("/wo_presets/<preset_id>", methods=["GET"])
@login_required
@permission_required("settings.manage_org")
def wo_presets_detail(preset_id: str):
    """Return preset JSON for the edit modal."""
    master = get_master_db()
    user = _load_current_user(master)
    if not user:
        return jsonify({"error": "auth"}), 401

    sdb, shop_oid = _get_shop_db(master)
    if sdb is None:
        return jsonify({"error": "no_shop"}), 400

    oid = _maybe_oid(preset_id)
    if not oid:
        return jsonify({"error": "bad_id"}), 400

    doc = sdb.wo_presets.find_one({"_id": oid, "shop_id": shop_oid, "is_active": True})
    if not doc:
        return jsonify({"error": "not_found"}), 404

    return jsonify({
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "labor_hours": doc.get("labor_hours"),
        "labor_rate_code": doc.get("labor_rate_code"),
        "fixed_labor_price": doc.get("fixed_labor_price"),
        "fixed_parts_total": doc.get("fixed_parts_total"),
        "fixed_total_price": doc.get("fixed_total_price"),
        "allow_discount": bool(doc.get("allow_discount")),
        "parts": doc.get("parts") or [],
    })


@settings_bp.route("/wo_presets/<preset_id>/update", methods=["POST"])
@login_required
@permission_required("settings.manage_org")
def wo_presets_update(preset_id: str):
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db(master)
    if sdb is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    oid = _maybe_oid(preset_id)
    if not oid:
        flash("Invalid preset id.", "error")
        return redirect(url_for("settings.wo_presets_index"))

    existing = sdb.wo_presets.find_one(
        {"_id": oid, "shop_id": shop_oid, "is_active": True}, {"_id": 1}
    )
    if not existing:
        flash("Preset not found.", "error")
        return redirect(url_for("settings.wo_presets_index"))

    data, err = _parse_preset_form(request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("settings.wo_presets_index"))

    now = _utcnow()
    sdb.wo_presets.update_one(
        {"_id": oid},
        {"$set": {
            **data,
            "updated_at": now,
            "updated_by": user["_id"],
        }},
    )

    flash(f"Preset \"{data['name']}\" updated.", "success")
    return redirect(url_for("settings.wo_presets_index"))


@settings_bp.route("/wo_presets/<preset_id>/delete", methods=["POST"])
@login_required
@permission_required("settings.manage_org")
def wo_presets_delete(preset_id: str):
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db(master)
    if sdb is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    oid = _maybe_oid(preset_id)
    if not oid:
        flash("Invalid preset id.", "error")
        return redirect(url_for("settings.wo_presets_index"))

    now = _utcnow()
    sdb.wo_presets.update_one(
        {"_id": oid, "shop_id": shop_oid, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now, "updated_by": user["_id"]}},
    )

    flash("Preset deleted.", "success")
    return redirect(url_for("settings.wo_presets_index"))
