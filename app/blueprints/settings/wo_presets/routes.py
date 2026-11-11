from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import current_app, render_template, redirect, url_for, flash, session, request, jsonify
from bson import ObjectId

from app.utils import audit_log
from app.blueprints.settings import settings_bp
from app.extensions import get_master_db
from app.utils.auth import login_required, SESSION_USER_ID, SESSION_TENANT_ID
from app.utils.permissions import permission_required, filter_nav_items
from app.utils.tenant import get_shop_db as tenant_get_shop_db
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


def _persist_healed_identity(sdb, preset_id, patches: dict) -> None:
    """Сохранить вылеченные ссылки строк (свежий part_id / актуальный
    part_number). Страница отрисуется и без записи — сбой только логируем."""
    if not patches:
        return
    try:
        sdb.wo_presets.update_one({"_id": preset_id}, {"$set": patches})
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Service template %s: failed to save healed part references", preset_id)


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
    """(shop_db, shop_id) активного магазина — через общий get_shop_db
    (проверяет, что магазин принадлежит тенанту сессии)."""
    shop_db, shop = tenant_get_shop_db(master)
    if shop_db is None or not shop:
        return None, None
    return shop_db, shop["_id"]


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
            # Цена, введённая вручную: фиксируется в пресете и применяется в WO
            # вместо динамической (selling price парта / прайс-матрица).
            price_overridden = bool(p.get("price_overridden")) and price is not None

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
                "price_overridden": price_overridden,
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
@permission_required("settings.manage_wo_presets")
def wo_presets_index():
    from app.blueprints.work_orders.services import preset_pricing

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
    # Та же шкала и то же правило цены, что у work order кастомера без
    # собственной шкалы: карточка показывает то, что поставит WO.
    pricing_rules = preset_pricing.standard_pricing(sdb, shop_oid)

    all_rows = [
        pt for pr in presets for pt in (pr.get("parts") or []) if isinstance(pt, dict)
    ]
    by_id, by_number = preset_pricing.load_live_parts(sdb, shop_oid, all_rows)

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
        patches = {}
        rows = [pt for pt in (p.get("parts") or []) if isinstance(pt, dict)]
        for idx, pt in enumerate(p.get("parts") or []):
            if not isinstance(pt, dict):
                continue
            live = preset_pricing.resolve_live_part(pt, by_id, by_number)

            # Keep the displayed identity in sync with the catalog (renumbered or
            # re-created part) so the card never shows a non-existent part_number.
            healed = preset_pricing.heal_patches(idx, pt, live)
            if healed:
                patches.update(healed)
                pt["part_id"] = str(live["_id"])
                pt["part_number"] = str(live.get("part_number") or "").strip() or pt.get("part_number")

            # Live cost/price/misc — exactly what a work order gets (pinned price,
            # else the part's fixed selling price, else the default scale on the
            # current average cost). The stored snapshot is shown only when the
            # part is no longer in the catalog.
            info = preset_pricing.describe_part(pt, live, pricing_rules)
            qty = float(pt.get("qty") or 0)
            pt["price"] = info["price"]
            pt["price_source"] = info["price_source"]
            pt["below_cost"] = info["below_cost"]
            pt["cost_now"] = info["cost"]
            parts_sum += qty * info["price"]
            for mc in info["misc_charges"]:
                misc_sum += qty * float(mc.get("price") or 0)
        _persist_healed_identity(sdb, p["_id"], patches)

        p["parts"] = rows
        # Rows beyond the five shown on the card can still sell below cost.
        p["hidden_below_cost"] = sum(1 for pt in rows[5:] if pt.get("below_cost"))
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
@permission_required("settings.manage_wo_presets")
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
    audit_log.log_event("create", "preset", doc.get("_id"), label=data.get("name") or "Preset")

    flash(f"Preset \"{data['name']}\" created.", "success")
    return redirect(url_for("settings.wo_presets_index"))


@settings_bp.route("/wo_presets/<preset_id>", methods=["GET"])
@login_required
@permission_required("settings.manage_wo_presets")
def wo_presets_detail(preset_id: str):
    """Return preset JSON for the edit modal."""
    from app.blueprints.work_orders.services import preset_pricing

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

    # Enrich parts with live data from the parts collection. A stale part_id
    # (part deleted/recreated) falls back to a part_number lookup, and the
    # healed reference is saved back so the next load is consistent.
    raw_parts = [p for p in (doc.get("parts") or []) if isinstance(p, dict)]
    pricing_rules = preset_pricing.standard_pricing(sdb, shop_oid)
    by_id, by_number = preset_pricing.load_live_parts(sdb, shop_oid, raw_parts)

    preset_patches = {}  # "parts.<i>.<field>" -> value
    enriched_parts = []
    for idx, p in enumerate(doc.get("parts") or []):
        if not isinstance(p, dict):
            continue
        ep = dict(p)
        live = preset_pricing.resolve_live_part(p, by_id, by_number)
        healed = preset_pricing.heal_patches(idx, p, live)
        if healed:
            preset_patches.update(healed)
            ep["part_id"] = str(live["_id"])
            ep["part_number"] = str(live.get("part_number") or "").strip() or ep.get("part_number")

        # Same price a work order gets: pinned > the part's fixed selling price >
        # the default scale on the current average cost. auto_price is returned
        # even for a pinned row so the editor can restore automatic pricing.
        info = preset_pricing.describe_part(p, live, pricing_rules)
        ep["price_overridden"] = preset_pricing.is_pinned(p)
        ep["price_source"] = info["price_source"]
        ep["below_cost"] = info["below_cost"]
        if live is not None:
            ep["core_has_charge"] = bool(live.get("core_has_charge"))
            ep["core_cost"] = float(live.get("core_cost") or 0)
            ep["misc_has_charge"] = bool(live.get("misc_has_charge"))
            ep["misc_charges"] = [
                {
                    "description": str(m.get("description") or "").strip(),
                    "price": float(m.get("price") or 0),
                }
                for m in (live.get("misc_charges") or [])
                if isinstance(m, dict)
            ]
            ep["cost"] = info["cost"]
            ep["auto_price"] = info["auto_price"]
            ep["auto_source"] = info["auto_source"]
            ep["price"] = info["price"]
        enriched_parts.append(ep)

    _persist_healed_identity(sdb, doc["_id"], preset_patches)

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
@permission_required("settings.manage_wo_presets")
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

    audit_log.log_event("update", "preset", oid, label=data.get("name") or "Preset")
    flash(f"Preset \"{data['name']}\" updated.", "success")
    return redirect(url_for("settings.wo_presets_index"))


@settings_bp.route("/wo_presets/<preset_id>/delete", methods=["POST"])
@login_required
@permission_required("settings.manage_wo_presets")
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

    audit_log.log_event("delete", "preset", oid, label="Preset")
    flash("Preset deleted.", "success")
    return redirect(url_for("settings.wo_presets_index"))
