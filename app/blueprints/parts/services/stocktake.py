"""
Инвентаризация (stocktake) без остановки склада.

Принцип: склад НЕ замораживается. В момент ввода факта по позиции фиксируется
expected_at_count — системный остаток этой локации прямо сейчас. Расхождение
variance = counted - expected_at_count применяется при завершении сессии
ДЕЛЬТОЙ через apply_stock_change, поэтому приёмки/списания WO, случившиеся
после подсчёта, не затираются поправкой.

Коллекции (shop DB):
- stocktakes: сессии {shop_id, number, name, scope, status: open|completed|cancelled, totals}
- stocktake_items: позиции (part, location) c expected/counted/variance
"""
from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.blueprints.parts.services.stock import (
    PART_STOCK_PROJECTION,
    apply_stock_change,
    get_location_breakdown,
    materialize_location_rows,
)
from app.utils.parts_locations import (
    collect_descendant_ids,
    location_path_map,
)


def utcnow():
    return datetime.now(timezone.utc)


def get_next_stocktake_number(shop_db, shop_id) -> int:
    counter = shop_db.counters.find_one_and_update(
        {"_id": f"stocktake_number_{shop_id}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(counter.get("seq") or 1)


def get_current_location_qty(shop_db, shop_id, part_id, location_id) -> int:
    """Системный остаток локации на данный момент (виртуальный «хвост» учтён)."""
    part = shop_db.parts.find_one({"_id": part_id}, PART_STOCK_PROJECTION)
    if not part:
        return 0
    for row in get_location_breakdown(shop_db, shop_id, part):
        if row.get("location_id") == location_id:
            return int(row.get("qty") or 0)
    return 0


def create_stocktake(shop_db, shop, user_id, name="", location_id=None, category_id=None):
    """
    Создаёт сессию и генерирует позиции: по одной на каждую пару
    (part, location) в охвате. location_id задаёт поддерево локаций
    (считаются и все подлокации). Возвращает (stocktake_doc, None) или
    (None, error).
    """
    shop_id = shop["_id"]
    now = utcnow()

    all_locations = list(shop_db.parts_locations.find({"shop_id": shop_id}))
    paths = location_path_map(all_locations)

    scope_location_ids = None
    scope_location_path = ""
    if location_id is not None:
        scope_location_ids = collect_descendant_ids(all_locations, location_id)
        if not scope_location_ids:
            return None, "Location not found"
        scope_location_path = paths.get(str(location_id), "")

    category_name = ""
    if category_id is not None:
        cat = shop_db.parts_categories.find_one({"_id": category_id, "shop_id": shop_id})
        if not cat:
            return None, "Category not found"
        category_name = str(cat.get("name") or "")

    parts_query = {
        "shop_id": shop_id,
        "is_active": True,
        "do_not_track_inventory": {"$ne": True},
    }
    if category_id is not None:
        parts_query["category_id"] = category_id

    parts = list(shop_db.parts.find(parts_query, dict(PART_STOCK_PROJECTION, description=1)))
    if not parts:
        return None, "No tracked parts match the selected scope"

    # Одним запросом собираем все строки остатков магазина по нужным партам,
    # чтобы не делать find на каждый парт.
    part_ids = [p["_id"] for p in parts]
    rows_by_part: dict = {}
    for row in shop_db.part_location_stock.find({"shop_id": shop_id, "part_id": {"$in": part_ids}}):
        rows_by_part.setdefault(row.get("part_id"), []).append(row)

    stocktake_id = None
    items = []
    for part in parts:
        rows = rows_by_part.get(part["_id"]) or []
        rows_sum = sum(int(r.get("qty") or 0) for r in rows)
        remainder = int(part.get("in_stock") or 0) - rows_sum
        if remainder != 0:
            # Легаси-«хвост» материализуем в primary-локацию, чтобы позиция
            # инвентаризации соответствовала реальной строке остатка.
            materialize_location_rows(shop_db, shop_id, part, user_id=user_id)
            rows = list(shop_db.part_location_stock.find(
                {"shop_id": shop_id, "part_id": part["_id"]}
            ))

        seen_locations = set()
        for row in rows:
            loc = row.get("location_id")
            if scope_location_ids is not None and loc not in scope_location_ids:
                continue
            loc_key = str(loc) if loc else ""
            if loc_key in seen_locations:
                continue
            seen_locations.add(loc_key)
            items.append(_new_item(shop_id, part, loc, paths, int(row.get("qty") or 0), now))

        # Парт вообще без строк остатков (in_stock == 0): включаем позицию в
        # primary-локации, чтобы фактические находки тоже были посчитаны.
        if not rows:
            primary = part.get("location_id")
            if scope_location_ids is None or primary in scope_location_ids:
                items.append(_new_item(shop_id, part, primary, paths, 0, now))

    if not items:
        return None, "No stock positions match the selected scope"

    number = get_next_stocktake_number(shop_db, shop_id)
    stocktake_doc = {
        "shop_id": shop_id,
        "tenant_id": shop.get("tenant_id"),
        "number": number,
        "name": str(name or "").strip(),
        "scope": {
            "location_id": location_id,
            "location_path": scope_location_path,
            "category_id": category_id,
            "category_name": category_name,
        },
        "status": "open",
        "items_total": len(items),
        "is_active": True,
        "created_at": now,
        "created_by": user_id,
        "updated_at": now,
        "updated_by": user_id,
    }
    res = shop_db.stocktakes.insert_one(stocktake_doc)
    stocktake_id = res.inserted_id
    stocktake_doc["_id"] = stocktake_id

    for item in items:
        item["stocktake_id"] = stocktake_id
    shop_db.stocktake_items.insert_many(items)

    return stocktake_doc, None


def _new_item(shop_id, part, location_id, paths, expected_initial, now):
    loc_key = str(location_id) if location_id else ""
    return {
        "shop_id": shop_id,
        "part_id": part["_id"],
        "part_number": str(part.get("part_number") or ""),
        "description": str(part.get("description") or ""),
        "location_id": location_id,
        "location_path": paths.get(loc_key) or ("Unassigned" if not loc_key else "Deleted location"),
        "expected_initial": int(expected_initial),
        "expected_at_count": None,
        "counted_qty": None,
        "variance": None,
        "average_cost": float(part.get("average_cost") or 0.0),
        "status": "pending",
        "counted_at": None,
        "counted_by": None,
        "applied": False,
        "created_at": now,
    }


def count_stocktake_item(shop_db, shop, stocktake, item_id, counted_qty, user_id):
    """
    Сохранить факт по позиции. Снимает expected_at_count = системный остаток
    локации ПРЯМО СЕЙЧАС (не на старте сессии) — движения после подсчёта
    учитываются отдельно и не искажают variance. Повторный ввод — пересчёт.
    """
    if stocktake.get("status") != "open":
        return None, "Stocktake is not open"

    counted_qty = int(counted_qty)
    if counted_qty < 0:
        return None, "Counted quantity cannot be negative"

    item = shop_db.stocktake_items.find_one({"_id": item_id, "stocktake_id": stocktake["_id"]})
    if not item:
        return None, "Item not found"

    part = shop_db.parts.find_one({"_id": item.get("part_id")}, PART_STOCK_PROJECTION)
    if not part:
        return None, "Part not found"

    expected = get_current_location_qty(shop_db, shop["_id"], item["part_id"], item.get("location_id"))
    variance = counted_qty - expected
    now = utcnow()

    shop_db.stocktake_items.update_one(
        {"_id": item["_id"]},
        {"$set": {
            "counted_qty": counted_qty,
            "expected_at_count": int(expected),
            "variance": int(variance),
            "average_cost": float(part.get("average_cost") or 0.0),
            "status": "counted",
            "counted_at": now,
            "counted_by": user_id,
        }},
    )
    item.update({
        "counted_qty": counted_qty,
        "expected_at_count": int(expected),
        "variance": int(variance),
        "status": "counted",
        "counted_at": now,
    })
    return item, None


def add_stocktake_item(shop_db, shop, stocktake, part_id, location_id, user_id):
    """Добавить позицию, найденную при счёте, но отсутствующую в списке."""
    if stocktake.get("status") != "open":
        return None, "Stocktake is not open"

    part = shop_db.parts.find_one(
        {"_id": part_id, "shop_id": shop["_id"], "is_active": True},
        dict(PART_STOCK_PROJECTION, description=1),
    )
    if not part:
        return None, "Part not found"
    if bool(part.get("do_not_track_inventory")):
        return None, "Inventory tracking is disabled for this part"

    existing = shop_db.stocktake_items.find_one({
        "stocktake_id": stocktake["_id"],
        "part_id": part_id,
        "location_id": location_id,
    })
    if existing:
        return existing, None

    paths = location_path_map(list(shop_db.parts_locations.find({"shop_id": shop["_id"]})))
    expected = get_current_location_qty(shop_db, shop["_id"], part_id, location_id)
    item = _new_item(shop["_id"], part, location_id, paths, expected, utcnow())
    item["stocktake_id"] = stocktake["_id"]
    res = shop_db.stocktake_items.insert_one(item)
    item["_id"] = res.inserted_id

    shop_db.stocktakes.update_one(
        {"_id": stocktake["_id"]},
        {"$inc": {"items_total": 1}, "$set": {"updated_at": utcnow(), "updated_by": user_id}},
    )
    return item, None


def build_recount_flags(shop_db, shop, stocktake, items) -> set:
    """
    id позиций, по которым были движения склада ПОСЛЕ подсчёта (кроме поправок
    этой же инвентаризации) — им рекомендуется пересчёт перед завершением.
    """
    counted = [it for it in items if it.get("status") == "counted" and it.get("counted_at")]
    if not counted:
        return set()

    min_counted = min(it["counted_at"] for it in counted)
    part_ids = list({it["part_id"] for it in counted})

    movements = shop_db.inventory_movements.find(
        {
            "shop_id": shop["_id"],
            "part_id": {"$in": part_ids},
            "created_at": {"$gt": min_counted},
        },
        {"part_id": 1, "location_id": 1, "created_at": 1, "type": 1, "ref": 1},
    )

    by_part_loc: dict = {}
    st_id = stocktake["_id"]
    for mv in movements:
        ref = mv.get("ref") or {}
        if mv.get("type") == "stocktake" and ref.get("id") == st_id:
            continue
        key = (mv.get("part_id"), mv.get("location_id"))
        stamps = by_part_loc.setdefault(key, [])
        stamps.append(mv.get("created_at"))

    flagged = set()
    for it in counted:
        key = (it["part_id"], it.get("location_id"))
        for stamp in by_part_loc.get(key, []):
            if stamp and stamp > it["counted_at"]:
                flagged.add(str(it["_id"]))
                break
    return flagged


def complete_stocktake(shop_db, shop, stocktake, user_id, zero_uncounted=False):
    """
    Применяет расхождения: для каждой посчитанной позиции с variance != 0 —
    apply_stock_change(variance) в её локацию с движением "stocktake".

    zero_uncounted=False (циклический пересчёт): непосчитанные позиции не
    трогаются. zero_uncounted=True (полная инвентаризация): непосчитанное
    считается ненайденным и обнуляется — тоже дельтой от остатка НА МОМЕНТ
    завершения, чтобы не съесть параллельные приёмки/списания.

    Возвращает (summary, None) | (None, err).
    """
    if stocktake.get("status") != "open":
        return None, "Stocktake is not open"

    items = list(shop_db.stocktake_items.find({"stocktake_id": stocktake["_id"]}))
    counted = [it for it in items if it.get("status") == "counted"]
    pending = [it for it in items if it.get("status") != "counted"]

    now = utcnow()
    ref = {
        "kind": "stocktake",
        "id": stocktake["_id"],
        "label": f"ST-{stocktake.get('number')}",
    }

    adjusted = 0
    zeroed = 0
    variance_qty = 0
    shortage_value = 0.0
    overage_value = 0.0
    errors = []

    def _tally(variance, avg_cost):
        nonlocal variance_qty, shortage_value, overage_value
        variance_qty += variance
        value = variance * float(avg_cost or 0.0)
        if value < 0:
            shortage_value += -value
        else:
            overage_value += value

    for it in counted:
        variance = int(it.get("variance") or 0)
        if variance == 0:
            continue
        result = apply_stock_change(
            shop_db, shop["_id"], it["part_id"], variance, "stocktake",
            user_id=user_id, location_id=it.get("location_id"), ref=ref,
        )
        if not result["ok"]:
            errors.append(f"{it.get('part_number')}: {result.get('error')}")
            continue

        adjusted += 1
        _tally(variance, it.get("average_cost"))

        shop_db.stocktake_items.update_one(
            {"_id": it["_id"]},
            {"$set": {"applied": True, "applied_at": now}},
        )

    if zero_uncounted:
        for it in pending:
            current = get_current_location_qty(
                shop_db, shop["_id"], it["part_id"], it.get("location_id")
            )
            variance = -int(current)
            if variance != 0:
                result = apply_stock_change(
                    shop_db, shop["_id"], it["part_id"], variance, "stocktake",
                    user_id=user_id, location_id=it.get("location_id"), ref=ref,
                )
                if not result["ok"]:
                    errors.append(f"{it.get('part_number')}: {result.get('error')}")
                    continue
                adjusted += 1
                _tally(variance, it.get("average_cost"))

            zeroed += 1
            shop_db.stocktake_items.update_one(
                {"_id": it["_id"]},
                {"$set": {
                    "counted_qty": 0,
                    "expected_at_count": int(current),
                    "variance": variance,
                    "status": "counted",
                    "auto_zeroed": True,
                    "counted_at": now,
                    "counted_by": user_id,
                    "applied": variance != 0,
                    "applied_at": now if variance != 0 else None,
                }},
            )

    totals = {
        "items_total": len(items),
        "items_counted": len(counted),
        "items_uncounted": 0 if zero_uncounted else len(pending),
        "items_zeroed": zeroed,
        "items_adjusted": adjusted,
        "variance_qty": variance_qty,
        "shortage_value": round(shortage_value, 2),
        "overage_value": round(overage_value, 2),
    }

    shop_db.stocktakes.update_one(
        {"_id": stocktake["_id"]},
        {"$set": {
            "status": "completed",
            "totals": totals,
            "completed_at": now,
            "completed_by": user_id,
            "updated_at": now,
            "updated_by": user_id,
        }},
    )

    return {"totals": totals, "errors": errors}, None


def cancel_stocktake(shop_db, stocktake, user_id):
    if stocktake.get("status") != "open":
        return "Stocktake is not open"
    now = utcnow()
    shop_db.stocktakes.update_one(
        {"_id": stocktake["_id"]},
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now,
            "cancelled_by": user_id,
            "updated_at": now,
            "updated_by": user_id,
        }},
    )
    return None
