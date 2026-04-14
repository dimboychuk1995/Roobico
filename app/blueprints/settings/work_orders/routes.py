from __future__ import annotations

from datetime import datetime, timezone
import re

from flask import render_template, redirect, url_for, flash, session, request, jsonify

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_USER_ID, SESSION_TENANT_ID, SESSION_SHOP_ID
from app.utils.permissions import permission_required, filter_nav_items
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context
from bson import ObjectId


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _load_current_user(master):
    user_id = _maybe_object_id(session.get(SESSION_USER_ID))
    if not user_id:
        return None
    return master.users.find_one({"_id": user_id, "is_active": True})


def _load_current_tenant(master):
    tenant_id = _maybe_object_id(session.get(SESSION_TENANT_ID))
    if not tenant_id:
        return None
    return master.tenants.find_one({"_id": tenant_id, "status": "active"})


def _get_shop_db_strict(master):
    client = get_mongo_client()

    shop_id = _maybe_object_id(session.get(SESSION_SHOP_ID))
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


def _render_settings_page(template_name: str, **ctx):
    """
    Общий рендер для settings-страниц через единый layout builder.
    """
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")

    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    layout.update(ctx)
    return render_template(template_name, **layout)


def _load_labor_rates(labor_rates_col, shop_oid):
    rows = list(
        labor_rates_col.find(
            {"shop_id": shop_oid, "is_active": True},
            {"code": 1, "name": 1, "hourly_rate": 1},
        ).sort([("name", 1), ("code", 1)])
    )
    out = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        out.append(
            {
                "id": str(row.get("_id")),
                "code": code,
                "name": str(row.get("name") or code).strip() or code,
                "hourly_rate": float(row.get("hourly_rate") or 0.0),
            }
        )
    return out


def _validate_labor_rate_payload(code_raw, name_raw, hourly_rate_raw):
    code = str(code_raw or "").strip().lower().replace(" ", "_")
    name = str(name_raw or "").strip()
    if not code:
        return None, None, None, "Labor rate code is required."
    if not name:
        return None, None, None, "Labor rate name is required."

    try:
        hourly_rate = float(hourly_rate_raw)
    except Exception:
        return None, None, None, "Hourly rate must be a number."

    if hourly_rate < 0:
        return None, None, None, "Hourly rate cannot be negative."

    return code, name, float(hourly_rate), None


def _slugify_rate_code(value: str) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "labor_rate"


def _generate_unique_labor_rate_code(labor_rates_col, shop_oid, base_name: str) -> str:
    base = _slugify_rate_code(base_name)
    candidate = base
    idx = 2
    while labor_rates_col.find_one(
        {
            "shop_id": shop_oid,
            "code": candidate,
            "is_active": True,
        },
        {"_id": 1},
    ):
        candidate = f"{base}_{idx}"
        idx += 1
    return candidate


def _validate_labor_rate_form(name_raw, hourly_rate_raw):
    name = str(name_raw or "").strip()
    if not name:
        return None, None, "Labor rate name is required."

    try:
        hourly_rate = float(hourly_rate_raw)
    except Exception:
        return None, None, "Hourly rate must be a number."

    if hourly_rate < 0:
        return None, None, "Hourly rate cannot be negative."

    return name, float(hourly_rate), None


@settings_bp.route("/work_orders", methods=["GET", "POST"])
@login_required
@permission_required("settings.manage_org")
def work_orders_index():
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)

    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db_strict(master)
    if sdb is None or shop_oid is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    rules_col = sdb.shop_supply_amount_rules
    existing = rules_col.find_one({"shop_id": shop_oid})
    core_rules_col = sdb.core_charge_rules
    core_existing = core_rules_col.find_one({"shop_id": shop_oid})
    labor_rates_col = sdb.labor_rates

    if request.method == "POST":
        raw = (request.form.get("shop_supply_procentage") or "").strip()
        charge_for_cores_default = bool(request.form.get("charge_for_cores_default"))
        try:
            value = float(raw)
        except Exception:
            flash("Shop supply amount must be a number.", "error")
            return redirect(url_for("settings.work_orders_index"))

        now = datetime.now(timezone.utc)
        rules_col.update_one(
            {"shop_id": shop_oid},
            {
                "$set": {
                    "shop_supply_procentage": value,
                    "is_active": True,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "shop_id": shop_oid,
                    "created_at": now,
                },
            },
            upsert=True,
        )

        core_rules_col.update_one(
            {"shop_id": shop_oid},
            {
                "$set": {
                    "charge_for_cores_default": charge_for_cores_default,
                    "updated_at": now,
                    "updated_by": user.get("_id"),
                },
                "$setOnInsert": {
                    "shop_id": shop_oid,
                    "created_at": now,
                    "created_by": user.get("_id"),
                },
            },
            upsert=True,
        )

        flash("Work order settings updated.", "success")
        return redirect(url_for("settings.work_orders_index"))

    if existing is None:
        rules_col.update_one(
            {"shop_id": shop_oid},
            {
                "$setOnInsert": {
                    "shop_id": shop_oid,
                    "shop_supply_procentage": 5,
                    "is_active": True,
                    "created_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        existing = rules_col.find_one({"shop_id": shop_oid})

    if core_existing is None:
        now = datetime.now(timezone.utc)
        core_rules_col.update_one(
            {"shop_id": shop_oid},
            {
                "$setOnInsert": {
                    "shop_id": shop_oid,
                    "charge_for_cores_default": False,
                    "created_at": now,
                    "created_by": user.get("_id"),
                },
                "$set": {
                    "updated_at": now,
                    "updated_by": user.get("_id"),
                },
            },
            upsert=True,
        )
        core_existing = core_rules_col.find_one({"shop_id": shop_oid})

    supply_value = existing.get("shop_supply_procentage") if isinstance(existing, dict) else 5
    core_charge_default = bool(core_existing.get("charge_for_cores_default")) if isinstance(core_existing, dict) else False
    labor_rates = _load_labor_rates(labor_rates_col, shop_oid)
    return _render_settings_page(
        "public/settings/work_orders.html",
        shop_supply_procentage=supply_value,
        core_charge_default=core_charge_default,
        labor_rates=labor_rates,
    )


@settings_bp.post("/work_orders/labor_rates/create")
@login_required
@permission_required("settings.manage_org")
def work_orders_labor_rates_create():
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db_strict(master)
    if sdb is None or shop_oid is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    name, hourly_rate, err = _validate_labor_rate_form(
        request.form.get("name"),
        request.form.get("hourly_rate"),
    )
    if err:
        flash(err, "error")
        return redirect(url_for("settings.work_orders_index"))

    labor_rates_col = sdb.labor_rates
    code = _generate_unique_labor_rate_code(labor_rates_col, shop_oid, name)

    now = datetime.now(timezone.utc)
    labor_rates_col.insert_one(
        {
            "shop_id": shop_oid,
            "code": code,
            "name": name,
            "hourly_rate": float(hourly_rate),
            "is_active": True,
            "created_at": now,
            "created_by": user.get("_id"),
            "updated_at": now,
            "updated_by": user.get("_id"),
        }
    )

    flash("Labor rate created.", "success")
    return redirect(url_for("settings.work_orders_index"))


@settings_bp.post("/work_orders/labor_rates/<rate_id>/update")
@login_required
@permission_required("settings.manage_org")
def work_orders_labor_rates_update(rate_id: str):
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db_strict(master)
    if sdb is None or shop_oid is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    rate_oid = _maybe_object_id(rate_id)
    if not rate_oid:
        flash("Invalid labor rate id.", "error")
        return redirect(url_for("settings.work_orders_index"))

    name, hourly_rate, err = _validate_labor_rate_form(
        request.form.get("name"),
        request.form.get("hourly_rate"),
    )
    if err:
        flash(err, "error")
        return redirect(url_for("settings.work_orders_index"))

    labor_rates_col = sdb.labor_rates
    target = labor_rates_col.find_one(
        {"_id": rate_oid, "shop_id": shop_oid, "is_active": True},
        {"_id": 1},
    )
    if not target:
        flash("Labor rate not found.", "error")
        return redirect(url_for("settings.work_orders_index"))

    now = datetime.now(timezone.utc)
    labor_rates_col.update_one(
        {"_id": rate_oid},
        {
            "$set": {
                "name": name,
                "hourly_rate": float(hourly_rate),
                "updated_at": now,
                "updated_by": user.get("_id"),
            }
        },
    )

    flash("Labor rate updated.", "success")
    return redirect(url_for("settings.work_orders_index"))


@settings_bp.post("/work_orders/labor_rates/<rate_id>/delete")
@login_required
@permission_required("settings.manage_org")
def work_orders_labor_rates_delete(rate_id: str):
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    sdb, shop_oid = _get_shop_db_strict(master)
    if sdb is None or shop_oid is None:
        flash("Please select an active shop first.", "error")
        return redirect(url_for("main.settings"))

    rate_oid = _maybe_object_id(rate_id)
    if not rate_oid:
        flash("Invalid labor rate id.", "error")
        return redirect(url_for("settings.work_orders_index"))

    now = datetime.now(timezone.utc)
    res = sdb.labor_rates.update_one(
        {"_id": rate_oid, "shop_id": shop_oid, "is_active": True},
        {
            "$set": {
                "is_active": False,
                "updated_at": now,
                "updated_by": user.get("_id"),
            }
        },
    )
    if res.matched_count == 0:
        flash("Labor rate not found.", "error")
        return redirect(url_for("settings.work_orders_index"))

    flash("Labor rate deleted.", "success")
    return redirect(url_for("settings.work_orders_index"))


# =====================================================================
#  PDF Design settings
# =====================================================================

_PDF_DESIGN_BOOL_FIELDS = [
    "show_logo",
    "show_customer_email",
    "show_customer_phone",
    "show_unit_number",
    "show_vin",
    "show_mileage",
    "show_labor_hours",
    "show_labor_rate",
    "show_parts_detail",
    "show_core_charges",
    "show_misc_charges",
    "show_shop_supply",
]

_PDF_DESIGN_TEXT_FIELDS = [
    "header_color",
    "accent_color",
    "thank_you_message",
    "footer_notes",
]

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@settings_bp.get("/pdf-design")
@login_required
@permission_required("settings.manage_org")
def pdf_design_index():
    master = get_master_db()
    shop_db, shop_oid = _get_shop_db_strict(master)
    if shop_db is None:
        flash("Active shop not set.", "error")
        return redirect(url_for("main.settings"))

    shop = master.shops.find_one({"_id": shop_oid}) or {}
    cfg = shop_db.pdf_design.find_one({"shop_id": shop_oid}) or {}

    shop_name = shop.get("name") or ""
    addr_parts = [shop.get("address_line"), shop.get("city"), shop.get("state"), shop.get("zip")]
    shop_address = ", ".join(str(p).strip() for p in addr_parts if p and str(p).strip())
    shop_phone = str(shop.get("phone") or "").strip()
    shop_email = str(shop.get("email") or "").strip()
    has_logo = bool(shop.get("logo_data"))

    return _render_settings_page(
        "public/settings/pdf_design.html",
        cfg=cfg,
        shop_name=shop_name,
        shop_address=shop_address,
        shop_phone=shop_phone,
        shop_email=shop_email,
        has_logo=has_logo,
    )


@settings_bp.post("/api/pdf-design")
@login_required
@permission_required("settings.manage_org")
def api_pdf_design_save():
    master = get_master_db()
    user = _load_current_user(master)
    shop_db, shop_oid = _get_shop_db_strict(master)
    if shop_db is None or not user:
        return jsonify({"ok": False, "errors": ["Session mismatch"]}), 401

    data = request.get_json(silent=True) or {}

    doc = {}
    for field in _PDF_DESIGN_BOOL_FIELDS:
        doc[field] = bool(data.get(field, True))

    for field in _PDF_DESIGN_TEXT_FIELDS:
        doc[field] = str(data.get(field) or "").strip()

    # Validate colors
    if doc["header_color"] and not _HEX_RE.match(doc["header_color"]):
        doc["header_color"] = "#1f6b43"
    if doc["accent_color"] and not _HEX_RE.match(doc["accent_color"]):
        doc["accent_color"] = "#1f6b43"

    # Truncate
    doc["thank_you_message"] = doc["thank_you_message"][:200]
    doc["footer_notes"] = doc["footer_notes"][:2000]

    now = datetime.now(timezone.utc)
    doc["updated_at"] = now
    doc["updated_by"] = user.get("_id")

    shop_db.pdf_design.update_one(
        {"shop_id": shop_oid},
        {"$set": doc, "$setOnInsert": {"shop_id": shop_oid, "created_at": now}},
        upsert=True,
    )

    return jsonify({"ok": True})
