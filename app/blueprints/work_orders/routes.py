from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from flask import g, request, session, redirect, url_for, flash, jsonify, render_template, make_response

from app.blueprints.work_orders import work_orders_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client, csrf
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.parts_search import build_query_tokens, part_matches_query
from app.utils.parts_interchange import attach_alternates
from app.utils.entity_search import build_unit_search_terms, search_customer_ids, search_unit_ids
from app.utils.mongo_tx import run_atomically
from app.blueprints.work_orders.services.authorizations import (
    _authorizations_collection,
    _build_authorization_context,
    _load_authorization_attachments,
    _resolve_token,
)
from app.blueprints.work_orders.services.pdf_contexts import (
    _build_annual_inspection_pdf_context,
    _build_wo_pdf_context,
)
from app.blueprints.work_orders.services.listing import (
    _paginate_by_customer,
    get_estimates_list,
    get_work_orders_list,
    get_work_orders_totals,
)
from app.blueprints.work_orders.services.lookups import (
    _tenant_variants_from_shop,
    apply_assignments_to_labors,
    customer_label,
    get_assignable_mechanics,
    get_core_charge_default,
    get_customers,
    get_labor_rates,
    get_pricing_rules_json,
    get_shop_supply_percentage,
    get_units,
    normalize_assigned_mechanics,
    unit_label,
)
from app.blueprints.work_orders.services.inventory import (
    _collect_inventory_qty_by_part,
    _resolve_part_for_core_tracking,
    _resolve_part_for_inventory,
    adjust_inventory_for_part_changes,
    apply_core_delta,
    build_core_delta,
    collect_unpaid_core_requirements,
    deduct_parts_from_inventory,
    restore_parts_to_inventory,
    sync_work_order_cores,
)
from app.blueprints.work_orders.services.payments import (
    _build_work_order_payment_summary,
    _sum_active_work_order_payments,
    _sync_work_order_payment_state,
)
from app.blueprints.work_orders.services.bulk_payments import (
    apply_bulk_payment,
    get_bulk_payment_customers,
    get_unpaid_work_orders_for_customer,
)
from app.blueprints.work_orders.services.totals import (
    _apply_sales_tax_to_totals,
    _calc_misc_total_from_parts,
    _get_shop_sales_tax_context,
    _is_customer_taxable,
    _parse_misc_items,
    _work_order_grand_total,
    _work_order_tax_total,
    align_totals_with_labors,
    get_next_wo_number,
    normalize_parts_payload,
    normalize_saved_labors,
    normalize_totals_payload,
)
from app.blueprints.work_orders.services.common import (
    _date_range_for_preset,
    _fmt_dt_iso,
    _parse_iso_date_utc,
    _start_of_month,
    _start_of_quarter,
    _start_of_week_monday,
    _start_of_year,
    _to_iso_date,
    append_and_filter,
    as_bool,
    build_created_at_range_filter,
    ensure_labor_id,
    build_preferred_date_range_filter,
    f64,
    format_dt_label,
    format_preferred_date_label,
    get_date_range_filters,
    i32,
    oid,
    round2,
    utcnow,
)
from app.utils.permissions import (
    enforce_mechanic_status,
    has_permission,
    is_mechanic_mode,
    permission_required,
)
from app.utils.display_datetime import (
    format_date_mmddyyyy,
    format_preferred_shop_date,
    get_active_shop_today_iso,
    shop_date_input_value,
    shop_local_date_to_utc,
)
from app.utils.date_filters import build_date_range_filters
from app.utils.contacts import get_contacts, get_main_contact_email, get_main_contact_name, get_main_contact_phone, normalize_contacts
from app.utils.email_sender import send_email
from app.utils.pdf_utils import render_html_to_pdf
from app.utils.sales_tax import get_shop_zip_code, get_zip_sales_tax_rate
from app.utils.issue_describer import polish_issue_description


















































































def current_user_id():
    return oid(session.get(SESSION_USER_ID))


from app.utils.tenant import tenant_id_variants, get_shop_db as _tenant_get_shop_db


def get_shop_db():
    return _tenant_get_shop_db(get_master_db())




























def render_details(shop_db, shop, customer_id, unit_id, form_state=None):
    from bson import ObjectId as _ObjId
    mechanics = get_assignable_mechanics(shop)

    # Only load the selected customer for initial render; full list loaded via JS
    selected_customer = None
    if customer_id:
        c = shop_db.customers.find_one({"_id": customer_id, "is_active": True})
        if c:
            rate_rows = list(shop_db.labor_rates.find({"is_active": True}, {"_id": 1, "code": 1}))
            rates_by_id = {r.get("_id"): str(r.get("code") or "").strip() for r in rate_rows if r.get("_id")}
            def _resolve_rate(value):
                if isinstance(value, ObjectId):
                    return rates_by_id.get(value, "")
                legacy = str(value or "").strip().lower()
                if legacy == "standart":
                    return "standard"
                return legacy
            selected_customer = {
                "id": str(c["_id"]),
                "label": customer_label(c),
                "default_labor_rate": _resolve_rate(c.get("default_labor_rate")),
                "taxable": bool(c.get("taxable", False)),
                "company_name": (c.get("company_name") or "").strip(),
                "contact_name": get_main_contact_name(c, entity_type="customer"),
                "phone": get_main_contact_phone(c, entity_type="customer"),
                "email": get_main_contact_email(c, entity_type="customer"),
                "address": (c.get("address") or "").strip(),
            }

    customers = [selected_customer] if selected_customer else []

    units = []
    if customer_id:
        units = get_units(shop_db, customer_id)
        if unit_id and not any(u["id"] == str(unit_id) for u in units):
            unit_id = None

    # Generate a temporary ObjectId for attachment uploads before WO is created
    pending_attachment_id = str(_ObjId()) if not (form_state or {}).get("work_order_created") else ""

    # If we are opening an existing WO, lock the displayed tax rate to the value
    # that was saved on the WO itself (so address changes on the shop don't
    # silently re-quote tax for old work orders).
    sales_tax_context = _get_shop_sales_tax_context(shop, shop_db)
    initial_totals_state = (form_state or {}).get("initial_totals") or {}
    locked_rate = initial_totals_state.get("sales_tax_rate") if isinstance(initial_totals_state, dict) else None
    if (form_state or {}).get("work_order_created") and locked_rate is not None:
        # Existing WO: rate is locked, so suppress the "state-only estimate"
        # warning even if the current shop config is api-ninjas based.
        sales_tax_context = {
            **sales_tax_context,
            "rate": float(locked_rate or 0),
            "is_state_only_estimate": False,
            "source": "locked",
        }

    # Lock shop-supply percentage to whatever was applied when this WO was
    # saved, so editing an existing WO doesn't silently re-rate it with the
    # current shop configuration.
    shop_supply_pct = get_shop_supply_percentage(shop_db, shop["_id"])
    if (form_state or {}).get("work_order_created"):
        try:
            supply_total = float((initial_totals_state or {}).get("shop_supply_total") or 0)
            labor_base = float((initial_totals_state or {}).get("labor") or 0)
            if labor_base > 0:
                shop_supply_pct = round((supply_total / labor_base) * 100, 4)
            elif supply_total <= 0:
                # Saved WO had no shop supply — keep it at zero on edit.
                shop_supply_pct = 0.0
        except Exception:
            pass

    ctx = {
        "sales_tax_context": sales_tax_context,
        "active_page": "work_orders",
        "customers": customers,
        "units": units,
        "selected_customer_id": str(customer_id) if customer_id else "",
        "selected_unit_id": str(unit_id) if unit_id else "",
        "labor_rates": get_labor_rates(shop_db, shop["_id"]),
        "mechanics": mechanics,
        "parts_pricing_rules": get_pricing_rules_json(shop_db, shop["_id"], customer_id=customer_id),
        "shop_supply_procentage": shop_supply_pct,
        "charge_for_cores_default": get_core_charge_default(shop_db, shop["_id"]),

        # старые поля (оставляем как у тебя было)
        "labor_description": (form_state or {}).get("labor_description") or "",
        "labor_hours": (form_state or {}).get("labor_hours") or "",
        "labor_rate_code": (form_state or {}).get("labor_rate_code") or "",

        # NEW: флаг, чтобы после create UI стал неактивным
        "work_order_created": bool((form_state or {}).get("work_order_created")),
        "created_work_order_id": (form_state or {}).get("created_work_order_id") or "",
        "wo_number": (form_state or {}).get("wo_number"),
        "work_order_date": (form_state or {}).get("work_order_date") or get_active_shop_today_iso(),
        "today_date_input_value": get_active_shop_today_iso(),

        "initial_labors": (form_state or {}).get("initial_labors") or [],
        "time_summary": (form_state or {}).get("time_summary") or {},
        "initial_totals": normalize_totals_payload((form_state or {}).get("initial_totals") or {}),
        "work_order_status": (form_state or {}).get("work_order_status") or "open",
        "pending_attachment_id": pending_attachment_id,
        "initial_authorizations": (form_state or {}).get("authorizations") or [],
    }

    return _render_app_page("public/work_orders/work_order_details.html", **ctx)


# -------------------- INVENTORY MANAGEMENT --------------------

















@work_orders_bp.get("/work_orders")
@login_required
@permission_required("work_orders.view")
def work_orders_page():
    if is_mechanic_mode():
        return redirect(url_for("mechanic.work_orders_list"))

    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    q = (request.args.get("q") or "").strip()
    paid_status = (request.args.get("paid_status") or "all").strip().lower()
    if paid_status not in ("all", "paid", "unpaid"):
        paid_status = "all"

    date_filters = get_date_range_filters(request.args)
    date_from = date_filters["date_from"]
    date_to = date_filters["date_to"]
    date_preset = date_filters["date_preset"]
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)
    work_orders, pagination, work_orders_totals = get_work_orders_list(
        shop_db,
        shop["_id"],
        page,
        per_page,
        q=q,
        paid_status=paid_status,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
    )

    return _render_app_page(
        "public/work_orders/work_orders.html",
        active_page="work_orders",
        work_orders=work_orders,
        pagination=pagination,
        work_orders_totals=work_orders_totals,
        q=q,
        paid_status=paid_status,
        date_from=date_from,
        date_to=date_to,
        date_preset=date_preset,
        today_date_input_value=get_active_shop_today_iso(),
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@work_orders_bp.get("/work_orders/api/estimates")
@login_required
@permission_required("work_orders.view")
def estimates_api():
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured."}), 400

    q = (request.args.get("q") or "").strip()
    date_filters = get_date_range_filters(request.args)
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    estimates, pagination = get_estimates_list(
        shop_db,
        shop["_id"],
        page,
        per_page,
        q=q,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
    )

    return jsonify({
        "ok": True,
        "estimates": estimates,
        "pagination": {
            "page": pagination.page if hasattr(pagination, "page") else pagination.get("page", 1),
            "pages": pagination.pages if hasattr(pagination, "pages") else pagination.get("pages", 1),
            "total": pagination.total if hasattr(pagination, "total") else pagination.get("total", 0),
            "has_prev": pagination.has_prev if hasattr(pagination, "has_prev") else pagination.get("has_prev", False),
            "has_next": pagination.has_next if hasattr(pagination, "has_next") else pagination.get("has_next", False),
            "prev_page": pagination.prev_page if hasattr(pagination, "prev_page") else pagination.get("prev_page", 1),
            "next_page": pagination.next_page if hasattr(pagination, "next_page") else pagination.get("next_page", 1),
            "per_page": pagination.per_page if hasattr(pagination, "per_page") else pagination.get("per_page", 20),
        },
    })


@work_orders_bp.get("/work_orders/api/customers")
@login_required
@permission_required("work_orders.view")
def customers_api():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured."}), 400

    customers = get_customers(shop_db)
    return jsonify({"ok": True, "customers": customers})


@work_orders_bp.get("/work_orders/details")
@login_required
@permission_required("work_orders.create")
def work_order_details_page():
    if is_mechanic_mode():
        wo_id = (request.args.get("work_order_id") or "").strip()
        if wo_id:
            return redirect(url_for("mechanic.work_order_details", work_order_id=wo_id))
        return redirect(url_for("mechanic.work_order_new"))

    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    customer_id = oid(request.args.get("customer_id"))
    unit_id = oid(request.args.get("unit_id"))

    work_order_id = oid(request.args.get("work_order_id"))
    if work_order_id:
        wo = shop_db.work_orders.find_one({"_id": work_order_id, "shop_id": shop["_id"], "is_active": True})
        if not wo:
            flash("Work order not found.", "error")
            return redirect(url_for("work_orders.work_orders_page"))

        customer_id = wo.get("customer_id")
        unit_id = wo.get("unit_id")
        work_order_status = (wo.get("status") or "open").strip().lower()
        if work_order_status not in ("open", "paid"):
            work_order_status = "open"

        from app.blueprints.work_orders.services.time_tracking import summarize_wo_time

        time_summary = summarize_wo_time(shop_db, shop["_id"], wo["_id"])

        return render_details(
            shop_db,
            shop,
            customer_id,
            unit_id,
            form_state={
                "work_order_created": True,
                "created_work_order_id": str(wo.get("_id")),
                "wo_number": wo.get("wo_number"),
                "work_order_date": shop_date_input_value(wo.get("work_order_date") or wo.get("created_at"), default_today=True),
                "initial_labors": normalize_saved_labors(wo.get("labors") or wo.get("blocks") or [], shop_db=shop_db),
                "initial_totals": wo.get("totals")
                or {
                    "labor": wo.get("labor_total") or 0,
                    "labor_total": wo.get("labor_total") or 0,
                    "parts": wo.get("parts_total") or 0,
                    "parts_total": wo.get("parts_total") or 0,
                    "core_total": 0,
                    "misc_total": 0,
                    "shop_supply_total": 0,
                    "parts_taxable_total": wo.get("parts_total") or 0,
                    "sales_tax_rate": 0,
                    "sales_tax_total": wo.get("sales_tax_total") or 0,
                    "is_taxable": False,
                    "cost_total": wo.get("parts_total") or 0,
                    "grand_total": wo.get("grand_total") or 0,
                    "labors": [],
                },
                "work_order_status": work_order_status,
                "authorizations": list(wo.get("authorizations") or []),
                "time_summary": time_summary,
            },
        )

    return render_details(shop_db, shop, customer_id, unit_id)


@work_orders_bp.post("/work_orders/units/create")
@login_required
@permission_required("work_orders.create")
def create_unit():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    customer_id = oid(request.form.get("customer_id"))
    if not customer_id:
        flash("Customer is required.", "error")
        return redirect(url_for("work_orders.work_order_details_page"))

    customer = shop_db.customers.find_one({"_id": customer_id, "is_active": True})
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("work_orders.work_order_details_page"))

    now = utcnow()
    user_id = current_user_id()

    doc = {
        "customer_id": customer_id,
        "vin": (request.form.get("vin") or "").strip() or None,
        "unit_number": (request.form.get("unit_number") or "").strip() or None,
        "make": (request.form.get("make") or "").strip() or None,
        "model": (request.form.get("model") or "").strip() or None,
        "year": i32(request.form.get("year")),
        "type": (request.form.get("type") or "").strip() or None,
        "mileage": i32(request.form.get("mileage")),
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    doc["search_terms"] = build_unit_search_terms(doc)

    res = shop_db.units.insert_one(doc)
    unit_id = res.inserted_id

    flash("Unit created.", "success")
    return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id), unit_id=str(unit_id)))


@work_orders_bp.post("/work_orders/create")
@login_required
@permission_required("work_orders.create")
def create_work_order():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    customer_id = oid(request.form.get("customer_id"))
    unit_id = oid(request.form.get("unit_id"))

    if not customer_id:
        flash("Customer is required.", "error")
        return redirect(url_for("work_orders.work_order_details_page"))

    if not unit_id:
        flash("Unit is required.", "error")
        return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id)))

    # ---- parse labors ----
    # inputs come like:
    # labors[0][labor_description], labors[0][labor_hours], labors[0][labor_rate_code]
    # labors[0][parts][0][part_number] ... etc
    import re
    import json

    labors_map: dict[int, dict] = {}

    # labor
    labor_re = re.compile(r"^(?:labors|blocks)\[(\d+)\]\[(labor_id|labor_description|labor_hours|labor_rate_code|labor_total_ui|labor_full_total|assigned_mechanics_json|issue_description)\]$")
    # parts
    parts_re = re.compile(
        r"^(?:labors|blocks)\[(\d+)\]\[parts\]\[(\d+)\]\[(part_id|part_number|description|qty|cost|price|core_charge|misc_charge|misc_charge_description|one_time_part)\]$"
    )

    for key, val in request.form.items():
        m = labor_re.match(key)
        if m:
            bidx = int(m.group(1))
            field = m.group(2)
            b = labors_map.setdefault(bidx, {"labor": {}, "parts": []})

            if field == "labor_id":
                b["labor_id"] = (val or "").strip()
            elif field == "labor_description":
                b["labor"]["description"] = (val or "").strip()
            elif field == "labor_hours":
                b["labor"]["hours"] = (val or "").strip()
            elif field == "labor_rate_code":
                b["labor"]["rate_code"] = (val or "").strip()
            elif field in ("labor_total_ui", "labor_full_total"):
                b["labor"]["labor_full_total"] = round2(val)
            elif field == "assigned_mechanics_json":
                b["labor"]["assigned_mechanics_json"] = (val or "").strip()
            elif field == "issue_description":
                b["labor"]["issue_description"] = (val or "").strip()
            continue

        m = parts_re.match(key)
        if m:
            bidx = int(m.group(1))
            ridx = int(m.group(2))
            field = m.group(3)

            b = labors_map.setdefault(bidx, {"labor": {}, "parts": []})
            while len(b["parts"]) <= ridx:
                b["parts"].append({})

            if field in ("part_id", "part_number", "description"):
                b["parts"][ridx][field] = (val or "").strip()
            elif field == "qty":
                b["parts"][ridx]["qty"] = (val or "").strip()
            elif field == "cost":
                b["parts"][ridx]["cost"] = (val or "").strip()
            elif field == "price":
                b["parts"][ridx]["price"] = (val or "").strip()
            elif field == "core_charge":
                b["parts"][ridx]["core_charge"] = (val or "").strip()
            elif field == "misc_charge":
                b["parts"][ridx]["misc_charge"] = (val or "").strip()
            elif field == "misc_charge_description":
                b["parts"][ridx]["misc_charge_description"] = (val or "").strip()
            elif field == "one_time_part":
                raw = str(val or "").strip().lower()
                b["parts"][ridx]["one_time_part"] = raw in ("1", "true", "yes", "on")
            continue

    # normalize labors list in order
    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    labors = []
    for bidx in sorted(labors_map.keys()):
        b = labors_map[bidx]

        parts_clean = normalize_parts_payload(b.get("parts") or [])

        labor = b.get("labor") or {}
        assigned_mechanics = []
        assigned_raw = (labor.get("assigned_mechanics_json") or "").strip()
        if assigned_raw:
            try:
                assigned_data = json.loads(assigned_raw)
            except Exception:
                assigned_data = []
            assigned_mechanics = normalize_assigned_mechanics(assigned_data, mechanics_by_id)

        labors.append({
            "labor_id": ensure_labor_id(b.get("labor_id")),
            "labor": {
                "description": (labor.get("description") or "").strip(),
                "hours": (labor.get("hours") or "").strip(),
                "rate_code": (labor.get("rate_code") or "").strip(),
                "labor_full_total": round2(labor.get("labor_full_total")),
                "assigned_mechanics": assigned_mechanics,
                "issue_description": (labor.get("issue_description") or "").strip(),
            },
            "parts": parts_clean,
        })

    # ✅ totals from front (we just store)
    totals = {}
    totals_raw = (request.form.get("totals_json") or "").strip()
    if totals_raw:
        try:
            totals = json.loads(totals_raw)
            if not isinstance(totals, dict):
                totals = {}
        except Exception:
            totals = {}

    # Honor an explicit per-WO taxable override coming from the UI; fall back to
    # the customer's taxable flag when the front-end didn't send one.
    if isinstance(totals, dict) and "is_taxable" in totals:
        is_taxable_override = bool(totals.get("is_taxable"))
    else:
        is_taxable_override = _is_customer_taxable(shop_db, customer_id)

    totals = align_totals_with_labors(normalize_totals_payload(totals), labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        is_taxable_override,
    )
    work_order_date = shop_local_date_to_utc(request.form.get("work_order_date"), default_today=True)

    now = utcnow()
    user_id = current_user_id()

    # Номер WO нужен до списания — он уходит в ref журнала движений склада.
    wo_number = get_next_wo_number(shop_db, shop["_id"])

    # ✅ Deduct parts from inventory before creating work order
    inventory_result = deduct_parts_from_inventory(
        shop_db, labors, user_id,
        ref={"kind": "work_order", "label": str(wo_number)},
    )
    if not inventory_result["success"] and inventory_result["errors"]:
        for error in inventory_result["errors"]:
            flash(f"Inventory error: {error}", "warning")
        # Still proceed with creating work order, but flag it
        # If you prefer to cancel, uncomment the redirect below
        # return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id), unit_id=str(unit_id)))

    # ✅ Update unit mileage if provided
    unit_mileage = request.form.get("unit_mileage")
    if unit_mileage:
        try:
            shop_db.units.update_one(
                {"_id": unit_id, "shop_id": shop["_id"], "is_active": True},
                {
                    "$set": {
                        "mileage": unit_mileage,
                        "updated_at": now,
                        "updated_by": user_id,
                    }
                }
            )
        except Exception:
            pass  # Silently ignore mileage update errors

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "wo_number": wo_number,
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": "in_progress" if enforce_mechanic_status((request.form.get("create_status") or "").strip().lower()) == "in_progress" else "open",
        "labors": labors,
        "work_order_date": work_order_date,

        # ✅ store totals from UI
        "totals": totals,

        # ✅ track inventory deductions
        "inventory_deducted": len(inventory_result["deducted"]) > 0,
        "inventory_deductions": inventory_result["deducted"],

        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }

    res = shop_db.work_orders.insert_one(doc)
    new_wo_id = res.inserted_id

    # ✅ Reassign pending attachments to the real work order ID
    pending_att_id = oid(request.form.get("pending_attachment_id"))
    if pending_att_id:
        shop_db.attachments.update_many(
            {"entity_id": pending_att_id},
            {"$set": {"entity_id": new_wo_id}},
        )

    # Sync cores collection using unpaid-core logic from this work order.
    core_sync = sync_work_order_cores(shop_db, shop, [], labors, user_id)

    flash("Work order created.", "success")

    if inventory_result["deducted"]:
        deducted_info = ", ".join([f"{d['part_number']} (qty: {d['qty_used']})" for d in inventory_result["deducted"]])
        flash(f"Inventory deducted: {deducted_info}", "info")

    if core_sync.get("changes"):
        added_cores = [x for x in core_sync["changes"] if int(x.get("qty_delta") or 0) > 0]
        if added_cores:
            cores_info = ", ".join([f"{c['part_number']} (qty: {c['qty_delta']})" for c in added_cores])
            flash(f"Cores added: {cores_info}", "info")
    if core_sync.get("errors"):
        for err in core_sync["errors"]:
            flash(f"Core sync warning: {err}", "warning")

    return redirect(url_for("work_orders.work_order_details_page", work_order_id=str(new_wo_id)))


# -----------------------------
# FAST PARTS SEARCH API
# -----------------------------

@work_orders_bp.get("/work_orders/api/parts/search")
@login_required
@permission_required("work_orders.create")
def api_parts_search():
    """
    Very fast search for parts in active shop DB.
    Query params:
      q: search string (min 3)
      limit: default 20 (max 50)
    Returns:
      {"items":[{id, part_number, description, reference, average_cost, in_stock}]}
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"items": [], "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"items": []}), 200

    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 50))

    import re

    parts_col = shop_db.parts
    normalized_query, query_tokens = build_query_tokens(q)
    if not normalized_query:
        return jsonify({"items": []}), 200

    query = {
        "shop_id": shop["_id"],
        "is_active": True,
    }
    if len(query_tokens) <= 1:
        query["search_terms"] = normalized_query
    else:
        query["search_terms"] = {"$all": query_tokens}

    projection = {
        "part_number": 1,
        "description": 1,
        "reference": 1,
        "average_cost": 1,
        "in_stock": 1,
        "do_not_track_inventory": 1,
        "has_selling_price": 1,
        "selling_price": 1,
        "core_has_charge": 1,
        "core_cost": 1,
        "misc_has_charge": 1,
        "misc_charges": 1,
    }

    def _serialize_item(p):
        misc_items = []
        for m in (p.get("misc_charges") or []):
            if not isinstance(m, dict):
                continue
            misc_items.append({
                "description": str(m.get("description") or "").strip(),
                "price": float(m.get("price") or 0),
            })

        return {
            "id": str(p.get("_id")),
            "part_number": p.get("part_number") or "",
            "description": p.get("description") or "",
            "reference": p.get("reference") or "",
            "average_cost": float(p.get("average_cost") or 0),
            "in_stock": int(p.get("in_stock") or 0),
            "do_not_track_inventory": bool(p.get("do_not_track_inventory")),
            "has_selling_price": bool(p.get("has_selling_price")),
            "selling_price": float(p.get("selling_price") or 0),
            "core_has_charge": bool(p.get("core_has_charge")),
            "core_cost": float(p.get("core_cost") or 0),
            "misc_has_charge": bool(p.get("misc_has_charge")),
            "misc_charges": misc_items,
        }

    fetch_limit = min(300, max(50, limit * 6))
    cursor = parts_col.find(query, projection).sort([("part_number", 1)]).limit(fetch_limit)

    items = []
    seen_ids = set()
    for p in cursor:
        if not part_matches_query(
            normalized_query,
            p.get("part_number"),
            p.get("description"),
            p.get("reference"),
        ):
            continue

        part_id = p.get("_id")
        if part_id in seen_ids:
            continue
        seen_ids.add(part_id)

        items.append(_serialize_item(p))

        if len(items) >= limit:
            break

    if len(items) < limit:
        contains = re.escape(q)
        # Fallback must not be limited to docs without search_terms,
        # because many legacy/seeded docs may have incomplete token arrays.
        fallback_query = {
            "shop_id": shop["_id"],
            "is_active": True,
            "$or": [
                {"part_number": {"$regex": contains, "$options": "i"}},
                {"description": {"$regex": contains, "$options": "i"}},
                {"reference": {"$regex": contains, "$options": "i"}},
            ],
        }

        fallback_cursor = (
            parts_col.find(fallback_query, projection)
            .sort([("part_number", 1)])
            .limit(fetch_limit)
        )

        for p in fallback_cursor:
            if not part_matches_query(
                normalized_query,
                p.get("part_number"),
                p.get("description"),
                p.get("reference"),
            ):
                continue

            part_id = p.get("_id")
            if part_id in seen_ids:
                continue
            seen_ids.add(part_id)

            items.append(_serialize_item(p))

            if len(items) >= limit:
                break

    attach_alternates(parts_col, shop["_id"], items, _serialize_item)

    if not has_permission("work_orders.view_costs"):
        items = [strip_part_search_item(i) for i in items]
        for i in items:
            if i.get("alternates"):
                i["alternates"] = [strip_part_search_item(a) for a in i["alternates"]]

    return jsonify({"items": items}), 200


@work_orders_bp.post("/work_orders/api/parse-handwritten")
@login_required
@permission_required("work_orders.create")
def api_parse_handwritten_work_order():
    """
    Upload a handwritten Work Order blank (PDF or image) and use AI to extract
    labors with their parts. Customer / unit info is intentionally ignored.

    For each handwritten part the backend returns a ranked list of CANDIDATE
    matches from the shop DB (top 5). The user reviews and picks the right one
    in the UI before anything is applied to the work order.

    Response shape:
      {
        "ok": True,
        "labors": [
          {
            "labor_description": str,
            "labor_hours": float,
            "parts": [
              {
                "written_part_number": str,
                "written_description": str,
                "qty": int,
                "candidates": [
                  {
                    "part_id": str,
                    "part_number": str,
                    "description": str,
                    "average_cost": float,
                    "selling_price": float,
                    "has_selling_price": bool,
                    "in_stock": int,
                    "core_has_charge": bool,
                    "core_cost": float,
                    "score": int,
                    "reason": str
                  }, ...
                ]
              }, ...
            ]
          }, ...
        ]
      }
    """
    from app.utils.wo_parser import parse_work_order

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured."}), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    allowed_types = {
        "application/pdf",
        "image/jpeg", "image/png", "image/gif", "image/webp",
        "image/bmp", "image/tiff",
    }
    ct = f.content_type or ""
    if ct not in allowed_types:
        return jsonify({"ok": False, "error": f"Unsupported file type: {ct}"}), 400

    file_bytes = f.read()
    if len(file_bytes) > 16 * 1024 * 1024:
        return jsonify({"ok": False, "error": "File too large (max 16 MB)."}), 400

    try:
        parsed = parse_work_order(file_bytes, ct)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": "Failed to parse work order. Please try again."}), 500

    parts_col = shop_db.parts
    shop_id = shop["_id"]

    # ----- Build a fast in-memory fuzzy index over the active catalog -----
    from app.utils.parts_matcher import build_index, match_part

    catalog_proj = {
        "part_number": 1, "description": 1,
        "average_cost": 1, "selling_price": 1, "has_selling_price": 1,
        "in_stock": 1, "do_not_track_inventory": 1,
        "core_has_charge": 1, "core_cost": 1,
        "misc_has_charge": 1, "misc_charges": 1,
    }
    catalog_cursor = parts_col.find(
        {"shop_id": shop_id, "is_active": True},
        catalog_proj,
    )
    index = build_index(catalog_cursor)

    def _hydrate(cands: list[dict]) -> list[dict]:
        out = []
        for c in cands:
            d = c["doc"]
            has_selling = bool(d.get("has_selling_price"))
            misc_items = []
            for m in (d.get("misc_charges") or []):
                if not isinstance(m, dict):
                    continue
                misc_items.append({
                    "description": str(m.get("description") or "").strip(),
                    "price": float(m.get("price") or 0),
                    "taxable": m.get("taxable") is not False,
                })
            out.append({
                "part_id": str(d.get("_id")),
                "part_number": d.get("part_number") or "",
                "description": d.get("description") or "",
                "average_cost": float(d.get("average_cost") or 0),
                "selling_price": float(d.get("selling_price") or 0) if has_selling else 0.0,
                "has_selling_price": has_selling,
                "in_stock": int(d.get("in_stock") or 0),
                "do_not_track_inventory": bool(d.get("do_not_track_inventory")),
                "core_has_charge": bool(d.get("core_has_charge")),
                "core_cost": float(d.get("core_cost") or 0),
                "misc_has_charge": bool(d.get("misc_has_charge")),
                "misc_charges": misc_items,
                "score": int(c.get("score") or 0),
                "reason": c.get("reason") or "",
            })
        return out

    out_labors = []
    for block in (parsed.get("labors") or []):
        out_parts = []
        for p in (block.get("parts") or []):
            written_pn = p.get("part_number") or ""
            written_desc = p.get("description") or ""
            try:
                qty = int(p.get("qty") or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                qty = 1

            # Suggest top candidates from the catalog (rapidfuzz). The user
            # also has a live search box in the UI to override.
            cands: list[dict] = []
            if written_pn.strip() or written_desc.strip():
                cands = match_part(written_pn, written_desc, index, limit=5, min_score=60)

            out_parts.append({
                "written_part_number": written_pn,
                "written_description": written_desc,
                "qty": qty,
                "candidates": _hydrate(cands),
            })

        out_labors.append({
            "labor_description": block.get("labor_description") or "",
            "labor_hours": float(block.get("labor_hours") or 0),
            "parts": out_parts,
        })

    return jsonify({
        "ok": True,
        "labors": out_labors,
        "preview_image_urls": parsed.get("preview_image_urls") or [],
    }), 200


@work_orders_bp.get("/work_orders/api/units")
@login_required
@permission_required("work_orders.create")
def api_units_for_customer():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"items": [], "error": "shop_db_missing"}), 200

    customer_id = oid(request.args.get("customer_id"))
    if not customer_id:
        return jsonify({"items": []}), 200

    rows = list(
        shop_db.units.find({"customer_id": customer_id, "is_active": True})
        .sort([("created_at", -1)])
        .limit(500)
    )

    items = [{"id": str(u["_id"]), "label": unit_label(u)} for u in rows]
    return jsonify({"items": items}), 200


@work_orders_bp.get("/work_orders/api/parts_pricing")
@login_required
@permission_required("work_orders.create")
def api_parts_pricing_for_customer():
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"pricing": None, "error": "shop_db_missing"}), 200

    customer_id = oid(request.args.get("customer_id")) if request.args.get("customer_id") else None
    pricing = get_pricing_rules_json(shop_db, shop["_id"], customer_id=customer_id)
    return jsonify({"pricing": pricing}), 200


@work_orders_bp.get("/work_orders/api/unit")
@login_required
@permission_required("work_orders.create")
def api_unit_details():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "item": None, "error": "shop_db_missing"}), 200

    unit_id = oid(request.args.get("id"))
    if not unit_id:
        return jsonify({"ok": False, "item": None}), 200

    u = shop_db.units.find_one({"_id": unit_id, "is_active": True})
    if not u:
        return jsonify({"ok": False, "item": None}), 200

    item = {
        "id": str(u.get("_id")),
        "customer_id": str(u.get("customer_id")) if u.get("customer_id") else "",
        "vin": u.get("vin") or "",
        "unit_number": u.get("unit_number") or "",
        "make": u.get("make") or "",
        "model": u.get("model") or "",
        "year": u.get("year") or "",
        "type": u.get("type") or "",
        "mileage": u.get("mileage") or "",
    }
    return jsonify({"ok": True, "item": item}), 200


@work_orders_bp.post("/work_orders/api/annual_inspections/create")
@login_required
@permission_required("work_orders.create")
def api_create_annual_inspection():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}

    unit_id = oid(data.get("unit_id"))
    if not unit_id:
        return jsonify({"ok": False, "error": "unit_required"}), 200

    unit = shop_db.units.find_one({"_id": unit_id, "shop_id": shop["_id"], "is_active": True})
    if not unit:
        return jsonify({"ok": False, "error": "unit_not_found"}), 200

    customer_id = oid(data.get("customer_id")) or unit.get("customer_id")
    inspection_date = shop_local_date_to_utc(data.get("date"), default_today=True)

    now = utcnow()
    user_id = current_user_id()

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "unit_id": unit_id,
        "customer_id": customer_id,
        "work_order_id": oid(data.get("work_order_id")),
        "inspection_date": inspection_date,
        "motor_carrier_operator": (data.get("motor_carrier_operator") or "").strip(),
        "address": (data.get("address") or "").strip(),
        "city_state_zip": (data.get("city_state_zip") or "").strip(),
        "inspector_name": (data.get("inspector_name") or "").strip(),
        "inspector_qualified": bool(data.get("inspector_qualified", True)),
        "vehicle_id_type": "vin",
        "vin": (data.get("vin") or "").strip().upper(),
        "inspection_agency": (data.get("inspection_agency") or "").strip(),
        "vehicle_type": (data.get("vehicle_type") or "").strip().lower() or (unit.get("type") or "").strip().lower(),
        # Component checklist (Brake System, Coupling Devices, ...) — filled later.
        "components": {},
        "created_at": now,
        "created_by": user_id,
    }

    # Only one annual inspection per unit: a new one replaces the previous one.
    shop_db.annual_inspections.delete_many({"unit_id": unit_id, "shop_id": shop["_id"]})
    res = shop_db.annual_inspections.insert_one(doc)

    return jsonify({"ok": True, "id": str(res.inserted_id)}), 200






@work_orders_bp.get("/work_orders/api/annual_inspections/preview-pdf")
@login_required
@permission_required("work_orders.create")
def api_preview_annual_inspection_pdf():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 404

    args = request.args
    draft = {
        "_id": "",
        "unit_id": oid(args.get("unit_id")),
        "inspection_date": shop_local_date_to_utc(args.get("date"), default_today=True),
        "motor_carrier_operator": (args.get("motor_carrier_operator") or "").strip(),
        "address": (args.get("address") or "").strip(),
        "city_state_zip": (args.get("city_state_zip") or "").strip(),
        "inspector_name": (args.get("inspector_name") or "").strip(),
        "inspector_qualified": (args.get("inspector_qualified") or "1") not in ("0", "false", ""),
        "vin": (args.get("vin") or "").strip().upper(),
        "inspection_agency": (args.get("inspection_agency") or "").strip(),
        "vehicle_type": (args.get("vehicle_type") or "").strip().lower(),
    }

    ctx = _build_annual_inspection_pdf_context(shop_db, draft)
    ctx["report_number"] = ""
    pdf_html = render_template("emails/annual_inspection_pdf.html", **ctx)

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        return jsonify({"ok": False, "error": "pdf_generation_failed"}), 500

    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = 'inline; filename="AnnualInspection-Preview.pdf"'
    return resp


@work_orders_bp.get("/work_orders/api/annual_inspections/<inspection_id>/download-pdf")
@login_required
@permission_required("work_orders.view")
def api_download_annual_inspection_pdf(inspection_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 404

    insp_id = oid(inspection_id)
    if not insp_id:
        return jsonify({"ok": False, "error": "invalid_inspection_id"}), 400

    inspection = shop_db.annual_inspections.find_one({"_id": insp_id, "shop_id": shop["_id"]})
    if not inspection:
        return jsonify({"ok": False, "error": "inspection_not_found"}), 404

    ctx = _build_annual_inspection_pdf_context(shop_db, inspection)
    pdf_html = render_template("emails/annual_inspection_pdf.html", **ctx)

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        return jsonify({"ok": False, "error": "pdf_generation_failed"}), 500

    date_label = ctx["inspection_date_label"].replace("/", "-")
    filename = f"AnnualInspection-{ctx['fleet_unit_number'] or ctx['report_number']}-{date_label}.pdf"
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers.set("Content-Disposition", "attachment", filename=filename)
    return resp


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/update")
@login_required
@permission_required("work_orders.create")
def api_work_order_update(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    data = request.get_json(silent=True) or {}
    labors = data.get("labors", data.get("blocks"))
    raw_totals = data.get("totals") or {}
    totals = normalize_totals_payload(raw_totals)
    unit_mileage = data.get("unit_mileage")
    work_order_date = shop_local_date_to_utc(data.get("work_order_date"), default_today=True)

    # Allow changing customer/unit on existing WO
    new_customer_id = oid(data.get("customer_id")) if data.get("customer_id") else None
    new_unit_id = oid(data.get("unit_id")) if data.get("unit_id") else None
    if new_customer_id:
        cust = shop_db.customers.find_one(
            {"_id": new_customer_id, "shop_id": shop["_id"], "is_active": True},
            {"_id": 1},
        )
        if not cust:
            return jsonify({"ok": False, "error": "customer_not_found"}), 200
    if new_unit_id:
        unit_doc = shop_db.units.find_one(
            {"_id": new_unit_id, "shop_id": shop["_id"], "is_active": True},
            {"_id": 1, "customer_id": 1},
        )
        if not unit_doc:
            return jsonify({"ok": False, "error": "unit_not_found"}), 200
        if new_customer_id and unit_doc.get("customer_id") != new_customer_id:
            return jsonify({"ok": False, "error": "unit_customer_mismatch"}), 200

    if not isinstance(labors, list):
        return jsonify({"ok": False, "error": "labors_required"}), 200

    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    labors = apply_assignments_to_labors(labors, mechanics_by_id)
    totals = align_totals_with_labors(totals, labors)

    # Use the rate that was locked-in when the WO was originally created.
    # We never re-quote tax for an existing WO if the shop address changed.
    existing_totals = wo.get("totals") or {}
    locked_tax_rate = existing_totals.get("sales_tax_rate")
    if locked_tax_rate is None:
        # Legacy WOs without a stored rate: fall back to the current shop rate.
        locked_tax_rate = (_get_shop_sales_tax_context(shop, shop_db).get("rate") or 0)

    # Honor an explicit per-WO taxable override coming from the UI; fall back to
    # whatever the WO already had stored, then to the customer's flag.
    if isinstance(raw_totals, dict) and "is_taxable" in raw_totals:
        is_taxable_override = bool(raw_totals.get("is_taxable"))
    elif "is_taxable" in existing_totals:
        is_taxable_override = bool(existing_totals.get("is_taxable"))
    else:
        is_taxable_override = _is_customer_taxable(shop_db, wo.get("customer_id"))

    totals = _apply_sales_tax_to_totals(
        totals,
        locked_tax_rate,
        is_taxable_override,
    )

    # (опционально) можно запретить редактирование, если paid
    if (wo.get("status") or "open") == "paid":
        return jsonify({"ok": False, "error": "paid_cannot_edit"}), 200

    now = utcnow()
    user_id = current_user_id()

    # ✅ Adjust inventory for part changes
    # Treat inventory issues (e.g. unknown part numbers, missing inventory rows)
    # as non-fatal warnings: stock is already allowed to go negative, and
    # blocking the save here prevents users from editing WOs that contain
    # one-off / legacy / preset parts not present in the inventory catalog.
    old_labors = wo.get("labors") or []
    inventory_adjustment = adjust_inventory_for_part_changes(
        shop_db, old_labors, labors, user_id,
        ref={"kind": "work_order", "id": wo["_id"], "label": str(wo.get("wo_number") or "")},
    )
    inventory_warnings = list(inventory_adjustment.get("errors") or [])

    # ✅ Update unit mileage if provided
    if unit_mileage is not None:
        unit_id = new_unit_id or wo.get("unit_id")
        if unit_id:
            try:
                shop_db.units.update_one(
                    {"_id": unit_id, "shop_id": shop["_id"], "is_active": True},
                    {
                        "$set": {
                            "mileage": unit_mileage,
                            "updated_at": now,
                            "updated_by": user_id,
                        }
                    }
                )
            except Exception:
                pass  # Silently ignore mileage update errors

    core_sync = sync_work_order_cores(shop_db, shop, old_labors, labors, user_id)

    set_fields = {
        "labors": labors,
        "work_order_date": work_order_date,
        "totals": totals,  # ✅ сохраняем totals от фронта
        # ✅ update inventory tracking
        "inventory_adjusted_at": now,
        "inventory_adjustment_count": (wo.get("inventory_adjustment_count", 0) or 0) + len(inventory_adjustment["adjusted"]),
        "updated_at": now,
        "updated_by": user_id,
    }
    if new_customer_id:
        set_fields["customer_id"] = new_customer_id
    if new_unit_id:
        set_fields["unit_id"] = new_unit_id

    # ✅ Optional explicit status transition: "open" (completed) or "in_progress"
    # В механик-режиме статус форсится в in_progress независимо от клиента.
    save_status = enforce_mechanic_status((data.get("save_status") or "").strip().lower())
    if save_status in ("open", "in_progress"):
        set_fields["status"] = save_status

    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": set_fields,
            "$unset": {
                "blocks": "",
                "labor_total": "",
                "parts_total": "",
                "grand_total": "",
            },
        }
    )

    return jsonify({
        "ok": True,
        "status": set_fields.get("status") or (wo.get("status") or "open"),
        "inventory_adjusted": len(inventory_adjustment["adjusted"]) > 0,
        "inventory_changes": inventory_adjustment["adjusted"],
        "inventory_warnings": inventory_warnings,
        "cores_synced": len(core_sync.get("changes") or []) > 0,
        "core_changes": core_sync.get("changes") or [],
        "core_sync_errors": core_sync.get("errors") or [],
    }), 200



@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/payment")
@login_required
@permission_required("work_orders.create")
def api_work_order_payment(work_order_id):
    """
    Record a payment for a work order.
    Request body: {amount, payment_method, notes}
    Saves to work_order_payments collection and updates work_order status if fully paid.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    data = request.get_json(silent=True) or {}
    amount = f64(data.get("amount"))
    payment_method = (data.get("payment_method") or "").strip() or "cash"
    notes = (data.get("notes") or "").strip()
    payment_date = shop_local_date_to_utc(data.get("payment_date"), default_today=True)

    if amount is None or not (isinstance(amount, (int, float)) and amount > 0):
        return jsonify({"ok": False, "error": "invalid_amount"}), 200

    grand_total = _work_order_grand_total(wo)
    paid_amount = _sum_active_work_order_payments(shop_db, wo_id)

    # Calculate new balance
    new_paid_amount = round2(paid_amount + amount)
    remaining_balance = round2(grand_total - new_paid_amount)

    # If payment exceeds total, return error
    if new_paid_amount > grand_total:
        return jsonify({
            "ok": False,
            "error": "overpayment",
            "message": f"Payment would exceed invoice total. Current balance: ${round2(grand_total - paid_amount)}"
        }), 200

    now = utcnow()
    user_id = current_user_id()

    # Save payment record
    payment_doc = {
        "work_order_id": wo_id,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "amount": round2(amount),
        "payment_method": payment_method,
        "notes": notes,
        "payment_date": payment_date,
        "is_active": True,
        "created_at": now,
        "created_by": user_id,
    }

    pending_att_id = oid(data.get("pending_attachment_id"))

    # Платёж + перенос вложений + статус WO — одна логическая операция.
    # На replica set выполняется транзакцией; на standalone — последовательно
    # (session=None), как раньше. См. app/utils/mongo_tx.py.
    def _apply_payment(session):
        result = shop_db.work_order_payments.insert_one(payment_doc, session=session)
        new_payment_id = result.inserted_id

        # Reassign pending attachments to the real payment ID
        if pending_att_id:
            shop_db.attachments.update_many(
                {"entity_id": pending_att_id},
                {"$set": {"entity_id": new_payment_id}},
                session=session,
            )

        refreshed_wo = shop_db.work_orders.find_one(
            {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, session=session
        ) or wo
        summary = _sync_work_order_payment_state(
            shop_db, refreshed_wo, user_id, now, session=session
        ) or {"status": "open", "is_fully_paid": False}
        return new_payment_id, summary

    payment_id, payment_summary = run_atomically(get_mongo_client(), _apply_payment)

    return jsonify({
        "ok": True,
        "payment_id": str(payment_id),
        "amount_paid": round2(new_paid_amount),
        "remaining_balance": remaining_balance,
        "status": payment_summary["status"],
        "is_fully_paid": payment_summary["is_fully_paid"],
    }), 200


@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/payments")
@login_required
@permission_required("work_orders.create")
def api_get_work_order_payments(work_order_id):
    """
    Get all payments for a work order with balance info.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    payments = list(
        shop_db.work_order_payments.find({"work_order_id": wo_id, "is_active": True})
        .sort([("payment_date", -1), ("created_at", -1)])
    )

    summary = _build_work_order_payment_summary(wo, sum(round2(p.get("amount") or 0) for p in payments))

    payment_list = [
        {
            "id": str(p.get("_id")),
            "amount": round2(p.get("amount") or 0),
            "payment_method": p.get("payment_method") or "cash",
            "notes": p.get("notes") or "",
            "payment_date": _fmt_dt_iso(p.get("payment_date") or p.get("created_at")),
            "payment_date_label": format_preferred_date_label(p.get("payment_date"), p.get("created_at")),
            "created_at": _fmt_dt_iso(p.get("created_at")),
        }
        for p in payments
    ]

    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}, {"email": 1, "contacts": 1, "first_name": 1, "last_name": 1, "phone": 1}) or {}
    customer_email = get_main_contact_email(customer, entity_type="customer")
    customer_contacts = get_contacts(customer, entity_type="customer")

    return jsonify({
        "ok": True,
        "work_order_date": _fmt_dt_iso(wo.get("work_order_date") or wo.get("created_at")),
        "work_order_date_label": format_preferred_date_label(wo.get("work_order_date"), wo.get("created_at")),
        "created_at": _fmt_dt_iso(wo.get("created_at")),
        "created_at_label": format_dt_label(wo.get("created_at")),
        "grand_total": summary["grand_total"],
        "paid_amount": summary["paid_amount"],
        "remaining_balance": summary["remaining_balance"],
        "status": summary["status"],
        "customer_email": customer_email,
        "customer_contacts": customer_contacts,
        "payments": payment_list
    }), 200


@work_orders_bp.post("/work_orders/api/payments/<payment_id>/delete")
@login_required
@permission_required("work_orders.create")
def api_delete_work_order_payment(payment_id):
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    pay_id = oid(payment_id)
    if not pay_id:
        return jsonify({"ok": False, "error": "invalid_payment_id"}), 200

    payment = shop_db.work_order_payments.find_one({"_id": pay_id, "shop_id": shop["_id"], "is_active": True})
    if not payment:
        return jsonify({"ok": False, "error": "payment_not_found"}), 200

    wo_id = payment.get("work_order_id")
    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    now = utcnow()
    user_id = current_user_id()

    # Деактивация платежа + пересчёт статуса WO — атомарно (см. mongo_tx).
    def _apply_delete(session):
        shop_db.work_order_payments.update_one(
            {"_id": pay_id},
            {
                "$set": {
                    "is_active": False,
                    "deleted_at": now,
                    "deleted_by": user_id,
                    "updated_at": now,
                    "updated_by": user_id,
                }
            },
            session=session,
        )

        refreshed_wo = shop_db.work_orders.find_one(
            {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, session=session
        ) or wo
        return _sync_work_order_payment_state(
            shop_db, refreshed_wo, user_id, now, session=session
        ) or {
            "status": "open",
            "paid_amount": 0.0,
            "remaining_balance": _work_order_grand_total(wo),
            "is_fully_paid": False,
        }

    summary = run_atomically(get_mongo_client(), _apply_delete)

    return jsonify(
        {
            "ok": True,
            "payment_id": str(pay_id),
            "work_order_id": str(wo_id) if wo_id else "",
            "status": summary["status"],
            "amount_paid": summary["paid_amount"],
            "remaining_balance": summary["remaining_balance"],
            "is_fully_paid": summary["is_fully_paid"],
        }
    ), 200


@work_orders_bp.get("/work_orders/api/bulk-payments/customers")
@login_required
@permission_required("work_orders.view")
def api_bulk_payment_customers():
    """Customers that have unpaid invoices (for the bulk payment modal)."""
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({"ok": True, "customers": get_bulk_payment_customers(shop_db, shop["_id"])}), 200


@work_orders_bp.get("/work_orders/api/bulk-payments/customers/<customer_id>/unpaid")
@login_required
@permission_required("work_orders.view")
def api_bulk_payment_unpaid_work_orders(customer_id):
    """Unpaid invoices of one customer with balances, oldest first."""
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    cust_id = oid(customer_id)
    if not cust_id:
        return jsonify({"ok": False, "error": "invalid_customer_id"}), 200

    return jsonify({
        "ok": True,
        "work_orders": get_unpaid_work_orders_for_customer(shop_db, shop["_id"], cust_id),
    }), 200


@work_orders_bp.post("/work_orders/api/bulk-payments")
@login_required
@permission_required("work_orders.create")
def api_bulk_payment():
    """
    Record one payment split across several work orders.
    Request body: {customer_id, payment_method, notes, payment_date,
                   allocations: [{work_order_id, amount}, ...]}
    All-or-nothing: any invalid allocation rejects the whole request.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    result = apply_bulk_payment(
        shop_db,
        shop,
        get_mongo_client(),
        allocations=data.get("allocations"),
        payment_method=data.get("payment_method"),
        notes=data.get("notes"),
        payment_date=shop_local_date_to_utc(data.get("payment_date"), default_today=True),
        user_id=current_user_id(),
        customer_id=oid(data.get("customer_id")),
    )
    return jsonify(result), 200


@work_orders_bp.get("/work_orders/api/work_orders/all-payments")
@login_required
@permission_required("work_orders.view")
def api_get_all_payments():
    """
    Get all payments for the current shop.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    shop_id = shop["_id"]
    q = (request.args.get("q") or "").strip()

    date_filters = get_date_range_filters(request.args)
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    payments_query = {"shop_id": shop_id, "is_active": True}

    payments_search = build_regex_search_filter(
        q,
        text_fields=["payment_method", "notes"],
        numeric_fields=["amount"],
        object_id_fields=["_id", "work_order_id", "shop_id", "created_by"],
    )

    if q:
        customer_ids = search_customer_ids(shop_db.customers, q)

        wo_base = {"shop_id": shop_id}
        wo_search = build_regex_search_filter(
            q,
            text_fields=["status"],
            numeric_fields=["wo_number"],
            object_id_fields=["_id", "customer_id", "unit_id", "shop_id"],
        )
        if customer_ids and wo_search:
            wo_query = {"$and": [wo_base, {"$or": [wo_search, {"customer_id": {"$in": customer_ids}}]}]}
        elif customer_ids:
            wo_query = {"$and": [wo_base, {"customer_id": {"$in": customer_ids}}]}
        elif wo_search:
            wo_query = {"$and": [wo_base, wo_search]}
        else:
            wo_query = wo_base

        wo_ids = [wo.get("_id") for wo in shop_db.work_orders.find(wo_query, {"_id": 1}) if wo.get("_id")]

        extra = []
        if wo_ids:
            extra.append({"work_order_id": {"$in": wo_ids}})

        if payments_search and extra:
            payments_query = {"$and": [payments_query, {"$or": [payments_search, *extra]}]}
        elif payments_search:
            payments_query = {"$and": [payments_query, payments_search]}
        elif extra:
            payments_query = {"$and": [payments_query, {"$or": extra}]}
    elif payments_search:
        payments_query = {"$and": [payments_query, payments_search]}

    created_at_filter = build_preferred_date_range_filter("payment_date", created_from, created_to_exclusive)
    if created_at_filter:
        payments_query = append_and_filter(payments_query, created_at_filter)

    page, per_page = get_pagination_params(
        request.args, default_per_page=50, max_per_page=200,
        page_key="payments_page", per_page_key="payments_per_page",
    )

    sort_params = get_sort_params(
        request.args,
        [("payment_date", -1), ("created_at", -1)],
        ["payment_date", "amount", "payment_method", "created_at", "customer"],
    )

    if sort_params and sort_params[0][0] == "customer":
        payments, payments_pagination = _paginate_by_customer(
            shop_db,
            shop_db.work_order_payments,
            payments_query,
            page,
            per_page,
            sort_params[0][1],
            indirect_lookup=(shop_db.work_orders, "work_order_id", "customer_id"),
        )
    else:
        payments, payments_pagination = paginate_find(
            shop_db.work_order_payments,
            payments_query,
            sort=sort_params,
            page=page,
            per_page=per_page,
        )

    work_order_ids = [p.get("work_order_id") for p in payments if p.get("work_order_id")]
    work_orders_map = {}
    customer_ids = []
    if work_order_ids:
        work_orders = list(
            shop_db.work_orders.find(
                {"_id": {"$in": work_order_ids}, "shop_id": shop_id},
                {"customer_id": 1, "wo_number": 1},
            )
        )
        for wo in work_orders:
            wo_id = wo.get("_id")
            if wo_id:
                work_orders_map[wo_id] = wo
            customer_id = wo.get("customer_id")
            if customer_id:
                customer_ids.append(customer_id)

    customers_map = {}
    if customer_ids:
        customers = list(
            shop_db.customers.find(
                {"_id": {"$in": customer_ids}},
                {"company_name": 1, "first_name": 1, "last_name": 1, "contacts": 1},
            )
        )
        for c in customers:
            c_id = c.get("_id")
            if c_id:
                customers_map[c_id] = customer_label(c)

    # Batch-fetch attachment counts for all payments on this page.
    payment_ids = [p["_id"] for p in payments if p.get("_id")]
    att_counts_map: dict = {}
    if payment_ids:
        for doc in shop_db.attachments.aggregate([
            {"$match": {"entity_type": "work_order_payment", "entity_id": {"$in": payment_ids}}},
            {"$group": {"_id": "$entity_id", "count": {"$sum": 1}}},
        ]):
            att_counts_map[doc["_id"]] = doc["count"]

    payment_list = [
        {
            "id": str(p.get("_id")),
            "work_order_id": str(p.get("work_order_id")) if p.get("work_order_id") else "",
            "wo_number": (work_orders_map.get(p.get("work_order_id")) or {}).get("wo_number") or "-",
            "customer": customers_map.get((work_orders_map.get(p.get("work_order_id")) or {}).get("customer_id")) or "-",
            "amount": round2(p.get("amount") or 0),
            "payment_method": p.get("payment_method") or "cash",
            "notes": p.get("notes") or "",
            "payment_date": (p.get("payment_date") or p.get("created_at")).isoformat() if (p.get("payment_date") or p.get("created_at")) else "",
            "created_at": p.get("created_at").isoformat() if p.get("created_at") else "",
            "att_count": att_counts_map.get(p.get("_id"), 0),
        }
        for p in payments
    ]

    return jsonify({
        "ok": True,
        "payments": payment_list,
        "pagination": payments_pagination,
    }), 200


@work_orders_bp.post("/work_orders/api/test/create-sample-payment/<work_order_id>")
@login_required
@permission_required("work_orders.create")
def api_test_create_sample_payment(work_order_id):
    """
    TEST ENDPOINT: Create a sample payment for testing (remove in production).
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    now = utcnow()
    user_id = current_user_id()

    # Create test payment
    payment_doc = {
        "work_order_id": wo_id,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "amount": 99.99,
        "payment_method": "cash",
        "notes": "[TEST PAYMENT]",
        "is_active": True,
        "created_at": now,
        "created_by": user_id,
    }

    result = shop_db.work_order_payments.insert_one(payment_doc)

    return jsonify({
        "ok": True,
        "message": "Test payment created",
        "payment_id": str(result.inserted_id)
    }), 200


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/status")
@login_required
@permission_required("work_orders.create")
def api_work_order_set_status(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()

    if status not in ("open", "paid", "in_progress"):
        return jsonify({"ok": False, "error": "invalid_status"}), 200

    # Механик-режим: разрешён только in_progress, и нельзя трогать paid WO —
    # иначе смена статуса удалит записи платежей (delete_many ниже).
    if is_mechanic_mode():
        if status != "in_progress":
            return jsonify({"ok": False, "error": "forbidden_status"}), 403
        if (wo.get("status") or "").strip().lower() == "paid":
            return jsonify({"ok": False, "error": "paid_cannot_change"}), 403

    now = utcnow()
    user_id = current_user_id()

    # If changing to "open" (unpaid) or "in_progress", delete all payment records for this work order
    if status in ("open", "in_progress"):
        shop_db.work_order_payments.delete_many({"work_order_id": wo_id})

    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "status": status,
                "updated_at": now,
                "updated_by": user_id,
            }
        }
    )

    return jsonify({"ok": True, "status": status}), 200

@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/delete")
@login_required
@permission_required("work_orders.delete")
def api_work_order_delete(work_order_id):
    """
    Delete a work order and restore parts to inventory.
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    now = utcnow()
    user_id = current_user_id()

    # ✅ Restore parts to inventory before deleting
    labors = wo.get("labors") or []
    restore_result = restore_parts_to_inventory(
        shop_db, labors, user_id,
        ref={"kind": "work_order", "id": wo["_id"], "label": str(wo.get("wo_number") or "")},
    )

    # Remove cores generated by this work order unpaid-core logic.
    core_sync = sync_work_order_cores(shop_db, shop, labors, [], user_id)

    # Delete all payments associated with this work order
    payments_deleted = shop_db.work_order_payments.delete_many({"work_order_id": wo_id}).deleted_count

    # Delete all attachments associated with this work order:
    #   - attachments on the WO itself        (entity_type="work_order",         entity_id=wo_id)
    #   - attachments on labor blocks         (entity_type="work_order_labor",   parent_id=wo_id)
    #   - attachments on payments             (entity_type="work_order_payment", parent_id=wo_id)
    attachments_deleted = shop_db.attachments.delete_many({
        "$or": [
            {"entity_type": "work_order", "entity_id": wo_id},
            {"entity_type": {"$in": ["work_order_labor", "work_order_payment"]}, "parent_id": wo_id},
        ]
    }).deleted_count

    # Mark work order as inactive (soft delete)
    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "is_active": False,
                "deleted_at": now,
                "deleted_by": user_id,
                "updated_at": now,
                "updated_by": user_id,
            }
        }
    )

    return jsonify({
        "ok": True,
        "inventory_restored": len(restore_result["restored"]) > 0,
        "inventory_changes": restore_result["restored"],
        "cores_synced": len(core_sync.get("changes") or []) > 0,
        "core_changes": core_sync.get("changes") or [],
        "core_sync_errors": core_sync.get("errors") or [],
        "payments_deleted": payments_deleted,
        "attachments_deleted": attachments_deleted,
    }), 200


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _save_new_contact_to_customer(shop_db, customer_id, new_contact):
    """Append a new contact to a customer's contacts array."""
    if not isinstance(new_contact, dict):
        return
    email = str(new_contact.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return

    customer = shop_db.customers.find_one({"_id": customer_id})
    if not customer:
        return

    existing = get_contacts(customer, entity_type="customer")
    # Don't add duplicate emails
    if any(c.get("email", "").lower() == email for c in existing):
        return

    existing.append({
        "first_name": str(new_contact.get("first_name") or "").strip(),
        "last_name": str(new_contact.get("last_name") or "").strip(),
        "phone": str(new_contact.get("phone") or "").strip(),
        "email": email,
        "is_main": False,
    })

    shop_db.customers.update_one(
        {"_id": customer_id},
        {"$set": {"contacts": normalize_contacts(existing)}},
    )


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/download-pdf")
@login_required
@permission_required("work_orders.view")
def api_download_work_order_pdf(work_order_id):
    # PDF содержит все цены — недоступен без права видеть стоимости.
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    ctx = _build_wo_pdf_context(shop_db, shop, wo)
    pdf_html = render_template("emails/work_order_pdf.html", **ctx)

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        return jsonify({"ok": False, "error": "PDF generation failed"}), 500

    wo_number = ctx["wo_number"]
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="WorkOrder-{wo_number}.pdf"'
    return resp


# ---------------------------------------------------------------------------
# Email routes
# ---------------------------------------------------------------------------

@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/send-email")
@login_required
@permission_required("work_orders.create")
def api_send_work_order_email(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    data = request.get_json(silent=True) or {}

    # Accept either single "email" or "emails" array
    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False, "error": "At least one valid email address required"}), 400

    # Optionally save new contact to customer
    new_contact = data.get("new_contact")
    if isinstance(new_contact, dict) and wo.get("customer_id"):
        _save_new_contact_to_customer(shop_db, wo["customer_id"], new_contact)

    shared_ctx = _build_wo_pdf_context(shop_db, shop, wo)
    wo_number = shared_ctx["wo_number"]
    shop_name = shared_ctx["shop_name"]

    # Add a customer-portal link so the recipient can browse all their data.
    try:
        if wo.get("customer_id"):
            from app.blueprints.customer_portal.routes import (
                get_or_create_portal_token,
                _customer_portal_url,
            )
            _portal_doc = get_or_create_portal_token(shop, wo["customer_id"])
            shared_ctx["portal_url"] = _customer_portal_url(_portal_doc["token"])
    except Exception:
        pass

    html_body = render_template("emails/work_order_email.html", **shared_ctx)
    pdf_html = render_template("emails/work_order_pdf.html", **shared_ctx)

    subject = f"Work Order #{wo_number} — {shop_name}" if shop_name else f"Work Order #{wo_number}"

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        pdf_bytes = None

    attachments = None
    if pdf_bytes:
        attachments = [{
            "filename": f"WorkOrder-{wo_number}.pdf",
            "data": pdf_bytes,
            "content_type": "application/pdf",
        }]

    user_email = (g.user.get("email") or "").strip() if g.user else ""

    try:
        send_email(to_emails, subject, html_body, attachments=attachments,
                   reply_to=user_email or None)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "sent_to": to_emails}), 200


@work_orders_bp.post("/work_orders/api/payments/<payment_id>/send-receipt")
@login_required
@permission_required("work_orders.create")
def api_send_payment_receipt(payment_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    pay_id = oid(payment_id)
    if not pay_id:
        return jsonify({"ok": False, "error": "Invalid payment ID"}), 400

    payment = shop_db.work_order_payments.find_one({"_id": pay_id, "is_active": True})
    if not payment:
        return jsonify({"ok": False, "error": "Payment not found"}), 404

    data = request.get_json(silent=True) or {}

    # Accept either single "email" or "emails" array
    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False, "error": "At least one valid email address required"}), 400

    wo = shop_db.work_orders.find_one({"_id": payment.get("work_order_id")}) or {}

    # Optionally save new contact to customer
    new_contact = data.get("new_contact")
    if isinstance(new_contact, dict) and wo.get("customer_id"):
        _save_new_contact_to_customer(shop_db, wo["customer_id"], new_contact)

    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}) or {}
    unit = shop_db.units.find_one({"_id": wo.get("unit_id")}) or {}

    cust_name = customer_label(customer)
    unit_lbl = unit_label(unit)

    shop_name = str(shop.get("name") or "").strip()
    addr_full = str(shop.get("address") or "").strip()
    addr_parts = [shop.get("address_line"), shop.get("city"), shop.get("state"), shop.get("zip")]
    shop_address = addr_full or ", ".join(str(p).strip() for p in addr_parts if p and str(p).strip())
    contact_parts = [str(shop.get("phone") or "").strip(), str(shop.get("email") or "").strip()]
    shop_contact = " · ".join(p for p in contact_parts if p)

    pay_date = payment.get("payment_date") or payment.get("created_at")
    pay_date_label = format_date_mmddyyyy(pay_date) if pay_date else ""

    amount = round2(payment.get("amount") or 0)
    method = str(payment.get("payment_method") or "cash").replace("_", " ").title()
    notes = str(payment.get("notes") or "").strip()
    payment_ref = str(pay_id)[-8:].upper()

    paid_total = _sum_active_work_order_payments(shop_db, payment.get("work_order_id"))
    summary = _build_work_order_payment_summary(wo, paid_total)

    pdf_cfg = shop_db.pdf_design.find_one({"shop_id": shop["_id"]}) or {}
    shop_logo_url = ""
    if pdf_cfg.get("show_logo", True) and shop.get("logo_data"):
        ct = shop.get("logo_content_type") or "image/png"
        b64 = base64.b64encode(bytes(shop["logo_data"])).decode("ascii")
        shop_logo_url = f"data:{ct};base64,{b64}"

    html_body = render_template(
        "emails/payment_receipt_email.html",
        shop_name=shop_name,
        shop_address=shop_address,
        shop_contact=shop_contact,
        shop_logo_url=shop_logo_url,
        wo_number=str(wo.get("wo_number") or ""),
        pay_date_label=pay_date_label,
        cust_name=cust_name,
        unit_label=unit_lbl,
        amount=amount,
        method=method,
        notes=notes,
        payment_ref=payment_ref,
        grand_total=summary["grand_total"],
        paid_total=summary["paid_amount"],
        remaining=summary["remaining_balance"],
        is_fully_paid=summary["is_fully_paid"],
    )

    wo_number = str(wo.get("wo_number") or "")
    subject = (
        f"Payment Receipt — WO #{wo_number} — {shop_name}"
        if wo_number
        else f"Payment Receipt — {shop_name}"
    )

    user_email = (g.user.get("email") or "").strip() if g.user else ""

    try:
        send_email(to_emails, subject, html_body,
                   reply_to=user_email or None)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "sent_to": to_emails}), 200


# ---------------------------------------------------------------------------
# Customer Authorization (email-based approve/decline)
# ---------------------------------------------------------------------------









@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/send-authorization")
@login_required
@permission_required("work_orders.create")
def api_send_authorization_email(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    data = request.get_json(silent=True) or {}

    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False, "error": "At least one valid email address required"}), 400

    scope = str(data.get("scope") or "work_order").strip().lower()
    if scope not in ("work_order", "labor"):
        return jsonify({"ok": False, "error": "Invalid scope"}), 400

    labor_index = None
    if scope == "labor":
        try:
            labor_index = int(data.get("labor_index"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "labor_index required for scope=labor"}), 400
        labors_count = len(wo.get("labors") or [])
        if labor_index < 0 or labor_index >= labors_count:
            return jsonify({"ok": False, "error": "labor_index out of range"}), 400

    new_contact = data.get("new_contact")
    if isinstance(new_contact, dict) and wo.get("customer_id"):
        _save_new_contact_to_customer(shop_db, wo["customer_id"], new_contact)

    ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
    wo_number = ctx["wo_number"]
    shop_name = ctx["shop_name"]

    # Build a PDF of the full work order to attach (same as regular send-email).
    pdf_attachment = None
    try:
        pdf_html = render_template("emails/work_order_pdf.html", **ctx)
        pdf_bytes = render_html_to_pdf(pdf_html)
        if pdf_bytes:
            pdf_attachment = {
                "filename": f"WorkOrder-{wo_number}.pdf",
                "data": pdf_bytes,
                "content_type": "application/pdf",
            }
    except Exception:
        pdf_attachment = None

    # WO/labor uploaded attachments (already capped in size).
    uploaded_attachments = ctx.get("attachment_files") or []

    email_attachments = []
    if pdf_attachment:
        email_attachments.append(pdf_attachment)
    for att in uploaded_attachments:
        email_attachments.append({
            "filename": att["filename"],
            "data": att["data"],
            "content_type": att.get("content_type") or "application/octet-stream",
        })

    db_name = str(shop.get("db_name") or "")
    auth_col = _authorizations_collection()
    user_email = (g.user.get("email") or "").strip() if g.user else ""
    user_id = g.user.get("_id") if g.user else None
    now = utcnow()

    sent_to = []
    failed = []
    for email in to_emails:
        token = secrets.token_urlsafe(32)
        auth_doc = {
            "token": token,
            "shop_id": shop["_id"],
            "db_name": db_name,
            "work_order_id": wo_id,
            "wo_number": wo_number,
            "scope": scope,
            "labor_index": labor_index,
            "recipient_email": email,
            "customer_id": wo.get("customer_id"),
            "status": "pending",
            "created_at": now,
            "created_by": user_id,
            "responded_at": None,
            "response_ip": None,
            "response_comment": None,
        }
        try:
            auth_col.insert_one(auth_doc)
        except Exception:
            failed.append(email)
            continue

        approve_url = url_for("work_orders.public_authorization_page",
                              token=token, _external=True)

        email_ctx = dict(ctx)
        email_ctx["approve_url"] = approve_url
        email_ctx["recipient_email"] = email
        try:
            if wo.get("customer_id"):
                from app.blueprints.customer_portal.routes import (
                    get_or_create_portal_token,
                    _customer_portal_url,
                )
                _portal_doc = get_or_create_portal_token(shop, wo["customer_id"])
                email_ctx["portal_url"] = _customer_portal_url(_portal_doc["token"])
        except Exception:
            pass
        html_body = render_template("emails/authorization_email.html", **email_ctx)

        if scope == "labor":
            subject_prefix = f"Authorization needed — Work Order #{wo_number} (Labor #{(labor_index or 0) + 1})"
        else:
            subject_prefix = f"Authorization needed — Work Order #{wo_number}"
        subject = f"{subject_prefix} — {shop_name}" if shop_name else subject_prefix

        try:
            send_email([email], subject, html_body,
                       attachments=email_attachments or None,
                       reply_to=user_email or None)
            sent_to.append(email)
        except RuntimeError:
            # Cleanup the token we just created so user isn't left with a
            # dangling record.
            auth_col.delete_one({"token": token})
            failed.append(email)

    if not sent_to:
        return jsonify({"ok": False, "error": "Failed to send authorization email",
                        "failed": failed}), 500

    return jsonify({"ok": True, "sent_to": sent_to, "failed": failed}), 200


# ---------------------------------------------------------------------------
# Describe Issue — AI polish + per-labor save
# ---------------------------------------------------------------------------

@work_orders_bp.post("/work_orders/api/ai/polish-issue")
@login_required
@permission_required("work_orders.create")
def api_polish_issue_description():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Text is empty."}), 400
    try:
        result = polish_issue_description(text)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"AI request failed: {exc}"}), 500
    return jsonify({"ok": True, **result}), 200


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/labors/<int:labor_index>/issue-description")
@login_required
@permission_required("work_orders.create")
def api_save_labor_issue_description(work_order_id, labor_index):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        {"labors": 1},
    )
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    labors_count = len(wo.get("labors") or [])
    if labor_index < 0 or labor_index >= labors_count:
        return jsonify({"ok": False, "error": "labor_index out of range"}), 400

    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if len(text) > 4000:
        text = text[:4000]

    update_res = shop_db.work_orders.update_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        {
            "$set": {
                f"labors.{labor_index}.labor.issue_description": text,
                f"labors.{labor_index}.issue_description": text,
                "updated_at": utcnow(),
                "updated_by": current_user_id(),
            }
        },
    )
    if update_res.matched_count == 0:
        return jsonify({"ok": False, "error": "Failed to save."}), 500
    return jsonify({"ok": True, "issue_description": text}), 200




@work_orders_bp.get("/authorize/<token>")
def public_authorization_page(token):
    auth_doc, shop_db, shop, wo, err = _resolve_token(token)
    if err and not (auth_doc and shop_db and shop and wo):
        return render_template("public/work_orders/authorization_page.html",
                               error=err, auth=None, ctx=None), 404

    scope = auth_doc.get("scope") or "work_order"
    labor_index = auth_doc.get("labor_index")
    ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
    return render_template(
        "public/work_orders/authorization_page.html",
        error=None,
        auth=auth_doc,
        ctx=ctx,
        token=token,
        already_responded=auth_doc.get("status") in ("approved", "declined"),
    )


@work_orders_bp.post("/authorize/<token>")
@csrf.exempt
def public_authorization_submit(token):
    auth_doc, shop_db, shop, wo, err = _resolve_token(token)
    if err and not (auth_doc and shop_db and shop and wo):
        return render_template("public/work_orders/authorization_page.html",
                               error=err, auth=None, ctx=None), 404

    if auth_doc.get("status") in ("approved", "declined"):
        # Already responded — re-render the page.
        scope = auth_doc.get("scope") or "work_order"
        labor_index = auth_doc.get("labor_index")
        ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
        return render_template(
            "public/work_orders/authorization_page.html",
            error=None,
            auth=auth_doc,
            ctx=ctx,
            token=token,
            already_responded=True,
        )

    decision = (request.form.get("decision") or "").strip().lower()
    if decision not in ("approve", "decline"):
        flash("Please choose Approve or Decline.", "error")
        return redirect(url_for("work_orders.public_authorization_page", token=token))

    comment = (request.form.get("comment") or "").strip()[:2000]
    # Best-effort client IP: honor X-Forwarded-For first hop when present.
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")
    client_ip = (forwarded[0].strip() if forwarded and forwarded[0].strip()
                 else (request.remote_addr or ""))
    new_status = "approved" if decision == "approve" else "declined"
    now = utcnow()

    auth_col = _authorizations_collection()
    auth_col.update_one(
        {"_id": auth_doc["_id"], "status": "pending"},
        {"$set": {
            "status": new_status,
            "responded_at": now,
            "response_ip": client_ip,
            "response_comment": comment,
            "updated_at": now,
        }},
    )

    # Mirror onto the work order itself for easy display in shop UI.
    scope = auth_doc.get("scope") or "work_order"
    labor_index = auth_doc.get("labor_index")
    record = {
        "token": auth_doc.get("token"),
        "scope": scope,
        "labor_index": labor_index,
        "recipient_email": auth_doc.get("recipient_email"),
        "status": new_status,
        "responded_at": now,
        "response_ip": client_ip,
        "response_comment": comment,
    }
    shop_db.work_orders.update_one(
        {"_id": wo["_id"]},
        {"$push": {"authorizations": record}},
    )

    auth_doc = auth_col.find_one({"_id": auth_doc["_id"]}) or auth_doc
    ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
    return render_template(
        "public/work_orders/authorization_page.html",
        error=None,
        auth=auth_doc,
        ctx=ctx,
        token=token,
        already_responded=True,
        just_submitted=True,
    )


# ──────────────── WO Presets API ────────────────

@work_orders_bp.get("/work_orders/api/presets")
@login_required
@permission_required("work_orders.create")
def api_presets_list():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify([]), 200

    rows = list(
        shop_db.wo_presets.find(
            {"shop_id": shop["_id"], "is_active": True},
            {"name": 1},
        ).sort([("name", 1)])
    )
    return jsonify([{"id": str(r["_id"]), "name": r.get("name") or ""} for r in rows]), 200


@work_orders_bp.get("/work_orders/api/presets/<preset_id>")
@login_required
@permission_required("work_orders.create")
def api_preset_detail(preset_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"error": "no_shop"}), 400

    pid = oid(preset_id)
    if not pid:
        return jsonify({"error": "bad_id"}), 400

    doc = shop_db.wo_presets.find_one({"_id": pid, "shop_id": shop["_id"], "is_active": True})
    if not doc:
        return jsonify({"error": "not_found"}), 404

    # Enrich parts with current data from the parts collection.
    # Resolve by part_id first (stable); fall back to part_number if the id is
    # stale (part re-imported). Self-heal the stored snapshot so a preset never
    # keeps a non-existent part_number, and always serve dynamic cost/price.
    raw_parts = doc.get("parts") or []
    part_ids = []
    fallback_numbers = []
    for p in raw_parts:
        pid_str = p.get("part_id")
        if pid_str:
            o = oid(pid_str)
            if o:
                part_ids.append(o)
        pn = str(p.get("part_number") or "").strip()
        if pn:
            fallback_numbers.append(pn)

    proj = {
        "_id": 1,
        "part_number": 1,
        "average_cost": 1,
        "core_has_charge": 1,
        "core_cost": 1,
        "misc_has_charge": 1,
        "misc_charges": 1,
        "has_selling_price": 1,
        "selling_price": 1,
    }

    by_id = {}
    if part_ids:
        for pdoc in shop_db.parts.find(
            {"_id": {"$in": part_ids}, "is_active": True}, proj
        ):
            by_id[str(pdoc["_id"])] = pdoc

    by_number = {}
    if fallback_numbers:
        for pdoc in shop_db.parts.find(
            {"part_number": {"$in": fallback_numbers}, "is_active": True}, proj
        ):
            key = str(pdoc.get("part_number") or "").strip()
            if key and key not in by_number:
                by_number[key] = pdoc

    patches = {}  # "parts.<i>.<field>" -> value; persisted back to heal the doc
    enriched_parts = []
    for idx, p in enumerate(raw_parts):
        ep = dict(p)
        pid_str = p.get("part_id")
        live = by_id.get(pid_str) if pid_str else None

        # Stale/missing part_id but a matching part_number is still in catalog.
        if live is None:
            pn_key = str(p.get("part_number") or "").strip()
            if pn_key and pn_key in by_number:
                live = by_number[pn_key]
                fresh_id = str(live.get("_id"))
                ep["part_id"] = fresh_id
                if pid_str != fresh_id:
                    patches[f"parts.{idx}.part_id"] = fresh_id

        if live is not None:
            # Self-heal the stored part_number if the part was renumbered.
            live_pn = str(live.get("part_number") or "").strip()
            stored_pn = str(p.get("part_number") or "").strip()
            if live_pn and live_pn != stored_pn:
                ep["part_number"] = live_pn
                patches[f"parts.{idx}.part_number"] = live_pn

            ep["core_has_charge"] = bool(live.get("core_has_charge"))
            ep["core_cost"] = float(live.get("core_cost") or 0)
            misc_items = []
            for m in (live.get("misc_charges") or []):
                if isinstance(m, dict):
                    misc_items.append({
                        "description": str(m.get("description") or "").strip(),
                        "price": float(m.get("price") or 0),
                        "taxable": m.get("taxable", True),
                    })
            ep["misc_has_charge"] = bool(live.get("misc_has_charge"))
            ep["misc_charges"] = misc_items

            # Dynamic pricing: always reflect the part's current cost/price.
            if live.get("average_cost") is not None:
                ep["cost"] = float(live["average_cost"])
            if bool(live.get("has_selling_price")):
                ep["price"] = float(live.get("selling_price") or 0)
            else:
                # No fixed selling price -> let the WO markup recompute from cost.
                ep["price"] = None
        enriched_parts.append(ep)

    # Persist healed identity snapshots (fresh part_id / current part_number) so
    # the stored preset never drifts to a non-existent part_number.
    if patches:
        try:
            shop_db.wo_presets.update_one({"_id": doc["_id"]}, {"$set": patches})
        except Exception:
            pass

    # Механик-режим: пресет отдаём без денег (цены проставит сервер при
    # сохранении WO) — только парт/описание/количество.
    if not has_permission("work_orders.view_costs"):
        money_keys = ("cost", "price", "core_has_charge", "core_cost", "misc_has_charge", "misc_charges")
        enriched_parts = [
            {k: v for k, v in ep.items() if k not in money_keys}
            for ep in enriched_parts
        ]

    return jsonify({
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "labor_hours": doc.get("labor_hours"),
        "labor_rate_code": doc.get("labor_rate_code"),
        "allow_discount": bool(doc.get("allow_discount")),
        "parts": enriched_parts,
    }), 200

# -------------------- MECHANIC MODE (безденежные эндпоинты + таймеры) --------------------
# Общий API для веб-страницы механика и мобильного приложения. Доступны любому
# пользователю с соответствующим правом (менеджерам тоже) — просто никогда не
# возвращают цены/тоталы. Логика — в services/mechanic_view.py,
# services/mechanic_editor.py и services/time_tracking.py.

from app.blueprints.work_orders.services.mechanic_view import (
    mechanic_wo_payload,
    strip_part_search_item,
    strip_wo_list_item,
)
from app.blueprints.work_orders.services.mechanic_editor import (
    build_mechanic_labors_payload,
    mechanic_done_fields,
    merge_mechanic_edit,
    parse_mileage,
)
from app.blueprints.work_orders.services import time_tracking


def _server_now_iso() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


def _current_user_display_name() -> str:
    user = get_master_db().users.find_one(
        {"_id": current_user_id()},
        {"first_name": 1, "last_name": 1, "name": 1, "email": 1},
    ) or {}
    full = f"{str(user.get('first_name') or '').strip()} {str(user.get('last_name') or '').strip()}".strip()
    return full or str(user.get("name") or "").strip() or str(user.get("email") or "").strip()


@work_orders_bp.get("/work_orders/api/mechanic/work_orders")
@login_required
@permission_required("work_orders.view")
def api_mechanic_work_orders():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    paid_status = status if status in ("in_progress",) else ("unpaid" if status == "open" else "all")
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    items, pagination, _totals = get_work_orders_list(
        shop_db, shop["_id"], page, per_page, q=q, paid_status=paid_status,
    )

    running = time_tracking.get_running_timer(shop_db, shop, current_user_id())
    running_wo_id = str(running.get("work_order_id")) if running else ""

    out_items = []
    for item in items:
        stripped = strip_wo_list_item(item)
        stripped["my_timer_running"] = bool(running_wo_id) and item.get("id") == running_wo_id
        out_items.append(stripped)

    return jsonify({
        "ok": True,
        "items": out_items,
        "pagination": pagination,
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.get("/work_orders/api/mechanic/work_orders/<work_order_id>")
@login_required
@permission_required("work_orders.view")
def api_mechanic_work_order_details(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    payload = mechanic_wo_payload(shop_db, shop, wo, current_user_id())
    payload["server_now"] = _server_now_iso()
    return jsonify(payload), 200


@work_orders_bp.post("/work_orders/api/mechanic/work_orders")
@login_required
@permission_required("work_orders.create")
def api_mechanic_work_order_create():
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    customer_id = oid(data.get("customer_id"))
    unit_id = oid(data.get("unit_id"))
    if not customer_id or not unit_id:
        return jsonify({"ok": False, "error": "customer_unit_required"}), 400

    customer = shop_db.customers.find_one({"_id": customer_id, "shop_id": shop["_id"], "is_active": True})
    if not customer:
        return jsonify({"ok": False, "error": "customer_not_found"}), 404
    unit = shop_db.units.find_one({"_id": unit_id, "shop_id": shop["_id"], "is_active": True})
    if not unit or unit.get("customer_id") != customer_id:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    labors_payload = data.get("labors")
    if not isinstance(labors_payload, list) or not labors_payload:
        return jsonify({"ok": False, "error": "labors_required"}), 400

    # Клиентские цены/часы/статус игнорируются; сервер автозаполняет цены.
    enriched = build_mechanic_labors_payload(shop_db, shop, customer_id, labors_payload)
    labors, totals_raw = compute_labors_and_totals(shop_db, shop, enriched)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        _is_customer_taxable(shop_db, customer_id),
    )

    now = utcnow()
    user_id = current_user_id()

    wo_number = get_next_wo_number(shop_db, shop["_id"])
    inventory_result = deduct_parts_from_inventory(
        shop_db, labors, user_id,
        ref={"kind": "work_order", "label": str(wo_number)},
    )

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "wo_number": wo_number,
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": "in_progress",
        "labors": labors,
        "work_order_date": now,
        "totals": totals,
        "inventory_deducted": len(inventory_result["deducted"]) > 0,
        "inventory_deductions": inventory_result["deducted"],
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
        **mechanic_done_fields(data, user_id, now),
    }
    res = shop_db.work_orders.insert_one(doc)
    core_sync = sync_work_order_cores(shop_db, shop, [], labors, user_id)

    return jsonify({
        "ok": True,
        "id": str(res.inserted_id),
        "wo_number": doc["wo_number"],
        "status": "in_progress",
        "inventory_warnings": inventory_result.get("errors") or [],
        "core_sync_errors": core_sync.get("errors") or [],
    }), 200


@work_orders_bp.post("/work_orders/api/mechanic/work_orders/<work_order_id>")
@login_required
@permission_required("work_orders.create")
def api_mechanic_work_order_update(work_order_id):
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404
    if (wo.get("status") or "open") == "paid":
        return jsonify({"ok": False, "error": "paid_cannot_edit"}), 400

    data = request.get_json(silent=True) or {}
    labors_payload = data.get("labors")
    if not isinstance(labors_payload, list) or not labors_payload:
        return jsonify({"ok": False, "error": "labors_required"}), 400

    merged = merge_mechanic_edit(shop_db, shop, wo, labors_payload)
    labors, totals_raw = compute_labors_and_totals(shop_db, shop, merged)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)

    existing_totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    locked_tax_rate = existing_totals.get("sales_tax_rate")
    if locked_tax_rate is None:
        locked_tax_rate = _get_shop_sales_tax_context(shop, shop_db).get("rate") or 0
    is_taxable = existing_totals.get("is_taxable")
    if is_taxable is None:
        is_taxable = _is_customer_taxable(shop_db, wo.get("customer_id"))
    totals = _apply_sales_tax_to_totals(totals, locked_tax_rate, bool(is_taxable))

    now = utcnow()
    user_id = current_user_id()
    old_labors = wo.get("labors") or []

    inventory_adjustment = adjust_inventory_for_part_changes(
        shop_db, old_labors, labors, user_id,
        ref={"kind": "work_order", "id": wo["_id"], "label": str(wo.get("wo_number") or "")},
    )
    core_sync = sync_work_order_cores(shop_db, shop, old_labors, labors, user_id)

    set_fields = {
        "labors": labors,
        "totals": totals,
        "status": "in_progress",
        "inventory_adjusted_at": now,
        "updated_at": now,
        "updated_by": user_id,
        **mechanic_done_fields(data, user_id, now),
    }

    unit_mileage = parse_mileage(data.get("unit_mileage"))
    if unit_mileage is not None:
        set_fields["mileage"] = unit_mileage
        shop_db.units.update_one(
            {"_id": wo.get("unit_id"), "shop_id": shop["_id"], "is_active": True},
            {"$set": {"mileage": unit_mileage, "updated_at": now, "updated_by": user_id}},
        )

    shop_db.work_orders.update_one({"_id": wo_id}, {"$set": set_fields})

    return jsonify({
        "ok": True,
        "id": str(wo_id),
        "status": "in_progress",
        "inventory_warnings": inventory_adjustment.get("errors") or [],
        "core_sync_errors": core_sync.get("errors") or [],
    }), 200


@work_orders_bp.post("/work_orders/api/mechanic/timers/start")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timer_start():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    user_id = current_user_id()

    log, stopped_prev, error = time_tracking.start_timer(
        shop_db, shop, user_id, _current_user_display_name(),
        data.get("work_order_id"), data.get("labor_id"),
    )
    if error:
        code = 404 if error in ("work_order_not_found", "labor_not_found") else 400
        return jsonify({"ok": False, "error": error}), code

    return jsonify({
        "ok": True,
        "timer": time_tracking._log_payload(log),
        "stopped_previous": time_tracking._log_payload(stopped_prev) if stopped_prev else None,
        "time_summary": time_tracking.summarize_wo_time(
            shop_db, shop["_id"], log.get("work_order_id"), user_id=user_id
        ),
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.post("/work_orders/api/mechanic/timers/stop")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timer_stop():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    user_id = current_user_id()
    log, error = time_tracking.stop_timer(shop_db, shop, user_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    return jsonify({
        "ok": True,
        "timer": time_tracking._log_payload(log),
        "time_summary": time_tracking.summarize_wo_time(
            shop_db, shop["_id"], log.get("work_order_id"), user_id=user_id
        ),
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.get("/work_orders/api/mechanic/timers/current")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timer_current():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({
        "ok": True,
        "timer": time_tracking.running_timer_payload(shop_db, shop, current_user_id()),
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.get("/work_orders/api/mechanic/timers/active")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timers_active():
    """Все идущие таймеры магазина — блок «сейчас в работе» в списке WO."""
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({
        "ok": True,
        "items": time_tracking.active_timers(shop_db, shop, current_user_id()),
        "server_now": _server_now_iso(),
    }), 200
