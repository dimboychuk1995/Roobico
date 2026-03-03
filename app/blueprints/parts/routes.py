from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from flask import request, redirect, url_for, flash, session, jsonify

from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.pagination import get_pagination_params, paginate_find
from app.utils.parts_search import build_parts_search_terms, build_query_tokens, part_matches_query
from app.utils.permissions import permission_required

from . import parts_bp


def utcnow():
    return datetime.now(timezone.utc)


def _oid(value) -> Optional[ObjectId]:
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _tenant_id_variants():
    """
    Нужна из-за того, что tenant_id исторически мог быть то строкой, то ObjectId.
    """
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


def _parts_collections():
    """
    Все коллекции живут в SHOP DB:
      - parts
      - vendors
      - parts_categories
      - parts_locations
      - parts_orders
    """
    master = get_master_db()
    db, shop = _get_shop_db(master)
    if db is None:
        return None, None, None, None, None, shop, master

    return (
        db.parts,
        db.vendors,
        db.parts_categories,
        db.parts_locations,
        db.parts_orders,
        shop,
        master,
    )

def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _validate_ref(coll, ref_id_raw: str, label: str):
    """
    Универсальная валидация ссылок на документы (vendor/category/location) в SHOP DB.
    Если ref_id_raw пустой — вернём (None, None) без ошибки.
    """
    ref_id_raw = (ref_id_raw or "").strip()
    if not ref_id_raw:
        return None, None

    ref_oid = _oid(ref_id_raw)
    if not ref_oid:
        return None, f"Invalid {label} id."

    doc = coll.find_one({"_id": ref_oid})
    if not doc:
        return None, f"{label} not found."

    if doc.get("is_active") is False:
        return None, f"{label} is inactive."

    return ref_oid, None


def _name_from_doc(doc: dict) -> str:
    """
    Универсально вытаскиваем отображаемое имя.
    Подходит и для vendors, и для категорий/локаций.
    """
    return (
        doc.get("name")
        or doc.get("company_name")
        or doc.get("title")
        or doc.get("label")
        or ""
    ).strip()


def _fmt_dt_iso(dt) -> str:
    if isinstance(dt, datetime):
        try:
            return dt.astimezone().isoformat()
        except Exception:
            return dt.isoformat()
    return ""


def _get_next_order_number(shop_db, shop_id):
    """
    Get next parts order number using atomic counter.
    Returns integer starting from 1000.
    """
    from pymongo import ReturnDocument
    
    result = shop_db.counters.find_one_and_update(
        {"_id": f"order_number_{shop_id}"},
        {
            "$inc": {"seq": 1},
            "$setOnInsert": {"initial_value": 1000}
        },
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    
    seq = result.get("seq", 1)
    initial = result.get("initial_value", 1000)
    
    return initial + seq - 1


@parts_bp.get("/")
@login_required
@permission_required("parts.view")
def parts_page():
    """
    Показать те запчасти что у нас есть:
      - is_active=True
      - in_stock > 0

    Также сразу тянем справочники для селектов в модалке:
      vendors / categories / locations (active only)

    + last_order_id из session (чтобы показать кнопку Received)
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("main.dashboard"))

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    # 1) Parts in stock
    parts_in_stock, pagination = paginate_find(
        parts_coll,
        {"is_active": True, "in_stock": {"$gt": 0}},
        [("part_number", 1), ("description", 1), ("created_at", -1)],
        page,
        per_page,
    )

    # 2) Reference lists for modal selects
    vendors = []
    categories = []
    locations = []

    if vendors_coll is not None:
        vendors = list(
            vendors_coll.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("company_name", 1), ("created_at", -1)])
        )

    if cats_coll is not None:
        categories = list(
            cats_coll.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("created_at", -1)])
        )

    if locs_coll is not None:
        locations = list(
            locs_coll.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("created_at", -1)])
        )

    # 3) Make lookup maps for showing names in the table
    vendor_map = {v["_id"]: _name_from_doc(v) for v in vendors if v.get("_id")}
    category_map = {c["_id"]: _name_from_doc(c) for c in categories if c.get("_id")}
    location_map = {l["_id"]: _name_from_doc(l) for l in locations if l.get("_id")}

    for p in parts_in_stock:
        vid = p.get("vendor_id")
        cid = p.get("category_id")
        lid = p.get("location_id")

        if vid:
            p["vendor_name"] = vendor_map.get(vid) or ""
        if cid:
            p["category_name"] = category_map.get(cid) or ""
        if lid:
            p["location_name"] = location_map.get(lid) or ""

    last_order_id = session.get("last_parts_order_id")

    return _render_app_page(
        "public/parts.html",
        active_page="parts",
        parts=parts_in_stock,
        pagination=pagination,
        vendors=vendors,
        categories=categories,
        locations=locations,
        last_order_id=last_order_id,
    )



@parts_bp.post("/create")
@login_required
@permission_required("parts.edit")
def parts_create():
    """
    Добавление запчасти:
      part_number (required)
      description
      reference
      vendor_id (из vendors в этом shop DB)
      category_id (из parts_categories в этом shop DB)
      location_id (из parts_locations в этом shop DB)
      in_stock (int)
      average_cost (float)
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page"))

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    part_number = (request.form.get("part_number") or "").strip()
    description = (request.form.get("description") or "").strip()
    reference = (request.form.get("reference") or "").strip()

    vendor_id_raw = (request.form.get("vendor_id") or "").strip()
    category_id_raw = (request.form.get("category_id") or "").strip()
    location_id_raw = (request.form.get("location_id") or "").strip()

    in_stock_raw = (request.form.get("in_stock") or "").strip()
    avg_cost_raw = (request.form.get("average_cost") or "").strip()
    core_has_charge_raw = (request.form.get("core_has_charge") or "").strip()
    core_cost_raw = (request.form.get("core_cost") or "").strip()
    misc_has_charge_raw = (request.form.get("misc_has_charge") or "").strip()

    if not part_number:
        flash("Part number is required.", "error")
        return redirect(url_for("parts.parts_page"))

    in_stock = _parse_int(in_stock_raw, default=0)
    if in_stock < 0:
        flash("In stock cannot be negative.", "error")
        return redirect(url_for("parts.parts_page"))

    average_cost = _parse_float(avg_cost_raw, default=0.0)
    if average_cost < 0:
        flash("Average cost cannot be negative.", "error")
        return redirect(url_for("parts.parts_page"))

    core_has_charge = core_has_charge_raw == "1"
    core_cost = _parse_float(core_cost_raw, default=0.0)
    if core_has_charge and core_cost < 0:
        flash("Core cost cannot be negative.", "error")
        return redirect(url_for("parts.parts_page"))

    misc_has_charge = misc_has_charge_raw == "1"
    misc_charges = []
    if misc_has_charge:
        import re

        misc_re = re.compile(r"^misc_charges\[(\d+)\]\[(description|price)\]$")
        misc_map: dict[int, dict] = {}

        for key, val in request.form.items():
            m = misc_re.match(key)
            if not m:
                continue

            idx = int(m.group(1))
            field = m.group(2)
            item = misc_map.setdefault(idx, {})

            if field == "description":
                item["description"] = (val or "").strip()
            elif field == "price":
                item["price"] = (val or "").strip()

        for idx in sorted(misc_map.keys()):
            item = misc_map[idx]
            desc = (item.get("description") or "").strip()
            price_raw = (item.get("price") or "").strip()
            if not (desc or price_raw):
                continue

            price = _parse_float(price_raw, default=0.0)
            if price < 0:
                flash("Misc charge price cannot be negative.", "error")
                return redirect(url_for("parts.parts_page"))

            misc_charges.append({
                "description": desc,
                "price": float(price),
            })

    # ✅ PyMongo Collection нельзя проверять через bool()
    vendor_oid, err = (
        _validate_ref(vendors_coll, vendor_id_raw, "Vendor")
        if vendors_coll is not None
        else (None, None)
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page"))

    category_oid, err = (
        _validate_ref(cats_coll, category_id_raw, "Category")
        if cats_coll is not None
        else (None, None)
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page"))

    location_oid, err = (
        _validate_ref(locs_coll, location_id_raw, "Location")
        if locs_coll is not None
        else (None, None)
    )
    if err:
        flash(err, "error")
        return redirect(url_for("parts.parts_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    doc = {
        "part_number": part_number,
        "description": description or None,
        "reference": reference or None,
        "search_terms": build_parts_search_terms(part_number, description, reference),

        "vendor_id": vendor_oid,
        "category_id": category_oid,
        "location_id": location_oid,

        "in_stock": in_stock,
        "average_cost": float(average_cost),
        "core_has_charge": bool(core_has_charge),
        "core_cost": float(core_cost) if core_has_charge else None,
        "misc_has_charge": bool(misc_has_charge),
        "misc_charges": misc_charges if misc_has_charge else [],

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

    parts_coll.insert_one(doc)

    flash("Part created successfully.", "success")
    return redirect(url_for("parts.parts_page"))


@parts_bp.get("/api/search")
@login_required
def parts_api_search():
    """
    Search parts for dropdown/autocomplete.
    Query params:
      q: search string
      limit: optional (default 20, max 50)

    Returns: { ok: true, items: [{id, part_number, description, average_cost, vendor_id}] }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return {"ok": False, "error": "Shop database not configured."}, 400

    q = (request.args.get("q") or "").strip()
    limit = _parse_int(request.args.get("limit") or "20", default=20)
    if limit <= 0:
        limit = 20
    if limit > 50:
        limit = 50

    # Если пустой запрос — не грузим базу
    if not q:
        return {"ok": True, "items": []}

    import re

    normalized_query, query_tokens = build_query_tokens(q)
    if not normalized_query:
        return {"ok": True, "items": []}

    query_filter = {
        "shop_id": shop["_id"],
        "is_active": True,
    }
    if len(query_tokens) <= 1:
        query_filter["search_terms"] = normalized_query
    else:
        query_filter["search_terms"] = {"$all": query_tokens}

    projection = {
        "part_number": 1,
        "description": 1,
        "reference": 1,
        "average_cost": 1,
        "vendor_id": 1,
    }

    fetch_limit = min(300, max(50, limit * 6))
    cursor = (
        parts_coll.find(query_filter, projection)
        .sort([("part_number", 1)])
        .limit(fetch_limit)
    )

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

        items.append({
            "id": str(p["_id"]),
            "part_number": p.get("part_number") or "",
            "description": p.get("description") or "",
            "average_cost": float(p.get("average_cost") or 0.0),
            "vendor_id": str(p["vendor_id"]) if p.get("vendor_id") else "",
        })

        if len(items) >= limit:
            break

    if len(items) < limit:
        contains = re.escape(q)
        fallback_filter = {
            "shop_id": shop["_id"],
            "is_active": True,
            "search_terms": {"$exists": False},
            "$or": [
                {"part_number": {"$regex": contains, "$options": "i"}},
                {"description": {"$regex": contains, "$options": "i"}},
                {"reference": {"$regex": contains, "$options": "i"}},
            ],
        }

        fallback_cursor = (
            parts_coll.find(fallback_filter, projection)
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

            items.append({
                "id": str(p["_id"]),
                "part_number": p.get("part_number") or "",
                "description": p.get("description") or "",
                "average_cost": float(p.get("average_cost") or 0.0),
                "vendor_id": str(p["vendor_id"]) if p.get("vendor_id") else "",
            })

            if len(items) >= limit:
                break

    return {"ok": True, "items": items}


@parts_bp.post("/api/orders/create")
@login_required
@permission_required("parts.edit")
def parts_api_orders_create():
    """
    AJAX create order.
    Accepts JSON:
      {
        "vendor_id": "<oid str>",
        "items": [
          {"part_id":"<oid str>", "quantity": 2, "price": 12.34},
          ...
        ]
      }

    Returns JSON:
      { "ok": true, "order_id": "<oid str>", "items_count": N }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or vendors_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    tenant_oid = _oid(session.get(SESSION_TENANT_ID))
    if not tenant_oid:
        session.clear()
        return jsonify({"ok": False, "error": "Tenant session missing. Please login again."}), 401

    data = request.get_json(silent=True) or {}

    vendor_id_raw = (data.get("vendor_id") or "").strip()
    if not vendor_id_raw:
        return jsonify({"ok": False, "error": "Vendor is required."}), 400

    vendor_oid, err = _validate_ref(vendors_coll, vendor_id_raw, "Vendor")
    if err:
        return jsonify({"ok": False, "error": err}), 400

    items_in = data.get("items") or []
    if not isinstance(items_in, list) or len(items_in) == 0:
        return jsonify({"ok": False, "error": "Add at least one item."}), 400

    items = []
    for it in items_in:
        pid = _oid((it.get("part_id") or "").strip())
        if not pid:
            continue

        qty = _parse_int(it.get("quantity"), default=0)
        price = _parse_float(it.get("price"), default=-1.0)

        if qty <= 0:
            continue
        if price < 0:
            return jsonify({"ok": False, "error": "Price cannot be negative."}), 400

        part = parts_coll.find_one({"_id": pid, "is_active": True})
        if not part:
            continue

        items.append({
            "part_id": pid,
            "part_number": part.get("part_number"),
            "description": part.get("description"),
            "price": float(price),
            "quantity": int(qty),
        })

    if not items:
        return jsonify({"ok": False, "error": "No valid items in order."}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    # Get next order number
    shop_db = orders_coll.database
    order_number = _get_next_order_number(shop_db, shop["_id"])

    order_doc = {
        "vendor_id": vendor_oid,
        "order_number": order_number,
        "items": items,
        "status": "ordered",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_oid,
        "updated_by": user_oid,
        "shop_id": shop["_id"],
        "tenant_id": tenant_oid,
    }

    res = orders_coll.insert_one(order_doc)

    return jsonify({"ok": True, "order_id": str(res.inserted_id), "items_count": len(items)})

def _recalc_weighted_avg(old_qty: int, old_avg: float, add_qty: int, add_price: float) -> float:
    """
    Weighted average:
      (old_avg * old_qty + add_price * add_qty) / (old_qty + add_qty)

    Safeguards for 0 qty.
    """
    old_qty = int(old_qty or 0)
    add_qty = int(add_qty or 0)
    old_avg = float(old_avg or 0.0)
    add_price = float(add_price or 0.0)

    denom = old_qty + add_qty
    if denom <= 0:
        return 0.0

    return (old_avg * old_qty + add_price * add_qty) / denom


@parts_bp.post("/api/orders/<order_id>/receive")
@login_required
@permission_required("parts.edit")
def parts_api_orders_receive(order_id: str):
    """
    AJAX receive order (full receive).
    Returns JSON { ok: true, updated_parts: N }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop database not configured for this shop."}), 400

    oid = _oid(order_id)
    if not oid:
        return jsonify({"ok": False, "error": "Invalid order id."}), 400

    order = orders_coll.find_one({"_id": oid})
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404

    if order.get("is_active") is False:
        return jsonify({"ok": False, "error": "Order is inactive."}), 400

    if order.get("status") == "received":
        return jsonify({"ok": True, "updated_parts": 0, "message": "Order already received."})

    items = order.get("items") or []
    if not isinstance(items, list) or len(items) == 0:
        return jsonify({"ok": False, "error": "Order has no items."}), 400

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    updated = 0
    for it in items:
        pid = it.get("part_id")
        if not pid:
            continue

        recv_qty = int(it.get("quantity") or 0)
        recv_price = float(it.get("price") or 0.0)

        if recv_qty <= 0:
            continue
        if recv_price < 0:
            return jsonify({"ok": False, "error": "Order contains negative price."}), 400

        part = parts_coll.find_one({"_id": pid})
        if not part or part.get("is_active") is False:
            continue

        old_qty = int(part.get("in_stock") or 0)
        old_avg = float(part.get("average_cost") or 0.0)

        new_avg = _recalc_weighted_avg(old_qty, old_avg, recv_qty, recv_price)
        new_qty = old_qty + recv_qty

        parts_coll.update_one(
            {"_id": pid},
            {"$set": {
                "in_stock": int(new_qty),
                "average_cost": float(new_avg),
                "updated_at": now,
                "updated_by": user_oid,
            }},
        )

        updated += 1

    orders_coll.update_one(
        {"_id": oid},
        {"$set": {
            "status": "received",
            "received_at": now,
            "received_by": user_oid,
            "updated_at": now,
            "updated_by": user_oid,
        }},
    )

    return jsonify({"ok": True, "updated_parts": updated})


@parts_bp.get("/api/<part_id>")
@login_required
@permission_required("parts.view")
def parts_api_get(part_id: str):
    """
    AJAX get part data for edit modal.
    Returns JSON with full part info.
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    pid = _oid(part_id)
    if not pid:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    part = parts_coll.find_one({"_id": pid})
    if not part:
        return jsonify({"ok": False, "error": "Part not found"}), 404

    # Convert ObjectIds to strings for JSON
    return jsonify({
        "ok": True,
        "item": {
            "_id": str(part["_id"]),
            "part_number": part.get("part_number") or "",
            "description": part.get("description") or "",
            "reference": part.get("reference") or "",
            "vendor_id": str(part["vendor_id"]) if part.get("vendor_id") else "",
            "category_id": str(part["category_id"]) if part.get("category_id") else "",
            "location_id": str(part["location_id"]) if part.get("location_id") else "",
            "in_stock": int(part.get("in_stock") or 0),
            "average_cost": float(part.get("average_cost") or 0.0),
            "core_has_charge": bool(part.get("core_has_charge", False)),
            "core_cost": float(part.get("core_cost") or 0.0),
            "misc_has_charge": bool(part.get("misc_has_charge", False)),
            "misc_charges": part.get("misc_charges") or [],
        }
    })


@parts_bp.get("/api/<part_id>/history")
@login_required
@permission_required("parts.view")
def parts_api_history(part_id: str):
    """
    Return part usage history from:
      - parts_orders (ordered/received)
      - work_orders (used in jobs)
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or orders_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    pid = _oid(part_id)
    if not pid:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    part = parts_coll.find_one({"_id": pid, "shop_id": shop["_id"]})
    if not part:
        return jsonify({"ok": False, "error": "Part not found"}), 404

    part_number = str(part.get("part_number") or "").strip()

    # Orders history
    orders_rows = list(
        orders_coll.find(
            {
                "shop_id": shop["_id"],
                "is_active": {"$ne": False},
                "items.part_id": pid,
            }
        ).sort([("created_at", -1)]).limit(200)
    )

    vendor_map = {}
    if vendors_coll is not None:
        vendor_ids = [r.get("vendor_id") for r in orders_rows if r.get("vendor_id")]
        if vendor_ids:
            for v in vendors_coll.find({"_id": {"$in": vendor_ids}}):
                vendor_map[v.get("_id")] = _name_from_doc(v)

    orders_out = []
    for row in orders_rows:
        qty = 0
        latest_price = None
        for it in (row.get("items") or []):
            if not isinstance(it, dict):
                continue
            if it.get("part_id") != pid:
                continue
            item_qty = _parse_int(it.get("quantity"), default=0)
            qty += max(0, item_qty)
            latest_price = _parse_float(it.get("price"), default=0.0)

        if qty <= 0:
            continue

        orders_out.append({
            "order_id": str(row.get("_id")),
            "order_number": row.get("order_number"),
            "status": str(row.get("status") or ""),
            "vendor": vendor_map.get(row.get("vendor_id")) or "-",
            "quantity": qty,
            "price": float(latest_price or 0.0),
            "created_at": _fmt_dt_iso(row.get("created_at")),
            "received_at": _fmt_dt_iso(row.get("received_at")),
        })

    # Work order history (prefer part_id match, fallback by part_number for legacy docs)
    db, _ = _get_shop_db(master)
    if db is None:
        return jsonify({"ok": True, "part": {"id": str(pid), "part_number": part_number}, "orders": orders_out, "work_orders": []})

    work_orders_coll = db.work_orders
    wo_filter = {
        "shop_id": shop["_id"],
        "is_active": {"$ne": False},
        "$or": [
            {"labors.parts.part_id": pid},
            {"labors.parts.part_number": part_number},
        ],
    }
    wo_rows = list(work_orders_coll.find(wo_filter).sort([("created_at", -1)]).limit(300))

    customer_ids = [w.get("customer_id") for w in wo_rows if w.get("customer_id")]
    unit_ids = [w.get("unit_id") for w in wo_rows if w.get("unit_id")]

    customers_map = {}
    if customer_ids:
        for c in db.customers.find({"_id": {"$in": customer_ids}}):
            company = str(c.get("company_name") or "").strip()
            full = (str(c.get("first_name") or "").strip() + " " + str(c.get("last_name") or "").strip()).strip()
            customers_map[c.get("_id")] = company or full or "-"

    units_map = {}
    if unit_ids:
        for u in db.units.find({"_id": {"$in": unit_ids}}):
            bits = []
            if u.get("unit_number"):
                bits.append(str(u.get("unit_number")))
            if u.get("year"):
                bits.append(str(u.get("year")))
            if u.get("make"):
                bits.append(str(u.get("make")))
            if u.get("model"):
                bits.append(str(u.get("model")))
            units_map[u.get("_id")] = " ".join([x for x in bits if x]).strip() or "-"

    work_orders_out = []
    for w in wo_rows:
        used_qty = 0
        for labor in (w.get("labors") or []):
            if not isinstance(labor, dict):
                continue
            for p in (labor.get("parts") or []):
                if not isinstance(p, dict):
                    continue
                p_pid = p.get("part_id")
                p_num = str(p.get("part_number") or "").strip()
                if p_pid == pid or (not p_pid and part_number and p_num == part_number):
                    used_qty += max(0, _parse_int(p.get("qty"), default=0))

        if used_qty <= 0:
            continue

        totals = w.get("totals") if isinstance(w.get("totals"), dict) else {}
        grand_total = totals.get("grand_total") if totals.get("grand_total") is not None else w.get("grand_total")

        work_orders_out.append({
            "work_order_id": str(w.get("_id")),
            "wo_number": w.get("wo_number"),
            "status": str(w.get("status") or "open"),
            "customer": customers_map.get(w.get("customer_id")) or "-",
            "unit": units_map.get(w.get("unit_id")) or "-",
            "used_qty": used_qty,
            "grand_total": float(_parse_float(grand_total, default=0.0)),
            "created_at": _fmt_dt_iso(w.get("created_at")),
        })

    return jsonify({
        "ok": True,
        "part": {
            "id": str(pid),
            "part_number": part_number,
            "description": str(part.get("description") or ""),
        },
        "orders": orders_out,
        "work_orders": work_orders_out,
    })


@parts_bp.post("/api/<part_id>/update")
@login_required
@permission_required("parts.edit")
def parts_api_update(part_id: str):
    """
    AJAX update part.
    Accepts JSON with part data.
    Returns JSON { ok: true/false, ... }
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        return jsonify({"ok": False, "error": "Shop not configured"}), 400

    pid = _oid(part_id)
    if not pid:
        return jsonify({"ok": False, "error": "Invalid part id"}), 400

    part = parts_coll.find_one({"_id": pid})
    if not part:
        return jsonify({"ok": False, "error": "Part not found"}), 404

    data = request.get_json(silent=True) or {}

    # Validate and extract fields
    part_number = (data.get("part_number") or "").strip()
    if not part_number:
        return jsonify({"ok": False, "error": "Part number is required"}), 400

    description = (data.get("description") or "").strip()
    reference = (data.get("reference") or "").strip()

    in_stock = _parse_int(data.get("in_stock", 0), default=0)
    if in_stock < 0:
        return jsonify({"ok": False, "error": "In stock cannot be negative"}), 400

    average_cost = _parse_float(data.get("average_cost", 0.0), default=0.0)
    if average_cost < 0:
        return jsonify({"ok": False, "error": "Average cost cannot be negative"}), 400

    core_has_charge = data.get("core_has_charge", False)
    core_cost = _parse_float(data.get("core_cost", 0.0), default=0.0)
    if core_has_charge and core_cost < 0:
        return jsonify({"ok": False, "error": "Core cost cannot be negative"}), 400

    misc_has_charge = data.get("misc_has_charge", False)
    misc_charges = data.get("misc_charges", []) or []
    if misc_has_charge and not isinstance(misc_charges, list):
        return jsonify({"ok": False, "error": "Invalid misc charges"}), 400

    # Validate references
    vendor_oid, err = (
        _validate_ref(vendors_coll, data.get("vendor_id", ""), "Vendor")
        if vendors_coll is not None
        else (None, None)
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    category_oid, err = (
        _validate_ref(cats_coll, data.get("category_id", ""), "Category")
        if cats_coll is not None
        else (None, None)
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    location_oid, err = (
        _validate_ref(locs_coll, data.get("location_id", ""), "Location")
        if locs_coll is not None
        else (None, None)
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    # Update document
    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    parts_coll.update_one(
        {"_id": pid},
        {"$set": {
            "part_number": part_number,
            "description": description or None,
            "reference": reference or None,
            "search_terms": build_parts_search_terms(part_number, description, reference),
            "vendor_id": vendor_oid,
            "category_id": category_oid,
            "location_id": location_oid,
            "in_stock": in_stock,
            "average_cost": float(average_cost),
            "core_has_charge": bool(core_has_charge),
            "core_cost": float(core_cost) if core_has_charge else None,
            "misc_has_charge": bool(misc_has_charge),
            "misc_charges": misc_charges if misc_has_charge else [],
            "updated_at": now,
            "updated_by": user_oid,
        }}
    )

    return jsonify({"ok": True, "message": "Part updated successfully"})


@parts_bp.post("/<part_id>/deactivate")
@login_required
@permission_required("parts.edit")
def parts_deactivate(part_id: str):
    """
    Удаление запчасти = деактивация (soft delete).
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page"))

    pid = _oid(part_id)
    if not pid:
        flash("Invalid part id.", "error")
        return redirect(url_for("parts.parts_page"))

    existing = parts_coll.find_one({"_id": pid})
    if not existing:
        flash("Part not found.", "error")
        return redirect(url_for("parts.parts_page"))

    if existing.get("is_active") is False:
        flash("Part is already deactivated.", "info")
        return redirect(url_for("parts.parts_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    parts_coll.update_one(
        {"_id": pid},
        {"$set": {
            "is_active": False,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": now,
            "deactivated_by": user_oid,
        }},
    )

    flash("Part deactivated.", "success")
    return redirect(url_for("parts.parts_page"))


@parts_bp.post("/<part_id>/restore")
@login_required
@permission_required("parts.deactivate")
def parts_restore(part_id: str):
    """
    Restore (reactivate) part.
    Используем то же право parts.deactivate.
    """
    parts_coll, vendors_coll, cats_coll, locs_coll, orders_coll, shop, master = _parts_collections()
    if parts_coll is None or shop is None:
        flash("Shop database not configured for this shop.", "error")
        return redirect(url_for("parts.parts_page"))

    pid = _oid(part_id)
    if not pid:
        flash("Invalid part id.", "error")
        return redirect(url_for("parts.parts_page"))

    existing = parts_coll.find_one({"_id": pid})
    if not existing:
        flash("Part not found.", "error")
        return redirect(url_for("parts.parts_page"))

    if existing.get("is_active") is True:
        flash("Part is already active.", "info")
        return redirect(url_for("parts.parts_page"))

    now = utcnow()
    user_oid = _oid(session.get(SESSION_USER_ID))

    parts_coll.update_one(
        {"_id": pid},
        {"$set": {
            "is_active": True,
            "updated_at": now,
            "updated_by": user_oid,
            "deactivated_at": None,
            "deactivated_by": None,
        }},
    )

    flash("Part restored.", "success")
    return redirect(url_for("parts.parts_page"))
