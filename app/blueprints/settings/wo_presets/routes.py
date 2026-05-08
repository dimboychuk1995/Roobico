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

            # misc charges
            misc_charges = []
            for m in (p.get("misc_charges") or []):
                if not isinstance(m, dict):
                    continue
                md = str(m.get("description") or "").strip()
                mp = _f64(m.get("price"))
                if md or (mp is not None and mp > 0):
                    misc_charges.append({"description": md, "price": mp or 0})

            parts.append({
                "part_id": part_id,
                "part_number": part_number,
                "description": desc,
                "qty": qty,
                "cost": cost,
                "price": price,
                "misc_charges": misc_charges,
            })

    data = {
        "name": name,
        "description": description,
        "labor_hours": labor_hours,
        "labor_rate_code": labor_rate_code,
        "allow_discount": allow_discount,
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

    labor_rates = _load_labor_rates(sdb, shop_oid)
    pricing_rules = _load_pricing_rules(sdb, shop_oid)

    # Build a code→hourly_rate lookup
    rate_map = {}
    standard_rate = 0.0
    for lr in labor_rates:
        code = lr.get("code") or ""
        hr = float(lr.get("hourly_rate") or 0)
        rate_map[code] = hr
        if code == "standard":
            standard_rate = hr
    if not standard_rate and labor_rates:
        standard_rate = float(labor_rates[0].get("hourly_rate") or 0)

    for p in presets:
        p["id"] = str(p["_id"])
        # Estimate totals for card display
        hours = float(p.get("labor_hours") or 0)
        rate_code = p.get("labor_rate_code") or ""
        rate = rate_map.get(rate_code, standard_rate)
        p["est_labor"] = round(hours * rate, 2)

        parts_sum = 0.0
        misc_sum = 0.0
        for pt in (p.get("parts") or []):
            qty = float(pt.get("qty") or 0)
            price = float(pt.get("price") or 0)
            parts_sum += qty * price
            for mc in (pt.get("misc_charges") or []):
                misc_sum += qty * float(mc.get("price") or 0)
        p["est_parts"] = round(parts_sum, 2)
        p["est_misc"] = round(misc_sum, 2)
        p["est_total"] = round(p["est_labor"] + parts_sum + misc_sum, 2)

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

    # Enrich parts with live core/misc data from parts collection.
    # If a row's stored part_id is stale (part deleted/recreated), fall back to
    # part_number lookup so the WO still gets up-to-date cost/core/misc data.
    raw_parts = doc.get("parts") or []
    part_ids = []
    fallback_part_numbers = []
    for p in raw_parts:
        pid_str = p.get("part_id")
        if pid_str:
            o = _maybe_oid(pid_str)
            if o:
                part_ids.append(o)
        pn = str(p.get("part_number") or "").strip()
        if pn:
            fallback_part_numbers.append(pn)

    proj = {
        "_id": 1,
        "part_number": 1,
        "average_cost": 1,
        "core_has_charge": 1,
        "core_cost": 1,
        "misc_has_charge": 1,
        "misc_charges": 1,
    }

    parts_lookup = {}
    if part_ids:
        for pdoc in sdb.parts.find(
            {"_id": {"$in": part_ids}, "is_active": True},
            proj,
        ):
            parts_lookup[str(pdoc["_id"])] = pdoc

    parts_by_number = {}
    if fallback_part_numbers:
        for pdoc in sdb.parts.find(
            {"part_number": {"$in": fallback_part_numbers}, "is_active": True},
            proj,
        ):
            pn_key = str(pdoc.get("part_number") or "").strip()
            if pn_key and pn_key not in parts_by_number:
                parts_by_number[pn_key] = pdoc

    # Cache pending preset patches (stale part_id -> fresh _id) to apply once.
    preset_patches = []  # list of (row_index, fresh_id_str)

    enriched_parts = []
    for idx, p in enumerate(raw_parts):
        ep = dict(p)
        pid_str = p.get("part_id")
        live = parts_lookup.get(pid_str) if pid_str else None
        # Fallback: stale or missing part_id, but we have a matching part_number.
        if live is None:
            pn_key = str(p.get("part_number") or "").strip()
            if pn_key and pn_key in parts_by_number:
                live = parts_by_number[pn_key]
                fresh_id = str(live.get("_id"))
                ep["part_id"] = fresh_id
                if pid_str != fresh_id:
                    preset_patches.append((idx, fresh_id))

        if live is not None:
            ep["core_has_charge"] = bool(live.get("core_has_charge"))
            ep["core_cost"] = float(live.get("core_cost") or 0)
            misc_items = []
            for m in (live.get("misc_charges") or []):
                if isinstance(m, dict):
                    misc_items.append({
                        "description": str(m.get("description") or "").strip(),
                        "price": float(m.get("price") or 0),
                    })
            ep["misc_has_charge"] = bool(live.get("misc_has_charge"))
            ep["misc_charges"] = misc_items
            if live.get("average_cost") is not None:
                ep["cost"] = float(live["average_cost"])
        enriched_parts.append(ep)

    # Persist any healed part_id references back into the preset doc so the
    # next load is fast and consistent.
    if preset_patches:
        update_set = {f"parts.{i}.part_id": pid for i, pid in preset_patches}
        try:
            sdb.wo_presets.update_one({"_id": doc["_id"]}, {"$set": update_set})
        except Exception:
            pass

    return jsonify({
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "labor_hours": doc.get("labor_hours"),
        "labor_rate_code": doc.get("labor_rate_code"),
        "allow_discount": bool(doc.get("allow_discount")),
        "parts": enriched_parts,
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
