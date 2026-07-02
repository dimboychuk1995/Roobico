"""
Деньги work orders: тоталы, налоги, нормализация labor/parts-пейлоадов,
нумерация. Часть распила routes.py на сервисный слой; Flask-контекст
здесь не используется — все зависимости приходят аргументами.
"""
from __future__ import annotations

import json

from bson import ObjectId

from app.extensions import get_master_db
from app.utils.sales_tax import get_shop_zip_code
from app.blueprints.work_orders.services.common import (
    as_bool,
    f64,
    i32,
    oid,
    round2,
    utcnow,
)


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

    source = rate_doc.get("source") or None
    # API-Ninjas free tier returns only the state rate (county/city/special are
    # premium-only), so we flag this so the UI can warn the user that the
    # auto-pulled rate is a STATE-only estimate, not the full combined rate.
    is_state_only_estimate = bool(
        source == "api_ninjas"
        and not (rate_doc.get("county_rate") or 0)
        and not (rate_doc.get("city_rate") or 0)
        and not (rate_doc.get("special_rate") or 0)
    )

    return {
        "zip_code": zip_code,
        "rate": round(rate + 1e-12, 6),
        "source": source,
        "is_state_only_estimate": is_state_only_estimate,
        "state": rate_doc.get("state") or "",
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

    # Snapshot current labor rate values per rate_code so the WO keeps the rate
    # that was in effect at save time (rates may later be edited or deleted).
    labor_rate_map: dict[str, float] = {}
    if shop_db is not None:
        try:
            for r in shop_db.labor_rates.find({}, {"code": 1, "hourly_rate": 1}):
                code = str(r.get("code") or "").strip()
                if not code:
                    continue
                try:
                    labor_rate_map[code] = float(r.get("hourly_rate") or 0)
                except (ValueError, TypeError):
                    labor_rate_map[code] = 0.0
        except Exception:
            labor_rate_map = {}

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

        # Resolve hourly rate snapshot: prefer value explicitly carried on the
        # incoming payload, otherwise look up the current value for this code.
        try:
            incoming_rate = labor_src.get("hourly_rate")
            labor_hourly_rate = float(incoming_rate) if incoming_rate not in (None, "") else 0.0
        except (ValueError, TypeError):
            labor_hourly_rate = 0.0
        if labor_hourly_rate <= 0 and labor_rate_code:
            labor_hourly_rate = float(labor_rate_map.get(labor_rate_code) or 0.0)
        labor_hourly_rate = round2(labor_hourly_rate)

        issue_description = str(
            labor_src.get("issue_description")
            if labor_src.get("issue_description") is not None
            else block.get("issue_description")
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
                    "user_id": user_id,
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
                    "hourly_rate": labor_hourly_rate,
                    "assigned_mechanics": assigned_mechanics,
                    "issue_description": issue_description,
                },
                "parts": parts_out,
            }
        )

    return out
