from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from flask import g, request, session, redirect, url_for, flash, jsonify, render_template, make_response

from app.blueprints.work_orders import work_orders_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.parts_search import build_query_tokens, part_matches_query
from app.utils.permissions import permission_required
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


def _work_order_grand_total(wo: dict) -> float:
    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    return round2(
        totals_doc.get("grand_total")
        if totals_doc.get("grand_total") is not None
        else wo.get("grand_total") or 0
    )


def _work_order_tax_total(wo: dict) -> float:
    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    return round2(
        totals_doc.get("sales_tax_total")
        if totals_doc.get("sales_tax_total") is not None
        else wo.get("sales_tax_total") or 0
    )


def _get_shop_sales_tax_context(shop: dict, shop_db=None) -> dict:
    from app.utils.sales_tax import resolve_active_shop_sales_tax_rate
    master = get_master_db()
    zip_code = get_shop_zip_code(shop)
    shop_id = shop.get("_id")
    
    rate_doc = resolve_active_shop_sales_tax_rate(master, shop_id, shop_db) or {}
    try:
        rate = float(rate_doc.get("combined_rate") or 0)
    except Exception:
        rate = 0.0

    return {
        "zip_code": zip_code,
        "rate": round(rate + 1e-12, 6),
    }


def _is_customer_taxable(shop_db, customer_id: ObjectId | None) -> bool:
    if not customer_id:
        return False
    customer = shop_db.customers.find_one({"_id": customer_id}, {"taxable": 1}) or {}
    return bool(customer.get("taxable", False))


def _apply_sales_tax_to_totals(totals: dict, tax_rate: float, is_taxable: bool) -> dict:
    src = normalize_totals_payload(totals or {})

    parts_only = round2(src.get("parts") if src.get("parts") is not None else src.get("parts_total"))
    misc_taxable_total = round2(src.get("misc_taxable_total") or 0)
    parts_taxable_total = round2(parts_only + misc_taxable_total)
    safe_tax_rate = 0.0
    try:
        safe_tax_rate = max(0.0, float(tax_rate or 0))
    except Exception:
        safe_tax_rate = 0.0

    sales_tax_total = round2(parts_taxable_total * safe_tax_rate) if is_taxable else 0.0
    grand_total = round2(
        round2(src.get("labor_total"))
        + round2(src.get("parts_total"))
        + sales_tax_total
    )

    src["parts_taxable_total"] = parts_taxable_total
    src["sales_tax_rate"] = round(safe_tax_rate + 1e-12, 6)
    src["sales_tax_total"] = sales_tax_total
    src["is_taxable"] = bool(is_taxable)
    src["grand_total"] = grand_total
    return src


def _sum_active_work_order_payments(shop_db, wo_id) -> float:
    if shop_db is None or not wo_id:
        return 0.0

    payments = shop_db.work_order_payments.find({"work_order_id": wo_id, "is_active": True})
    return round2(sum(round2(payment.get("amount") or 0) for payment in payments))


def _build_work_order_payment_summary(wo: dict, paid_amount: float) -> dict:
    grand_total = _work_order_grand_total(wo or {})
    paid = round2(max(0.0, paid_amount or 0.0))
    remaining_balance = round2(max(0.0, grand_total - paid))
    status = "paid" if remaining_balance <= 0.01 else "open"
    return {
        "grand_total": grand_total,
        "paid_amount": paid,
        "remaining_balance": remaining_balance,
        "status": status,
        "is_fully_paid": status == "paid",
    }


def _sync_work_order_payment_state(shop_db, wo: dict, user_id, now):
    if shop_db is None or not isinstance(wo, dict):
        return None

    wo_id = wo.get("_id")
    if not wo_id:
        return None

    summary = _build_work_order_payment_summary(wo, _sum_active_work_order_payments(shop_db, wo_id))
    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "status": summary["status"],
                "updated_at": now,
                "updated_by": user_id,
            }
        },
    )
    return summary


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if v is None:
        return False
    raw = str(v).strip().lower()
    return raw in ("1", "true", "yes", "on")


def get_next_wo_number(shop_db, shop_id):
    """
    Get next work order number using atomic counter.
    Returns integer starting from 1000.
    """
    from pymongo import ReturnDocument
    
    result = shop_db.counters.find_one_and_update(
        {"_id": f"wo_number_{shop_id}"},
        {
            "$inc": {"seq": 1},
            "$setOnInsert": {"initial_value": 1000}
        },
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    
    seq = result.get("seq", 1)
    initial = result.get("initial_value", 1000)
    
    # First call: seq=1, return 1000
    # Second call: seq=2, return 1001, etc
    return initial + seq - 1


def normalize_parts_payload(raw_parts):
    if not isinstance(raw_parts, list):
        return []

    out = []
    for p in raw_parts:
        if not isinstance(p, dict):
            continue

        part_number = str(p.get("part_number") or "").strip()
        part_id = oid(p.get("part_id"))
        one_time_part = as_bool(p.get("one_time_part"))
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
        if qty_raw is not None and qty_raw > 0:
            has_any = True
        if cost_raw is not None and cost_raw > 0:
            has_any = True
        if price_raw is not None and price_raw > 0:
            has_any = True
        if core_raw is not None and core_raw > 0:
            has_any = True
        if misc_raw is not None and misc_raw > 0:
            has_any = True

        if not has_any:
            continue

        item = {
            "part_number": part_number,
            "description": description,
            "one_time_part": one_time_part,
            "qty": int(qty_raw if qty_raw is not None else 0),
            "cost": round2(cost_raw if cost_raw is not None else 0),
            "price": round2(price_raw if price_raw is not None else 0),
            "core_charge": round2(core_raw if core_raw is not None else 0),
            "misc_charge": round2(misc_raw if misc_raw is not None else 0),
            "misc_charge_description": misc_charge_description,
        }
        if part_id:
            item["part_id"] = part_id

        out.append(item)

    return out


def _resolve_part_for_inventory(shop_db, raw_part: dict):
    """Resolve part document by part_id first, then by part_number."""
    if not isinstance(raw_part, dict):
        return None

    if as_bool(raw_part.get("one_time_part")):
        return None

    part_id = oid(raw_part.get("part_id"))
    part_number = str(raw_part.get("part_number") or "").strip()

    query = {"is_active": True}
    if part_id:
        query["_id"] = part_id
    elif part_number:
        query["part_number"] = part_number
    else:
        return None

    return shop_db.parts.find_one(
        query,
        {
            "_id": 1,
            "part_number": 1,
            "in_stock": 1,
            "do_not_track_inventory": 1,
        },
    )


def _collect_inventory_qty_by_part(shop_db, labors: list):
    """Collect tracked parts qty from labor blocks as {part_id: {part_number, qty}}."""
    out = {}
    errors = []

    if not isinstance(labors, list):
        return out, errors

    for labor_block in labors:
        if not isinstance(labor_block, dict):
            continue

        parts = labor_block.get("parts") or []
        if not isinstance(parts, list):
            continue

        for part in parts:
            if not isinstance(part, dict):
                continue

            qty = i32(part.get("qty"))
            if qty is None or qty <= 0:
                continue

            part_doc = _resolve_part_for_inventory(shop_db, part)
            raw_part_number = str(part.get("part_number") or "").strip()
            if not part_doc:
                if raw_part_number and not as_bool(part.get("one_time_part")):
                    errors.append(f"Part '{raw_part_number}' not found in inventory")
                continue

            if bool(part_doc.get("do_not_track_inventory")):
                continue

            part_id = part_doc.get("_id")
            if not part_id:
                continue

            key = str(part_id)
            if key not in out:
                out[key] = {
                    "part_id": part_id,
                    "part_number": str(part_doc.get("part_number") or raw_part_number or "").strip(),
                    "qty": 0,
                }
            out[key]["qty"] += int(qty)

    return out, errors


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
    parts_taxable_total = round2(src.get("parts_taxable_total") if src.get("parts_taxable_total") is not None else parts)
    misc_taxable_total = round2(src.get("misc_taxable_total") or 0)
    sales_tax_rate = round(float(src.get("sales_tax_rate") or 0) + 1e-12, 6)
    sales_tax_total = round2(src.get("sales_tax_total"))
    is_taxable = bool(src.get("is_taxable", False))

    labor_total = round2(labor + shop_supply_total)
    parts_total = round2(parts + core_total + misc_total)
    calculated_grand_total = round2(labor_total + parts_total)
    grand_total = round2(
        src.get("grand_total")
        if src.get("grand_total") is not None
        else round2(calculated_grand_total + sales_tax_total)
    )

    return {
        "labor": labor,
        "labor_total": labor_total,
        "parts": parts,
        "parts_total": parts_total,
        "core_total": core_total,
        "misc_total": misc_total,
        "misc_taxable_total": misc_taxable_total,
        "cost_total": cost_total,
        "shop_supply_total": shop_supply_total,
        "parts_taxable_total": parts_taxable_total,
        "sales_tax_rate": sales_tax_rate,
        "sales_tax_total": sales_tax_total,
        "is_taxable": is_taxable,
        "grand_total": grand_total,
        "labors": blocks,
    }


def _parse_misc_items(raw):
    value = str(raw or "").strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "description": str(item.get("description") or "").strip(),
                "quantity": f64(item.get("quantity") if item.get("quantity") is not None else 1) or 0,
                "price": f64(item.get("price")),
                "manual": item.get("manual") is True,
                "taxable": item.get("taxable") is not False,
            }
        )
    return out


def _calc_misc_total_from_parts(parts: list) -> tuple:
    """Returns (misc_total, misc_taxable_total)."""
    if not isinstance(parts, list) or not parts:
        return 0.0, 0.0

    first_row_items = _parse_misc_items((parts[0] or {}).get("misc_charge_description"))
    if first_row_items:
        total = 0.0
        taxable_total = 0.0
        for item in first_row_items:
            price = f64(item.get("price"))
            qty = f64(item.get("quantity") if item.get("quantity") is not None else 0)
            if not price or price <= 0 or not qty or qty <= 0:
                continue
            amount = round2(price * qty)
            total += amount
            if item.get("taxable") is not False:
                taxable_total += amount
        return round2(total), round2(taxable_total)

    # Backward compatibility for older rows where misc was stored per-part row.
    total = 0.0
    taxable_total = 0.0
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_qty = i32(part.get("qty")) or 0

        row_items = _parse_misc_items(part.get("misc_charge_description"))
        if row_items:
            for item in row_items:
                price = f64(item.get("price"))
                if not price or price <= 0:
                    continue
                if item.get("manual"):
                    qty = f64(item.get("quantity") if item.get("quantity") is not None else 0) or 0
                else:
                    qty = part_qty
                if qty <= 0:
                    continue
                amount = round2(price * qty)
                total += amount
                if item.get("taxable") is not False:
                    taxable_total += amount
            continue

        misc_charge = f64(part.get("misc_charge")) or 0
        if misc_charge > 0 and part_qty > 0:
            amount = round2(misc_charge * part_qty)
            total += amount
            taxable_total += amount  # legacy items default to taxable

    return round2(total), round2(taxable_total)


def align_totals_with_labors(totals: dict, labors: list) -> dict:
    src = normalize_totals_payload(totals or {})
    blocks_src = src.get("labors") if isinstance(src.get("labors"), list) else []

    out_blocks = []
    parts_base_sum = 0.0
    core_sum = 0.0
    misc_sum = 0.0
    misc_taxable_sum = 0.0

    normalized_labors = labors if isinstance(labors, list) else []

    for idx, block in enumerate(normalized_labors):
        block_src = blocks_src[idx] if idx < len(blocks_src) and isinstance(blocks_src[idx], dict) else {}
        parts = normalize_parts_payload((block or {}).get("parts") or [])

        parts_base = 0.0
        core_total = 0.0
        for part in parts:
            qty = i32(part.get("qty")) or 0
            if qty <= 0:
                continue
            price = round2(part.get("price") if part.get("price") is not None else 0)
            core_charge = round2(part.get("core_charge") if part.get("core_charge") is not None else 0)
            parts_base += round2(price * qty)
            core_total += round2(core_charge * qty)

        misc_total, misc_taxable_block = _calc_misc_total_from_parts(parts)
        misc_taxable_block = round2(misc_taxable_block)

        labor_base = round2(
            block_src.get("labor") if block_src.get("labor") is not None else block_src.get("labor_total")
        )
        shop_supply_total = round2(block_src.get("shop_supply_total"))
        labor_total = round2(labor_base + shop_supply_total)
        parts_total = round2(parts_base + core_total + misc_total)

        out_blocks.append(
            {
                "labor": labor_base,
                "labor_total": labor_total,
                "parts": round2(parts_base),
                "parts_total": parts_total,
                "core_total": round2(core_total),
                "misc_total": round2(misc_total),
                "misc_taxable_total": misc_taxable_block,
                "cost_total": round2(parts_base),
                "shop_supply_total": shop_supply_total,
                "labor_full_total": round2(labor_total + parts_total),
            }
        )

        parts_base_sum += parts_base
        core_sum += core_total
        misc_sum += misc_total
        misc_taxable_sum += misc_taxable_block

    labor_base_sum = round2(sum(round2(b.get("labor") or 0) for b in out_blocks))
    shop_supply_from_blocks = round2(sum(round2(b.get("shop_supply_total") or 0) for b in out_blocks))
    requested_shop_supply = round2(src.get("shop_supply_total"))
    shop_supply_sum = requested_shop_supply if requested_shop_supply > 0 else shop_supply_from_blocks

    if out_blocks:
        if shop_supply_sum > 0 and labor_base_sum > 0:
            allocated = 0.0
            for idx, block in enumerate(out_blocks):
                labor_base = round2(block.get("labor") or 0)
                if idx == len(out_blocks) - 1:
                    block_supply = round2(shop_supply_sum - allocated)
                else:
                    block_supply = round2(shop_supply_sum * (labor_base / labor_base_sum))
                    allocated = round2(allocated + block_supply)

                block["shop_supply_total"] = block_supply
                block["labor_total"] = round2(labor_base + block_supply)
                block["labor_full_total"] = round2(block["labor_total"] + round2(block.get("parts_total") or 0))
        else:
            for block in out_blocks:
                labor_base = round2(block.get("labor") or 0)
                block["shop_supply_total"] = 0.0
                block["labor_total"] = round2(labor_base)
                block["labor_full_total"] = round2(block["labor_total"] + round2(block.get("parts_total") or 0))

    shop_supply_sum = round2(sum(round2(b.get("shop_supply_total") or 0) for b in out_blocks)) if out_blocks else shop_supply_sum
    labor_total_sum = round2(labor_base_sum + shop_supply_sum)
    parts_base_sum = round2(parts_base_sum)
    core_sum = round2(core_sum)
    misc_sum = round2(misc_sum)
    misc_taxable_sum = round2(misc_taxable_sum)
    parts_total_sum = round2(parts_base_sum + core_sum + misc_sum)
    sales_tax_rate = round(float(src.get("sales_tax_rate") or 0) + 1e-12, 6)
    sales_tax_total = round2(src.get("sales_tax_total"))
    is_taxable = bool(src.get("is_taxable", False))
    misc_taxable_total = round2(
        src.get("misc_taxable_total") if src.get("misc_taxable_total") is not None else misc_taxable_sum
    )
    parts_taxable_total = round2(
        src.get("parts_taxable_total") if src.get("parts_taxable_total") is not None else round2(parts_base_sum + misc_taxable_total)
    )

    return {
        "labor": labor_base_sum,
        "labor_total": labor_total_sum,
        "parts": parts_base_sum,
        "parts_total": parts_total_sum,
        "core_total": core_sum,
        "misc_total": misc_sum,
        "misc_taxable_total": misc_taxable_total,
        "cost_total": parts_base_sum,
        "shop_supply_total": shop_supply_sum,
        "parts_taxable_total": parts_taxable_total,
        "sales_tax_rate": sales_tax_rate,
        "sales_tax_total": sales_tax_total,
        "is_taxable": is_taxable,
        "grand_total": round2(labor_total_sum + parts_total_sum + sales_tax_total),
        "labors": out_blocks,
    }


def normalize_saved_labors(raw, shop_db=None):
    if not isinstance(raw, list):
        return []

    part_id_values = []
    part_numbers = []
    for block in raw:
        if not isinstance(block, dict):
            continue
        for p in normalize_parts_payload(block.get("parts") or []):
            part_id = p.get("part_id")
            if part_id:
                part_id_values.append(part_id)
            pn = str(p.get("part_number") or "").strip()
            if pn:
                part_numbers.append(pn)

    core_base_by_id = {}
    core_base_by_number = {}
    if shop_db is not None and (part_id_values or part_numbers):
        parts_query = {"is_active": True}
        or_filters = []
        if part_id_values:
            or_filters.append({"_id": {"$in": list({x for x in part_id_values if x})}})
        if part_numbers:
            or_filters.append({"part_number": {"$in": list({x for x in part_numbers if x})}})
        if or_filters:
            parts_query["$or"] = or_filters

        for doc in shop_db.parts.find(
            parts_query,
            {"_id": 1, "part_number": 1, "core_has_charge": 1, "core_cost": 1},
        ):
            has_core_charge = bool(doc.get("core_has_charge"))
            core_cost = round2(doc.get("core_cost") or 0)
            if not has_core_charge or core_cost <= 0:
                continue
            part_id = doc.get("_id")
            if part_id:
                core_base_by_id[str(part_id)] = core_cost
            part_number = str(doc.get("part_number") or "").strip()
            if part_number:
                core_base_by_number[part_number] = core_cost

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
            part_id_str = str(p.get("part_id")) if p.get("part_id") else ""
            part_number_str = str(p.get("part_number") or "").strip()
            saved_core_charge = round2(p.get("core_charge") if p.get("core_charge") is not None else 0)
            core_charge_base = saved_core_charge
            if core_charge_base <= 0:
                core_charge_base = round2(
                    core_base_by_id.get(part_id_str)
                    if part_id_str
                    else core_base_by_number.get(part_number_str)
                )

            parts_out.append(
                {
                    "part_id": part_id_str,
                    "one_time_part": as_bool(p.get("one_time_part")),
                    "part_number": part_number_str,
                    "description": str(p.get("description") or "").strip(),
                    "qty": str(p.get("qty") if p.get("qty") is not None else ""),
                    "cost": str(p.get("cost") if p.get("cost") is not None else ""),
                    "price": str(p.get("price") if p.get("price") is not None else ""),
                    "core_charge": str(p.get("core_charge") if p.get("core_charge") is not None else ""),
                    "core_charge_base": str(core_charge_base if core_charge_base > 0 else ""),
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
    return format_date_mmddyyyy(dt)


def _fmt_dt_iso(dt):
    if isinstance(dt, datetime):
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return dt.isoformat()
    return ""


def _parse_iso_date_utc(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _to_iso_date(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%d")


def _start_of_week_monday(value):
    return value - timedelta(days=value.weekday())


def _start_of_month(value):
    return value.replace(day=1)


def _start_of_quarter(value):
    quarter_start_month = ((value.month - 1) // 3) * 3 + 1
    return value.replace(month=quarter_start_month, day=1)


def _start_of_year(value):
    return value.replace(month=1, day=1)


def _date_range_for_preset(preset: str, today):
    if preset == "today":
        return today, today
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "this_week":
        return _start_of_week_monday(today), today
    if preset == "last_week":
        this_week_start = _start_of_week_monday(today)
        last_week_start = this_week_start - timedelta(days=7)
        return last_week_start, this_week_start - timedelta(days=1)
    if preset == "this_month":
        return _start_of_month(today), today
    if preset == "last_month":
        this_month_start = _start_of_month(today)
        last_month_end = this_month_start - timedelta(days=1)
        return _start_of_month(last_month_end), last_month_end
    if preset == "this_quarter":
        return _start_of_quarter(today), today
    if preset == "last_quarter":
        this_quarter_start = _start_of_quarter(today)
        last_quarter_end = this_quarter_start - timedelta(days=1)
        return _start_of_quarter(last_quarter_end), last_quarter_end
    if preset == "this_year":
        return _start_of_year(today), today
    if preset == "last_year":
        this_year_start = _start_of_year(today)
        last_year_end = this_year_start - timedelta(days=1)
        return _start_of_year(last_year_end), last_year_end
    return None, None


def get_date_range_filters(args, from_key: str = "date_from", to_key: str = "date_to", preset_key: str = "date_preset"):
    return build_date_range_filters(args, from_key=from_key, to_key=to_key, preset_key=preset_key)


def append_and_filter(query: dict, extra_filter: dict):
    if not extra_filter:
        return query
    return {"$and": [query, extra_filter]}


def build_created_at_range_filter(created_from=None, created_to_exclusive=None):
    created_filter = {}
    if created_from:
        created_filter["$gte"] = created_from
    if created_to_exclusive:
        created_filter["$lt"] = created_to_exclusive
    if not created_filter:
        return None
    return {"created_at": created_filter}


def build_preferred_date_range_filter(date_field: str, created_from=None, created_to_exclusive=None):
    created_filter = build_created_at_range_filter(created_from, created_to_exclusive)
    if not created_filter:
        return None

    range_filter = created_filter["created_at"]
    return {
        "$or": [
            {date_field: range_filter},
            {date_field: {"$exists": False}, "created_at": range_filter},
            {date_field: None, "created_at": range_filter},
        ]
    }


def format_preferred_date_label(primary_dt, fallback_dt):
    return format_preferred_shop_date(primary_dt, fallback=fallback_dt)


def get_work_orders_totals(shop_db, query: dict):
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "labor_total": {"$sum": {"$ifNull": ["$totals.labor_total", {"$ifNull": ["$labor_total", 0]}]}},
                "parts_total": {"$sum": {"$ifNull": ["$totals.parts_total", {"$ifNull": ["$parts_total", 0]}]}},
                "sales_tax_total": {"$sum": {"$ifNull": ["$totals.sales_tax_total", {"$ifNull": ["$sales_tax_total", 0]}]}},
                "grand_total": {"$sum": {"$ifNull": ["$totals.grand_total", {"$ifNull": ["$grand_total", 0]}]}},
            }
        },
    ]

    rows = list(shop_db.work_orders.aggregate(pipeline))
    if not rows:
        return {
            "labor_total": 0.0,
            "parts_total": 0.0,
            "sales_tax_total": 0.0,
            "grand_total": 0.0,
        }

    row = rows[0] if isinstance(rows[0], dict) else {}
    return {
        "labor_total": round2(row.get("labor_total") or 0),
        "parts_total": round2(row.get("parts_total") or 0),
        "sales_tax_total": round2(row.get("sales_tax_total") or 0),
        "grand_total": round2(row.get("grand_total") or 0),
    }


def get_work_orders_list(
    shop_db,
    shop_id: ObjectId,
    page: int,
    per_page: int,
    q: str = "",
    paid_status: str = "all",
    created_from=None,
    created_to_exclusive=None,
):
    query = {"shop_id": shop_id, "is_active": True}

    if paid_status == "paid":
        query["status"] = "paid"
    elif paid_status == "unpaid":
        query["status"] = {"$ne": "paid"}

    search_filter = build_regex_search_filter(
        q,
        text_fields=["status"],
        numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
        object_id_fields=["_id", "customer_id", "unit_id", "shop_id", "tenant_id"],
    )

    if q:
        customer_ids = [
            c.get("_id")
            for c in shop_db.customers.find(
                {
                    "$or": [
                        {"company_name": {"$regex": q, "$options": "i"}},
                        {"first_name": {"$regex": q, "$options": "i"}},
                        {"last_name": {"$regex": q, "$options": "i"}},
                        {"phone": {"$regex": q, "$options": "i"}},
                        {"email": {"$regex": q, "$options": "i"}},
                        {"contacts.first_name": {"$regex": q, "$options": "i"}},
                        {"contacts.last_name": {"$regex": q, "$options": "i"}},
                        {"contacts.phone": {"$regex": q, "$options": "i"}},
                        {"contacts.email": {"$regex": q, "$options": "i"}},
                        {"address": {"$regex": q, "$options": "i"}},
                    ]
                },
                {"_id": 1},
            )
            if c.get("_id")
        ]

        unit_ids = [
            u.get("_id")
            for u in shop_db.units.find(
                {
                    "$or": [
                        {"unit_number": {"$regex": q, "$options": "i"}},
                        {"vin": {"$regex": q, "$options": "i"}},
                        {"make": {"$regex": q, "$options": "i"}},
                        {"model": {"$regex": q, "$options": "i"}},
                        {"type": {"$regex": q, "$options": "i"}},
                    ]
                },
                {"_id": 1},
            )
            if u.get("_id")
        ]

        extra = []
        if customer_ids:
            extra.append({"customer_id": {"$in": customer_ids}})
        if unit_ids:
            extra.append({"unit_id": {"$in": unit_ids}})

        if search_filter and extra:
            query = {"$and": [query, {"$or": [search_filter, *extra]}]}
        elif search_filter:
            query = {"$and": [query, search_filter]}
        elif extra:
            query = {"$and": [query, {"$or": extra}]}
    elif search_filter:
        query = {"$and": [query, search_filter]}

    created_at_filter = build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
    if created_at_filter:
        query = append_and_filter(query, created_at_filter)

    totals_summary = get_work_orders_totals(shop_db, query)

    rows, pagination = paginate_find(
        shop_db.work_orders,
        query,
        get_sort_params(request.args, [("work_order_date", -1), ("created_at", -1)], ["wo_number", "status", "work_order_date", "grand_total", "created_at"]),
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
        sales_tax_total = round2(totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else x.get("sales_tax_total"))
        grand_total = round2(totals.get("grand_total") if totals.get("grand_total") is not None else x.get("grand_total"))

        status = (x.get("status") or "open").strip().lower()

        items.append(
            {
                "id": str(x.get("_id")),
                "wo_number": x.get("wo_number"),
                "customer": customers_map.get(x.get("customer_id")) or "-",
                "date": format_preferred_date_label(x.get("work_order_date"), x.get("created_at")),
                "unit": units_map.get(x.get("unit_id")) or "-",
                "labor_total": labor_total,
                "parts_total": parts_total,
                "sales_tax_total": sales_tax_total,
                "grand_total": grand_total,
                "is_paid": status == "paid",
            }
        )

    return items, pagination, totals_summary


def get_estimates_list(
    shop_db,
    shop_id: ObjectId,
    page: int,
    per_page: int,
    q: str = "",
    created_from=None,
    created_to_exclusive=None,
):
    estimate_statuses = ["estimate", "estimated", "quote", "quoted"]
    query = {"shop_id": shop_id, "status": {"$in": estimate_statuses}, "is_active": True}

    search_filter = build_regex_search_filter(
        q,
        text_fields=["status"],
        numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
        object_id_fields=["_id", "customer_id", "unit_id", "shop_id", "tenant_id"],
    )

    if q:
        customer_ids = [
            c.get("_id")
            for c in shop_db.customers.find(
                {
                    "$or": [
                        {"company_name": {"$regex": q, "$options": "i"}},
                        {"first_name": {"$regex": q, "$options": "i"}},
                        {"last_name": {"$regex": q, "$options": "i"}},
                        {"phone": {"$regex": q, "$options": "i"}},
                        {"email": {"$regex": q, "$options": "i"}},
                        {"contacts.first_name": {"$regex": q, "$options": "i"}},
                        {"contacts.last_name": {"$regex": q, "$options": "i"}},
                        {"contacts.phone": {"$regex": q, "$options": "i"}},
                        {"contacts.email": {"$regex": q, "$options": "i"}},
                        {"address": {"$regex": q, "$options": "i"}},
                    ]
                },
                {"_id": 1},
            )
            if c.get("_id")
        ]

        unit_ids = [
            u.get("_id")
            for u in shop_db.units.find(
                {
                    "$or": [
                        {"unit_number": {"$regex": q, "$options": "i"}},
                        {"vin": {"$regex": q, "$options": "i"}},
                        {"make": {"$regex": q, "$options": "i"}},
                        {"model": {"$regex": q, "$options": "i"}},
                        {"type": {"$regex": q, "$options": "i"}},
                    ]
                },
                {"_id": 1},
            )
            if u.get("_id")
        ]

        extra = []
        if customer_ids:
            extra.append({"customer_id": {"$in": customer_ids}})
        if unit_ids:
            extra.append({"unit_id": {"$in": unit_ids}})

        if search_filter and extra:
            query = {"$and": [query, {"$or": [search_filter, *extra]}]}
        elif search_filter:
            query = {"$and": [query, search_filter]}
        elif extra:
            query = {"$and": [query, {"$or": extra}]}
    elif search_filter:
        query = {"$and": [query, search_filter]}

    created_at_filter = build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
    if created_at_filter:
        query = append_and_filter(query, created_at_filter)

    rows, pagination = paginate_find(
        shop_db.work_orders,
        query,
        get_sort_params(request.args, [("work_order_date", -1), ("created_at", -1)], ["wo_number", "status", "work_order_date", "grand_total", "created_at"]),
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
        sales_tax_total = round2(totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else x.get("sales_tax_total"))
        grand_total = round2(totals.get("grand_total") if totals.get("grand_total") is not None else x.get("grand_total"))

        status = (x.get("status") or "estimate").strip().lower()

        items.append(
            {
                "id": str(x.get("_id")),
                "wo_number": x.get("wo_number"),
                "customer": customers_map.get(x.get("customer_id")) or "-",
                "date": format_preferred_date_label(x.get("work_order_date"), x.get("created_at")),
                "unit": units_map.get(x.get("unit_id")) or "-",
                "labor_total": labor_total,
                "parts_total": parts_total,
                "sales_tax_total": sales_tax_total,
                "grand_total": grand_total,
                "status": status,
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
    name = get_main_contact_name(c, entity_type="customer")
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

    rate_rows = list(
        shop_db.labor_rates.find({"is_active": True}, {"_id": 1, "code": 1})
    )
    rates_by_id = {r.get("_id"): str(r.get("code") or "").strip() for r in rate_rows if r.get("_id")}

    def resolve_customer_rate_code(value):
        if isinstance(value, ObjectId):
            return rates_by_id.get(value, "")
        legacy = str(value or "").strip().lower()
        if legacy == "standart":
            return "standard"
        return legacy

    return [
        {
            "id": str(x["_id"]),
            "label": customer_label(x),
            "default_labor_rate": resolve_customer_rate_code(x.get("default_labor_rate")),
            "taxable": bool(x.get("taxable", False)),
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
            }

    customers = [selected_customer] if selected_customer else []

    units = []
    if customer_id:
        units = get_units(shop_db, customer_id)
        if unit_id and not any(u["id"] == str(unit_id) for u in units):
            unit_id = None

    # Generate a temporary ObjectId for attachment uploads before WO is created
    pending_attachment_id = str(_ObjId()) if not (form_state or {}).get("work_order_created") else ""

    ctx = {
        "sales_tax_context": _get_shop_sales_tax_context(shop, shop_db),
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
        "wo_number": (form_state or {}).get("wo_number"),
        "work_order_date": (form_state or {}).get("work_order_date") or get_active_shop_today_iso(),
        "today_date_input_value": get_active_shop_today_iso(),

        "initial_labors": (form_state or {}).get("initial_labors") or [],
        "initial_totals": normalize_totals_payload((form_state or {}).get("initial_totals") or {}),
        "work_order_status": (form_state or {}).get("work_order_status") or "open",
        "pending_attachment_id": pending_attachment_id,
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

    required_map, collect_errors = _collect_inventory_qty_by_part(shop_db, labors)
    errors.extend(collect_errors)

    for item in required_map.values():
        part_id = item.get("part_id")
        part_number = item.get("part_number") or ""
        qty = int(item.get("qty") or 0)
        if not part_id or qty <= 0:
            continue

        part_doc = shop_db.parts.find_one({"_id": part_id, "is_active": True}, {"in_stock": 1})
        if not part_doc:
            errors.append(f"Part '{part_number}' not found in inventory")
            continue

        current_stock = int(part_doc.get("in_stock") or 0)
        if current_stock < qty:
            errors.append(
                f"Insufficient stock for '{part_number}': "
                f"need {qty}, have {current_stock}"
            )
            continue

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

    required_map, collect_errors = _collect_inventory_qty_by_part(shop_db, labors)
    errors.extend(collect_errors)

    for item in required_map.values():
        part_id = item.get("part_id")
        part_number = item.get("part_number") or ""
        qty = int(item.get("qty") or 0)
        if not part_id or qty <= 0:
            continue

        part_doc = shop_db.parts.find_one({"_id": part_id, "is_active": True}, {"in_stock": 1})
        if not part_doc:
            errors.append(f"Part '{part_number}' not found when restoring")
            continue

        current_stock = int(part_doc.get("in_stock") or 0)
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

    old_parts_map, old_errors = _collect_inventory_qty_by_part(shop_db, old_labors)
    new_parts_map, new_errors = _collect_inventory_qty_by_part(shop_db, new_labors)
    errors.extend(old_errors)
    errors.extend(new_errors)

    all_part_keys = set(old_parts_map.keys()) | set(new_parts_map.keys())

    for part_key in all_part_keys:
        old_item = old_parts_map.get(part_key) or {}
        new_item = new_parts_map.get(part_key) or {}
        old_qty = int(old_item.get("qty") or 0)
        new_qty = int(new_item.get("qty") or 0)
        qty_diff = new_qty - old_qty

        if qty_diff == 0:
            continue

        part_id = new_item.get("part_id") or old_item.get("part_id")
        part_number = (new_item.get("part_number") or old_item.get("part_number") or "").strip()

        if not part_id:
            continue

        part_doc = shop_db.parts.find_one({"_id": part_id, "is_active": True}, {"in_stock": 1})
        if not part_doc:
            errors.append(f"Part '{part_number}' not found when adjusting")
            continue

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


def _resolve_part_for_core_tracking(shop_db, shop_id: ObjectId, part: dict, cache: dict):
    if as_bool((part or {}).get("one_time_part")):
        return None

    part_id = oid(part.get("part_id")) if isinstance(part, dict) else None
    part_number = str((part or {}).get("part_number") or "").strip()

    cache_key = None
    if part_id:
        cache_key = f"id:{str(part_id)}"
    elif part_number:
        cache_key = f"pn:{part_number.lower()}"

    if cache_key and cache_key in cache:
        return cache[cache_key]

    query = {"shop_id": shop_id, "is_active": True}
    if part_id:
        query["_id"] = part_id
    elif part_number:
        query["part_number"] = part_number
    else:
        return None

    doc = shop_db.parts.find_one(
        query,
        {
            "_id": 1,
            "part_number": 1,
            "description": 1,
            "core_has_charge": 1,
            "core_cost": 1,
        },
    )
    if cache_key:
        cache[cache_key] = doc
    return doc


def collect_unpaid_core_requirements(shop_db, shop_id: ObjectId, labors: list) -> dict:
    """
    Build map of cores that should be collected from customers.
    Rule: if part has core charge capability but line core_charge is 0, add qty to cores.
    Returns map: {part_id_str: {part_id, part_number, description, core_cost, quantity}}
    """
    if not isinstance(labors, list):
        return {}

    cache = {}
    required = {}

    for labor_block in labors:
        if not isinstance(labor_block, dict):
            continue
        parts = labor_block.get("parts") or []
        if not isinstance(parts, list):
            continue

        for part in parts:
            if not isinstance(part, dict):
                continue

            qty = i32(part.get("qty")) or 0
            if qty <= 0:
                continue

            part_doc = _resolve_part_for_core_tracking(shop_db, shop_id, part, cache)
            if not part_doc:
                continue

            has_core_charge = bool(part_doc.get("core_has_charge"))
            core_cost = round2(part_doc.get("core_cost") or 0)
            if not has_core_charge or core_cost <= 0:
                continue

            charged_core = round2(part.get("core_charge") if part.get("core_charge") is not None else 0)
            if charged_core > 0:
                continue

            part_oid = part_doc.get("_id")
            if not part_oid:
                continue

            key = str(part_oid)
            if key not in required:
                required[key] = {
                    "part_id": part_oid,
                    "part_number": str(part_doc.get("part_number") or "").strip(),
                    "description": str(part_doc.get("description") or "").strip(),
                    "core_cost": core_cost,
                    "quantity": 0,
                }
            required[key]["quantity"] += qty

    return required


def build_core_delta(old_required: dict, new_required: dict) -> list:
    deltas = []
    all_keys = set(old_required.keys()) | set(new_required.keys())

    for key in all_keys:
        old_item = old_required.get(key) or {}
        new_item = new_required.get(key) or {}

        old_qty = int(old_item.get("quantity") or 0)
        new_qty = int(new_item.get("quantity") or 0)
        qty_delta = new_qty - old_qty
        if qty_delta == 0:
            continue

        source = new_item if new_item else old_item
        deltas.append(
            {
                "part_id": source.get("part_id"),
                "part_number": source.get("part_number") or "",
                "description": source.get("description") or "",
                "core_cost": round2(source.get("core_cost") or 0),
                "old_quantity": old_qty,
                "new_quantity": new_qty,
                "qty_delta": qty_delta,
            }
        )

    return deltas


def apply_core_delta(shop_db, shop: dict, core_deltas: list, user_id: ObjectId) -> dict:
    if not isinstance(core_deltas, list) or not core_deltas:
        return {"ok": True, "changes": [], "errors": []}

    cores_coll = shop_db.cores
    now = utcnow()
    changes = []
    errors = []

    for item in core_deltas:
        part_id = item.get("part_id")
        qty_delta = int(item.get("qty_delta") or 0)
        if not part_id or qty_delta == 0:
            continue

        base_query = {
            "shop_id": shop.get("_id"),
            "part_id": part_id,
            "is_active": {"$ne": False},
        }

        try:
            if qty_delta > 0:
                cores_coll.update_one(
                    base_query,
                    {
                        "$inc": {"quantity": qty_delta},
                        "$set": {
                            "part_number": item.get("part_number") or "",
                            "description": item.get("description") or "",
                            "core_cost": round2(item.get("core_cost") or 0),
                            "tenant_id": shop.get("tenant_id"),
                            "updated_at": now,
                            "updated_by": user_id,
                            "is_active": True,
                        },
                        "$setOnInsert": {
                            "created_at": now,
                            "created_by": user_id,
                        },
                    },
                    upsert=True,
                )
            else:
                current_doc = cores_coll.find_one(base_query, {"quantity": 1})
                current_qty = int((current_doc or {}).get("quantity") or 0)
                new_qty = current_qty + qty_delta

                if new_qty > 0:
                    cores_coll.update_one(
                        base_query,
                        {
                            "$set": {
                                "quantity": new_qty,
                                "updated_at": now,
                                "updated_by": user_id,
                            }
                        },
                    )
                else:
                    cores_coll.delete_one(base_query)

            changes.append(
                {
                    "part_id": str(part_id),
                    "part_number": item.get("part_number") or "",
                    "qty_delta": qty_delta,
                    "old_quantity": int(item.get("old_quantity") or 0),
                    "new_quantity": int(item.get("new_quantity") or 0),
                }
            )
        except Exception as exc:
            errors.append(
                f"Failed to sync core for part '{item.get('part_number') or str(part_id)}': {str(exc)}"
            )

    return {"ok": len(errors) == 0, "changes": changes, "errors": errors}


def sync_work_order_cores(shop_db, shop: dict, old_labors: list, new_labors: list, user_id: ObjectId) -> dict:
    old_required = collect_unpaid_core_requirements(shop_db, shop.get("_id"), old_labors)
    new_required = collect_unpaid_core_requirements(shop_db, shop.get("_id"), new_labors)
    core_deltas = build_core_delta(old_required, new_required)
    apply_result = apply_core_delta(shop_db, shop, core_deltas, user_id)

    return {
        "old_required": old_required,
        "new_required": new_required,
        "deltas": core_deltas,
        "changes": apply_result.get("changes") or [],
        "errors": apply_result.get("errors") or [],
        "ok": bool(apply_result.get("ok")),
    }


@work_orders_bp.get("/work_orders")
@login_required
@permission_required("work_orders.view")
def work_orders_page():
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
    labor_re = re.compile(r"^(?:labors|blocks)\[(\d+)\]\[(labor_description|labor_hours|labor_rate_code|labor_total_ui|labor_full_total|assigned_mechanics_json)\]$")
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

    totals = align_totals_with_labors(normalize_totals_payload(totals), labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        _is_customer_taxable(shop_db, customer_id),
    )
    work_order_date = shop_local_date_to_utc(request.form.get("work_order_date"), default_today=True)

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

    # Get next work order number
    wo_number = get_next_wo_number(shop_db, shop["_id"])

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "wo_number": wo_number,
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": "open",
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
            "do_not_track_inventory": bool(p.get("do_not_track_inventory")),
            "has_selling_price": bool(p.get("has_selling_price")),
            "selling_price": float(p.get("selling_price") or 0),
            "core_has_charge": bool(p.get("core_has_charge")),
            "core_cost": float(p.get("core_cost") or 0),
            "misc_has_charge": bool(p.get("misc_has_charge")),
            "misc_charges": misc_items,
        })

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
                "do_not_track_inventory": bool(p.get("do_not_track_inventory")),
                "has_selling_price": bool(p.get("has_selling_price")),
                "selling_price": float(p.get("selling_price") or 0),
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
    unit_mileage = data.get("unit_mileage")
    work_order_date = shop_local_date_to_utc(data.get("work_order_date"), default_today=True)

    if not isinstance(labors, list):
        return jsonify({"ok": False, "error": "labors_required"}), 200

    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    labors = apply_assignments_to_labors(labors, mechanics_by_id)
    totals = align_totals_with_labors(totals, labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        _is_customer_taxable(shop_db, wo.get("customer_id")),
    )

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

    # ✅ Update unit mileage if provided
    if unit_mileage is not None:
        unit_id = wo.get("unit_id")
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

    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "labors": labors,
                "work_order_date": work_order_date,
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

    payment_result = shop_db.work_order_payments.insert_one(payment_doc)
    payment_id = payment_result.inserted_id

    # Reassign pending attachments to the real payment ID
    pending_att_id = oid(data.get("pending_attachment_id"))
    if pending_att_id:
        shop_db.attachments.update_many(
            {"entity_id": pending_att_id},
            {"$set": {"entity_id": payment_id}},
        )

    refreshed_wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True}) or wo
    payment_summary = _sync_work_order_payment_state(shop_db, refreshed_wo, user_id, now) or {
        "status": "open",
        "is_fully_paid": False,
    }

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
    )

    refreshed_wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True}) or wo
    summary = _sync_work_order_payment_state(shop_db, refreshed_wo, user_id, now) or {
        "status": "open",
        "paid_amount": 0.0,
        "remaining_balance": _work_order_grand_total(wo),
        "is_fully_paid": False,
    }

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
        customer_ids = [
            c.get("_id")
            for c in shop_db.customers.find(
                {
                    "$or": [
                        {"company_name": {"$regex": q, "$options": "i"}},
                        {"first_name": {"$regex": q, "$options": "i"}},
                        {"last_name": {"$regex": q, "$options": "i"}},
                        {"phone": {"$regex": q, "$options": "i"}},
                        {"email": {"$regex": q, "$options": "i"}},
                        {"contacts.first_name": {"$regex": q, "$options": "i"}},
                        {"contacts.last_name": {"$regex": q, "$options": "i"}},
                        {"contacts.phone": {"$regex": q, "$options": "i"}},
                        {"contacts.email": {"$regex": q, "$options": "i"}},
                    ]
                },
                {"_id": 1},
            )
            if c.get("_id")
        ]

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

    payments, payments_pagination = paginate_find(
        shop_db.work_order_payments,
        payments_query,
        sort=[("payment_date", -1), ("created_at", -1)],
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

    # Remove cores generated by this work order unpaid-core logic.
    core_sync = sync_work_order_cores(shop_db, shop, labors, [], user_id)

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
        "cores_synced": len(core_sync.get("changes") or []) > 0,
        "core_changes": core_sync.get("changes") or [],
        "core_sync_errors": core_sync.get("errors") or [],
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

def _build_wo_pdf_context(shop_db, shop, wo):
    """Build the template context dict used by work_order_pdf.html."""
    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}) or {}
    cust_name = customer_label(customer)
    customer_email_val = get_main_contact_email(customer, entity_type="customer")
    customer_phone = get_main_contact_phone(customer, entity_type="customer")
    customer_address = str(customer.get("address") or "").strip()

    unit = shop_db.units.find_one({"_id": wo.get("unit_id")}) or {}
    unit_lbl = unit_label(unit)
    unit_vin = str(unit.get("vin") or "").strip()
    unit_mileage = str(unit.get("mileage") or "").strip()
    unit_number = str(unit.get("unit_number") or "").strip()
    unit_year = str(unit.get("year") or "").strip()
    unit_make = str(unit.get("make") or "").strip()
    unit_model = str(unit.get("model") or "").strip()

    shop_name = str(shop.get("name") or "").strip()
    addr_parts = [shop.get("address_line"), shop.get("city"), shop.get("state"), shop.get("zip")]
    shop_address = ", ".join(str(p).strip() for p in addr_parts if p and str(p).strip())
    contact_parts = [str(shop.get("phone") or "").strip(), str(shop.get("email") or "").strip()]
    shop_contact = " · ".join(p for p in contact_parts if p)

    wo_date = wo.get("work_order_date") or wo.get("created_at")
    wo_date_label = format_date_mmddyyyy(wo_date) if wo_date else ""
    wo_number = str(wo.get("wo_number") or str(wo["_id"]))

    raw_labors = wo.get("labors") or []
    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    totals_labors = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []

    labors_built = []
    for i, block in enumerate(raw_labors):
        if not isinstance(block, dict):
            continue
        labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        desc = str(labor_src.get("description") or block.get("labor_description") or "").strip()
        hours = str(labor_src.get("hours") or block.get("labor_hours") or "").strip()
        rate_code = str(labor_src.get("rate_code") or block.get("labor_rate_code") or "").strip()

        block_totals = totals_labors[i] if i < len(totals_labors) and isinstance(totals_labors[i], dict) else {}
        labor_base = round2(block_totals.get("labor") or 0)
        shop_supply = round2(block_totals.get("shop_supply_total") or 0)
        labor_total_val = round2(block_totals.get("labor_total") or block_totals.get("labor") or 0)
        parts_subtotal = round2(block_totals.get("parts_total") or 0)
        core_t = round2(block_totals.get("core_total") or 0)
        misc_t = round2(block_totals.get("misc_total") or 0)
        block_total = round2(block_totals.get("labor_full_total") or (labor_total_val + parts_subtotal))

        parts_detail = []
        for p in normalize_parts_payload(block.get("parts") or []):
            qty = i32(p.get("qty")) or 0
            if qty <= 0:
                continue
            part_number = str(p.get("part_number") or "").strip()
            description = str(p.get("description") or "").strip()
            price = round2(p.get("price") or 0)
            core_per = round2(p.get("core_charge") or 0)
            misc_per = round2(p.get("misc_charge") or 0)
            misc_desc = str(p.get("misc_charge_description") or "").strip()
            parts_detail.append({
                "part_number": part_number,
                "description": description,
                "label": part_number or description,
                "qty": qty,
                "price": price,
                "price_fmt": f"${price:.2f}",
                "total": round2(price * qty),
                "core": round2(core_per * qty),
                "misc": round2(misc_per * qty),
                "misc_desc": misc_desc,
            })

        labors_built.append({
            "labor_desc": desc or f"Labor {i + 1}",
            "hours": hours,
            "labor_hours": hours,
            "rate_code": rate_code,
            "labor_base": labor_base,
            "shop_supply": shop_supply,
            "labor_total": labor_total_val,
            "labor_total_fmt": f"${labor_total_val:.2f}",
            "parts_subtotal": parts_subtotal,
            "core_total": core_t,
            "misc_total": misc_t,
            "block_total": block_total,
            "parts": parts_detail,
        })

    t = {
        "labor_total": round2(totals_doc.get("labor_total") or totals_doc.get("labor") or 0),
        "parts_total": round2(totals_doc.get("parts_total") or totals_doc.get("parts") or 0),
        "core_total": round2(totals_doc.get("core_total") or 0),
        "shop_supply_total": round2(totals_doc.get("shop_supply_total") or 0),
        "misc_total": round2(totals_doc.get("misc_total") or 0),
        "grand_total": _work_order_grand_total(wo),
    }

    pdf_cfg = shop_db.pdf_design.find_one({"shop_id": shop["_id"]}) or {}

    # Build logo as base64 data URI (xhtml2pdf can't fetch localhost URLs)
    shop_logo_url = ""
    if pdf_cfg.get("show_logo", True) and shop.get("logo_data"):
        ct = shop.get("logo_content_type") or "image/png"
        b64 = base64.b64encode(bytes(shop["logo_data"])).decode("ascii")
        shop_logo_url = f"data:{ct};base64,{b64}"

    return dict(
        shop_name=shop_name,
        shop_address=shop_address,
        shop_contact=shop_contact,
        wo_number=wo_number,
        wo_date_label=wo_date_label,
        cust_name=cust_name,
        customer_email=customer_email_val,
        customer_phone=customer_phone,
        customer_address=customer_address,
        unit_label=unit_lbl,
        unit_vin=unit_vin,
        unit_mileage=unit_mileage,
        unit_number=unit_number,
        unit_year=unit_year,
        unit_make=unit_make,
        unit_model=unit_model,
        labors=labors_built,
        t=t,
        pdf_cfg=pdf_cfg,
        shop_logo_url=shop_logo_url,
    )


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/download-pdf")
@login_required
@permission_required("work_orders.view")
def api_download_work_order_pdf(work_order_id):
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
    addr_parts = [shop.get("address_line"), shop.get("city"), shop.get("state"), shop.get("zip")]
    shop_address = ", ".join(str(p).strip() for p in addr_parts if p and str(p).strip())
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

    html_body = render_template(
        "emails/payment_receipt_email.html",
        shop_name=shop_name,
        shop_address=shop_address,
        shop_contact=shop_contact,
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

    # Enrich parts with current core/misc data from the parts collection
    raw_parts = doc.get("parts") or []
    part_ids = []
    for p in raw_parts:
        pid_str = p.get("part_id")
        if pid_str:
            o = oid(pid_str)
            if o:
                part_ids.append(o)

    parts_lookup = {}
    if part_ids:
        for pdoc in shop_db.parts.find(
            {"_id": {"$in": part_ids}, "is_active": True},
            {
                "_id": 1,
                "average_cost": 1,
                "core_has_charge": 1,
                "core_cost": 1,
                "misc_has_charge": 1,
                "misc_charges": 1,
                "has_selling_price": 1,
                "selling_price": 1,
            },
        ):
            parts_lookup[str(pdoc["_id"])] = pdoc

    enriched_parts = []
    for p in raw_parts:
        ep = dict(p)
        pid_str = p.get("part_id")
        if pid_str and pid_str in parts_lookup:
            live = parts_lookup[pid_str]
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
            # Update cost to current average
            if live.get("average_cost") is not None:
                ep["cost"] = float(live["average_cost"])
        enriched_parts.append(ep)

    return jsonify({
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "labor_hours": doc.get("labor_hours"),
        "labor_rate_code": doc.get("labor_rate_code"),
        "allow_discount": bool(doc.get("allow_discount")),
        "parts": enriched_parts,
    }), 200