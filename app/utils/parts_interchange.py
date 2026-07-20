"""Взаимозаменяемые запчасти (cross references).

Модель: у парта есть опциональное поле `interchange_group` (ObjectId).
Все активные парты магазина с одинаковым `interchange_group` считаются
взаимозаменяемыми. Связывание двух партов сливает их группы (свойство
транзитивно: если A~B и B~C, то A~C); отвязка убирает парт из группы,
а группа, где остался один парт, распускается.
"""

from __future__ import annotations

from bson import ObjectId


def link_parts(parts_coll, shop_id, part_a: dict, part_b: dict) -> ObjectId:
    """Связать два парта как взаимозаменяемые. Возвращает id группы."""
    group_a = part_a.get("interchange_group")
    group_b = part_b.get("interchange_group")

    if group_a and group_b:
        if group_a == group_b:
            return group_a
        # Слияние групп: все парты группы B переходят в группу A.
        parts_coll.update_many(
            {"shop_id": shop_id, "interchange_group": group_b},
            {"$set": {"interchange_group": group_a}},
        )
        return group_a

    group = group_a or group_b or ObjectId()
    parts_coll.update_many(
        {"shop_id": shop_id, "_id": {"$in": [part_a["_id"], part_b["_id"]]}},
        {"$set": {"interchange_group": group}},
    )
    return group


def unlink_part(parts_coll, shop_id, part: dict) -> None:
    """Убрать парт из его группы; распустить группу, если остался один парт."""
    group = part.get("interchange_group")
    if not group:
        return

    parts_coll.update_one(
        {"shop_id": shop_id, "_id": part["_id"]},
        {"$unset": {"interchange_group": ""}},
    )

    remaining = list(
        parts_coll.find(
            {"shop_id": shop_id, "interchange_group": group}, {"_id": 1}
        ).limit(2)
    )
    if len(remaining) < 2:
        parts_coll.update_many(
            {"shop_id": shop_id, "interchange_group": group},
            {"$unset": {"interchange_group": ""}},
        )


def get_cross_refs(parts_coll, shop_id, part: dict, limit: int = 50) -> list[dict]:
    """Остальные активные парты группы (сам парт исключён), сорт по номеру."""
    group = part.get("interchange_group")
    if not group:
        return []
    return list(
        parts_coll.find({
            "shop_id": shop_id,
            "interchange_group": group,
            "is_active": True,
            "_id": {"$ne": part["_id"]},
        })
        .sort([("part_number", 1)])
        .limit(limit)
    )


def serialize_cross_ref(part: dict) -> dict:
    """Компактное представление кросс-референса для JSON-ответов."""
    return {
        "id": str(part["_id"]),
        "part_number": part.get("part_number") or "",
        "description": part.get("description") or "",
        "in_stock": int(part.get("in_stock") or 0),
        "do_not_track_inventory": bool(part.get("do_not_track_inventory")),
        "average_cost": float(part.get("average_cost") or 0.0),
    }


def attach_alternates(parts_coll, shop_id, items: list[dict], serialize, max_per_item: int = 10) -> list[dict]:
    """Дописать каждому результату поиска список взаимозаменяемых партов.

    items — результаты поиска с ключом "id" (str ObjectId); мутируются на месте:
    добавляется ключ "alternates" со списком, сериализованным колбэком
    `serialize` (той же формы, что и сами результаты, чтобы UI мог выбрать
    альтернативу как обычный парт). Альтернативы, уже присутствующие в
    результатах верхнего уровня, не дублируются.
    """
    items_by_id: dict[ObjectId, dict] = {}
    for it in items:
        try:
            pid = ObjectId(str(it.get("id")))
        except Exception:
            continue
        items_by_id[pid] = it
        it.setdefault("alternates", [])

    if not items_by_id:
        return items

    group_by_pid = {}
    for doc in parts_coll.find(
        {"shop_id": shop_id, "_id": {"$in": list(items_by_id)}},
        {"interchange_group": 1},
    ):
        group = doc.get("interchange_group")
        if group:
            group_by_pid[doc["_id"]] = group

    if not group_by_pid:
        return items

    members_by_group: dict[ObjectId, list[dict]] = {}
    cursor = parts_coll.find({
        "shop_id": shop_id,
        "interchange_group": {"$in": list(set(group_by_pid.values()))},
        "is_active": True,
    }).sort([("part_number", 1)])
    for doc in cursor:
        members_by_group.setdefault(doc["interchange_group"], []).append(doc)

    for pid, it in items_by_id.items():
        group = group_by_pid.get(pid)
        if not group:
            continue
        alternates = [
            doc for doc in members_by_group.get(group, [])
            if doc["_id"] != pid and doc["_id"] not in items_by_id
        ]
        it["alternates"] = [serialize(doc) for doc in alternates[:max_per_item]]

    return items
