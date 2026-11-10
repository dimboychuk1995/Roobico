"""Read-only доступ AI-помощника к данным активного магазина.

Модель получает три инструмента — db_find / db_count / db_aggregate — и может
отвечать по живым данным («сколько открытых WO», «кто нам должен»).

Guard-слой (модели не доверяем ни в чём):
- только чтение: find / count_documents / aggregate с белым списком стадий;
- только коллекции из реестра COLLECTIONS; каждая закрыта существующим
  permission'ом — ассистент видит ровно то, что пользователь видит в UI;
- денежные поля без соответствующего view_costs (механик-режим не получает
  цены через чат) закрыты тремя линиями:
  1) запрос не может назвать денежное поле — структурная проверка имён:
     пути фильтра/проекции/сортировки и пути через переменные
     ($$this.labor.hourly_rate, $$p.cost в $map/$filter/$reduce/$let);
     обходы, где имя — строка-литерал ($objectToArray, $getField/$setField/
     $unsetField, $arrayToObject), и переназначение CURRENT запрещены.
     Имена выходных полей верхнего уровня, которые задаёт сама модель
     ($group {"total": {"$sum": 1}}, $count "total"), — не деньги: их можно
     давать и потом читать ($sort {"total": -1});
  2) барьер: find / count / aggregate идут pipeline'ом, где денежные поля
     вырезаны ($project) сразу после скоупа магазина — ДО фильтра,
     сортировки и стадий модели; их не видит ни один оператор, как бы к ним
     ни обращались, в т.ч. $$ROOT (это и есть гарантия, проверка имён — лишь
     первая линия);
  3) из результатов денежные ключи вычищаются (кроме выходных полей
     верхнего уровня, заданных моделью);
- учётки порталов вендоров (portal_login, portal_password_enc) недоступны
  никому: ссылки на них (и $$ROOT-обходы в vendors) отклоняются, а find /
  count / aggregate по vendors идут pipeline'ом, где эти поля вырезаны ДО
  фильтра, сортировки и стадий модели (никакой оператор их не увидит, даже
  не называя по имени); из результатов они вычищаются всегда;
- каждый запрос жёстко скоуплен фильтром shop_id (сама база — база шопа,
  фильтр — вторая линия обороны, как и во всех сервисах);
- опасные операторы ($where, $function, $out, $merge, $lookup...) отклоняются
  на любом уровне вложенности; стадии — только из белого списка (нет
  $redact, $setWindowFields, $unset...);
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
#               (None — денег нет, вся коллекция уже закрыта perm'ом или
#               суммы видны в UI всем с perm: stocktakes, parts_orders,
#               parts_order_payments — список паритета и сверка с
#               db_schema.md в tests/test_assistant_data_tools.py).
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
    # average_cost строк, core_cost / credit_total коров: в UI эти колонки
    # видны только с parts.view_costs (как average_cost / core_cost самой
    # запчасти).
    "stocktake_items":      {"perm": "parts.view", "costs_perm": "parts.view_costs"},
    "cores":                {"perm": "parts.view", "costs_perm": "parts.view_costs"},
    "core_returns":         {"perm": "parts.view", "costs_perm": "parts.view_costs"},
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

# Операторы, которых не должно быть нигде в фильтре/пайплайне: JS
# ($where/$function/$accumulator), запись ($out/$merge), чтение других
# коллекций ($lookup/$graphLookup/$unionWith — мимо прав коллекции и её
# барьера), подконвейеры ($facet), служебное и тяжёлое.
_FORBIDDEN_OPS = {
    "$where", "$function", "$accumulator",
    "$out", "$merge", "$unionWith", "$lookup", "$graphLookup", "$facet",
    "$documents", "$collStats", "$indexStats", "$planCacheStats",
    "$currentOp", "$listSessions", "$listLocalSessions", "$listSearchIndexes",
    "$search", "$searchMeta", "$vectorSearch", "$queryStats", "$querySettings",
    "$sample", "$geoNear", "$changeStream", "$changeStreamSplitLargeEvent",
    # $jsonSchema (patternProperties / minProperties / additionalProperties)
    # проверяет поля, не называя их, — оракул для скрытых полей (учётки
    # вендоров); ассистенту он не нужен.
    "$jsonSchema",
}

_ALLOWED_STAGES = {
    "$match", "$project", "$group", "$sort", "$limit", "$skip", "$count",
    "$unwind", "$addFields", "$set", "$sortByCount", "$bucket", "$bucketAuto",
    "$replaceRoot", "$replaceWith",
}

# Денежные поля: когда у пользователя нет costs_perm коллекции (механик-режим),
# их нельзя назвать в запросе, они вырезаются барьером до стадий модели
# (_cost_barrier) и вычищаются из результатов.
_COST_KEYS = {
    "cost", "avg_cost", "average_cost", "unit_cost", "total_cost", "cost_total",
    "costs", "core_cost", "core_charge_base", "inventory_cost",
    "price", "unit_price", "sell_price", "selling_price", "list_price",
    "purchase_price", "price_override", "misc_charges",
    # parts: цена последней закупки и номер заказа, откуда она взята
    # (как PART_COST_FIELDS в parts/routes.py)
    "last_price", "last_price_order_number",
    "subtotal", "total", "totals", "grand_total", "amount", "amount_due",
    "amount_paid", "paid_amount", "remaining_balance", "credit_total",
    "balance", "balance_due", "paid_total", "unpaid_total", "due",
    "non_inventory_amounts",
    "tax", "tax_amount", "tax_rate", "tax_total", "sales_tax_total", "sales_tax_rate",
    "labor_total", "labor_full_total", "parts_total", "core_total", "misc_total",
    "shop_supply_total", "parts_taxable_total", "misc_taxable_total",
    "fees", "fee", "discount", "discounts",
    "hourly_rate", "rate", "margin", "markup", "shop_supplies",
    "supply_fee", "core_charge", "core_charges", "misc_charge",
    # строка запчасти WO: JSON-строка misc-позиций, внутри — их цены
    "misc_charge_description",
    "shortage_value", "overage_value",
}

# Где в документах лежат деньги (сверено с местами записи: work_orders —
# work_orders/routes.py, services/totals.py, mobile_editor.py, импорт; parts —
# parts/routes.py, services/costing.py). Без costs_perm _scoped_head вырезает
# в каждом контейнере коллекции все поля с именами из _COST_KEYS:
#   work_orders: totals{...} и легаси-тоталы верхнего уровня (grand_total,
#     labor_total, parts_total, sales_tax_total); labors[]: сам блок (легаси
#     плоский labor_full_total), labor{hourly_rate, labor_full_total,
#     labor_total}, parts[]{cost, price, core_charge, misc_charge,
#     misc_charge_description, легаси core_cost}; легаси-массив blocks[] той
#     же формы (до сохранения в новом редакторе);
#   parts: average_cost, selling_price, core_cost, misc_charges[],
#     last_price, last_price_order_number;
#   stocktake_items: average_cost (снимок себестоимости запчасти —
#     parts/services/stocktake.py); cores: core_cost; core_returns:
#     core_cost, credit_total (parts/routes.py, work_orders/services/
#     inventory.py) — всё на верхнем уровне;
#   part_location_stock / inventory_movements: денег сейчас нет — верхний
#     уровень на случай новых полей.
# Верхний уровень ("") есть у каждой коллекции: на нём не остаётся ни одного
# имени из _COST_KEYS — на это опирается разрешение выходных полей модели
# с такими именами (_output_ref). Коллекция с costs_perm, которой здесь нет,
# получает верхний уровень.
_COST_CONTAINERS: dict[str, tuple[str, ...]] = {
    "work_orders": (
        "", "labors", "labors.labor", "labors.parts",
        "blocks", "blocks.labor", "blocks.parts",
    ),
    "parts": ("",),
    "part_location_stock": ("",),
    "inventory_movements": ("",),
    "stocktake_items": ("",),
    "cores": ("",),
    "core_returns": ("",),
}

# Учётки порталов вендоров (логин и Fernet-токен пароля) ассистенту
# недоступны никогда, даже владельцу: вычищаются из результатов, а фильтр /
# проекция / сортировка / pipeline, которые на них ссылаются, отклоняются —
# иначе $group/$project переложили бы их в поле с другим именем, а $regex по
# логину работал бы как оракул.
_SECRET_KEYS = {"portal_login", "portal_password_enc"}
# В коллекциях с такими полями запрещён и доступ к документу «целиком»:
# через $$ROOT / $objectToArray / $getField поле читается, не называя его.
_SECRET_COLLECTIONS = {"vendors"}
_SECRET_BYPASS_TOKENS = ("$$ROOT", "$$CURRENT", "$objectToArray", "$getField")
# Без costs_perm — операторы, где имя поля — строка-литерал или становится
# значением: $objectToArray ({k: "price", v: ...} мимо чистки ключей),
# $getField / $setField / $unsetField ({"field": "price"}) и $arrayToObject
# ([["hourly_rate", 137]]). Проверка имён литералы не читает, а собранный
# так документ — оракул сравнения поддокументов целиком
# ({"$eq": ["$labors.labor", <собранный документ>]}). Сам $$ROOT / $$CURRENT
# не запрещён: это документ после барьера — как find без проекции
# («последний WO по юниту»: {"$first": "$$ROOT"}).
_COST_BYPASS_TOKENS = ("$objectToArray", "$getField", "$setField", "$unsetField", "$arrayToObject")

# Операторы с именованными аргументами: их собственные ключи («amount» у
# $dateAdd, «input»/«as»/«in» у $map, «sortBy» у $sortArray) — не имена
# полей (неизвестный аргумент Mongo отклоняет, полем он не станет).
# Значения проверяются как обычно: ключи внутри sortBy / output / newRoot /
# vars — снова имена.
_ARG_STYLE_OPS = {
    "$map", "$filter", "$reduce", "$let", "$cond", "$switch", "$zip",
    "$sortArray", "$getField", "$setField", "$unsetField", "$convert",
    "$dateAdd", "$dateSubtract", "$dateDiff", "$dateTrunc", "$dateToString",
    "$dateFromString", "$dateToParts", "$dateFromParts",
    "$regexMatch", "$regexFind", "$regexFindAll", "$replaceOne", "$replaceAll",
    "$trim", "$ltrim", "$rtrim",
    "$firstN", "$lastN", "$maxN", "$minN", "$topN", "$bottomN", "$top", "$bottom",
    "$median", "$percentile",
    "$unwind", "$bucket", "$bucketAuto", "$replaceRoot",
}
# Строковое значение этих ключей — имя выходного поля, а не литерал:
# {"$count": "total"}, {"$unwind": {"path": ..., "includeArrayIndex": "price"}}.
_NAME_VALUED_KEYS = {"$count", "includeArrayIndex"}
# Внутри них — снова фильтры, ключи которых — пути от корня документа.
_LOGICAL_OPS = {"$and", "$or", "$nor"}
# '$$ROOT.a' / '$$CURRENT.a' — то же, что '$a' (путь от корня).
_ROOT_VARS = {"ROOT", "CURRENT"}

# Служебные поля, которые модели не нужны никогда.
_ALWAYS_STRIP_KEYS = {"search_terms"} | _SECRET_KEYS

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


def _cost_barrier(collection: str) -> dict:
    """{path: 0} для $project-барьера: каждое имя из _COST_KEYS в каждом
    контейнере коллекции (_COST_CONTAINERS). Пути не пересекаются (Mongo
    отклоняет $project с path collision): путь под уже вырезанным предком
    не добавляется. Исключение через массив поддокументов действует на
    каждый элемент: labors.parts.cost — у каждой запчасти каждой строки."""
    containers = _COST_CONTAINERS.get(collection, ("",))
    candidates = {f"{prefix}.{key}" if prefix else key for prefix in containers for key in _COST_KEYS}
    barrier: dict[str, int] = {}
    for path in sorted(candidates, key=lambda p: (p.count("."), p)):
        parts = path.split(".")
        if any(".".join(parts[:i]) in barrier for i in range(1, len(parts))):
            continue
        barrier[path] = 0
    return barrier


_COST_BARRIERS = {
    name: _cost_barrier(name) for name, cfg in COLLECTIONS.items() if cfg["costs_perm"]
}


# Без work_orders.view_costs (механик-режим) статус paid не отдаём — как в
# истории юнита у механика, он виден как completed. Стадия стоит в голове
# pipeline'а: фильтр, группировка и сортировка модели по "paid" ничего не
# находят. Документ без status поля не получает ($status — missing).
_MASK_PAID_STAGE = {
    "$set": {"status": {"$cond": [{"$eq": ["$status", "paid"]}, "completed", "$status"]}}
}


def _needs_barrier(collection: str, permissions: set[str]) -> bool:
    """Есть ли в коллекции поля, скрытые от этого пользователя: тогда find
    и count тоже идут pipeline'ом с _scoped_head."""
    return collection in _SECRET_COLLECTIONS or not _costs_allowed(collection, permissions)


def _scoped_head(collection: str, shop_variants: list, permissions: set[str]) -> list:
    """Начало pipeline'а: скоуп магазина и вырезание скрытых полей ДО
    фильтра/сортировки/стадий модели — учёток вендоров (всегда) и денег
    (без costs_perm). После этого их не видит ни один оператор ($jsonSchema,
    $expr, пути через $$this/$$ROOT, сравнение поддокументов целиком...),
    даже если guard по именам не распознал обход."""
    head = [{"$match": {"shop_id": {"$in": shop_variants}}}]
    if collection in _SECRET_COLLECTIONS:
        head.append({"$project": {key: 0 for key in sorted(_SECRET_KEYS)}})
    if not _costs_allowed(collection, permissions):
        head.append({"$project": dict(_COST_BARRIERS[collection])})
        if collection == "work_orders":
            head.append(_MASK_PAID_STAGE)
    return head


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


# Ссылка — (компоненты имени/пути, skip): первые skip компонентов не
# проверяются на деньги. Это имя выходного поля ВЕРХНЕГО уровня, которое
# раньше в pipeline'е задала сама модель ({"$group": {"total": {"$sum": 1}}},
# {"$count": "total"}, {"$project": {"rate": "$labors.labor.rate_code"}}):
# денежного поля с таким именем у документа быть не может — барьер вырезает
# каждое имя из _COST_KEYS на верхнем уровне (контейнер ""), а значение
# модель собрала из выражений, прошедших проверку. Остальные компоненты
# проверяются всегда: '$total.labor.hourly_rate' — отказ. Учётки вендоров
# проверяются по всем компонентам, без исключений.


def _path_ref(text: str, defined) -> tuple:
    """'$a.b' — путь от корня; '$$var.a.b' — через переменную: у $$ROOT /
    $$CURRENT дальше снова корень, у $$this / $$value / $$p / переменных
    $let проверяется весь путь (переменная может держать любой поддокумент)."""
    comps = tuple(text.lstrip("$").split("."))
    if not text.startswith("$$"):
        return comps, (1 if comps[0] in defined else 0)
    if comps[0] in _ROOT_VARS and len(comps) > 1:
        return comps, (2 if comps[1] in defined else 1)
    return comps, 0


def _key_ref(key: str, defined, *, from_root: bool) -> tuple:
    """Ключ-имя: путь от корня (фильтр, сортировка, проекция) или вложенное
    имя (литеральный документ, $elemMatch, sortBy, переменная $let)."""
    comps = tuple(key.split("."))
    return comps, (1 if from_root and comps[0] in defined else 0)


def _output_ref(name: str, defined) -> tuple:
    """Имя выходного поля, которое задаёт модель: верхнего уровня — её поле,
    на деньги не проверяется; вложенное 'a.b' — проверяется как путь (чистка
    результатов вложенные денежные имена всё равно вырезала бы)."""
    comps = tuple(name.split("."))
    if len(comps) == 1:
        return comps, 1
    return _key_ref(name, defined, from_root=True)


def _refs(node, defined=frozenset(), depth: int = 0, *, op_args: bool = False, from_root: bool = False):
    """Ссылки (компоненты, skip) на всё, что запрос адресует или называет:
    - ключи-не-операторы: пути фильтра/проекции/сортировки (from_root —
      ключи от корня документа; внутри $and/$or/$nor — тоже), ключи
      литеральных документов (в т.ч. в $elemMatch и равенстве поддокументу),
      ключи sortBy у $sortArray, имена переменных $let;
    - строки, начинающиеся с '$': пути '$a.b' и пути через переменные
      '$$this.labor.hourly_rate', '$$p.cost', '$$value' ($map/$filter/
      $reduce/$let) — регэксп по JSON их не видел (два '$' подряд);
    - строковые значения _NAME_VALUED_KEYS.
    Прочие строки — литералы (шаблон $regex, $options, операнды $eq/$in):
    поиск по тексту «balance» — не ссылка на поле balance. op_args — node
    является аргументами оператора из _ARG_STYLE_OPS (его ключи не имена).
    defined — выходные поля верхнего уровня, заданные моделью (см. выше)."""
    if depth > _MAX_DEPTH:
        raise DataToolError("query is nested too deeply")
    if isinstance(node, dict):
        for key, value in node.items():
            key = str(key)
            if key in _NAME_VALUED_KEYS and isinstance(value, str):
                yield tuple(value.split(".")), 0
            if key.startswith("$"):
                yield from _refs(
                    value, defined, depth + 1,
                    op_args=key in _ARG_STYLE_OPS and isinstance(value, dict),
                    from_root=from_root and key in _LOGICAL_OPS,
                )
                continue
            if not op_args:
                yield _key_ref(key, defined, from_root=from_root)
            yield from _refs(value, defined, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item, defined, depth + 1, from_root=from_root)
    elif isinstance(node, str) and node.startswith("$"):
        yield _path_ref(node, defined)


def _is_literal_doc(node) -> bool:
    """Документ с ключами-именами (не выражение-оператор {"$op": ...})."""
    return isinstance(node, dict) and bool(node) and not any(str(k).startswith("$") for k in node)


def _stage_refs(op: str, spec, defined: frozenset, refs: list) -> frozenset:
    """Ссылки одной стадии → в refs; возвращает выходные поля верхнего
    уровня, заданные моделью, ПОСЛЕ стадии. Выражения стадии видят документ
    до неё — поэтому ссылки считаются с прежним defined."""
    def walk(node, **kwargs):
        refs.extend(_refs(node, defined, 2, **kwargs))

    if op == "$match":
        walk(spec, from_root=True)
        return defined
    if op == "$count":
        if not isinstance(spec, str):
            walk(spec)
            return defined
        refs.append(_output_ref(spec, defined))
        return frozenset({spec})
    if op == "$sortByCount":
        walk(spec)
        return frozenset({"_id", "count"})
    if op in ("$replaceRoot", "$replaceWith"):
        root = spec
        if op == "$replaceRoot" and isinstance(spec, dict):
            root = spec.get("newRoot")
            walk({k: v for k, v in spec.items() if k != "newRoot"}, op_args=True)
        if _is_literal_doc(root):
            for key, value in root.items():
                refs.append(_output_ref(str(key), defined))
                walk(value)
            return frozenset(str(key) for key in root if "." not in str(key))
        # Корень — поддокумент источника ("$labors.labor", $mergeObjects...):
        # на верхнем уровне снова его поля, не поля модели.
        walk(root)
        return frozenset()
    if not isinstance(spec, dict):
        walk(spec)  # $limit / $skip / '$unwind: "$path"'; невалидное отклонит Mongo
        return defined
    if op == "$sort":
        walk(spec, from_root=True)
        return defined
    if op == "$unwind":
        index = spec.get("includeArrayIndex")
        walk({k: v for k, v in spec.items() if k != "includeArrayIndex"}, op_args=True)
        if isinstance(index, str):
            refs.append(_output_ref(index, defined))
            if "." not in index:
                return defined | {index}
        return defined
    if op == "$group":
        for key, value in spec.items():
            if key != "_id":
                refs.append(_output_ref(str(key), defined))
            walk(value)
        return frozenset(str(key) for key in spec) | {"_id"}
    if op in ("$bucket", "$bucketAuto"):
        output = spec.get("output")
        walk({k: v for k, v in spec.items() if k != "output"}, op_args=True)
        if not isinstance(output, dict):
            walk(output)
            return frozenset({"_id", "count"})
        for key, value in output.items():
            refs.append(_output_ref(str(key), defined))
            walk(value)
        return frozenset(str(key) for key in output) | {"_id"}
    if op in ("$project", "$addFields", "$set"):
        after = set(defined)
        for key, value in spec.items():
            key = str(key)
            if op == "$project" and (isinstance(value, (bool, int, float)) or _is_literal_doc(value)):
                # 1/0/true — включение/исключение, {...} — вложенная проекция:
                # это чтение поля документа, а не новое поле модели.
                refs.append(_key_ref(key, defined, from_root=True))
            else:
                refs.append(_output_ref(key, defined))
                if "." not in key:
                    after.add(key)
            walk(value)
        return frozenset(after)
    walk(spec)
    return defined


def _pipeline_refs(stages: list) -> tuple[list, frozenset]:
    """Ссылки pipeline'а (стадии уже провалидированы: {"$stage": spec}) и
    выходные поля верхнего уровня, заданные моделью к его концу."""
    refs: list = []
    defined: frozenset = frozenset()
    for stage in stages:
        op, spec = next(iter(stage.items()))
        defined = _stage_refs(op, spec, defined, refs)
    return refs, defined


def _rebinds_current(node, depth: int = 0) -> bool:
    """$let {"vars": {"CURRENT": ...}} / $map, $filter {"as": "CURRENT"}:
    внутри '$поле' читает уже то, к чему привязан CURRENT, а не документ —
    _path_ref на это не рассчитан ('$total' стал бы '<поддокумент>.total').
    ROOT Mongo переназначать не даёт."""
    if depth > _MAX_DEPTH:
        raise DataToolError("query is nested too deeply")
    if isinstance(node, dict):
        if node.get("as") == "CURRENT":
            return True
        variables = node.get("vars")
        if isinstance(variables, dict) and "CURRENT" in variables:
            return True
        return any(_rebinds_current(value, depth + 1) for value in node.values())
    if isinstance(node, list):
        return any(_rebinds_current(item, depth + 1) for item in node)
    return False


def _reject_cost_probes(node, collection: str, refs: list) -> None:
    """Без costs_perm нельзя даже назвать денежное поле: иначе $group /
    $project / $map переложат цены в поля с другими именами (strip
    результатов их не поймает), а фильтр/сортировка по цене станут
    оракулом («сколько WO дороже $500?»). Запрещены и обходы, которые
    достают или собирают поле по имени-строке ($objectToArray, $getField,
    $setField, $unsetField, $arrayToObject; имя собирается через $concat и
    проверке имён не видно), и переназначение CURRENT. Гарантия — барьер в
    _scoped_head; это первая линия, которая объясняет модели, что нельзя."""
    for comps, skip in refs:
        for part in comps[skip:]:
            if part in _COST_KEYS:
                raise DataToolError(
                    f"field '{part}' is a cost/price field — this user is not allowed to see costs "
                    "(don't reference money fields; give nested output fields non-money names)"
                )
    if _rebinds_current(node):
        raise DataToolError(
            "rebinding CURRENT is not allowed here — this user can't see costs; "
            "use a named variable ($$name) instead"
        )
    dumped = json.dumps(node, default=str)
    for token in _COST_BYPASS_TOKENS:
        if token in dumped:
            raise DataToolError(
                f"{token} is not allowed here — this user can't see costs; name the fields you need instead"
            )


def _reject_secret_references(node, collection: str, refs: list) -> None:
    """Учётки порталов вендоров: ни ссылок на поля (по всем компонентам, в
    т.ч. имена выходных полей), ни обхода через $$ROOT."""
    for comps, _skip in refs:
        for part in comps:
            if part in _SECRET_KEYS:
                raise DataToolError(
                    f"field '{part}' is a vendor portal credential — it is not available to the assistant"
                )
    if collection in _SECRET_COLLECTIONS:
        dumped = json.dumps(node, default=str)
        for token in _SECRET_BYPASS_TOKENS:
            if token in dumped:
                raise DataToolError(
                    f"{token} is not allowed on '{collection}' — name the fields you need instead"
                )


def _guard_names(nodes: list, collection: str, permissions: set[str]) -> None:
    """Первая линия для find / count: имена в фильтре, проекции и сортировке
    (по отдельности; ключи — пути от корня): учётки вендоров — для всех,
    деньги — без costs_perm коллекции."""
    costs_hidden = not _costs_allowed(collection, permissions)
    for node in nodes:
        refs = list(_refs(node, from_root=True))
        _reject_secret_references(node, collection, refs)
        if costs_hidden:
            _reject_cost_probes(node, collection, refs)


def _guard_pipeline(stages: list, collection: str, permissions: set[str]) -> frozenset:
    """Первая линия для aggregate (стадии уже провалидированы). Возвращает
    выходные поля верхнего уровня, заданные моделью, — их чистка результатов
    не вырезает (для пользователя без costs_perm; иначе — пусто)."""
    refs, outputs = _pipeline_refs(stages)
    _reject_secret_references(stages, collection, refs)
    if _costs_allowed(collection, permissions):
        return frozenset()
    _reject_cost_probes(stages, collection, refs)
    return outputs


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


def _clean_docs(docs: list, collection: str, permissions: set[str], keep_top=frozenset()) -> list:
    """Вычистить служебные поля и (без costs_perm) денежные ключи на любой
    глубине. keep_top — выходные поля верхнего уровня, заданные моделью
    (_guard_pipeline): {"total": {"$sum": 1}} — её счётчик, а не деньги;
    сам ключ остаётся, его значение чистится как обычно."""
    strip = set(_ALWAYS_STRIP_KEYS)
    if not _costs_allowed(collection, permissions):
        strip |= _COST_KEYS
    top = strip - (set(keep_top) - _ALWAYS_STRIP_KEYS)
    return [
        _jsonable({k: _strip_keys(v, strip) for k, v in doc.items() if k not in top})
        for doc in docs
    ]


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
    _guard_names([args.get("filter"), args.get("projection"), args.get("sort")], collection, permissions)
    user_filter = _coerce_filter(args.get("filter"))

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

    if _needs_barrier(collection, permissions):
        # find = aggregate, где скрытые поля (учётки вендоров, деньги без
        # costs_perm) вырезаны ещё до фильтра и сортировки модели
        # (_scoped_head): оракул по скрытому полю невозможен.
        pipeline = _scoped_head(collection, shop_variants, permissions)
        if user_filter:
            pipeline.append({"$match": user_filter})
        if sort_list:
            pipeline.append({"$sort": dict(sort_list)})
        pipeline.append({"$limit": limit})
        if projection:
            pipeline.append({"$project": projection})
        docs = list(shop_db[collection].aggregate(pipeline, maxTimeMS=MAX_TIME_MS))
    else:
        flt = _scope_filter(user_filter, shop_variants)
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
    _guard_names([args.get("filter")], collection, permissions)
    user_filter = _coerce_filter(args.get("filter"))
    if _needs_barrier(collection, permissions):
        # Как в _tool_find: считаем документы, из которых скрытые поля уже вырезаны.
        pipeline = _scoped_head(collection, shop_variants, permissions)
        if user_filter:
            pipeline.append({"$match": user_filter})
        pipeline.append({"$count": "n"})
        rows = list(shop_db[collection].aggregate(pipeline, maxTimeMS=MAX_TIME_MS))
        return {"count": int(rows[0]["n"]) if rows else 0}
    flt = _scope_filter(user_filter, shop_variants)
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
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise DataToolError("each stage must be an object with exactly one $-operator")
        op = next(iter(stage))
        if op not in _ALLOWED_STAGES:
            raise DataToolError(
                f"stage {op} is not allowed; allowed: {', '.join(sorted(_ALLOWED_STAGES))}"
            )
    keep_top = _guard_pipeline(pipeline, collection, permissions)

    stages = []
    for stage in pipeline:
        op = next(iter(stage))
        if op == "$limit":
            try:
                stage = {"$limit": max(1, min(int(stage["$limit"]), MAX_AGG_DOCS))}
            except (TypeError, ValueError):
                raise DataToolError("$limit must be an integer")
        elif op == "$match":
            stage = {"$match": _coerce_filter(stage["$match"])}
        stages.append(stage)

    # Вторая линия обороны: учёток и (без costs_perm) денег нет в документах
    # ещё до стадий модели.
    full = _scoped_head(collection, shop_variants, permissions) + stages + [{"$limit": MAX_AGG_DOCS}]
    docs = list(shop_db[collection].aggregate(full, maxTimeMS=MAX_TIME_MS))
    return {"docs": _clean_docs(docs, collection, permissions, keep_top), "returned": len(docs)}


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
