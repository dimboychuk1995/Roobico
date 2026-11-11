"""Цены строк сервис-темплейта (wo_presets) для Settings → Service Templates.

Карточка и редактор темплейта показывают ровно то, что получит work order
кастомера без собственной шкалы, и считают это тем же кодом, что и сам WO
(``mobile_editor.price_part`` + ``lookups.get_pricing_rules_json``).

Раньше страница держала свою копию правила, и копия расходилась с WO:
  * когда автоцену посчитать было нельзя (cost $0, cost между ступенями
    шкалы), карточка показывала цену-снимок, сохранённую при создании
    темплейта, — WO такой цены не ставит;
  * шкалу брала первую активную, а не дефолтную;
  * список не находил перезаведённую запчасть по номеру и тоже показывал
    снимок.
Снимок (cost/price в документе пресета) остаётся только запасом для строки,
запчасти которой в каталоге больше нет.
"""
from __future__ import annotations

from app.blueprints.work_orders.services.common import f64, oid, round2
from app.blueprints.work_orders.services.lookups import get_pricing_rules_json
from app.blueprints.work_orders.services.mobile_editor import (
    PRICE_NONE,
    PRICE_PINNED,
    price_part,
)

# Запчасти строки нет в каталоге (удалена / деактивирована): показана цена,
# сохранённая в темплейте.
PRICE_SNAPSHOT = "snapshot"

PART_PROJECTION = {
    "_id": 1,
    "part_number": 1,
    "average_cost": 1,
    "has_selling_price": 1,
    "selling_price": 1,
    "core_has_charge": 1,
    "core_cost": 1,
    "misc_has_charge": 1,
    "misc_charges": 1,
}


def standard_pricing(shop_db, shop_id):
    """Шкала кастомера без собственной шкалы (дефолтная шкала магазина) —
    тот же выбор документа, что делает WO. None — шкал нет."""
    return get_pricing_rules_json(shop_db, shop_id)


def is_pinned(row: dict) -> bool:
    """Цена строки закреплена вручную в темплейте (price_overridden)."""
    return bool(row.get("price_overridden")) and row.get("price") is not None


def load_live_parts(shop_db, shop_id, rows) -> tuple[dict, dict]:
    """Живые активные запчасти для строк пресетов: ({part_id: doc},
    {part_number: doc}). По номеру ищем только строки, не найденные по id
    (запчасть перезавели — у неё новый _id)."""
    ids = set()
    for row in rows:
        part_id = oid(row.get("part_id"))
        if part_id:
            ids.add(part_id)

    by_id: dict[str, dict] = {}
    if ids:
        for doc in shop_db.parts.find(
            {"_id": {"$in": list(ids)}, "shop_id": shop_id, "is_active": True},
            PART_PROJECTION,
        ):
            by_id[str(doc["_id"])] = doc

    numbers = set()
    for row in rows:
        if str(row.get("part_id") or "") in by_id:
            continue
        number = str(row.get("part_number") or "").strip()
        if number:
            numbers.add(number)

    by_number: dict[str, dict] = {}
    if numbers:
        for doc in shop_db.parts.find(
            {"part_number": {"$in": list(numbers)}, "shop_id": shop_id, "is_active": True},
            PART_PROJECTION,
        ):
            key = str(doc.get("part_number") or "").strip()
            if key:
                by_number.setdefault(key, doc)
    return by_id, by_number


def resolve_live_part(row: dict, by_id: dict, by_number: dict):
    """Живая запчасть строки: по part_id, иначе по part_number; None — нет."""
    live = by_id.get(str(row.get("part_id") or ""))
    if live is None:
        live = by_number.get(str(row.get("part_number") or "").strip())
    return live


def heal_patches(idx: int, row: dict, live) -> dict:
    """$set-патчи идентичности строки (свежий part_id, актуальный
    part_number), чтобы пресет не ссылался на несуществующую запчасть."""
    if live is None:
        return {}
    patches = {}
    fresh_id = str(live["_id"])
    if str(row.get("part_id") or "") != fresh_id:
        patches[f"parts.{idx}.part_id"] = fresh_id
    live_number = str(live.get("part_number") or "").strip()
    if live_number and live_number != str(row.get("part_number") or "").strip():
        patches[f"parts.{idx}.part_number"] = live_number
    return patches


def _misc_items(items) -> list[dict]:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            out.append({
                "description": str(item.get("description") or "").strip(),
                "price": float(f64(item.get("price")) or 0),
            })
    return out


def describe_part(row: dict, live, pricing) -> dict:
    """Деньги строки темплейта так, как их посчитает WO (стандартная шкала).

    Возвращает::

        {
            "resolved": bool,        # запчасть найдена в каталоге
            "cost": float | None,    # текущая average cost (снимок, если не найдена)
            "auto_price": float | None,   # автоцена без учёта закрепления
            "auto_source": str | None,    # PRICE_FIXED / MATRIX / AT_COST / NONE
            "price": float,          # цена строки: закреплённая либо автоцена
            "price_source": str,     # PRICE_PINNED / auto_source / PRICE_SNAPSHOT
            "below_cost": bool,      # цена ниже текущей себестоимости
            "misc_charges": [{description, price}],
        }
    """
    pinned = is_pinned(row)

    if live is None:
        return {
            "resolved": False,
            "cost": f64(row.get("cost")),
            "auto_price": None,
            "auto_source": None,
            "price": round2(row.get("price")),
            "price_source": PRICE_PINNED if pinned else PRICE_SNAPSHOT,
            "below_cost": False,
            "misc_charges": _misc_items(row.get("misc_charges")),
        }

    cost = float(f64(live.get("average_cost")) or 0)
    auto_price, auto_source = price_part(live, pricing)
    if pinned:
        price, source = round2(row.get("price")), PRICE_PINNED
    else:
        price, source = auto_price, auto_source

    return {
        "resolved": True,
        "cost": cost,
        "auto_price": auto_price,
        "auto_source": auto_source,
        "price": price,
        "price_source": source,
        # «Нет цены» (cost не задан) — не «ниже себестоимости».
        "below_cost": cost > 0 and source != PRICE_NONE and price < round2(cost),
        "misc_charges": _misc_items(live.get("misc_charges")) if live.get("misc_has_charge") else [],
    }
