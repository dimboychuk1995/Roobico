"""Read-only доступ AI-помощника к данным активного магазина.

Модель получает три инструмента — db_find / db_count / db_aggregate — и может
отвечать по живым данным («сколько открытых WO», «кто нам должен»).

Guard-слой (модели не доверяем ни в чём):
- только чтение: find / count_documents / aggregate с белым списком стадий;
- только коллекции из реестра COLLECTIONS; каждая закрыта существующим
  permission'ом — ассистент видит ровно то, что пользователь видит в UI;
- денежные поля: без соответствующего view_costs документы чистятся от
  cost-полей, а pipeline, ссылающийся на них, отклоняется (механик-режим
  не получает цены через чат);
- каждый запрос жёстко скоуплен фильтром shop_id (сама база — база шопа,
  фильтр — вторая линия обороны, как и во всех сервисах);
- опасные операторы ($where, $function, $out, $merge, $lookup...) отклоняются
  на любом уровне вложенности;
- лимиты: maxTimeMS, число документов, размер JSON-ответа.

Конвенции для модели (описаны в промпте/спеках инструментов):
- id передаются 24-hex строками — эквалити/$in автоматически расширяются до
  {str, ObjectId}, т.к. ссылки исторически хранятся в обоих видах;
- даты передаются ISO-строками в UTC внутри $gt/$gte/$lt/$lte — конвертируются
  в datetime.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo.errors import PyMongoError


class DataToolError(ValueError):
    """Ошибка валидации/выполнения — текст безопасен для показа модели."""


# ── Реестр коллекций ─────────────────────────────────────────────────
# collection -> perm: право читать коллекцию;
#               costs_perm: право видеть денежные поля внутри неё
#               (None — денег нет или вся коллекция уже закрыта perm'ом).
COLLECTIONS: dict[str, dict] = {
    "customers":            {"perm": "customers.view", "costs_perm": None},
    "units":                {"perm": "customers.view", "costs_perm": None},
    "recall_notifications": {"perm": "customers.view", "costs_perm": None},
    "work_orders":          {"perm": "work_orders.view", "costs_perm": "work_orders.view_costs"},
    "work_order_payments":  {"perm": "work_orders.view_costs", "costs_perm": None},
    "wo_time_logs":         {"perm": "work_orders.view", "costs_perm": None},
    "labor_rates":          {"perm": "work_orders.view_costs", "costs_perm": None},
    "parts":                {"perm": "parts.view", "costs_perm": "parts.view_costs"},
    "parts_categories":     {"perm": "parts.view", "costs_perm": None},
    "parts_locations":      {"perm": "parts.view", "costs_perm": None},
    "part_location_stock":  {"perm": "parts.view", "costs_perm": "parts.view_costs"},
    "inventory_movements":  {"perm": "parts.view", "costs_perm": "parts.view_costs"},
    "stocktakes":           {"perm": "parts.view", "costs_perm": None},
    "stocktake_items":      {"perm": "parts.view", "costs_perm": None},
    "cores":                {"perm": "parts.view", "costs_perm": None},
    "core_returns":         {"perm": "parts.view", "costs_perm": None},
    "parts_pricing_rules":  {"perm": "parts.view_costs", "costs_perm": None},
    "parts_orders":         {"perm": "parts_orders.view", "costs_perm": None},
    "parts_order_payments": {"perm": "parts_orders.view", "costs_perm": None},
    "vendors":              {"perm": "vendors.view", "costs_perm": None},
    "calendar_events":      {"perm": "calendar.view", "costs_perm": None},
}

MAX_FIND_DOCS = 50
MAX_AGG_DOCS = 100
MAX_TIME_MS = 5000
MAX_PIPELINE_STAGES = 12
MAX_RESULT_CHARS = 24_000
MAX_STRING_CHARS = 400

# Операторы, которых не должно быть нигде в фильтре/пайплайне.
_FORBIDDEN_OPS = {
    "$where", "$function", "$accumulator",
    "$out", "$merge", "$unionWith", "$lookup", "$graphLookup", "$facet",
    "$documents", "$collStats", "$indexStats", "$planCacheStats",
    "$currentOp", "$listSessions", "$listLocalSessions", "$listSearchIndexes",
    "$search", "$searchMeta", "$vectorSearch", "$queryStats", "$querySettings",
    "$sample", "$geoNear", "$changeStream", "$changeStreamSplitLargeEvent",
}

_ALLOWED_STAGES = {
    "$match", "$project", "$group", "$sort", "$limit", "$skip", "$count",
    "$unwind", "$addFields", "$set", "$sortByCount", "$bucket", "$bucketAuto",
    "$replaceRoot", "$replaceWith",
}

# Денежные поля: вычищаются из результатов и запрещаются в pipeline'ах,
# когда у пользователя нет costs_perm коллекции (механик-режим).
_COST_KEYS = {
    "cost", "avg_cost", "average_cost", "unit_cost", "total_cost", "cost_total",
    "costs", "core_cost",
    "price", "unit_price", "sell_price", "selling_price", "list_price",
    "purchase_price", "price_override", "misc_charges",
    "subtotal", "total", "totals", "grand_total", "amount", "amount_due",
    "amount_paid", "paid_amount", "remaining_balance", "credit_total",
    "balance", "balance_due", "paid_total", "due", "non_inventory_amounts",
    "tax", "tax_amount", "tax_rate", "tax_total",
    "labor_total", "labor_full_total", "parts_total", "fees", "fee",
    "discount", "discounts",
    "hourly_rate", "rate", "margin", "markup", "shop_supplies",
    "supply_fee", "core_charge", "core_charges", "misc_charge",
    "shortage_value", "overage_value",
}

# Служебные поля, которые модели не нужны никогда.
_ALWAYS_STRIP_KEYS = {"search_terms"}

_OBJECTID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
_RANGE_OPS = {"$gt", "$gte", "$lt", "$lte"}
_MAX_DEPTH = 12


def allowed_collections(permissions: set[str]) -> set[str]:
    return {name for name, cfg in COLLECTIONS.items() if cfg["perm"] in permissions}


def _costs_allowed(collection: str, permissions: set[str]) -> bool:
    costs_perm = COLLECTIONS[collection]["costs_perm"]
    return costs_perm is None or costs_perm in permissions


# ── Валидация и коэрция входа ────────────────────────────────────────


def _reject_forbidden(node, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise DataToolError("query is nested too deeply")
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_OPS:
                raise DataToolError(f"operator {key} is not allowed")
            _reject_forbidden(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _reject_forbidden(item, depth + 1)


def _parse_iso_dt(text: str) -> datetime | None:
    raw = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw += "T00:00:00"
    raw = raw.replace(" ", "T", 1)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _id_variants(value: str) -> list:
    return [value, ObjectId(value)]


def _coerce(node, parent_key: str | None = None):
    """Рекурсивно приводит значения фильтра к тому, как данные лежат в Mongo.

    - 24-hex строка в равенстве по полю или в $in/$nin → оба варианта
      (str и ObjectId): ссылки исторически хранятся и так, и так;
    - ISO-строка даты внутри $gt/$gte/$lt/$lte → datetime (UTC).
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in ("$in", "$nin") and isinstance(value, list):
                expanded = []
                for item in value:
                    if isinstance(item, str) and _OBJECTID_RE.fullmatch(item):
                        expanded.extend(_id_variants(item))
                    else:
                        expanded.append(_coerce(item, key))
                out[key] = expanded
            else:
                out[key] = _coerce(value, key)
        return out
    if isinstance(node, list):
        return [_coerce(item, parent_key) for item in node]
    if isinstance(node, str):
        if parent_key is not None and not parent_key.startswith("$") and _OBJECTID_RE.fullmatch(node):
            # равенство по полю: {"customer_id": "<hex>"} → {"$in": [str, oid]}
            return {"$in": _id_variants(node)}
        if parent_key == "$eq" and _OBJECTID_RE.fullmatch(node):
            return {"$in": _id_variants(node)}  # некорректно внутри $eq — заменяем оператором
        if parent_key in _RANGE_OPS:
            dt = _parse_iso_dt(node)
            if dt is not None:
                return dt
    return node


def _coerce_filter(user_filter) -> dict:
    if user_filter is None:
        return {}
    if not isinstance(user_filter, dict):
        raise DataToolError("filter must be an object")
    _reject_forbidden(user_filter)
    coerced = _coerce(user_filter)
    # $eq с hex-строкой превращается в {$in: [...]} — но {$eq: {$in: ...}} невалиден,
    # поэтому подменяем сам $eq: {"f": {"$eq": hex}} → {"f": {"$in": [...]}}
    return _fix_eq_in(coerced)


def _fix_eq_in(node):
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$eq" and isinstance(value, dict) and set(value) == {"$in"}:
                out["$in"] = value["$in"]
            else:
                out[key] = _fix_eq_in(value)
        return out
    if isinstance(node, list):
        return [_fix_eq_in(item) for item in node]
    return node


def _scope_filter(user_filter: dict, shop_variants: list) -> dict:
    scope = {"shop_id": {"$in": shop_variants}}
    if not user_filter:
        return scope
    return {"$and": [scope, user_filter]}


def _shop_variants(shop_id) -> list:
    variants = {shop_id, str(shop_id)}
    if not isinstance(shop_id, ObjectId) and _OBJECTID_RE.fullmatch(str(shop_id)):
        variants.add(ObjectId(str(shop_id)))
    return list(variants)


def _require_collection(args: dict, permissions: set[str]) -> str:
    name = str(args.get("collection") or "")
    if name not in COLLECTIONS:
        raise DataToolError(
            f"unknown collection '{name}'; available: {', '.join(sorted(COLLECTIONS))}"
        )
    if name not in allowed_collections(permissions):
        raise DataToolError(f"you don't have permission to read '{name}' for this user")
    return name


def _reject_cost_references(pipeline, collection: str) -> None:
    """Без costs_perm нельзя даже ссылаться на денежные поля в pipeline —
    иначе $group пересложит цены в поля с другими именами и strip их не поймает."""
    dumped = json.dumps(pipeline)
    for key in _COST_KEYS:
        if re.search(rf'"\$?(?:[\w.]+\.)?{re.escape(key)}(?:\.[\w.]+)?"', dumped):
            raise DataToolError(
                f"field '{key}' is a cost/price field — this user is not allowed to see costs"
            )


# ── Чистка и сериализация результатов ────────────────────────────────


def _strip_keys(node, keys: set[str]):
    if isinstance(node, dict):
        return {k: _strip_keys(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_strip_keys(item, keys) for item in node]
    return node


def _jsonable(value, depth: int = 0):
    if depth > _MAX_DEPTH:
        return "<too deep>"
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
        return value[:MAX_STRING_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, depth + 1) for item in value]
    return value


def _clean_docs(docs: list, collection: str, permissions: set[str]) -> list:
    strip = set(_ALWAYS_STRIP_KEYS)
    if not _costs_allowed(collection, permissions):
        strip |= _COST_KEYS
    return [_jsonable(_strip_keys(doc, strip)) for doc in docs]


def _dump_result(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    docs = payload.get("docs")
    while isinstance(docs, list) and docs and len(text) > MAX_RESULT_CHARS:
        docs.pop()
        payload["truncated"] = True
        payload["note"] = (
            f"result too large, only {len(docs)} documents shown — "
            "narrow the query or use a projection"
        )
        text = json.dumps(payload, ensure_ascii=False, default=str)
    return text


# ── Инструменты ──────────────────────────────────────────────────────


def _tool_find(shop_db, shop_variants, permissions, args) -> dict:
    collection = _require_collection(args, permissions)
    flt = _scope_filter(_coerce_filter(args.get("filter")), shop_variants)

    projection = args.get("projection")
    if projection is not None:
        if not isinstance(projection, dict) or not all(
            isinstance(k, str) and not k.startswith("$") and v in (0, 1)
            for k, v in projection.items()
        ):
            raise DataToolError("projection must be an object of field: 0|1")
        projection = dict(projection)

    sort_spec = args.get("sort")
    sort_list = None
    if sort_spec is not None:
        if not isinstance(sort_spec, dict) or not all(
            isinstance(k, str) and v in (1, -1) for k, v in sort_spec.items()
        ):
            raise DataToolError("sort must be an object of field: 1|-1")
        sort_list = list(sort_spec.items())[:4]

    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        raise DataToolError("limit must be an integer")
    limit = max(1, min(limit, MAX_FIND_DOCS))

    cursor = shop_db[collection].find(flt, projection, max_time_ms=MAX_TIME_MS)
    if sort_list:
        cursor = cursor.sort(sort_list)
    docs = list(cursor.limit(limit))

    result = {"docs": _clean_docs(docs, collection, permissions), "returned": len(docs)}
    if len(docs) == limit:
        result["note"] = f"limit {limit} reached — there may be more matching documents"
    return result


def _tool_count(shop_db, shop_variants, permissions, args) -> dict:
    collection = _require_collection(args, permissions)
    flt = _scope_filter(_coerce_filter(args.get("filter")), shop_variants)
    count = shop_db[collection].count_documents(flt, maxTimeMS=MAX_TIME_MS)
    return {"count": int(count)}


def _tool_aggregate(shop_db, shop_variants, permissions, args) -> dict:
    collection = _require_collection(args, permissions)

    pipeline = args.get("pipeline")
    if not isinstance(pipeline, list) or not pipeline:
        raise DataToolError("pipeline must be a non-empty array of stages")
    if len(pipeline) > MAX_PIPELINE_STAGES:
        raise DataToolError(f"pipeline too long (max {MAX_PIPELINE_STAGES} stages)")

    _reject_forbidden(pipeline)
    if not _costs_allowed(collection, permissions):
        _reject_cost_references(pipeline, collection)

    stages = []
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise DataToolError("each stage must be an object with exactly one $-operator")
        op = next(iter(stage))
        if op not in _ALLOWED_STAGES:
            raise DataToolError(
                f"stage {op} is not allowed; allowed: {', '.join(sorted(_ALLOWED_STAGES))}"
            )
        if op == "$limit":
            try:
                stage = {"$limit": max(1, min(int(stage["$limit"]), MAX_AGG_DOCS))}
            except (TypeError, ValueError):
                raise DataToolError("$limit must be an integer")
        elif op == "$match":
            stage = {"$match": _coerce_filter(stage["$match"])}
        stages.append(stage)

    full = [{"$match": {"shop_id": {"$in": shop_variants}}}] + stages + [{"$limit": MAX_AGG_DOCS}]
    docs = list(shop_db[collection].aggregate(full, maxTimeMS=MAX_TIME_MS))
    return {"docs": _clean_docs(docs, collection, permissions), "returned": len(docs)}


_TOOL_HANDLERS = {
    "db_find": _tool_find,
    "db_count": _tool_count,
    "db_aggregate": _tool_aggregate,
}

TOOL_NAMES = frozenset(_TOOL_HANDLERS)


def run_tool(name: str, arguments: dict, *, shop_db, shop_id, permissions: set[str]) -> dict:
    """Выполнить инструмент; вернуть dict-результат (для JSON в tool-сообщение).

    Ошибки валидации возвращаются как {"error": ...} — модель должна увидеть
    причину и поправить запрос; неожиданные ошибки Mongo логируются выше.
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be a JSON object"}

    variants = _shop_variants(shop_id)
    try:
        return handler(shop_db, variants, permissions, arguments)
    except DataToolError as exc:
        return {"error": str(exc)}
    except PyMongoError:
        from flask import current_app

        current_app.logger.exception("Assistant data tool %s failed", name)
        return {"error": "query failed to execute — try a simpler query"}


def dump_tool_result(payload: dict) -> str:
    return _dump_result(payload)


# ── Спеки инструментов для OpenAI ────────────────────────────────────

_FILTER_DESC = (
    "MongoDB filter. IDs are 24-hex strings (matched against both string and "
    "ObjectId storage automatically). Dates: pass ISO 8601 UTC strings inside "
    "$gt/$gte/$lt/$lte. No $where/$function/$lookup."
)


def openai_tool_specs(collections: set[str]) -> list[dict]:
    enum = sorted(collections)
    return [
        {
            "type": "function",
            "function": {
                "name": "db_find",
                "description": (
                    "Read documents from a collection of the current shop's database. "
                    "Always use a projection with only the fields you need and a small limit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "enum": enum},
                        "filter": {"type": "object", "description": _FILTER_DESC},
                        "projection": {
                            "type": "object",
                            "description": "Fields to include, e.g. {\"name\": 1, \"status\": 1}",
                        },
                        "sort": {
                            "type": "object",
                            "description": "e.g. {\"created_at\": -1}",
                        },
                        "limit": {"type": "integer", "description": f"max {MAX_FIND_DOCS}, default 20"},
                    },
                    "required": ["collection"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "db_count",
                "description": "Count documents in a collection of the current shop's database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "enum": enum},
                        "filter": {"type": "object", "description": _FILTER_DESC},
                    },
                    "required": ["collection"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "db_aggregate",
                "description": (
                    "Run an aggregation pipeline on a collection of the current shop's database. "
                    "Allowed stages: " + ", ".join(sorted(_ALLOWED_STAGES)) + ". "
                    "A $limit of " + str(MAX_AGG_DOCS) + " is enforced."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "enum": enum},
                        "pipeline": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Aggregation stages. " + _FILTER_DESC,
                        },
                    },
                    "required": ["collection", "pipeline"],
                },
            },
        },
    ]
