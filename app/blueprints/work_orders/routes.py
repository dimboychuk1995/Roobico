from __future__ import annotations

from datetime import datetime, timezone
from bson import ObjectId
from flask import request, session, redirect, url_for, flash, jsonify

from app.blueprints.work_orders import work_orders_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.pagination import get_pagination_params, paginate_find
from app.utils.parts_search import build_query_tokens, part_matches_query
from app.utils.permissions import permission_required


def utcnow():
    return datetime.now(timezone.utc)


def oid(v):
    if not v:
        return None
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def i32(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def f64(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def round2(v):
    n = f64(v)
    if n is None:
        return 0.0
    return round(n + 1e-12, 2)


def normalize_parts_payload(raw_parts):
    if not isinstance(raw_parts, list):
        return []

    out = []
    for p in raw_parts:
        if not isinstance(p, dict):
            continue

        part_number = str(p.get("part_number") or "").strip()
        description = str(p.get("description") or "").strip()
        misc_charge_description = str(
            p.get("misc_charge_description") if p.get("misc_charge_description") is not None else ""
        ).strip()

        qty_raw = i32(p.get("qty"))
        cost_raw = f64(p.get("cost"))
        price_raw = f64(p.get("price"))
        core_raw = f64(
            p.get("core_charge") if p.get("core_charge") is not None else p.get("core_cost")
        )
        misc_raw = f64(p.get("misc_charge"))

        has_any = bool(part_number or description or misc_charge_description)
        if qty_raw is not None:
            has_any = True
        if any((
            cost_raw is not None,
            price_raw is not None,
            core_raw is not None,
            misc_raw is not None,
        )):
            has_any = True

        if not has_any:
            continue

        out.append(
            {
                "part_number": part_number,
                "description": description,
                "qty": int(qty_raw if qty_raw is not None else 0),
                "cost": round2(cost_raw if cost_raw is not None else 0),
                "price": round2(price_raw if price_raw is not None else 0),
                "core_charge": round2(core_raw if core_raw is not None else 0),
                "misc_charge": round2(misc_raw if misc_raw is not None else 0),
                "misc_charge_description": misc_charge_description,
            }
        )

    return out


def normalize_totals_payload(raw):
    src = raw if isinstance(raw, dict) else {}

    blocks = []
    for b in (src.get("labors") or []):
        if not isinstance(b, dict):
            continue
        labor = round2(b.get("labor") if b.get("labor") is not None else b.get("labor_total"))
        parts = round2(b.get("parts") if b.get("parts") is not None else b.get("parts_total"))
        core_total = round2(b.get("core_total"))
        misc_total = round2(b.get("misc_total"))
        shop_supply_total = round2(b.get("shop_supply_total"))
        cost_total = round2(b.get("cost_total") if b.get("cost_total") is not None else parts)
        labor_total = round2(labor + shop_supply_total)
        parts_total = round2(parts + core_total + misc_total)
        labor_full_total = round2(
            b.get("labor_full_total")
            if b.get("labor_full_total") is not None
            else (labor + parts_total + shop_supply_total)
        )
        blocks.append(
            {
                "labor": labor,
                "labor_total": labor_total,
                "parts": parts,
                "parts_total": parts_total,
                "core_total": core_total,
                "misc_total": misc_total,
                "cost_total": cost_total,
                "shop_supply_total": shop_supply_total,
                "labor_full_total": labor_full_total,
            }
        )

    labor = round2(src.get("labor") if src.get("labor") is not None else src.get("labor_total"))
    parts = round2(src.get("parts") if src.get("parts") is not None else src.get("parts_total"))
    core_total = round2(src.get("core_total"))
    misc_total = round2(src.get("misc_total"))
    shop_supply_total = round2(src.get("shop_supply_total"))
    cost_total = round2(src.get("cost_total") if src.get("cost_total") is not None else parts)

    labor_total = round2(labor + shop_supply_total)
    parts_total = round2(parts + core_total + misc_total)
    calculated_grand_total = round2(labor_total + parts_total)
    grand_total = round2(
        src.get("grand_total") if src.get("grand_total") is not None else calculated_grand_total
    )

    return {
        "labor": labor,
        "labor_total": labor_total,
        "parts": parts,
        "parts_total": parts_total,
        "core_total": core_total,
        "misc_total": misc_total,
        "cost_total": cost_total,
        "shop_supply_total": shop_supply_total,
        "grand_total": grand_total,
        "labors": blocks,
    }


def normalize_saved_labors(raw):
    if not isinstance(raw, list):
        return []

    out = []
    for block in raw:
        if not isinstance(block, dict):
            continue

        labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}

        labor_description = str(
            labor_src.get("description")
            if labor_src.get("description") is not None
            else block.get("labor_description")
            or ""
        ).strip()

        labor_hours = str(
            labor_src.get("hours")
            if labor_src.get("hours") is not None
            else block.get("labor_hours")
            or ""
        ).strip()

        labor_rate_code = str(
            labor_src.get("rate_code")
            if labor_src.get("rate_code") is not None
            else block.get("labor_rate_code")
            or ""
        ).strip()

        assigned_src = labor_src.get("assigned_mechanics")
        if not isinstance(assigned_src, list):
            assigned_src = block.get("assigned_mechanics")

        assigned_mechanics = []
        for item in (assigned_src or []):
            if not isinstance(item, dict):
                continue
            user_id = oid(item.get("user_id") or item.get("id"))
            if not user_id:
                continue
            assigned_mechanics.append(
                {
                    "user_id": str(user_id),
                    "name": str(item.get("name") or "").strip(),
                    "role": str(item.get("role") or "").strip(),
                    "percent": round2(item.get("percent")),
                }
            )

        parts_out = []
        for p in normalize_parts_payload(block.get("parts") or []):
            parts_out.append(
                {
                    "part_number": str(p.get("part_number") or "").strip(),
                    "description": str(p.get("description") or "").strip(),
                    "qty": str(p.get("qty") if p.get("qty") is not None else ""),
                    "cost": str(p.get("cost") if p.get("cost") is not None else ""),
                    "price": str(p.get("price") if p.get("price") is not None else ""),
                    "core_charge": str(p.get("core_charge") if p.get("core_charge") is not None else ""),
                    "misc_charge": str(p.get("misc_charge") if p.get("misc_charge") is not None else ""),
                    "misc_charge_description": str(
                        p.get("misc_charge_description") if p.get("misc_charge_description") is not None else ""
                    ),
                }
            )

        out.append(
            {
                "labor": {
                    "description": labor_description,
                    "hours": labor_hours,
                    "rate_code": labor_rate_code,
                    "assigned_mechanics": assigned_mechanics,
                },
                "parts": parts_out,
            }
        )

    return out


def format_dt_label(dt):
    if isinstance(dt, datetime):
        try:
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M")
    return "-"


def get_work_orders_list(shop_db, shop_id: ObjectId, page: int, per_page: int):
    rows, pagination = paginate_find(
        shop_db.work_orders,
        {"shop_id": shop_id, "is_active": True},
        [("created_at", -1)],
        page,
        per_page,
    )

    customer_ids = [x.get("customer_id") for x in rows if x.get("customer_id")]
    unit_ids = [x.get("unit_id") for x in rows if x.get("unit_id")]

    customers_map = {}
    if customer_ids:
        for c in shop_db.customers.find({"_id": {"$in": customer_ids}}):
            customers_map[c.get("_id")] = customer_label(c)

    units_map = {}
    if unit_ids:
        for u in shop_db.units.find({"_id": {"$in": unit_ids}}):
            units_map[u.get("_id")] = unit_label(u)

    items = []
    for x in rows:
        totals = x.get("totals") if isinstance(x.get("totals"), dict) else {}

        labor_total = round2(totals.get("labor_total") if totals.get("labor_total") is not None else x.get("labor_total"))
        parts_total = round2(totals.get("parts_total") if totals.get("parts_total") is not None else x.get("parts_total"))
        grand_total = round2(totals.get("grand_total") if totals.get("grand_total") is not None else x.get("grand_total"))

        status = (x.get("status") or "open").strip().lower()

        items.append(
            {
                "id": str(x.get("_id")),
                "customer": customers_map.get(x.get("customer_id")) or "-",
                "date": format_dt_label(x.get("created_at")),
                "unit": units_map.get(x.get("unit_id")) or "-",
                "labor_total": labor_total,
                "parts_total": parts_total,
                "grand_total": grand_total,
                "is_paid": status == "paid",
            }
        )

    return items, pagination


def current_user_id():
    return oid(session.get(SESSION_USER_ID))


def tenant_id_variants():
    raw = session.get(SESSION_TENANT_ID)
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    o = oid(raw)
    if o:
        out.add(o)
    return list(out)


def get_shop_db():
    master = get_master_db()

    shop_id = oid(session.get("shop_id"))
    if not shop_id:
        return None, None

    tenant_variants = tenant_id_variants()
    if not tenant_variants:
        return None, None

    shop = master.shops.find_one({"_id": shop_id, "tenant_id": {"$in": tenant_variants}})
    if not shop:
        return None, None

    db_name = shop.get("db_name")
    if not db_name:
        return None, shop

    client = get_mongo_client()
    return client[str(db_name)], shop


def customer_label(c: dict) -> str:
    company = (c.get("company_name") or "").strip()
    if company:
        return company
    fn = (c.get("first_name") or "").strip()
    ln = (c.get("last_name") or "").strip()
    name = (fn + " " + ln).strip()
    return name or "(no name)"


def unit_label(u: dict) -> str:
    parts = []
    if u.get("unit_number"):
        parts.append(str(u.get("unit_number")))
    if u.get("year"):
        parts.append(str(u.get("year")))
    if u.get("make"):
        parts.append(str(u.get("make")))
    if u.get("model"):
        parts.append(str(u.get("model")))
    if u.get("vin"):
        parts.append(f"VIN {u.get('vin')}")
    return " • ".join([p for p in parts if p]) or "(unit)"


def get_customers(shop_db):
    rows = list(
        shop_db.customers.find({"is_active": True}).sort(
            [("company_name", 1), ("last_name", 1), ("first_name", 1)]
        )
    )
    return [
        {
            "id": str(x["_id"]),
            "label": customer_label(x),
            "default_labor_rate": (x.get("default_labor_rate") or "").strip(),
        }
        for x in rows
    ]


def get_units(shop_db, customer_id: ObjectId):
    rows = list(
        shop_db.units.find({"customer_id": customer_id, "is_active": True}).sort([("created_at", -1)])
    )
    return [{"id": str(x["_id"]), "label": unit_label(x)} for x in rows]


def get_labor_rates(shop_db, shop_id: ObjectId):
    rows = list(
        shop_db.labor_rates.find({"shop_id": shop_id, "is_active": True}).sort([("name", 1)])
    )
    return [
        {
            "code": r.get("code") or "",
            "name": r.get("name") or (r.get("code") or ""),
            "hourly_rate": float(r.get("hourly_rate") or 0),
        }
        for r in rows
    ]


def _tenant_variants_from_shop(shop: dict):
    raw = shop.get("tenant_id")
    if raw is None:
        return []

    out = {raw, str(raw)}
    parsed = oid(raw)
    if parsed:
        out.add(parsed)
    return list(out)


def get_assignable_mechanics(shop: dict):
    shop_id = shop.get("_id")
    if not shop_id:
        return []

    tenant_variants = _tenant_variants_from_shop(shop)
    if not tenant_variants:
        return []

    shop_variants = [shop_id, str(shop_id)]
    master = get_master_db()
    rows = list(
        master.users.find(
            {
                "tenant_id": {"$in": tenant_variants},
                "is_active": True,
                "role": {"$in": ["senior_mechanic", "mechanic"]},
                "$or": [
                    {"shop_ids": {"$in": shop_variants}},
                    {"shop_id": {"$in": shop_variants}},
                ],
            },
            {
                "first_name": 1,
                "last_name": 1,
                "name": 1,
                "email": 1,
                "role": 1,
            },
        ).sort([("first_name", 1), ("last_name", 1), ("name", 1), ("email", 1)])
    )

    out = []
    for u in rows:
        first_name = str(u.get("first_name") or "").strip()
        last_name = str(u.get("last_name") or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        fallback_name = str(u.get("name") or "").strip()
        email = str(u.get("email") or "").strip()
        display_name = full_name or fallback_name or email
        if not display_name:
            continue

        out.append(
            {
                "id": str(u.get("_id")),
                "name": display_name,
                "role": str(u.get("role") or "").strip(),
            }
        )

    return out


def normalize_assigned_mechanics(raw, mechanics_by_id: dict[str, dict]):
    if not isinstance(raw, list):
        return []

    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue

        user_id = str(item.get("user_id") or item.get("id") or "").strip()
        if not user_id or user_id in seen:
            continue

        mechanic = mechanics_by_id.get(user_id)
        if not mechanic:
            continue

        percent = round2(item.get("percent"))
        if percent < 0:
            percent = 0.0

        user_oid = oid(user_id)
        if not user_oid:
            continue

        out.append(
            {
                "user_id": user_oid,
                "name": mechanic.get("name") or "",
                "role": mechanic.get("role") or "",
                "percent": percent,
            }
        )
        seen.add(user_id)

    if len(out) == 1 and out[0].get("percent", 0) <= 0:
        out[0]["percent"] = 100.0

    return out


def apply_assignments_to_labors(labors, mechanics_by_id: dict[str, dict]):
    if not isinstance(labors, list):
        return []

    out = []
    for block in labors:
        if not isinstance(block, dict):
            continue

        block_copy = dict(block)
        labor_src = block_copy.get("labor") if isinstance(block_copy.get("labor"), dict) else None
        if labor_src is not None:
            labor_copy = dict(labor_src)
            labor_copy["description"] = str(labor_copy.get("description") or "").strip()
            labor_copy["hours"] = str(labor_copy.get("hours") or "").strip()
            labor_copy["rate_code"] = str(labor_copy.get("rate_code") or "").strip()
            labor_copy["labor_full_total"] = round2(labor_copy.get("labor_full_total"))
            normalized = normalize_assigned_mechanics(labor_copy.get("assigned_mechanics"), mechanics_by_id)
            labor_copy["assigned_mechanics"] = normalized
            block_copy["labor"] = labor_copy
        else:
            normalized = normalize_assigned_mechanics(block_copy.get("assigned_mechanics"), mechanics_by_id)
            block_copy["assigned_mechanics"] = normalized
            block_copy["labor_full_total"] = round2(block_copy.get("labor_full_total"))

        block_copy["parts"] = normalize_parts_payload(block_copy.get("parts") or [])

        out.append(block_copy)

    return out


def get_pricing_rules_json(shop_db, shop_id: ObjectId):
    doc = shop_db.parts_pricing_rules.find_one({"shop_id": shop_id, "is_active": True})
    if not doc:
        return None

    mode = (doc.get("mode") or "margin").strip().lower()  # margin | markup
    rules = []
    for r in (doc.get("rules") or []):
        frm = r.get("from")
        to = r.get("to")
        vp = r.get("value_percent")

        try:
            frm_f = float(frm)
        except Exception:
            continue

        if to is None:
            to_f = None
        else:
            try:
                to_f = float(to)
            except Exception:
                to_f = None

        try:
            vp_f = float(vp)
        except Exception:
            continue

        rules.append({"from": frm_f, "to": to_f, "value_percent": vp_f})

    return {"mode": mode, "rules": rules}


def get_shop_supply_percentage(shop_db, shop_id: ObjectId) -> float:
    col = shop_db.shop_supply_amount_rules
    doc = col.find_one({"shop_id": shop_id})
    if not doc:
        now = utcnow()
        col.update_one(
            {"shop_id": shop_id},
            {
                "$setOnInsert": {
                    "shop_id": shop_id,
                    "shop_supply_procentage": 5,
                    "is_active": True,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                },
            },
            upsert=True,
        )
        doc = col.find_one({"shop_id": shop_id}) or {}
    try:
        raw = doc.get("shop_supply_procentage")
        return float(raw) if raw is not None else 0.0
    except Exception:
        return 0.0


def get_core_charge_default(shop_db, shop_id: ObjectId) -> bool:
    doc = shop_db.core_charge_rules.find_one({"shop_id": shop_id})
    if not doc:
        doc = shop_db.core_charge_rules.find_one({}) or {}
    return bool(doc.get("charge_for_cores_default", True))


def render_details(shop_db, shop, customer_id, unit_id, form_state=None):
    customers = get_customers(shop_db)
    mechanics = get_assignable_mechanics(shop)

    units = []
    if customer_id:
        units = get_units(shop_db, customer_id)
        if unit_id and not any(u["id"] == str(unit_id) for u in units):
            unit_id = None

    ctx = {
        "active_page": "work_orders",
        "customers": customers,
        "units": units,
        "selected_customer_id": str(customer_id) if customer_id else "",
        "selected_unit_id": str(unit_id) if unit_id else "",
        "labor_rates": get_labor_rates(shop_db, shop["_id"]),
        "mechanics": mechanics,
        "parts_pricing_rules": get_pricing_rules_json(shop_db, shop["_id"]),
        "shop_supply_procentage": get_shop_supply_percentage(shop_db, shop["_id"]),
        "charge_for_cores_default": get_core_charge_default(shop_db, shop["_id"]),

        # старые поля (оставляем как у тебя было)
        "labor_description": (form_state or {}).get("labor_description") or "",
        "labor_hours": (form_state or {}).get("labor_hours") or "",
        "labor_rate_code": (form_state or {}).get("labor_rate_code") or "",

        # NEW: флаг, чтобы после create UI стал неактивным
        "work_order_created": bool((form_state or {}).get("work_order_created")),
        "created_work_order_id": (form_state or {}).get("created_work_order_id") or "",

        "initial_labors": (form_state or {}).get("initial_labors") or [],
        "initial_totals": normalize_totals_payload((form_state or {}).get("initial_totals") or {}),
        "work_order_status": (form_state or {}).get("work_order_status") or "open",
    }

    return _render_app_page("public/work_orders/work_order_details.html", **ctx)


# -------------------- INVENTORY MANAGEMENT --------------------

def deduct_parts_from_inventory(shop_db, labors: list, user_id: ObjectId) -> dict:
    """
    Deduct parts used in a work order from inventory.
    Returns: {success: bool, deducted: [{part_id, part_number, qty_used}], errors: []}
    """
    if not isinstance(labors, list):
        return {"success": True, "deducted": [], "errors": []}

    deducted = []
    errors = []
    now = utcnow()

    for labor_block in labors:
        if not isinstance(labor_block, dict):
            continue

        parts = labor_block.get("parts") or []
        if not isinstance(parts, list):
            continue

        for part in parts:
            if not isinstance(part, dict):
                continue

            part_number = str(part.get("part_number") or "").strip()
            qty = i32(part.get("qty"))

            # Skip if no part_number or qty
            if not part_number or qty is None or qty <= 0:
                continue

            # Find part by number
            part_doc = shop_db.parts.find_one({"part_number": part_number, "is_active": True})
            if not part_doc:
                errors.append(f"Part '{part_number}' not found in inventory")
                continue

            part_id = part_doc.get("_id")
            current_stock = int(part_doc.get("in_stock") or 0)

            if current_stock < qty:
                errors.append(
                    f"Insufficient stock for '{part_number}': "
                    f"need {qty}, have {current_stock}"
                )
                continue

            # Deduct from inventory
            new_stock = current_stock - qty
            shop_db.parts.update_one(
                {"_id": part_id},
                {
                    "$set": {
                        "in_stock": new_stock,
                        "updated_at": now,
                        "updated_by": user_id,
                    }
                }
            )

            deducted.append({
                "part_id": str(part_id),
                "part_number": part_number,
                "qty_used": qty,
                "previous_stock": current_stock,
                "new_stock": new_stock,
            })

    return {
        "success": len(errors) == 0,
        "deducted": deducted,
        "errors": errors,
    }


def restore_parts_to_inventory(shop_db, labors: list, user_id: ObjectId) -> dict:
    """
    Restore parts back to inventory (when work order is updated or deleted).
    Returns inventory updates in reverse.
    """
    if not isinstance(labors, list):
        return {"success": True, "restored": [], "errors": []}

    restored = []
    errors = []
    now = utcnow()

    for labor_block in labors:
        if not isinstance(labor_block, dict):
            continue

        parts = labor_block.get("parts") or []
        if not isinstance(parts, list):
            continue

        for part in parts:
            if not isinstance(part, dict):
                continue

            part_number = str(part.get("part_number") or "").strip()
            qty = i32(part.get("qty"))

            if not part_number or qty is None or qty <= 0:
                continue

            part_doc = shop_db.parts.find_one({"part_number": part_number, "is_active": True})
            if not part_doc:
                errors.append(f"Part '{part_number}' not found when restoring")
                continue

            part_id = part_doc.get("_id")
            current_stock = int(part_doc.get("in_stock") or 0)

            # Restore to inventory
            new_stock = current_stock + qty
            shop_db.parts.update_one(
                {"_id": part_id},
                {
                    "$set": {
                        "in_stock": new_stock,
                        "updated_at": now,
                        "updated_by": user_id,
                    }
                }
            )

            restored.append({
                "part_id": str(part_id),
                "part_number": part_number,
                "qty_restored": qty,
            })

    return {
        "success": True,
        "restored": restored,
        "errors": errors,
    }


def adjust_inventory_for_part_changes(shop_db, old_labors: list, new_labors: list, user_id: ObjectId) -> dict:
    """
    When updating a work order, adjust inventory based on part quantity changes.
    Compares old vs new parts and makes adjustments.
    """
    if not isinstance(old_labors, list) or not isinstance(new_labors, list):
        return {"success": True, "adjusted": [], "errors": []}

    errors = []
    adjusted = []
    now = utcnow()

    # Build part maps for old and new
    old_parts_map = {}  # part_number -> qty
    for labor_block in old_labors:
        parts = labor_block.get("parts") or []
        for part in parts:
            pn = str(part.get("part_number") or "").strip()
            qty = i32(part.get("qty")) or 0
            if pn:
                old_parts_map[pn] = old_parts_map.get(pn, 0) + qty

    new_parts_map = {}  # part_number -> qty
    for labor_block in new_labors:
        parts = labor_block.get("parts") or []
        for part in parts:
            pn = str(part.get("part_number") or "").strip()
            qty = i32(part.get("qty")) or 0
            if pn:
                new_parts_map[pn] = new_parts_map.get(pn, 0) + qty

    # Find all changed part numbers
    all_part_numbers = set(old_parts_map.keys()) | set(new_parts_map.keys())

    for part_number in all_part_numbers:
        old_qty = old_parts_map.get(part_number, 0)
        new_qty = new_parts_map.get(part_number, 0)
        qty_diff = new_qty - old_qty

        if qty_diff == 0:
            continue

        part_doc = shop_db.parts.find_one({"part_number": part_number, "is_active": True})
        if not part_doc:
            errors.append(f"Part '{part_number}' not found when adjusting")
            continue

        part_id = part_doc.get("_id")
        current_stock = int(part_doc.get("in_stock") or 0)

        # qty_diff > 0: more parts needed, deduct from stock
        # qty_diff < 0: fewer parts needed, add back to stock
        new_stock = current_stock - qty_diff

        if qty_diff > 0 and current_stock < qty_diff:
            errors.append(
                f"Insufficient stock for '{part_number}': "
                f"need {qty_diff} more, have {current_stock}"
            )
            continue

        shop_db.parts.update_one(
            {"_id": part_id},
            {
                "$set": {
                    "in_stock": new_stock,
                    "updated_at": now,
                    "updated_by": user_id,
                }
            }
        )

        adjusted.append({
            "part_id": str(part_id),
            "part_number": part_number,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "qty_change": qty_diff,
            "previous_stock": current_stock,
            "new_stock": new_stock,
        })

    return {
        "success": len(errors) == 0,
        "adjusted": adjusted,
        "errors": errors,
    }


@work_orders_bp.get("/work_orders")
@login_required
@permission_required("work_orders.view")
def work_orders_page():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("main.dashboard"))

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)
    work_orders, pagination = get_work_orders_list(shop_db, shop["_id"], page, per_page)
    return _render_app_page(
        "public/work_orders/work_orders.html",
        active_page="work_orders",
        work_orders=work_orders,
        pagination=pagination,
    )


@work_orders_bp.get("/work_orders/details")
@login_required
@permission_required("work_orders.create")
def work_order_details_page():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("main.dashboard"))

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

        return render_details(
            shop_db,
            shop,
            customer_id,
            unit_id,
            form_state={
                "work_order_created": True,
                "created_work_order_id": str(wo.get("_id")),
                "initial_labors": normalize_saved_labors(wo.get("labors") or wo.get("blocks") or []),
                "initial_totals": wo.get("totals")
                or {
                    "labor": wo.get("labor_total") or 0,
                    "labor_total": wo.get("labor_total") or 0,
                    "parts": wo.get("parts_total") or 0,
                    "parts_total": wo.get("parts_total") or 0,
                    "core_total": 0,
                    "misc_total": 0,
                    "shop_supply_total": 0,
                    "cost_total": wo.get("parts_total") or 0,
                    "grand_total": wo.get("grand_total") or 0,
                    "labors": [],
                },
                "work_order_status": work_order_status,
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
        return redirect(url_for("main.dashboard"))

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
        return redirect(url_for("main.dashboard"))

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
    labor_re = re.compile(r"^(?:labors|blocks)\[(\d+)\]\[(labor_description|labor_hours|labor_rate_code|labor_total_ui|labor_full_total|assigned_mechanics_json)\]$")
    # parts
    parts_re = re.compile(
        r"^(?:labors|blocks)\[(\d+)\]\[parts\]\[(\d+)\]\[(part_number|description|qty|cost|price|core_charge|misc_charge|misc_charge_description)\]$"
    )

    for key, val in request.form.items():
        m = labor_re.match(key)
        if m:
            bidx = int(m.group(1))
            field = m.group(2)
            b = labors_map.setdefault(bidx, {"labor": {}, "parts": []})

            if field == "labor_description":
                b["labor"]["description"] = (val or "").strip()
            elif field == "labor_hours":
                b["labor"]["hours"] = (val or "").strip()
            elif field == "labor_rate_code":
                b["labor"]["rate_code"] = (val or "").strip()
            elif field in ("labor_total_ui", "labor_full_total"):
                b["labor"]["labor_full_total"] = round2(val)
            elif field == "assigned_mechanics_json":
                b["labor"]["assigned_mechanics_json"] = (val or "").strip()
            continue

        m = parts_re.match(key)
        if m:
            bidx = int(m.group(1))
            ridx = int(m.group(2))
            field = m.group(3)

            b = labors_map.setdefault(bidx, {"labor": {}, "parts": []})
            while len(b["parts"]) <= ridx:
                b["parts"].append({})

            if field in ("part_number", "description"):
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
            "labor": {
                "description": (labor.get("description") or "").strip(),
                "hours": (labor.get("hours") or "").strip(),
                "rate_code": (labor.get("rate_code") or "").strip(),
                "labor_full_total": round2(labor.get("labor_full_total")),
                "assigned_mechanics": assigned_mechanics,
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

    totals = normalize_totals_payload(totals)

    now = utcnow()
    user_id = current_user_id()

    # ✅ Deduct parts from inventory before creating work order
    inventory_result = deduct_parts_from_inventory(shop_db, labors, user_id)
    if not inventory_result["success"] and inventory_result["errors"]:
        for error in inventory_result["errors"]:
            flash(f"Inventory error: {error}", "warning")
        # Still proceed with creating work order, but flag it
        # If you prefer to cancel, uncomment the redirect below
        # return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id), unit_id=str(unit_id)))

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": "open",
        "labors": labors,

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
    flash("Work order created.", "success")

    if inventory_result["deducted"]:
        deducted_info = ", ".join([f"{d['part_number']} (qty: {d['qty_used']})" for d in inventory_result["deducted"]])
        flash(f"Inventory deducted: {deducted_info}", "info")

    return redirect(url_for("work_orders.work_order_details_page", work_order_id=str(res.inserted_id)))


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
        "core_has_charge": 1,
        "core_cost": 1,
        "misc_has_charge": 1,
        "misc_charges": 1,
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

        misc_items = []
        for m in (p.get("misc_charges") or []):
            if not isinstance(m, dict):
                continue
            misc_items.append({
                "description": str(m.get("description") or "").strip(),
                "price": float(m.get("price") or 0),
            })

        items.append({
            "id": str(p.get("_id")),
            "part_number": p.get("part_number") or "",
            "description": p.get("description") or "",
            "reference": p.get("reference") or "",
            "average_cost": float(p.get("average_cost") or 0),
            "in_stock": int(p.get("in_stock") or 0),
            "core_has_charge": bool(p.get("core_has_charge")),
            "core_cost": float(p.get("core_cost") or 0),
            "misc_has_charge": bool(p.get("misc_has_charge")),
            "misc_charges": misc_items,
        })

        if len(items) >= limit:
            break

    if len(items) < limit:
        contains = re.escape(q)
        fallback_query = {
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

            misc_items = []
            for m in (p.get("misc_charges") or []):
                if not isinstance(m, dict):
                    continue
                misc_items.append({
                    "description": str(m.get("description") or "").strip(),
                    "price": float(m.get("price") or 0),
                })

            items.append({
                "id": str(p.get("_id")),
                "part_number": p.get("part_number") or "",
                "description": p.get("description") or "",
                "reference": p.get("reference") or "",
                "average_cost": float(p.get("average_cost") or 0),
                "in_stock": int(p.get("in_stock") or 0),
                "core_has_charge": bool(p.get("core_has_charge")),
                "core_cost": float(p.get("core_cost") or 0),
                "misc_has_charge": bool(p.get("misc_has_charge")),
                "misc_charges": misc_items,
            })

            if len(items) >= limit:
                break

    return jsonify({"items": items}), 200


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
    totals = normalize_totals_payload(data.get("totals") or {})

    if not isinstance(labors, list):
        return jsonify({"ok": False, "error": "labors_required"}), 200

    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    labors = apply_assignments_to_labors(labors, mechanics_by_id)

    # (опционально) можно запретить редактирование, если paid
    if (wo.get("status") or "open") == "paid":
        return jsonify({"ok": False, "error": "paid_cannot_edit"}), 200

    now = utcnow()
    user_id = current_user_id()

    # ✅ Adjust inventory for part changes
    old_labors = wo.get("labors") or []
    inventory_adjustment = adjust_inventory_for_part_changes(shop_db, old_labors, labors, user_id)
    if not inventory_adjustment["success"] and inventory_adjustment["errors"]:
        return jsonify({
            "ok": False,
            "error": "inventory_adjustment_failed",
            "details": inventory_adjustment["errors"]
        }), 200

    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "labors": labors,
                "totals": totals,  # ✅ сохраняем totals от фронта
                # ✅ update inventory tracking
                "inventory_adjusted_at": now,
                "inventory_adjustment_count": (wo.get("inventory_adjustment_count", 0) or 0) + len(inventory_adjustment["adjusted"]),
                "updated_at": now,
                "updated_by": user_id,
            },
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
        "inventory_adjusted": len(inventory_adjustment["adjusted"]) > 0,
        "inventory_changes": inventory_adjustment["adjusted"],
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

    if amount is None or not (isinstance(amount, (int, float)) and amount > 0):
        return jsonify({"ok": False, "error": "invalid_amount"}), 200

    # Get work order grand total
    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    grand_total = round2(totals_doc.get("grand_total") if totals_doc.get("grand_total") is not None else wo.get("grand_total") or 0)

    # Get payments already made
    existing_payments = list(
        shop_db.work_order_payments.find({"work_order_id": wo_id, "is_active": True})
    )
    paid_amount = round2(sum(round2(p.get("amount") or 0) for p in existing_payments))

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
        "is_active": True,
        "created_at": now,
        "created_by": user_id,
    }

    payment_result = shop_db.work_order_payments.insert_one(payment_doc)
    payment_id = payment_result.inserted_id

    # Check if fully paid - if so, update work order status
    is_fully_paid = remaining_balance <= 0.01  # Allow 1 cent rounding difference
    if is_fully_paid:
        shop_db.work_orders.update_one(
            {"_id": wo_id},
            {
                "$set": {
                    "status": "paid",
                    "updated_at": now,
                    "updated_by": user_id,
                }
            }
        )

    return jsonify({
        "ok": True,
        "payment_id": str(payment_id),
        "amount_paid": round2(new_paid_amount),
        "remaining_balance": remaining_balance,
        "is_fully_paid": is_fully_paid
    }), 200


@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/payments")
@login_required
@permission_required("work_orders.create")
def api_get_work_order_payments(work_order_id):
    """
    Get all payments for a work order with balance info.
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

    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    grand_total = round2(totals_doc.get("grand_total") if totals_doc.get("grand_total") is not None else wo.get("grand_total") or 0)

    payments = list(
        shop_db.work_order_payments.find({"work_order_id": wo_id, "is_active": True})
        .sort([("created_at", -1)])
    )

    paid_amount = round2(sum(round2(p.get("amount") or 0) for p in payments))
    remaining_balance = round2(grand_total - paid_amount)

    payment_list = [
        {
            "id": str(p.get("_id")),
            "amount": round2(p.get("amount") or 0),
            "payment_method": p.get("payment_method") or "cash",
            "notes": p.get("notes") or "",
            "created_at": p.get("created_at").isoformat() if p.get("created_at") else "",
        }
        for p in payments
    ]

    return jsonify({
        "ok": True,
        "grand_total": grand_total,
        "paid_amount": paid_amount,
        "remaining_balance": remaining_balance,
        "payments": payment_list
    }), 200


@work_orders_bp.get("/work_orders/api/work_orders/all-payments")
@login_required
@permission_required("work_orders.view")
def api_get_all_payments():
    """
    Get all payments for the current shop.
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    shop_id = shop["_id"]

    payments = list(
        shop_db.work_order_payments.find({"shop_id": shop_id, "is_active": True})
        .sort([("created_at", -1)])
        .limit(500)
    )

    work_order_ids = [p.get("work_order_id") for p in payments if p.get("work_order_id")]
    work_orders_map = {}
    customer_ids = []
    if work_order_ids:
        work_orders = list(
            shop_db.work_orders.find(
                {"_id": {"$in": work_order_ids}, "shop_id": shop_id},
                {"customer_id": 1},
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
            shop_db.customers.find({"_id": {"$in": customer_ids}}, {"company_name": 1, "first_name": 1, "last_name": 1})
        )
        for c in customers:
            c_id = c.get("_id")
            if c_id:
                customers_map[c_id] = customer_label(c)

    payment_list = [
        {
            "id": str(p.get("_id")),
            "work_order_id": str(p.get("work_order_id")) if p.get("work_order_id") else "",
            "customer": customers_map.get((work_orders_map.get(p.get("work_order_id")) or {}).get("customer_id")) or "-",
            "amount": round2(p.get("amount") or 0),
            "payment_method": p.get("payment_method") or "cash",
            "notes": p.get("notes") or "",
            "created_at": p.get("created_at").isoformat() if p.get("created_at") else "",
        }
        for p in payments
    ]

    return jsonify({
        "ok": True,
        "payments": payment_list
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

    if status not in ("open", "paid"):
        return jsonify({"ok": False, "error": "invalid_status"}), 200

    now = utcnow()
    user_id = current_user_id()

    # If changing to "open" (unpaid), delete all payment records for this work order
    if status == "open":
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
@permission_required("work_orders.create")
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
    restore_result = restore_parts_to_inventory(shop_db, labors, user_id)

    # Delete all payments associated with this work order
    shop_db.work_order_payments.delete_many({"work_order_id": wo_id})

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
    }), 200