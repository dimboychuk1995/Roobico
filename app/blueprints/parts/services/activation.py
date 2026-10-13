"""
Активность запчастей.

Правило продукта: парт делают неактивным руками (кнопка Deactivate на
странице Parts) — он пропадает из поиска на work order'ах, из мобильного
приложения, инвентаризаций и итогов склада. Обратно активным парт становится
ТОЛЬКО через парт-ордер: как только он попадает в создаваемый, сохраняемый,
подтверждаемый (email-заказ) или принимаемый заказ поставщика, он
автоматически реактивируется. Отдельной кнопки Restore нет.

Парты, слитые скриптом merge_duplicate_parts (``merged_into``), неактивны
навсегда: их нельзя ни найти для заказа, ни реактивировать.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

# Фильтр «парт можно положить в заказ»: активный или деактивированный, но не слитый.
ORDERABLE_PART_FILTER = {"merged_into": {"$exists": False}}


def utcnow():
    return datetime.now(timezone.utc)


def find_orderable_part(parts_coll, part_id, shop_id=None):
    """Парт для строки заказа: активный или неактивный, но не слитый дубль."""
    query = {"_id": part_id, **ORDERABLE_PART_FILTER}
    if shop_id is not None:
        query["shop_id"] = shop_id
    return parts_coll.find_one(query)


def is_part_inactive(part_doc) -> bool:
    return bool(part_doc) and part_doc.get("is_active") is False


def reactivate_parts_for_order(parts_coll, part_ids, *, order_id, order_number=None,
                               user_id=None, now=None) -> int:
    """Сделать активными неактивные парты из строк заказа. Возвращает число
    реактивированных. На парте остаётся след: reactivated_at/by и ссылка на
    заказ, которым его вернули в оборот."""
    ids = [pid for pid in (part_ids or []) if isinstance(pid, ObjectId)]
    if not ids:
        return 0
    now = now or utcnow()
    result = parts_coll.update_many(
        {"_id": {"$in": ids}, "is_active": False, **ORDERABLE_PART_FILTER},
        {"$set": {
            "is_active": True,
            "updated_at": now,
            "updated_by": user_id,
            "deactivated_at": None,
            "deactivated_by": None,
            "reactivated_at": now,
            "reactivated_by": user_id,
            "reactivated_by_order_id": order_id,
            "reactivated_by_order_number": order_number,
        }},
    )
    return int(result.modified_count or 0)
