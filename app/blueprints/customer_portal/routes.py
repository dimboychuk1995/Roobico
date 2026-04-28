from __future__ import annotations

import io
import secrets
from datetime import datetime, timedelta

from bson import ObjectId
from flask import (
    abort,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)

from app.blueprints.customer_portal import customer_portal_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required
from app.utils.email_sender import send_email
from app.utils.permissions import permission_required
from app.utils.pdf_utils import render_html_to_pdf


# ──────────────────────── helpers ────────────────────────

PORTAL_TOKEN_TTL_DAYS = 30


def _utcnow():
    return datetime.utcnow()


def _as_naive_utc(dt):
    """Strip tzinfo so comparisons with naive Mongo datetimes never raise."""
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        from datetime import timezone as _tz
        return dt.astimezone(_tz.utc).replace(tzinfo=None)
    return dt


def _portal_tokens_collection():
    return get_master_db().customer_portal_tokens


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _customer_portal_url(token: str) -> str:
    return url_for("customer_portal.dashboard", token=token, _external=True)


def get_or_create_portal_token(shop, customer_id) -> dict:
    """Return an existing valid token doc for this customer, or create one.

    Tokens expire after PORTAL_TOKEN_TTL_DAYS; if the existing token is within
    7 days of expiry it gets refreshed so the link sent in an email keeps
    working for the customer's whole window.
    """
    cid = _oid(customer_id)
    if not cid or not shop:
        raise ValueError("Invalid customer or shop")

    col = _portal_tokens_collection()
    now = _utcnow()
    soon = now + timedelta(days=7)

    existing = col.find_one({
        "shop_id": shop["_id"],
        "customer_id": cid,
    })
    existing_exp = _as_naive_utc(existing.get("expires_at")) if existing else None
    if existing and existing_exp and existing_exp > soon:
        return existing

    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=PORTAL_TOKEN_TTL_DAYS)
    db_name = str(shop.get("db_name") or "")

    if existing:
        col.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "token": token,
                "db_name": db_name,
                "expires_at": expires_at,
                "updated_at": now,
            }},
        )
        existing.update({"token": token, "db_name": db_name,
                         "expires_at": expires_at})
        return existing

    doc = {
        "token": token,
        "shop_id": shop["_id"],
        "db_name": db_name,
        "customer_id": cid,
        "created_at": now,
        "expires_at": expires_at,
        "last_used_at": None,
    }
    col.insert_one(doc)
    return doc


def _resolve_portal_token(token: str):
    """Return (token_doc, shop_db, shop, customer, error) or values=None."""
    if not token or not isinstance(token, str) or len(token) < 16:
        return None, None, None, None, "Invalid portal link"

    col = _portal_tokens_collection()
    doc = col.find_one({"token": token})
    if not doc:
        return None, None, None, None, "This link is no longer valid."

    expires_at = _as_naive_utc(doc.get("expires_at"))
    if expires_at and expires_at < _utcnow():
        return doc, None, None, None, "This link has expired. Please request a new one."

    master = get_master_db()
    shop = master.shops.find_one({"_id": doc.get("shop_id")})
    if not shop:
        return doc, None, None, None, "Shop is no longer available."

    db_name = doc.get("db_name") or shop.get("db_name")
    if not db_name:
        return doc, None, shop, None, "Shop database is not configured."

    client = get_mongo_client()
    shop_db = client[str(db_name)]
    customer = shop_db.customers.find_one({"_id": doc.get("customer_id")})
    if not customer or not customer.get("is_active", True):
        return doc, shop_db, shop, None, "Customer record is unavailable."

    return doc, shop_db, shop, customer, None


def _touch_token(doc):
    try:
        _portal_tokens_collection().update_one(
            {"_id": doc["_id"]},
            {"$set": {"last_used_at": _utcnow()}},
        )
    except Exception:
        pass


def _shop_brand(shop):
    addr_full = str(shop.get("address") or "").strip()
    addr_parts = [shop.get("address_line"), shop.get("city"),
                  shop.get("state"), shop.get("zip")]
    shop_address = addr_full or ", ".join(
        str(p).strip() for p in addr_parts if p and str(p).strip()
    )
    contact = " · ".join(
        p for p in [str(shop.get("phone") or "").strip(),
                    str(shop.get("email") or "").strip()] if p
    )
    return {
        "shop_name": str(shop.get("name") or "").strip(),
        "shop_address": shop_address,
        "shop_contact": contact,
    }


def _format_date(d):
    if not d:
        return ""
    if isinstance(d, datetime):
        try:
            return d.strftime("%m/%d/%Y")
        except Exception:
            return ""
    return str(d)


def _customer_display(customer):
    from app.blueprints.work_orders.routes import customer_label  # local import to avoid cycle
    from app.utils.contacts import get_main_contact_email, get_main_contact_phone
    return {
        "id": str(customer.get("_id")),
        "label": customer_label(customer),
        "email": get_main_contact_email(customer, entity_type="customer"),
        "phone": get_main_contact_phone(customer, entity_type="customer"),
        "address": str(customer.get("address") or "").strip(),
    }


def _list_customer_units(shop_db, customer_id):
    from app.blueprints.work_orders.routes import unit_label
    rows = list(shop_db.units.find(
        {"customer_id": customer_id, "is_active": True}
    ).sort("unit_number", 1))
    units = []
    for u in rows:
        units.append({
            "id": str(u["_id"]),
            "label": unit_label(u),
            "unit_number": str(u.get("unit_number") or "").strip(),
            "year": str(u.get("year") or "").strip(),
            "make": str(u.get("make") or "").strip(),
            "model": str(u.get("model") or "").strip(),
            "vin": str(u.get("vin") or "").strip(),
            "mileage": str(u.get("mileage") or "").strip(),
        })
    return units


PORTAL_PAGE_SIZE = 30


def _unit_wo_ids(shop_db, customer_id, unit_oid):
    """Return list of WO _ids for a customer's specific unit."""
    if not unit_oid:
        return None
    return [w["_id"] for w in shop_db.work_orders.find(
        {"customer_id": customer_id, "unit_id": unit_oid, "is_active": True},
        {"_id": 1},
    )]


def _list_customer_work_orders(shop_db, customer_id, unit_oid=None,
                               limit=PORTAL_PAGE_SIZE, offset=0):
    from app.blueprints.work_orders.routes import (
        _sum_active_work_order_payments,
        _work_order_grand_total,
        unit_label,
    )
    q = {"customer_id": customer_id, "is_active": True}
    if unit_oid:
        q["unit_id"] = unit_oid

    cursor = (shop_db.work_orders.find(q)
              .sort([("work_order_date", -1), ("created_at", -1)])
              .skip(int(offset)).limit(int(limit) + 1))
    rows = list(cursor)
    has_more = len(rows) > limit
    rows = rows[:limit]

    out = []
    for wo in rows:
        unit = shop_db.units.find_one({"_id": wo.get("unit_id")}) or {}
        grand = _work_order_grand_total(wo)
        paid = _sum_active_work_order_payments(shop_db, wo["_id"])
        out.append({
            "id": str(wo["_id"]),
            "wo_number": str(wo.get("wo_number") or ""),
            "status": str(wo.get("status") or "open"),
            "date_label": _format_date(wo.get("work_order_date")
                                       or wo.get("created_at")),
            "unit_label": unit_label(unit) if unit else "",
            "grand_total": round(float(grand or 0), 2),
            "paid_total": round(float(paid or 0), 2),
            "remaining": round(max(0.0, float(grand or 0) - float(paid or 0)), 2),
        })
    return out, has_more


def _list_customer_payments(shop_db, customer_id, unit_oid=None,
                            limit=PORTAL_PAGE_SIZE, offset=0):
    q = {"customer_id": customer_id, "is_active": True}
    if unit_oid:
        wo_ids = _unit_wo_ids(shop_db, customer_id, unit_oid)
        if not wo_ids:
            return [], False
        q["work_order_id"] = {"$in": wo_ids}

    cursor = (shop_db.work_order_payments.find(q)
              .sort("payment_date", -1)
              .skip(int(offset)).limit(int(limit) + 1))
    rows = list(cursor)
    has_more = len(rows) > limit
    rows = rows[:limit]

    wo_numbers = {}
    out = []
    for p in rows:
        wo_id = p.get("work_order_id")
        if wo_id and wo_id not in wo_numbers:
            wo = shop_db.work_orders.find_one({"_id": wo_id}, {"wo_number": 1}) or {}
            wo_numbers[wo_id] = str(wo.get("wo_number") or "")
        out.append({
            "id": str(p["_id"]),
            "wo_id": str(wo_id) if wo_id else "",
            "wo_number": wo_numbers.get(wo_id, ""),
            "amount": round(float(p.get("amount") or 0), 2),
            "method": str(p.get("payment_method") or "").replace("_", " ").title(),
            "date_label": _format_date(p.get("payment_date") or p.get("created_at")),
            "notes": str(p.get("notes") or "").strip(),
        })
    return out, has_more


def _list_customer_authorizations(shop_db, customer_id, unit_oid=None,
                                  limit=PORTAL_PAGE_SIZE, offset=0):
    """Aggregate authorization history across all WOs for this customer."""
    q = {"customer_id": customer_id, "is_active": True,
         "authorizations": {"$exists": True, "$ne": []}}
    if unit_oid:
        q["unit_id"] = unit_oid

    rows = list(shop_db.work_orders.find(
        q, {"wo_number": 1, "authorizations": 1, "_id": 1},
    ))
    out = []
    for wo in rows:
        for rec in wo.get("authorizations") or []:
            out.append({
                "wo_id": str(wo["_id"]),
                "wo_number": str(wo.get("wo_number") or ""),
                "scope": rec.get("scope") or "work_order",
                "labor_index": rec.get("labor_index"),
                "status": rec.get("status") or "pending",
                "responded_at": _format_date(rec.get("responded_at")),
                "comment": str(rec.get("response_comment") or "").strip(),
            })
    out.sort(key=lambda x: x["responded_at"] or "", reverse=True)
    total = len(out)
    page = out[int(offset):int(offset) + int(limit)]
    has_more = (int(offset) + len(page)) < total
    return page, has_more


# ──────────────────────── public portal routes ────────────────────────

@customer_portal_bp.get("/portal/<token>")
def dashboard(token):
    doc, shop_db, shop, customer, err = _resolve_portal_token(token)
    if err or not customer:
        return render_template(
            "public/customer_portal/error.html",
            message=err or "Link unavailable.",
        ), 404

    _touch_token(doc)
    cid = customer["_id"]
    # Lightweight initial render: only customer info + tab shell.
    # Each tab loads its own fragment via fetch on first activation.
    return render_template(
        "public/customer_portal/dashboard.html",
        token=token,
        brand=_shop_brand(shop),
        customer=_customer_display(customer),
    )


# ---- lazy tab fragments (HTML) ----

def _portal_fragment_or_404(token):
    doc, shop_db, shop, customer, err = _resolve_portal_token(token)
    if err or not customer:
        abort(404)
    _touch_token(doc)
    return doc, shop_db, shop, customer


def _parse_paging():
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    return offset, PORTAL_PAGE_SIZE


def _parse_unit_filter():
    return _oid(request.args.get("unit_id"))


@customer_portal_bp.get("/portal/<token>/tab/units")
def tab_units(token):
    _doc, shop_db, _shop, customer = _portal_fragment_or_404(token)
    units = _list_customer_units(shop_db, customer["_id"])
    return render_template(
        "public/customer_portal/_tab_units.html", units=units,
    )


@customer_portal_bp.get("/portal/<token>/tab/work-orders")
def tab_work_orders(token):
    _doc, shop_db, _shop, customer = _portal_fragment_or_404(token)
    offset, limit = _parse_paging()
    unit_oid = _parse_unit_filter()
    work_orders, has_more = _list_customer_work_orders(
        shop_db, customer["_id"], unit_oid=unit_oid,
        limit=limit, offset=offset,
    )
    unit_label_filter = _unit_label_for(shop_db, unit_oid) if unit_oid else ""
    return render_template(
        "public/customer_portal/_tab_work_orders.html",
        work_orders=work_orders, token=token,
        offset=offset, page_size=limit, has_more=has_more,
        unit_id=request.args.get("unit_id") or "",
        unit_label_filter=unit_label_filter,
        is_append=offset > 0,
    )


@customer_portal_bp.get("/portal/<token>/tab/payments")
def tab_payments(token):
    _doc, shop_db, _shop, customer = _portal_fragment_or_404(token)
    offset, limit = _parse_paging()
    unit_oid = _parse_unit_filter()
    payments, has_more = _list_customer_payments(
        shop_db, customer["_id"], unit_oid=unit_oid,
        limit=limit, offset=offset,
    )
    unit_label_filter = _unit_label_for(shop_db, unit_oid) if unit_oid else ""
    return render_template(
        "public/customer_portal/_tab_payments.html",
        payments=payments,
        offset=offset, page_size=limit, has_more=has_more,
        unit_id=request.args.get("unit_id") or "",
        unit_label_filter=unit_label_filter,
        is_append=offset > 0,
    )


@customer_portal_bp.get("/portal/<token>/tab/authorizations")
def tab_authorizations(token):
    _doc, shop_db, _shop, customer = _portal_fragment_or_404(token)
    offset, limit = _parse_paging()
    unit_oid = _parse_unit_filter()
    authorizations, has_more = _list_customer_authorizations(
        shop_db, customer["_id"], unit_oid=unit_oid,
        limit=limit, offset=offset,
    )
    unit_label_filter = _unit_label_for(shop_db, unit_oid) if unit_oid else ""
    return render_template(
        "public/customer_portal/_tab_authorizations.html",
        authorizations=authorizations,
        offset=offset, page_size=limit, has_more=has_more,
        unit_id=request.args.get("unit_id") or "",
        unit_label_filter=unit_label_filter,
        is_append=offset > 0,
    )


def _unit_label_for(shop_db, unit_oid):
    from app.blueprints.work_orders.routes import unit_label
    if not unit_oid:
        return ""
    u = shop_db.units.find_one({"_id": unit_oid})
    return unit_label(u) if u else ""


# ──────────────────────── Maintenance Files (quarterly) ────────────────────────

QUARTER_MONTHS = {
    1: [1, 2, 3],
    2: [4, 5, 6],
    3: [7, 8, 9],
    4: [10, 11, 12],
}


def _quarter_range(year: int, quarter: int):
    from calendar import monthrange
    months = QUARTER_MONTHS.get(int(quarter)) or QUARTER_MONTHS[1]
    start = datetime(int(year), months[0], 1)
    last_day = monthrange(int(year), months[-1])[1]
    end = datetime(int(year), months[-1], last_day, 23, 59, 59)
    return start, end


def _maintenance_rows(shop_db, customer_id, unit_oid, year, quarter):
    """Return WO rows for the given unit + quarter as maintenance entries."""
    from app.blueprints.work_orders.routes import _work_order_grand_total
    start, end = _quarter_range(year, quarter)
    cursor = shop_db.work_orders.find({
        "customer_id": customer_id,
        "unit_id": unit_oid,
        "is_active": True,
        "work_order_date": {"$gte": start, "$lte": end},
    }).sort("work_order_date", 1)

    rows = []
    total = 0.0
    for wo in cursor:
        # Description = "Work Order #N, labor1, labor2, ..."
        descs = []
        for block in (wo.get("labors") or []):
            d = str(block.get("description")
                    or block.get("labor_desc")
                    or block.get("name") or "").strip()
            if d:
                descs.append(d)
        wo_num = str(wo.get("wo_number") or "").strip()
        prefix = f"Work Order #{wo_num}" if wo_num else "Work Order"
        description = (prefix + ", " + ", ".join(descs)) if descs else prefix
        cost = float(_work_order_grand_total(wo) or 0)
        total += cost
        rows.append({
            "date": wo.get("work_order_date") or wo.get("created_at"),
            "date_label": _format_date(wo.get("work_order_date")
                                       or wo.get("created_at")),
            "description": description,
            "wo_number": wo_num,
            "cost": round(cost, 2),
        })
    return rows, round(total, 2)


def _maintenance_unit_view(unit):
    """Vehicle identification block — only Make/Model/Year/VIN filled."""
    return {
        "make": str(unit.get("make") or "").strip(),
        "model": str(unit.get("model") or "").strip(),
        "year": str(unit.get("year") or "").strip(),
        "vin": str(unit.get("vin") or "").strip(),
        "unit_number": str(unit.get("unit_number") or "").strip(),
    }


@customer_portal_bp.get("/portal/<token>/tab/maintenance")
def tab_maintenance(token):
    _doc, shop_db, _shop, customer = _portal_fragment_or_404(token)
    units = _list_customer_units(shop_db, customer["_id"])
    current_year = datetime.utcnow().year
    years = list(range(current_year - 4, current_year + 1))
    return render_template(
        "public/customer_portal/_tab_maintenance.html",
        units=units, years=years, current_year=current_year, token=token,
    )


@customer_portal_bp.get("/portal/<token>/maintenance/<unit_id>/pdf")
def maintenance_pdf(token, unit_id):
    doc, shop_db, shop, customer, err = _resolve_portal_token(token)
    if err or not customer:
        abort(404)
    unit_oid = _oid(unit_id)
    if not unit_oid:
        abort(404)
    unit = shop_db.units.find_one({
        "_id": unit_oid, "customer_id": customer["_id"], "is_active": True,
    })
    if not unit:
        abort(404)

    try:
        year = int(request.args.get("year") or datetime.utcnow().year)
        quarter = int(request.args.get("quarter") or 1)
    except (TypeError, ValueError):
        abort(400)
    if quarter not in (1, 2, 3, 4):
        abort(400)

    _touch_token(doc)

    rows, total = _maintenance_rows(
        shop_db, customer["_id"], unit_oid, year, quarter,
    )
    cust = _customer_display(customer)

    # Quarterly grid: 5 years (current_year-4 .. current_year), all 12 months.
    current_year = datetime.utcnow().year
    grid_years = list(range(current_year - 4, current_year + 1))
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    selected_months = set(QUARTER_MONTHS[quarter])

    html = render_template(
        "public/customer_portal/maintenance_pdf.html",
        company_name=cust["label"],
        shop_name=str(shop.get("name") or "").strip(),
        unit=_maintenance_unit_view(unit),
        year=year, quarter=quarter,
        grid_years=grid_years, months=months,
        selected_months=selected_months, selected_year=year,
        rows=rows, total=total,
    )
    pdf = render_html_to_pdf(html)
    if not pdf:
        abort(500)
    fname = (f"Maintenance-{(unit.get('unit_number') or unit_id)}-"
             f"Q{quarter}-{year}.pdf")
    return send_file(
        io.BytesIO(pdf), mimetype="application/pdf",
        as_attachment=True, download_name=fname,
    )


@customer_portal_bp.get("/portal/<token>/work-orders/<wo_id>")
def work_order_view(token, wo_id):
    from app.blueprints.work_orders.routes import (
        _build_wo_pdf_context,
        _sum_active_work_order_payments,
        _build_work_order_payment_summary,
    )
    doc, shop_db, shop, customer, err = _resolve_portal_token(token)
    if err or not customer:
        return render_template(
            "public/customer_portal/error.html",
            message=err or "Link unavailable.",
        ), 404

    wo_oid = _oid(wo_id)
    wo = shop_db.work_orders.find_one({
        "_id": wo_oid, "is_active": True, "customer_id": customer["_id"],
    })
    if not wo:
        return render_template(
            "public/customer_portal/error.html",
            message="Work order not found for this customer.",
        ), 404

    _touch_token(doc)

    ctx = _build_wo_pdf_context(shop_db, shop, wo)
    paid_total = _sum_active_work_order_payments(shop_db, wo["_id"])
    summary = _build_work_order_payment_summary(wo, paid_total)

    payments = list(shop_db.work_order_payments.find(
        {"work_order_id": wo["_id"], "is_active": True}
    ).sort("payment_date", -1))
    payments_view = [{
        "id": str(p["_id"]),
        "amount": round(float(p.get("amount") or 0), 2),
        "method": str(p.get("payment_method") or "").replace("_", " ").title(),
        "date_label": _format_date(p.get("payment_date") or p.get("created_at")),
        "notes": str(p.get("notes") or "").strip(),
    } for p in payments]

    # Attachments (WO + labor scope) — list with download links via portal.
    att_docs = list(shop_db.attachments.find(
        {"entity_type": {"$in": ["work_order", "work_order_labor"]},
         "entity_id": wo["_id"]},
        {"data": 0},
    ).sort("uploaded_at", 1))
    attachments_list = [{
        "id": str(a["_id"]),
        "filename": a.get("filename") or "attachment",
        "content_type": a.get("content_type") or "",
        "size_kb": int(round((a.get("size") or 0) / 1024)),
        "is_image": str(a.get("content_type") or "").startswith("image/"),
        "labor_index": (None if a.get("entity_type") == "work_order"
                        else (a.get("parent_id"))),
        "scope": a.get("entity_type") or "work_order",
    } for a in att_docs]

    return render_template(
        "public/customer_portal/work_order.html",
        token=token,
        wo_id=str(wo["_id"]),
        brand=_shop_brand(shop),
        customer=_customer_display(customer),
        ctx=ctx,
        wo_status=str(wo.get("status") or "open"),
        payments=payments_view,
        payment_summary=summary,
        attachments_list=attachments_list,
        authorizations=list(wo.get("authorizations") or []),
    )


@customer_portal_bp.get("/portal/<token>/work-orders/<wo_id>/pdf")
def work_order_pdf(token, wo_id):
    from app.blueprints.work_orders.routes import _build_wo_pdf_context
    doc, shop_db, shop, customer, err = _resolve_portal_token(token)
    if err or not customer:
        abort(404)

    wo = shop_db.work_orders.find_one({
        "_id": _oid(wo_id), "is_active": True, "customer_id": customer["_id"],
    })
    if not wo:
        abort(404)

    _touch_token(doc)
    ctx = _build_wo_pdf_context(shop_db, shop, wo)
    pdf_html = render_template("emails/work_order_pdf.html", **ctx)
    pdf_bytes = render_html_to_pdf(pdf_html)
    if not pdf_bytes:
        abort(500)
    filename = f"WorkOrder-{ctx.get('wo_number') or wo_id}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@customer_portal_bp.get("/portal/<token>/attachments/<attachment_id>")
def attachment_download(token, attachment_id):
    doc, shop_db, shop, customer, err = _resolve_portal_token(token)
    if err or not customer:
        abort(404)

    att_oid = _oid(attachment_id)
    if not att_oid:
        abort(404)

    att = shop_db.attachments.find_one({"_id": att_oid})
    if not att:
        abort(404)

    # Authorize: attachment must belong to a WO/unit owned by this customer.
    et = att.get("entity_type") or ""
    eid = att.get("entity_id")
    allowed = False
    if et in ("work_order", "work_order_labor"):
        wo = shop_db.work_orders.find_one(
            {"_id": eid, "customer_id": customer["_id"], "is_active": True},
            {"_id": 1},
        )
        allowed = bool(wo)
    elif et == "unit":
        unit = shop_db.units.find_one(
            {"_id": eid, "customer_id": customer["_id"], "is_active": True},
            {"_id": 1},
        )
        allowed = bool(unit)
    elif et == "customer":
        allowed = (eid == customer["_id"])

    if not allowed:
        abort(404)

    _touch_token(doc)
    raw = bytes(att.get("data") or b"")
    return send_file(
        io.BytesIO(raw),
        mimetype=att.get("content_type") or "application/octet-stream",
        as_attachment=True,
        download_name=att.get("filename") or "attachment",
    )


# ──────────────────────── send-link (staff API) ────────────────────────

@customer_portal_bp.post("/portal/api/customers/<customer_id>/send-link")
@login_required
@permission_required("customers.update")
def api_send_portal_link(customer_id):
    from app.blueprints.work_orders.routes import get_shop_db, customer_label
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    cid = _oid(customer_id)
    if not cid:
        return jsonify({"ok": False, "error": "Invalid customer ID"}), 400

    customer = shop_db.customers.find_one({"_id": cid, "is_active": True})
    if not customer:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    data = request.get_json(silent=True) or {}
    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails
                 if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False,
                        "error": "At least one valid email address required"}), 400

    token_doc = get_or_create_portal_token(shop, cid)
    portal_url = _customer_portal_url(token_doc["token"])
    expires_label = _format_date(token_doc.get("expires_at"))

    brand = _shop_brand(shop)
    cust_name = customer_label(customer)
    user_email = (g.user.get("email") or "").strip() if g.user else ""

    html_body = render_template(
        "emails/customer_portal_link.html",
        portal_url=portal_url,
        expires_label=expires_label,
        cust_name=cust_name,
        **brand,
    )
    subject = (
        f"Your customer portal — {brand['shop_name']}"
        if brand["shop_name"] else "Your customer portal link"
    )

    try:
        send_email(to_emails, subject, html_body,
                   reply_to=user_email or None)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "sent_to": to_emails,
                    "portal_url": portal_url,
                    "expires_at": expires_label}), 200
