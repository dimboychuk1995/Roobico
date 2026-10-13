"""
Себестоимость и последняя цена закупки запчастей.

- ``average_cost`` — средняя взвешенная себестоимость ТОГО, ЧТО ЛЕЖИТ на
  складе. Приход: (old_avg × old_qty + price × qty) / (old_qty + qty).
  Остаток ≤ 0 (work order списал в минус) или нулевая средняя (себестоимость
  неизвестна) в среднем не участвуют — средняя становится ценой прихода.
  Отмена приёмки / удаление принятого заказа вынимает партию из средней
  обратной формулой. Списания на WO среднюю не меняют.
- ``last_price`` — цена из ПОСЛЕДНЕГО по дате парт-ордера с этим партом
  (активного, не возврата, подтверждённого). Пересчитывается при создании /
  изменении / удалении заказа и подтверждении / отклонении email-заказа;
  подставляется как цена по умолчанию при ручном заказе. ``None`` — заказов
  с партом ещё не было.
- ``vendor_id`` парта — вендор того же последнего заказа: у кого купили в
  последний раз, тот и вендор. Отдельных полей «last vendor» нет. Если
  заказов с партом нет, вендор, выставленный руками, не трогается.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

# Заказы, которые считаются «настоящей» закупкой для last_price.
LAST_PRICE_ORDER_FILTER = {
    "is_active": {"$ne": False},
    "is_return": {"$ne": True},
    "needs_confirmation": {"$ne": True},
}


def utcnow():
    return datetime.now(timezone.utc)


def weighted_average_cost(old_qty, old_avg, add_qty, add_price) -> float:
    """Средняя себестоимость после прихода ``add_qty`` по ``add_price``.

    Пример из жизни: 2 шт по $100 на складе + приход 2 шт по $200 → $150.
    Отрицательный / нулевой остаток и нулевая средняя не усредняются:
    средняя = цена прихода (иначе минус от WO тянет среднюю в абсурд).
    """
    old_qty = int(old_qty or 0)
    add_qty = int(add_qty or 0)
    old_avg = float(old_avg or 0.0)
    add_price = float(add_price or 0.0)

    if add_qty <= 0:
        return round(old_avg, 4)
    if old_qty <= 0 or old_avg <= 0:
        return round(add_price, 4)
    return round((old_avg * old_qty + add_price * add_qty) / (old_qty + add_qty), 4)


def reverse_weighted_average_cost(cur_qty, cur_avg, remove_qty, price) -> float:
    """Обратная операция к ``weighted_average_cost``: вынуть партию
    ``remove_qty`` по ``price`` из средней (отмена приёмки). Если после
    отката остаток ≤ 0 или стоимость уходит в минус — средняя не трогается."""
    cur_qty = int(cur_qty or 0)
    remove_qty = int(remove_qty or 0)
    cur_avg = float(cur_avg or 0.0)
    price = float(price or 0.0)

    remaining = cur_qty - remove_qty
    if remove_qty <= 0 or remaining <= 0:
        return round(cur_avg, 4)
    value = cur_avg * cur_qty - price * remove_qty
    if value <= 0:
        return round(cur_avg, 4)
    return round(value / remaining, 4)


def latest_order_price(shop_db, shop_id, part_id):
    """Последний по дате заказ с партом → {"price", "order_id",
    "order_number", "at"} или None."""
    order = shop_db.parts_orders.find_one(
        {"shop_id": shop_id, **LAST_PRICE_ORDER_FILTER, "items.part_id": part_id},
        {"items.$": 1, "order_number": 1, "order_date": 1, "created_at": 1, "vendor_id": 1},
        sort=[("order_date", -1), ("created_at", -1)],
    )
    if not order:
        return None
    items = order.get("items") or []
    item = items[0] if items and isinstance(items[0], dict) else None
    if item is None:
        return None
    try:
        price = round(float(item.get("price") or 0.0), 2)
    except (TypeError, ValueError):
        price = 0.0
    vendor_id = order.get("vendor_id")
    return {
        "price": price,
        "order_id": order["_id"],
        "order_number": order.get("order_number"),
        "at": order.get("order_date") or order.get("created_at"),
        "vendor_id": vendor_id if isinstance(vendor_id, ObjectId) else None,
    }


def refresh_last_price(shop_db, shop_id, part_ids, *, now=None) -> int:
    """Синхронизировать парты из ``part_ids`` с их последним заказом:
    ``last_price`` (+ ссылка на заказ) и ``vendor_id`` = вендор этого заказа.
    Без заказов last_price = None, вендор не трогается. Возвращает число
    обработанных партов."""
    ids = []
    seen = set()
    for pid in part_ids or []:
        if isinstance(pid, ObjectId) and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    if not ids:
        return 0

    now = now or utcnow()
    for pid in ids:
        info = latest_order_price(shop_db, shop_id, pid)
        if info:
            fields = {
                "last_price": info["price"],
                "last_price_at": info["at"],
                "last_price_order_id": info["order_id"],
                "last_price_order_number": info["order_number"],
            }
            # У кого купили в последний раз — тот и вендор парта.
            if info.get("vendor_id") is not None:
                fields["vendor_id"] = info["vendor_id"]
        else:
            fields = {
                "last_price": None,
                "last_price_at": None,
                "last_price_order_id": None,
                "last_price_order_number": None,
            }
        fields["last_price_updated_at"] = now
        shop_db.parts.update_one({"_id": pid}, {"$set": fields})
    return len(ids)


def order_part_ids(order) -> list:
    """ObjectId партов из строк заказа (для пересчёта last_price)."""
    out = []
    for item in (order or {}).get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("part_id"), ObjectId):
            out.append(item["part_id"])
    return out
