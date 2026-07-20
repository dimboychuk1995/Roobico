"""
Складские остатки запчастей по локациям + журнал движений.

Модель данных (shop DB):
- parts.in_stock — общий остаток парта (сумма по локациям);
- part_location_stock — раскладка остатка по локациям:
  {shop_id, part_id, location_id (ObjectId | None), qty};
  location_id=None — «Unassigned» (без локации);
- inventory_movements — append-only журнал каждого изменения остатка:
  {shop_id, part_id, part_number, location_id, type, qty_delta, stock_after,
   ref: {kind, id, label} | None, created_at, created_by}.

Любое изменение остатка делается ТОЛЬКО через apply_stock_change /
transfer_stock — единственное место, где согласованно обновляются все три
коллекции. Всё через $inc, поэтому параллельные записи не теряются
(в отличие от старой схемы read-then-$set).

Легаси-данные (in_stock есть, строк по локациям нет) достраиваются
materialize_location_rows: остаток парта кладётся в его primary-локацию
(parts.location_id) с движением типа "backfill".
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

# Отличаем «локация не указана — взять primary у парта» (UNSET)
# от «явно Unassigned» (None).
UNSET = object()

MOVEMENT_TYPES = (
    "initial",        # стартовый остаток при создании парта
    "receive",        # приёмка заказа поставщика
    "unreceive",      # отмена приёмки / удаление полученного заказа
    "wo_deduct",      # списание в work order
    "wo_restore",     # возврат при удалении WO
    "wo_adjust",      # дельта при редактировании WO
    "manual_edit",    # ручная правка остатка на парте
    "transfer",       # перенос между локациями (in_stock не меняется)
    "stocktake",      # поправка по итогам инвентаризации
    "vendor_return",  # возврат вендору (минус) / отмена возврата (плюс)
    "tracking_off",   # отключили учёт остатков у парта
    "backfill",       # достройка строк по локациям для легаси-данных
)


def utcnow():
    return datetime.now(timezone.utc)


PART_STOCK_PROJECTION = {
    "in_stock": 1,
    "part_number": 1,
    "location_id": 1,
    "do_not_track_inventory": 1,
    "shop_id": 1,
    "average_cost": 1,
}


def _normalize_ref(ref):
    if not isinstance(ref, dict):
        return None
    kind = str(ref.get("kind") or "").strip()
    if not kind:
        return None
    out = {"kind": kind}
    ref_id = ref.get("id")
    if isinstance(ref_id, ObjectId):
        out["id"] = ref_id
    elif ref_id:
        try:
            out["id"] = ObjectId(str(ref_id))
        except Exception:
            pass
    label = ref.get("label")
    if label is not None and str(label).strip():
        out["label"] = str(label).strip()
    return out


def _insert_movement(shop_db, shop_id, part_doc, location_id, movement_type,
                     qty_delta, stock_after, ref, user_id, now, session=None,
                     extra=None):
    doc = {
        "shop_id": shop_id,
        "part_id": part_doc["_id"],
        "part_number": str(part_doc.get("part_number") or ""),
        "location_id": location_id,
        "type": str(movement_type),
        "qty_delta": int(qty_delta),
        "stock_after": int(stock_after) if stock_after is not None else None,
        "ref": _normalize_ref(ref),
        "created_at": now,
        "created_by": user_id,
    }
    if extra:
        doc.update(extra)
    shop_db.inventory_movements.insert_one(doc, session=session)


def _inc_location_row(shop_db, shop_id, part_doc, location_id, qty_delta,
                      user_id, now, session=None):
    shop_db.part_location_stock.update_one(
        {"shop_id": shop_id, "part_id": part_doc["_id"], "location_id": location_id},
        {
            "$inc": {"qty": int(qty_delta)},
            "$set": {
                "part_number": str(part_doc.get("part_number") or ""),
                "updated_at": now,
                "updated_by": user_id,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        session=session,
    )


def apply_stock_change(shop_db, shop_id, part_id, qty_delta, movement_type, *,
                       user_id=None, location_id=UNSET, ref=None,
                       part_doc=None, session=None) -> dict:
    """
    Единая точка изменения остатка: $inc parts.in_stock, $inc строки локации,
    запись в журнал движений.

    location_id: UNSET — primary-локация парта; None — явно Unassigned;
    ObjectId — конкретная локация.

    Возвращает {"ok": bool, "skipped": bool, "stock_after": int|None,
                "location_id": ..., "error": str|None}.
    """
    qty_delta = int(qty_delta)

    if part_doc is None:
        part_doc = shop_db.parts.find_one({"_id": part_id}, PART_STOCK_PROJECTION, session=session)
    if not part_doc:
        return {"ok": False, "skipped": True, "stock_after": None, "location_id": None,
                "error": "Part not found"}

    if bool(part_doc.get("do_not_track_inventory")):
        return {"ok": True, "skipped": True, "stock_after": None, "location_id": None,
                "error": None}

    if qty_delta == 0:
        return {"ok": True, "skipped": True,
                "stock_after": int(part_doc.get("in_stock") or 0),
                "location_id": None, "error": None}

    target_loc = part_doc.get("location_id") if location_id is UNSET else location_id
    now = utcnow()

    updated = shop_db.parts.find_one_and_update(
        {"_id": part_doc["_id"]},
        {
            "$inc": {"in_stock": qty_delta},
            "$set": {"updated_at": now, "updated_by": user_id},
        },
        projection={"in_stock": 1},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    stock_after = int((updated or {}).get("in_stock") or 0)

    _inc_location_row(shop_db, shop_id, part_doc, target_loc, qty_delta, user_id, now, session=session)
    _insert_movement(shop_db, shop_id, part_doc, target_loc, movement_type,
                     qty_delta, stock_after, ref, user_id, now, session=session)

    return {"ok": True, "skipped": False, "stock_after": stock_after,
            "location_id": target_loc, "error": None}


def get_location_rows(shop_db, shop_id, part_id) -> list[dict]:
    return list(shop_db.part_location_stock.find(
        {"shop_id": shop_id, "part_id": part_id}
    ))


def materialize_location_rows(shop_db, shop_id, part_doc, user_id=None) -> int:
    """
    Достраивает строки по локациям так, чтобы sum(rows) == in_stock.
    Остаток-«хвост» (легаси-данные до внедрения раскладки) кладётся в
    primary-локацию парта. Возвращает величину дописанного остатка.
    Идемпотентно: при согласованных данных ничего не пишет.
    """
    if bool(part_doc.get("do_not_track_inventory")):
        return 0

    in_stock = int(part_doc.get("in_stock") or 0)
    rows = get_location_rows(shop_db, shop_id, part_doc["_id"])
    rows_sum = sum(int(r.get("qty") or 0) for r in rows)
    remainder = in_stock - rows_sum
    if remainder == 0:
        return 0

    target_loc = part_doc.get("location_id")
    now = utcnow()
    _inc_location_row(shop_db, shop_id, part_doc, target_loc, remainder, user_id, now)
    _insert_movement(shop_db, shop_id, part_doc, target_loc, "backfill",
                     remainder, in_stock, None, user_id, now)
    return remainder


def get_location_breakdown(shop_db, shop_id, part_doc) -> list[dict]:
    """
    Раскладка остатка по локациям для отображения (без записи в базу):
    [{location_id: ObjectId|None, qty: int}]. Несматериализованный «хвост»
    легаси-данных виртуально добавляется в primary-локацию парта.
    """
    rows = get_location_rows(shop_db, shop_id, part_doc["_id"])
    out = {}
    for r in rows:
        key = r.get("location_id")
        out[key] = out.get(key, 0) + int(r.get("qty") or 0)

    in_stock = int(part_doc.get("in_stock") or 0)
    remainder = in_stock - sum(out.values())
    if remainder != 0:
        key = part_doc.get("location_id")
        out[key] = out.get(key, 0) + remainder

    return [{"location_id": k, "qty": v} for k, v in out.items()]


def deduct_stock_distributed(shop_db, shop_id, part_id, qty, movement_type, *,
                             user_id=None, ref=None, part_doc=None) -> dict:
    """
    Списание с учётом раскладки по локациям: сначала дефолтная (primary)
    локация парта, затем остальные локации с положительным остатком (по
    убыванию); если всё равно не хватает — дефолтная уходит в минус
    (политика «WO сохраняется даже при нехватке» сохраняется).

    Пишет отдельное движение на каждую задействованную локацию.
    Возвращает тот же контракт, что apply_stock_change.
    """
    qty = int(qty)
    if part_doc is None:
        part_doc = shop_db.parts.find_one({"_id": part_id}, PART_STOCK_PROJECTION)
    if not part_doc:
        return {"ok": False, "skipped": True, "stock_after": None, "location_id": None,
                "error": "Part not found"}
    if bool(part_doc.get("do_not_track_inventory")):
        return {"ok": True, "skipped": True, "stock_after": None, "location_id": None,
                "error": None}
    if qty <= 0:
        return {"ok": True, "skipped": True,
                "stock_after": int(part_doc.get("in_stock") or 0),
                "location_id": None, "error": None}

    primary = part_doc.get("location_id")
    breakdown = {row["location_id"]: int(row["qty"]) for row in
                 get_location_breakdown(shop_db, shop_id, part_doc)}

    # План распределения: primary → остальные с плюсом → нехватка в primary.
    plan = []
    remaining = qty
    take_primary = min(remaining, max(breakdown.get(primary, 0), 0))
    if take_primary > 0:
        plan.append((primary, take_primary))
        remaining -= take_primary

    others = sorted(
        ((loc, avail) for loc, avail in breakdown.items() if loc != primary and avail > 0),
        key=lambda x: -x[1],
    )
    for loc, avail in others:
        if remaining <= 0:
            break
        take = min(remaining, avail)
        plan.append((loc, take))
        remaining -= take

    if remaining > 0:
        for i, (loc, take) in enumerate(plan):
            if loc == primary:
                plan[i] = (loc, take + remaining)
                break
        else:
            plan.append((primary, remaining))
        remaining = 0

    result = None
    chunks = []
    for loc, take in plan:
        result = apply_stock_change(
            shop_db, shop_id, part_doc["_id"], -take, movement_type,
            user_id=user_id, location_id=loc, ref=ref,
        )
        if not result["ok"]:
            result["chunks"] = chunks
            return result
        chunks.append({"location_id": loc, "qty": int(take)})

    if result is None:
        return {"ok": True, "skipped": True,
                "stock_after": int(part_doc.get("in_stock") or 0),
                "location_id": None, "error": None, "chunks": []}
    result["chunks"] = chunks
    return result


def transfer_stock(shop_db, shop_id, part_id, qty, from_location_id,
                   to_location_id, user_id=None, ref=None) -> dict:
    """
    Перенос qty между локациями (in_stock не меняется). from/to — ObjectId
    или None (Unassigned). Источник не может уйти в минус: списание идёт с
    атомарным гардом qty >= N.
    """
    qty = int(qty)
    if qty <= 0:
        return {"ok": False, "error": "Quantity must be greater than 0"}
    if from_location_id == to_location_id:
        return {"ok": False, "error": "Source and target locations are the same"}

    part_doc = shop_db.parts.find_one({"_id": part_id}, PART_STOCK_PROJECTION)
    if not part_doc:
        return {"ok": False, "error": "Part not found"}
    if bool(part_doc.get("do_not_track_inventory")):
        return {"ok": False, "error": "Inventory tracking is disabled for this part"}

    materialize_location_rows(shop_db, shop_id, part_doc, user_id=user_id)

    now = utcnow()
    res = shop_db.part_location_stock.update_one(
        {
            "shop_id": shop_id,
            "part_id": part_doc["_id"],
            "location_id": from_location_id,
            "qty": {"$gte": qty},
        },
        {"$inc": {"qty": -qty}, "$set": {"updated_at": now, "updated_by": user_id}},
    )
    if res.matched_count == 0:
        return {"ok": False, "error": "Not enough quantity at the source location"}

    _inc_location_row(shop_db, shop_id, part_doc, to_location_id, qty, user_id, now)

    stock_after = int(part_doc.get("in_stock") or 0)
    group_id = ObjectId()
    extra = {"transfer_group": group_id}
    _insert_movement(shop_db, shop_id, part_doc, from_location_id, "transfer",
                     -qty, stock_after, ref, user_id, now, extra=extra)
    _insert_movement(shop_db, shop_id, part_doc, to_location_id, "transfer",
                     qty, stock_after, ref, user_id, now, extra=extra)

    return {"ok": True, "error": None}


def set_location_qty(shop_db, shop_id, part_id, location_id, new_qty,
                     movement_type="manual_edit", user_id=None, ref=None) -> dict:
    """
    Установить фактическое количество в конкретной локации (ручная правка,
    поправка инвентаризации). Дельта применяется через apply_stock_change,
    поэтому параллельные движения не затираются.
    """
    new_qty = int(new_qty)
    part_doc = shop_db.parts.find_one({"_id": part_id}, PART_STOCK_PROJECTION)
    if not part_doc:
        return {"ok": False, "error": "Part not found"}
    if bool(part_doc.get("do_not_track_inventory")):
        return {"ok": False, "error": "Inventory tracking is disabled for this part"}

    materialize_location_rows(shop_db, shop_id, part_doc, user_id=user_id)

    row = shop_db.part_location_stock.find_one(
        {"shop_id": shop_id, "part_id": part_doc["_id"], "location_id": location_id},
        {"qty": 1},
    )
    current = int((row or {}).get("qty") or 0)
    delta = new_qty - current
    if delta == 0:
        return {"ok": True, "delta": 0, "stock_after": int(part_doc.get("in_stock") or 0)}

    result = apply_stock_change(
        shop_db, shop_id, part_doc["_id"], delta, movement_type,
        user_id=user_id, location_id=location_id, ref=ref, part_doc=part_doc,
    )
    if not result["ok"]:
        return {"ok": False, "error": result.get("error") or "Failed to update stock"}
    return {"ok": True, "delta": delta, "stock_after": result["stock_after"]}


def disable_tracking(shop_db, shop_id, part_doc, user_id=None) -> None:
    """
    Парт переводится в do_not_track_inventory: остаток обнуляется, строки по
    локациям удаляются, в журнале остаётся движение tracking_off.
    Вызывать ДО $unset in_stock / $set do_not_track_inventory на парте.
    """
    old_stock = int(part_doc.get("in_stock") or 0)
    now = utcnow()
    shop_db.part_location_stock.delete_many(
        {"shop_id": shop_id, "part_id": part_doc["_id"]}
    )
    if old_stock != 0:
        _insert_movement(shop_db, shop_id, part_doc, part_doc.get("location_id"),
                         "tracking_off", -old_stock, 0, None, user_id, now)
