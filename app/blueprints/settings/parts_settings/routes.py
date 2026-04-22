from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import (
    render_template,
    redirect,
    url_for,
    flash,
    session,
    request,
    jsonify,
)

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_USER_ID,
    SESSION_TENANT_ID,
    SESSION_TENANT_DB,
    SESSION_SHOP_ID,
)
from app.utils.permissions import permission_required, filter_nav_items
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.sales_tax import get_zip_sales_tax_rate, get_custom_shop_sales_tax_settings, get_shop_zip_code


# -----------------------------
# Helpers
# -----------------------------

def utcnow():
    return datetime.now(timezone.utc)


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _get_tenant_db():
    # оставляем (может быть полезно в другом месте),
    # но parts_settings больше НЕ использует tenant DB как fallback
    db_name = session.get(SESSION_TENANT_DB)
    if not db_name:
        return None
    client = get_mongo_client()
    return client[db_name]


def _load_current_user(master):
    user_id = _maybe_object_id(session.get(SESSION_USER_ID))
    if not user_id:
        return None
    return master.users.find_one({"_id": user_id, "is_active": True})


def _render_settings_page(template_name: str, **ctx):
    """
    Один общий рендер для settings-страниц через единый layout builder.
    """
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")

    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session expired. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    layout.update(ctx)
    return render_template(template_name, **layout)


def _load_shops_for_tenant(master, tenant_id):
    shops = []
    if not tenant_id:
        return shops

    for s in master.shops.find({"tenant_id": tenant_id}).sort("created_at", 1):
        shops.append({
            "id": str(s["_id"]),
            "name": s.get("name") or "—",
        })
    return shops


def _get_shop_db_strict(master):
    """
    STRICT: return ONLY active shop DB.
    No tenant DB fallback (otherwise categories become shared across shops).
    """
    client = get_mongo_client()

    shop_id = _maybe_object_id(session.get(SESSION_SHOP_ID))
    if not shop_id:
        return None

    shop = master.shops.find_one({"_id": shop_id})
    if not shop:
        return None

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None

    return client[str(db_name)]


def _clean_name(value: str) -> str:
    return (value or "").strip()


def _require_active_shop_or_redirect():
    shop_oid = _maybe_object_id(session.get(SESSION_SHOP_ID))
    if not shop_oid:
        flash("Please select an active shop first.", "error")
        return None
    return shop_oid


def _require_shop_db_or_redirect():
    master = get_master_db()
    shop_oid = _require_active_shop_or_redirect()
    if not shop_oid:
        return None, None, None

    sdb = _get_shop_db_strict(master)
    if sdb is None:
        flash("Shop database is not configured for the active shop.", "error")
        return None, None, None

    return master, sdb, shop_oid


def _find_one_by_id(col, _id_str: str, extra_filter: dict | None = None):
    oid = _maybe_object_id(_id_str)
    if not oid:
        return None
    q = {"_id": oid}
    if extra_filter:
        q.update(extra_filter)
    return col.find_one(q)


# -----------------------------
# UI Route: Parts Settings
# -----------------------------

@settings_bp.route("/parts-settings", methods=["GET"])
@login_required
@permission_required("parts.edit")
def parts_settings_index():
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    current_user = _load_current_user(master)
    if not current_user:
        flash("User session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    tenant_from_user = current_user.get("tenant_id") or tenant_id_raw
    tenant_oid = _maybe_object_id(tenant_from_user) or _maybe_object_id(tenant_id_raw)

    active_shop_id = str(session.get(SESSION_SHOP_ID) or "")
    shops_for_ui = _load_shops_for_tenant(master, tenant_oid)

    shop_oid = _maybe_object_id(active_shop_id)
    sdb = _get_shop_db_strict(master)

    if not shop_oid or sdb is None:
        return _render_settings_page(
            "public/settings/parts_settings.html",
            active_shop_id=active_shop_id,
            tenant_id=str(tenant_oid or tenant_id_raw),
            tenant_db_name=session.get(SESSION_TENANT_DB) or "",
            shops_for_ui=shops_for_ui,
            now_utc=utcnow(),
            current_user=current_user,
            parts_locations=[],
            parts_categories=[],
            edit_location_id=None,
            edit_category_id=None,
            pricing_mode="margin",
            pricing_rules=[],
            pricing_scales=[],
            active_scale_id="",
            api_tax_rate=None,
            custom_tax_rate=None,
            error_message="Please select an active shop (and ensure it has a shop DB).",
        )

    # ✅ ALWAYS filter by active shop_id
    loc_page, loc_per_page = get_pagination_params(
        request.args,
        default_per_page=20,
        max_per_page=100,
        page_key="loc_page",
        per_page_key="loc_per_page",
    )
    cat_page, cat_per_page = get_pagination_params(
        request.args,
        default_per_page=20,
        max_per_page=100,
        page_key="cat_page",
        per_page_key="cat_per_page",
    )

    parts_locations, pagination_locations = paginate_find(
        sdb.parts_locations,
        {"shop_id": shop_oid},
        get_sort_params(request.args, [("name", 1), ("created_at", 1)], ["name", "created_at"]),
        loc_page,
        loc_per_page,
    )
    parts_categories, pagination_categories = paginate_find(
        sdb.parts_categories,
        {"shop_id": shop_oid},
        get_sort_params(request.args, [("name", 1), ("created_at", 1)], ["name", "created_at"]),
        cat_page,
        cat_per_page,
    )

    # ✅ Load pricing rule scales from shop DB
    scales_cursor = sdb.parts_pricing_rules.find({"shop_id": shop_oid}).sort("created_at", 1)
    pricing_scales = []
    default_scale = None
    for s in scales_cursor:
        item = {
            "id": str(s["_id"]),
            "name": s.get("name") or "Default",
            "mode": (s.get("mode") or "margin"),
            "rules": s.get("rules") or [],
            "is_default": bool(s.get("is_default", False)),
        }
        pricing_scales.append(item)
        if item["is_default"]:
            default_scale = item

    if pricing_scales and default_scale is None:
        default_scale = pricing_scales[0]

    pricing_mode = (default_scale or {}).get("mode") or "margin"
    pricing_rules = (default_scale or {}).get("rules") or []
    active_scale_id = (default_scale or {}).get("id") or ""

    edit_location_id = request.args.get("edit_location_id") or None
    edit_category_id = request.args.get("edit_category_id") or None

    # ── Sales Tax Rate ──
    api_tax_rate = None
    custom_tax_rate = None

    shop_doc = master.shops.find_one({"_id": shop_oid})
    if shop_doc:
        zip_code = get_shop_zip_code(shop_doc)
        if zip_code:
            api_rate_doc = get_zip_sales_tax_rate(master, zip_code)
            if api_rate_doc:
                api_tax_rate = api_rate_doc.get("combined_rate")

        custom_settings = get_custom_shop_sales_tax_settings(sdb)
        if custom_settings:
            custom_tax_rate = custom_settings.get("combined_rate")

    return _render_settings_page(
        "public/settings/parts_settings.html",
        active_shop_id=active_shop_id,
        tenant_id=str(tenant_oid or tenant_id_raw),
        tenant_db_name=session.get(SESSION_TENANT_DB) or "",
        shops_for_ui=shops_for_ui,
        now_utc=utcnow(),
        current_user=current_user,
        parts_locations=parts_locations,
        parts_categories=parts_categories,
        pagination_locations=pagination_locations,
        pagination_categories=pagination_categories,
        edit_location_id=edit_location_id,
        edit_category_id=edit_category_id,
        pricing_mode=pricing_mode,
        pricing_rules=pricing_rules,
        pricing_scales=pricing_scales,
        active_scale_id=active_scale_id,
        api_tax_rate=api_tax_rate,
        custom_tax_rate=custom_tax_rate,
        error_message=None,
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


# -----------------------------
# Sales Tax Rate
# -----------------------------

@settings_bp.route("/parts-settings/tax-rate", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_settings_save_tax():
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    custom_tax_rate_str = (request.form.get("custom_tax_rate") or "").strip()
    reset_tax_rate = request.form.get("reset_tax_rate") == "true"

    now = utcnow()
    user = _load_current_user(master)

    if reset_tax_rate:
        sdb.shop_settings.delete_one({"key": "sales_tax_rate"})
        flash("Sales tax rate reset to API-based lookup.", "success")
    elif custom_tax_rate_str:
        try:
            custom_rate_percent = float(custom_tax_rate_str)
            if custom_rate_percent < 0 or custom_rate_percent > 100:
                flash("Tax rate must be between 0 and 100 (%).", "error")
                return redirect(url_for("settings.parts_settings_index"))

            custom_rate = custom_rate_percent / 100.0

            sdb.shop_settings.update_one(
                {"key": "sales_tax_rate"},
                {
                    "$set": {
                        "combined_rate": custom_rate,
                        "is_active": True,
                        "updated_at": now,
                        "updated_by": user.get("_id") if user else None,
                    },
                    "$setOnInsert": {
                        "created_at": now,
                        "created_by": user.get("_id") if user else None,
                    },
                },
                upsert=True,
            )
            flash(f"Custom sales tax rate set to {custom_rate * 100:.2f}%.", "success")
        except ValueError:
            flash("Invalid tax rate format. Please enter a decimal number.", "error")

    return redirect(url_for("settings.parts_settings_index"))


# -----------------------------
# CRUD: Parts Locations
# -----------------------------

@settings_bp.route("/parts-settings/locations/create", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_locations_create():
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    name = _clean_name(request.form.get("name", ""))
    if not name:
        flash("Location name is required.", "error")
        return redirect(url_for("settings.parts_settings_index"))

    doc = {
        "name": name,
        "shop_id": shop_oid,
        "is_active": True,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }

    sdb.parts_locations.insert_one(doc)
    flash("Location created.", "success")
    return redirect(url_for("settings.parts_settings_index"))


@settings_bp.route("/parts-settings/locations/<location_id>/update", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_locations_update(location_id: str):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    existing = _find_one_by_id(sdb.parts_locations, location_id, {"shop_id": shop_oid})
    if not existing:
        flash("Location not found.", "error")
        return redirect(url_for("settings.parts_settings_index"))

    name = _clean_name(request.form.get("name", ""))
    if not name:
        flash("Location name is required.", "error")
        return redirect(url_for("settings.parts_settings_index", edit_location_id=location_id))

    sdb.parts_locations.update_one(
        {"_id": existing["_id"]},
        {"$set": {"name": name, "updated_at": utcnow()}},
    )
    flash("Location updated.", "success")
    return redirect(url_for("settings.parts_settings_index"))


@settings_bp.route("/parts-settings/locations/<location_id>/delete", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_locations_delete(location_id: str):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    existing = _find_one_by_id(sdb.parts_locations, location_id, {"shop_id": shop_oid})
    if not existing:
        flash("Location not found.", "error")
        return redirect(url_for("settings.parts_settings_index"))

    sdb.parts_locations.delete_one({"_id": existing["_id"]})
    flash("Location deleted.", "success")
    return redirect(url_for("settings.parts_settings_index"))


# -----------------------------
# CRUD: Parts Categories
# -----------------------------

@settings_bp.route("/parts-settings/categories/create", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_categories_create():
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    name = _clean_name(request.form.get("name", ""))
    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("settings.parts_settings_index"))

    doc = {
        "name": name,
        "shop_id": shop_oid,
        "is_active": True,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }

    sdb.parts_categories.insert_one(doc)
    flash("Category created.", "success")
    return redirect(url_for("settings.parts_settings_index"))


@settings_bp.route("/parts-settings/categories/<category_id>/update", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_categories_update(category_id: str):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    existing = _find_one_by_id(sdb.parts_categories, category_id, {"shop_id": shop_oid})
    if not existing:
        flash("Category not found.", "error")
        return redirect(url_for("settings.parts_settings_index"))

    name = _clean_name(request.form.get("name", ""))
    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("settings.parts_settings_index", edit_category_id=category_id))

    sdb.parts_categories.update_one(
        {"_id": existing["_id"]},
        {"$set": {"name": name, "updated_at": utcnow()}},
    )
    flash("Category updated.", "success")
    return redirect(url_for("settings.parts_settings_index"))


@settings_bp.route("/parts-settings/categories/<category_id>/delete", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_categories_delete(category_id: str):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return redirect(url_for("settings.parts_settings_index"))

    existing = _find_one_by_id(sdb.parts_categories, category_id, {"shop_id": shop_oid})
    if not existing:
        flash("Category not found.", "error")
        return redirect(url_for("settings.parts_settings_index"))

    sdb.parts_categories.delete_one({"_id": existing["_id"]})
    flash("Category deleted.", "success")
    return redirect(url_for("settings.parts_settings_index"))


# -----------------------------
# CRUD: Parts Pricing Rules
# -----------------------------

from flask import jsonify


def _validate_pricing_rules_payload(payload: dict):
    """
    Validate and normalize payload:
    {
      "mode": "margin" | "markup",
      "rules": [{"from": number, "to": number|null, "value_percent": number}, ...]
    }
    """
    if not isinstance(payload, dict):
        return False, "Invalid payload."

    mode = (payload.get("mode") or "").strip().lower()
    if mode not in ("margin", "markup"):
        return False, "Mode must be 'margin' or 'markup'."

    rules = payload.get("rules")
    if not isinstance(rules, list):
        return False, "Rules must be a list."

    norm = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            return False, f"Rule #{i+1} is invalid."

        f = r.get("from")
        t = r.get("to")
        v = r.get("value_percent")

        try:
            f = float(f)
        except Exception:
            return False, f"Rule #{i+1}: 'from' must be a number."

        if f < 0:
            return False, f"Rule #{i+1}: 'from' must be >= 0."

        if t in ("", None):
            t = None
        else:
            try:
                t = float(t)
            except Exception:
                return False, f"Rule #{i+1}: 'to' must be a number or empty."
            if t < 0:
                return False, f"Rule #{i+1}: 'to' must be >= 0."
            if t <= f:
                return False, f"Rule #{i+1}: 'to' must be > 'from'."

        try:
            v = float(v)
        except Exception:
            return False, f"Rule #{i+1}: 'value_percent' must be a number."

        # Margin must be strictly less than 100% (mathematically impossible otherwise).
        if mode == "margin" and v >= 100:
            return False, f"Rule #{i+1}: margin must be less than 100%."
        if v < 0:
            return False, f"Rule #{i+1}: percent must be >= 0."

        norm.append({"from": f, "to": t, "value_percent": v})

    # sort by from to keep consistent
    norm.sort(key=lambda x: x["from"])

    return True, {"mode": mode, "rules": norm}


def _serialize_scale(doc):
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "Default",
        "mode": doc.get("mode") or "margin",
        "rules": doc.get("rules") or [],
        "is_default": bool(doc.get("is_default", False)),
        "is_active": bool(doc.get("is_active", True)),
    }


@settings_bp.route("/parts-settings/pricing-rules", methods=["GET"])
@login_required
@permission_required("parts.edit")
def parts_pricing_rules_get():
    """List all pricing rule scales for active shop."""
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return jsonify({"ok": False, "error": "No active shop DB."}), 400

    scales = [
        _serialize_scale(d)
        for d in sdb.parts_pricing_rules.find({"shop_id": shop_oid}).sort("created_at", 1)
    ]
    return jsonify({"ok": True, "scales": scales})


@settings_bp.route("/parts-settings/pricing-rules/<scale_id>", methods=["GET"])
@login_required
@permission_required("parts.edit")
def parts_pricing_rules_get_one(scale_id):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return jsonify({"ok": False, "error": "No active shop DB."}), 400

    sid = _maybe_object_id(scale_id)
    if not sid:
        return jsonify({"ok": False, "error": "Invalid scale id."}), 400

    doc = sdb.parts_pricing_rules.find_one({"_id": sid, "shop_id": shop_oid})
    if not doc:
        return jsonify({"ok": False, "error": "Scale not found."}), 404

    return jsonify({"ok": True, "scale": _serialize_scale(doc)})


@settings_bp.route("/parts-settings/pricing-rules/save", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_pricing_rules_save():
    """
    Save (update) one existing pricing rule scale identified by `id`.
    Payload: {id, mode, rules}.
    """
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return jsonify({"ok": False, "error": "No active shop DB."}), 400

    payload = request.get_json(silent=True) or {}
    sid = _maybe_object_id(payload.get("id"))
    if not sid:
        return jsonify({"ok": False, "error": "Missing or invalid scale id."}), 400

    existing = sdb.parts_pricing_rules.find_one({"_id": sid, "shop_id": shop_oid})
    if not existing:
        return jsonify({"ok": False, "error": "Scale not found."}), 404

    ok, result = _validate_pricing_rules_payload(payload)
    if not ok:
        return jsonify({"ok": False, "error": result}), 400

    sdb.parts_pricing_rules.update_one(
        {"_id": sid, "shop_id": shop_oid},
        {
            "$set": {
                "mode": result["mode"],
                "rules": result["rules"],
                "updated_at": utcnow(),
            },
        },
    )

    return jsonify({"ok": True})


@settings_bp.route("/parts-settings/pricing-rules/create", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_pricing_rules_create():
    """
    Create a new named pricing rule scale for active shop.
    Payload: {name, mode, rules}.
    """
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return jsonify({"ok": False, "error": "No active shop DB."}), 400

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name is required."}), 400
    if len(name) > 80:
        return jsonify({"ok": False, "error": "Name must be 80 characters or fewer."}), 400

    # Name must be unique per shop
    if sdb.parts_pricing_rules.find_one({"shop_id": shop_oid, "name": name}):
        return jsonify({"ok": False, "error": "A scale with this name already exists."}), 400

    ok, result = _validate_pricing_rules_payload(payload)
    if not ok:
        return jsonify({"ok": False, "error": result}), 400

    now = utcnow()
    res = sdb.parts_pricing_rules.insert_one({
        "shop_id": shop_oid,
        "name": name,
        "mode": result["mode"],
        "rules": result["rules"],
        "is_default": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    })

    return jsonify({"ok": True, "id": str(res.inserted_id)})


@settings_bp.route("/parts-settings/pricing-rules/<scale_id>/delete", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_pricing_rules_delete(scale_id):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return jsonify({"ok": False, "error": "No active shop DB."}), 400

    sid = _maybe_object_id(scale_id)
    if not sid:
        return jsonify({"ok": False, "error": "Invalid scale id."}), 400

    doc = sdb.parts_pricing_rules.find_one({"_id": sid, "shop_id": shop_oid})
    if not doc:
        return jsonify({"ok": False, "error": "Scale not found."}), 404

    if bool(doc.get("is_default", False)):
        return jsonify({"ok": False, "error": "Cannot delete the default scale."}), 400

    total = sdb.parts_pricing_rules.count_documents({"shop_id": shop_oid})
    if total <= 1:
        return jsonify({"ok": False, "error": "Cannot delete the last remaining scale."}), 400

    sdb.parts_pricing_rules.delete_one({"_id": sid, "shop_id": shop_oid})
    return jsonify({"ok": True})


@settings_bp.route("/parts-settings/pricing-rules/<scale_id>/set-default", methods=["POST"])
@login_required
@permission_required("parts.edit")
def parts_pricing_rules_set_default(scale_id):
    master, sdb, shop_oid = _require_shop_db_or_redirect()
    if sdb is None:
        return jsonify({"ok": False, "error": "No active shop DB."}), 400

    sid = _maybe_object_id(scale_id)
    if not sid:
        return jsonify({"ok": False, "error": "Invalid scale id."}), 400

    doc = sdb.parts_pricing_rules.find_one({"_id": sid, "shop_id": shop_oid})
    if not doc:
        return jsonify({"ok": False, "error": "Scale not found."}), 404

    now = utcnow()
    sdb.parts_pricing_rules.update_many(
        {"shop_id": shop_oid, "_id": {"$ne": sid}},
        {"$set": {"is_default": False, "updated_at": now}},
    )
    sdb.parts_pricing_rules.update_one(
        {"_id": sid, "shop_id": shop_oid},
        {"$set": {"is_default": True, "is_active": True, "updated_at": now}},
    )
    return jsonify({"ok": True})
