from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import request, redirect, url_for, flash, session, jsonify

from app.blueprints.vendors import vendors_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_TENANT_ID,
    SESSION_USER_ID,
)
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.permissions import permission_required
from app.utils.mongo_search import build_regex_search_filter
from app.utils.display_datetime import format_date_mmddyyyy
from app.utils.date_filters import build_date_range_filters
from app.utils.contacts import (
    build_contacts_from_form,
    build_contacts_from_payload,
    build_vendor_legacy_contact_fields,
    contact_full_name,
    get_contacts,
    get_main_contact,
    get_main_contact_email,
    get_main_contact_phone,
)


def utcnow():
    return datetime.now(timezone.utc)


def _fmt_dt_label(dt):
    return format_date_mmddyyyy(dt)


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _tenant_id_variants():
    raw = session.get(SESSION_TENANT_ID)
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    oid = _oid(raw)
    if oid:
        out.add(oid)
    return list(out)


def _get_active_shop(master):
    shop_id_raw = session.get("shop_id")
    shop_oid = _oid(shop_id_raw)
    if not shop_oid:
        return None

    tenant_variants = _tenant_id_variants()
    if not tenant_variants:
        return None

    return master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": tenant_variants}})


def _get_shop_db(master):
    shop = _get_active_shop(master)
    if not shop:
        return None, None

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None, shop

    client = get_mongo_client()
    return client[str(db_name)], shop


def _vendors_collection():
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return None, None, None
    return db.vendors, shop, master


def _decorate_vendor(vendor: dict) -> dict:
    contacts = get_contacts(vendor, entity_type="vendor")
    main_contact = get_main_contact(vendor, entity_type="vendor") or {}
    vendor["contacts"] = contacts
    vendor["main_contact_name"] = contact_full_name(main_contact) or ""
    vendor["main_contact_phone"] = get_main_contact_phone(vendor, entity_type="vendor")
    vendor["main_contact_email"] = get_main_contact_email(vendor, entity_type="vendor")
    return vendor


@vendors_bp.get("/")
@login_required
@permission_required("vendors.view")
def vendors_page():
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("dashboard.dashboard"))

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {}
    search_filter = build_regex_search_filter(
        q,
        text_fields=[
            "name",
            "phone",
            "email",
            "contacts.first_name",
            "contacts.last_name",
            "contacts.phone",
            "contacts.email",
            "website",
            "address",
            "primary_contact_first_name",
            "primary_contact_last_name",
            "notes",
        ],
        object_id_fields=["_id", "shop_id", "tenant_id", "created_by", "updated_by"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]} if query else search_filter

    vendors, pagination = paginate_find(
        coll,
        query,
        get_sort_params(request.args, [("is_active", -1), ("name", 1), ("created_at", -1)], ["name", "is_active", "created_at"]),
        page,
        per_page,
    )

    vendor_ids = [v.get("_id") for v in vendors if v.get("_id")]
    balance_map = {}
    if vendor_ids:
        orders_coll = coll.database.parts_orders
        pipeline = [
            {
                "$match": {
                    "shop_id": shop["_id"],
                    "vendor_id": {"$in": vendor_ids},
                    "is_active": {"$ne": False},
                }
            },
            {
                "$group": {
                    "_id": "$vendor_id",
                    "balance_total": {"$sum": {"$ifNull": ["$remaining_balance", 0]}},
                }
            },
        ]
        for row in orders_coll.aggregate(pipeline):
            if not isinstance(row, dict):
                continue
            vid = row.get("_id")
            if not vid:
                continue
            balance_map[vid] = round(_to_float(row.get("balance_total"), 0.0), 2)

    for vendor in vendors:
        vendor["balance"] = float(balance_map.get(vendor.get("_id"), 0.0))
        _decorate_vendor(vendor)

    return _render_app_page(
        "public/vendors.html",
        active_page="vendors",
        vendors=vendors,
        pagination=pagination,
        q=q,
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@vendors_bp.post("/create")
@login_required
@permission_required("vendors.edit")
def vendors_create():
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("vendors.vendors_page"))

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    name = (request.form.get("name") or "").strip()
    address = (request.form.get("address") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    website = (request.form.get("website") or "").strip()
    contacts = build_contacts_from_form(request.form)

    if not name:
        flash("Vendor name is required.", "error")
        return redirect(url_for("vendors.vendors_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    doc = {
        "name": name,
        "website": website or None,
        "address": address or None,
        "contacts": contacts,
        "notes": notes or None,

        "is_active": True,

        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
        "deactivated_at": None,
        "deactivated_by": None,

        "shop_id": shop["_id"],
        "tenant_id": tenant_oid,
    }
    doc.update(build_vendor_legacy_contact_fields(contacts))

    coll.insert_one(doc)

    flash("Vendor created successfully.", "success")
    return redirect(url_for("vendors.vendors_page"))


@vendors_bp.post("/<vendor_id>/deactivate")
@login_required
@permission_required("vendors.deactivate")
def vendors_deactivate(vendor_id):
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("vendors.vendors_page"))

    vid = _oid(vendor_id)
    if not vid:
        flash("Invalid vendor id.", "error")
        return redirect(url_for("vendors.vendors_page"))

    existing = coll.find_one({"_id": vid})
    if not existing:
        flash("Vendor not found.", "error")
        return redirect(url_for("vendors.vendors_page"))

    if existing.get("is_active") is False:
        flash("Vendor is already deactivated.", "info")
        return redirect(url_for("vendors.vendors_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": vid},
        {"$set": {
            "is_active": False,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": now,
            "deactivated_by": user_oid,
        }},
    )

    flash("Vendor deactivated.", "success")
    return redirect(url_for("vendors.vendors_page"))


@vendors_bp.post("/<vendor_id>/restore")
@login_required
@permission_required("vendors.deactivate")
def vendors_restore(vendor_id):
    """
    Restore (reactivate) vendor в SHOP DB.
    Используем то же право vendors.deactivate (можем переименовать позже).
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("vendors.vendors_page"))

    vid = _oid(vendor_id)
    if not vid:
        flash("Invalid vendor id.", "error")
        return redirect(url_for("vendors.vendors_page"))

    existing = coll.find_one({"_id": vid})
    if not existing:
        flash("Vendor not found.", "error")
        return redirect(url_for("vendors.vendors_page"))

    if existing.get("is_active") is True:
        flash("Vendor is already active.", "info")
        return redirect(url_for("vendors.vendors_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    coll.update_one(
        {"_id": vid},
        {"$set": {
            "is_active": True,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": None,
            "deactivated_by": None,
        }},
    )

    flash("Vendor restored.", "success")
    return redirect(url_for("vendors.vendors_page"))


@vendors_bp.get("/api/<vendor_id>")
@login_required
@permission_required("vendors.view")
def vendors_api_get(vendor_id):
    """
    AJAX get vendor data for edit modal.
    Returns JSON with full vendor info.
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    vid = _oid(vendor_id)
    if not vid:
        return jsonify({"ok": False, "error": "Invalid vendor id"}), 400

    vendor = coll.find_one({"_id": vid})
    if not vendor:
        return jsonify({"ok": False, "error": "Vendor not found"}), 404

    vendor = _decorate_vendor(vendor)

    return jsonify({
        "ok": True,
        "item": {
            "_id": str(vendor["_id"]),
            "name": vendor.get("name") or "",
            "phone": vendor.get("main_contact_phone") or "",
            "email": vendor.get("main_contact_email") or "",
            "website": vendor.get("website") or "",
            "primary_contact_first_name": (main_contact := (get_main_contact(vendor, entity_type="vendor") or {})).get("first_name") or "",
            "primary_contact_last_name": main_contact.get("last_name") or "",
            "contacts": vendor.get("contacts") or [],
            "address": vendor.get("address") or "",
            "notes": vendor.get("notes") or "",
            "is_active": vendor.get("is_active", True),
        }
    })


def _calc_order_total(order_doc: dict) -> float:
    total = 0.0
    for item in (order_doc.get("items") or []):
        if not isinstance(item, dict):
            continue
        qty = max(0, int(_to_float(item.get("quantity"), 0)))
        price = max(0.0, _to_float(item.get("price"), 0.0))
        total += qty * price
        # Add core charge per unit if applicable
        if item.get("core_has_charge") and _to_float(item.get("core_cost"), 0.0) > 0:
            total += qty * _to_float(item.get("core_cost"), 0.0)
    for line in (order_doc.get("non_inventory_amounts") or []):
        if not isinstance(line, dict):
            continue
        total += max(0.0, _to_float(line.get("amount"), 0.0))
    return round(total, 2)


def _build_vendor_orders_summary(orders_coll, payments_coll, query: dict) -> dict:
    all_orders = list(orders_coll.find(query, {
        "_id": 1, "status": 1, "items": 1, "non_inventory_amounts": 1,
    }))

    total_orders = len(all_orders)
    total_amount = 0.0
    received_count = 0
    not_received_count = 0
    order_ids = []

    for order in all_orders:
        total_amount += _calc_order_total(order)
        status = (order.get("status") or "ordered").strip().lower()
        if status == "received":
            received_count += 1
        else:
            not_received_count += 1
        order_ids.append(order["_id"])

    # Calculate paid amounts
    total_paid = 0.0
    if order_ids and payments_coll is not None:
        pipeline = [
            {"$match": {"parts_order_id": {"$in": order_ids}, "is_active": True}},
            {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        ]
        rows = list(payments_coll.aggregate(pipeline))
        if rows:
            total_paid = float(rows[0].get("total", 0))

    unpaid = max(0.0, total_amount - total_paid)

    return {
        "total_orders": total_orders,
        "total_amount": round(total_amount, 2),
        "total_paid": round(total_paid, 2),
        "unpaid": round(unpaid, 2),
        "received": received_count,
        "not_received": not_received_count,
    }


@vendors_bp.get("/api/<vendor_id>/part-orders")
@login_required
@permission_required("vendors.view")
def vendors_api_part_orders(vendor_id):
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    vid = _oid(vendor_id)
    if not vid:
        return jsonify({"ok": False, "error": "Invalid vendor id"}), 400

    vendor = coll.find_one({"_id": vid, "shop_id": shop["_id"]})
    if not vendor:
        return jsonify({"ok": False, "error": "Vendor not found"}), 404

    date_filters = build_date_range_filters(request.args, default_preset="this_month")

    page, per_page = get_pagination_params(request.args, default_per_page=10, max_per_page=100)
    orders_coll = coll.database.parts_orders
    payments_coll = coll.database.parts_order_payments

    query = {
        "shop_id": shop["_id"],
        "vendor_id": vid,
        "is_active": {"$ne": False},
    }

    if date_filters["created_from"] or date_filters["created_to_exclusive"]:
        date_cond = {}
        if date_filters["created_from"]:
            date_cond["$gte"] = date_filters["created_from"]
        if date_filters["created_to_exclusive"]:
            date_cond["$lt"] = date_filters["created_to_exclusive"]
        query["created_at"] = date_cond

    orders, pagination = paginate_find(
        orders_coll,
        query,
        [("created_at", -1)],
        page,
        per_page,
        projection={
            "order_number": 1,
            "status": 1,
            "items": 1,
            "non_inventory_amounts": 1,
            "created_at": 1,
        },
    )

    items = []
    for order in orders:
        raw_items = order.get("items") if isinstance(order.get("items"), list) else []
        order_total = _calc_order_total(order)
        items.append(
            {
                "id": str(order.get("_id")),
                "order_number": order.get("order_number") or "-",
                "status": (order.get("status") or "ordered").strip().lower(),
                "items_count": len(raw_items),
                "total_amount": order_total,
                "created_at": _fmt_dt_label(order.get("created_at")),
            }
        )

    # Build summary across ALL matching orders (not just current page)
    summary = _build_vendor_orders_summary(orders_coll, payments_coll, query)

    return jsonify(
        {
            "ok": True,
            "vendor": {
                "id": str(vendor.get("_id")),
                "name": vendor.get("name") or "-",
            },
            "items": items,
            "pagination": pagination,
            "summary": summary,
            "date_preset": date_filters["date_preset"],
            "date_from": date_filters["date_from"],
            "date_to": date_filters["date_to"],
        }
    )


@vendors_bp.post("/api/<vendor_id>/update")
@login_required
@permission_required("vendors.edit")
def vendors_api_update(vendor_id):
    """
    AJAX update vendor.
    Accepts JSON with vendor data.
    Returns JSON { ok: true/false, ... }
    """
    coll, shop, master = _vendors_collection()
    if coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    vid = _oid(vendor_id)
    if not vid:
        return jsonify({"ok": False, "error": "Invalid vendor id"}), 400

    vendor = coll.find_one({"_id": vid})
    if not vendor:
        return jsonify({"ok": False, "error": "Vendor not found"}), 404

    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Vendor name is required"}), 400

    website = (data.get("website") or "").strip()
    address = (data.get("address") or "").strip()
    notes = (data.get("notes") or "").strip()
    contacts = build_contacts_from_payload(data)
    is_active = data.get("is_active", True)

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    update_data = {
        "name": name,
        "website": website or None,
        "address": address or None,
        "notes": notes or None,
        "contacts": contacts,
        "is_active": bool(is_active),
        "updated_at": now,
        "updated_by": user_oid,
    }
    update_data.update(build_vendor_legacy_contact_fields(contacts))

    # If changing to inactive, set deactivated fields
    if not is_active and vendor.get("is_active", True):
        update_data["deactivated_at"] = now
        update_data["deactivated_by"] = user_oid
    # If reactivating, clear deactivated fields
    elif is_active and not vendor.get("is_active", True):
        update_data["deactivated_at"] = None
        update_data["deactivated_by"] = None

    coll.update_one(
        {"_id": vid},
        {"$set": update_data}
    )

    return jsonify({"ok": True, "message": "Vendor updated successfully"})
