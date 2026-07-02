"""
Инвентарь и core-charge синхронизация work orders: списание/возврат деталей
со склада при изменении WO, учёт невыплаченных core-требований.
"""
from __future__ import annotations

from bson import ObjectId

from app.blueprints.work_orders.services.common import as_bool, f64, i32, oid, round2, utcnow


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
        # Allow stock to go negative — operators must be able to create the WO
        # even if inventory is short.
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
        # Allow stock to go negative — operators must be able to save the WO
        # even if inventory is short; we'll surface the negative balance
        # elsewhere instead of blocking the save.
        new_stock = current_stock - qty_diff

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
