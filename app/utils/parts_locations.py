"""
Дерево складских локаций запчастей (parts_locations с parent_id).

Локаций у магазина немного (десятки), поэтому дерево всегда собирается в
памяти из полного списка — без рекурсивных запросов к Mongo. Все функции
принимают уже загруженный список документов parts_locations.
"""
from __future__ import annotations

MAX_LOCATION_DEPTH = 4  # уровней вложенности, включая корень
PATH_SEPARATOR = " › "


def load_shop_locations(shop_db, shop_id, include_inactive: bool = False) -> list[dict]:
    query = {"shop_id": shop_id}
    if not include_inactive:
        query["is_active"] = {"$ne": False}
    return list(shop_db.parts_locations.find(query))


def flatten_location_tree(locations) -> list[dict]:
    """
    DFS-обход дерева: [{id, oid, name, parent_id, depth, path, doc}] в порядке
    отображения (сиблинги по имени). Узлы с несуществующим parent_id
    поднимаются в корень, чтобы данные никогда не пропадали из списков.
    """
    by_id = {}
    for loc in locations or []:
        loc_id = loc.get("_id")
        if loc_id:
            by_id[str(loc_id)] = loc

    children: dict = {}
    for key, loc in by_id.items():
        parent = loc.get("parent_id")
        parent_key = str(parent) if parent is not None and str(parent) in by_id else None
        if parent_key == key:  # самоссылка — поднимаем в корень
            parent_key = None
        children.setdefault(parent_key, []).append(loc)

    for nodes in children.values():
        nodes.sort(key=lambda l: str(l.get("name") or "").lower())

    out = []
    visited = set()

    def walk(parent_key, depth, prefix):
        for loc in children.get(parent_key, []):
            key = str(loc["_id"])
            if key in visited:
                continue
            visited.add(key)
            name = str(loc.get("name") or "").strip()
            path = (prefix + PATH_SEPARATOR + name) if prefix else name
            out.append({
                "id": key,
                "oid": loc["_id"],
                "name": name,
                "parent_id": str(loc["parent_id"]) if loc.get("parent_id") else "",
                "depth": depth,
                "path": path,
                "doc": loc,
            })
            walk(key, depth + 1, path)

    walk(None, 0, "")

    # Страховка от цикла в данных: недостижимые узлы показываем как корневые.
    for key, loc in by_id.items():
        if key not in visited:
            visited.add(key)
            name = str(loc.get("name") or "").strip()
            out.append({
                "id": key,
                "oid": loc["_id"],
                "name": name,
                "parent_id": str(loc["parent_id"]) if loc.get("parent_id") else "",
                "depth": 0,
                "path": name,
                "doc": loc,
            })

    return out


def location_path_map(locations) -> dict:
    """{str(location_id): "Warehouse › Rack › Bin"} для отображения."""
    return {item["id"]: item["path"] for item in flatten_location_tree(locations)}


def collect_descendant_ids(locations, root_id) -> set:
    """ObjectId'ы: root + все его потомки (scope инвентаризации, guard перемещения)."""
    root_key = str(root_id)
    flat = flatten_location_tree(locations)
    parent_of = {item["id"]: item["parent_id"] for item in flat}

    result = set()
    for item in flat:
        key = item["id"]
        cursor = key
        while cursor:
            if cursor == root_key:
                result.add(item["oid"])
                break
            cursor = parent_of.get(cursor) or ""
    return result


def location_depth(locations, location_id) -> int:
    """Глубина узла (корень = 0); -1, если узел не найден."""
    for item in flatten_location_tree(locations):
        if item["id"] == str(location_id):
            return int(item["depth"])
    return -1


def subtree_height(locations, root_id) -> int:
    """Высота поддерева: 0 — лист, 1 — есть дети без внуков, и т.д."""
    flat = flatten_location_tree(locations)
    depths = {item["id"]: item["depth"] for item in flat}
    root_key = str(root_id)
    if root_key not in depths:
        return 0
    root_depth = depths[root_key]
    max_rel = 0
    for oid in collect_descendant_ids(locations, root_id):
        rel = depths.get(str(oid), root_depth) - root_depth
        if rel > max_rel:
            max_rel = rel
    return max_rel
