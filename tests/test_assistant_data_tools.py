"""Guard-слой read-only доступа AI-помощника к базе шопа + tool-use цикл чата.

Проверяем ровно то, чему нельзя доверять модель: изоляцию по shop_id,
permissions по коллекциям, вычистку денежных полей (механик-режим),
запрет опасных операторов и клампы лимитов. Плюс сквозной тест цикла
«модель → инструмент → модель» с фейковым OpenAI-клиентом.
"""
import json
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login

from app.blueprints.assistant.services import data_tools
from app.constants.permissions import ALL_PERMISSIONS

FULL_PERMS = set(ALL_PERMISSIONS)
# Механик: видит WO, но не видит цены (нет *.view_costs) и не видит parts.
MECHANIC_PERMS = {"work_orders.view", "work_orders.create", "calendar.view"}
# Старший механик: видит и каталог запчастей, но тоже без *.view_costs.
SENIOR_PERMS = {"work_orders.view", "parts.view", "customers.view", "calendar.view"}


@pytest.fixture(scope="module")
def shop_data(seed):
    """Живые документы в тестовой shop-базе A + «чужой» документ другого шопа."""
    client = MongoClient(TEST_MONGO_URI)
    shop_db = client[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    foreign_shop_id = seed["shop_b"]["_id"]

    customer_id = ObjectId()
    shop_db.customers.insert_one({
        "_id": customer_id, "shop_id": shop_id, "company_name": "Acme Trucking",
        "is_active": True, "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
    })

    wo_ids = []
    for i, (status, total, created) in enumerate([
        ("open", 100.0, datetime(2026, 6, 1, tzinfo=timezone.utc)),
        ("open", 250.0, datetime(2026, 7, 1, tzinfo=timezone.utc)),
        ("paid", 400.0, datetime(2026, 7, 10, tzinfo=timezone.utc)),
    ]):
        doc = {
            "_id": ObjectId(),
            # исторически shop_id встречается и ObjectId, и строкой
            "shop_id": shop_id if i % 2 == 0 else str(shop_id),
            "wo_number": 1000 + i,
            "customer_id": str(customer_id),  # легаси: ссылка строкой
            "status": status,
            "totals": {"grand_total": total, "labor_total": total / 2},
            "labors": [{"labor_id": "x", "labor": {"description": "brakes", "hourly_rate": 100.0},
                        "parts": [{"part_number": "BP-1", "qty": 1, "cost": 10.0, "price": 20.0}]}],
            "is_active": True,
            "created_at": created,
        }
        shop_db.work_orders.insert_one(doc)
        wo_ids.append(doc["_id"])

    # Чужой WO (другой магазин) в ТОЙ ЖЕ физической базе — не должен быть виден.
    shop_db.work_orders.insert_one({
        "_id": ObjectId(), "shop_id": foreign_shop_id, "wo_number": 9999,
        "status": "open", "totals": {"grand_total": 777.0}, "is_active": True,
        "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
    })

    yield {
        "shop_db": shop_db, "shop_id": shop_id,
        "customer_id": customer_id, "wo_ids": wo_ids,
    }

    shop_db.work_orders.delete_many({})
    shop_db.customers.delete_many({})
    client.close()


def _run(shop_data, name, args, perms=FULL_PERMS):
    return data_tools.run_tool(
        name, args,
        shop_db=shop_data["shop_db"], shop_id=shop_data["shop_id"],
        permissions=perms,
    )


# ── Изоляция и выборка ───────────────────────────────────────────────


def test_find_scoped_to_shop(shop_data):
    result = _run(shop_data, "db_find", {"collection": "work_orders"})
    assert "error" not in result
    numbers = {d["wo_number"] for d in result["docs"]}
    assert numbers == {1000, 1001, 1002}  # чужой 9999 не виден
    # и str, и ObjectId варианты shop_id попали в выборку
    assert result["returned"] == 3


def test_count_and_filter(shop_data):
    result = _run(shop_data, "db_count", {
        "collection": "work_orders", "filter": {"status": "open"},
    })
    assert result == {"count": 2}


def test_date_range_coercion(shop_data):
    result = _run(shop_data, "db_find", {
        "collection": "work_orders",
        "filter": {"created_at": {"$gte": "2026-07-01", "$lt": "2026-07-05T00:00:00Z"}},
    })
    assert [d["wo_number"] for d in result["docs"]] == [1001]


def test_id_string_matches_objectid_and_legacy_string(shop_data):
    # _id хранится ObjectId — hex-строка должна находить документ
    hex_id = str(shop_data["wo_ids"][0])
    result = _run(shop_data, "db_find", {
        "collection": "work_orders", "filter": {"_id": hex_id},
    })
    assert result["returned"] == 1
    assert result["docs"][0]["_id"] == hex_id  # ObjectId сериализован строкой

    # customer_id хранится СТРОКОЙ — тот же фильтр тоже должен работать
    result = _run(shop_data, "db_count", {
        "collection": "work_orders",
        "filter": {"customer_id": str(shop_data["customer_id"])},
    })
    assert result == {"count": 3}

    # и через $in
    result = _run(shop_data, "db_count", {
        "collection": "work_orders",
        "filter": {"customer_id": {"$in": [str(shop_data["customer_id"])]}},
    })
    assert result == {"count": 3}


def test_aggregate_group_scoped(shop_data):
    result = _run(shop_data, "db_aggregate", {
        "collection": "work_orders",
        "pipeline": [
            {"$match": {"is_active": True}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ],
    })
    assert "error" not in result
    by_status = {d["_id"]: d["n"] for d in result["docs"]}
    assert by_status == {"open": 2, "paid": 1}  # чужой шоп не участвует


def test_find_limit_clamped(shop_data):
    result = _run(shop_data, "db_find", {"collection": "work_orders", "limit": 10_000})
    assert result["returned"] <= data_tools.MAX_FIND_DOCS


# ── Права ────────────────────────────────────────────────────────────


def test_unknown_collection_rejected(shop_data):
    result = _run(shop_data, "db_find", {"collection": "roles"})
    assert "unknown collection" in result["error"]


def test_collection_without_permission_rejected(shop_data):
    result = _run(shop_data, "db_find", {"collection": "parts"}, perms=MECHANIC_PERMS)
    assert "permission" in result["error"]


def test_mechanic_collections_subset():
    allowed = data_tools.allowed_collections(MECHANIC_PERMS)
    assert "work_orders" in allowed
    assert "work_order_payments" not in allowed  # деньги закрыты view_costs
    assert "labor_rates" not in allowed
    assert "parts" not in allowed


def test_mechanic_cost_fields_stripped(shop_data):
    result = _run(shop_data, "db_find", {"collection": "work_orders"}, perms=MECHANIC_PERMS)
    assert result["returned"] == 3
    for doc in result["docs"]:
        dumped = json.dumps(doc)
        assert "totals" not in doc
        assert '"price"' not in dumped and '"cost"' not in dumped
        assert '"hourly_rate"' not in dumped
        assert doc["status"]  # не-денежные поля на месте

    # владельцу деньги видны
    result = _run(shop_data, "db_find", {"collection": "work_orders"})
    assert all("totals" in d for d in result["docs"])


def test_mechanic_aggregate_cost_reference_rejected(shop_data):
    pipeline = [{"$group": {"_id": None, "s": {"$sum": "$totals.grand_total"}}}]
    result = _run(
        shop_data, "db_aggregate",
        {"collection": "work_orders", "pipeline": pipeline}, perms=MECHANIC_PERMS,
    )
    assert "cost" in result["error"]

    # владельцу — можно
    result = _run(shop_data, "db_aggregate", {"collection": "work_orders", "pipeline": pipeline})
    assert result["docs"][0]["s"] == 750.0


@pytest.mark.parametrize("tool,args", [
    # count по цене — оракул: «сколько WO дороже $300»
    ("db_count", {"collection": "work_orders", "filter": {"totals.grand_total": {"$gt": 300}}}),
    ("db_find", {"collection": "work_orders", "filter": {"totals.grand_total": {"$gt": 300}}}),
    # порядок документов по цене выдаёт её
    ("db_find", {"collection": "work_orders", "sort": {"totals.grand_total": -1}}),
    ("db_find", {"collection": "work_orders", "projection": {"totals": 1}}),
    # обход проверки по именам: документ «целиком»
    ("db_count", {"collection": "work_orders", "filter": {
        "$expr": {"$gt": [{"$size": {"$objectToArray": "$$ROOT"}}, 0]}}}),
    ("db_aggregate", {"collection": "work_orders", "pipeline": [
        {"$project": {"kv": {"$objectToArray": "$$ROOT"}}}]}),
])
def test_mechanic_cannot_probe_costs(shop_data, tool, args):
    result = _run(shop_data, tool, args, perms=MECHANIC_PERMS)
    assert "error" in result, result
    assert "cost" in result["error"]


def test_owner_can_count_and_sort_by_costs(shop_data):
    result = _run(shop_data, "db_count", {
        "collection": "work_orders", "filter": {"totals.grand_total": {"$gt": 300}}})
    assert result["count"] == 1
    result = _run(shop_data, "db_find", {
        "collection": "work_orders", "sort": {"totals.grand_total": -1}, "limit": 1})
    assert result["docs"][0]["totals"]["grand_total"] == 400.0


def test_mechanic_plain_queries_still_work(shop_data):
    result = _run(shop_data, "db_count", {
        "collection": "work_orders", "filter": {"status": "open"}}, perms=MECHANIC_PERMS)
    assert result["count"] == 2
    result = _run(shop_data, "db_find", {
        "collection": "work_orders", "sort": {"wo_number": 1}}, perms=MECHANIC_PERMS)
    assert result["returned"] == 3


# ── Деньги без view_costs: пути через переменные и барьер ────────────
# Отдельный «магазин» (свой shop_id) в той же базе: уникальные суммы, по
# которым видна любая утечка, и документы, невидимые остальным тестам модуля.

MISC_JSON = json.dumps([{"description": "Shop rag", "quantity": 1, "price": 31.37, "taxable": True}])
WO_MONEY = (
    # современный WO 7001: ставки, тоталы строк, цены/себестоимость/коры/misc запчастей, totals
    "137.13", "141.14", "589.59", "212.21", "11.17", "23.29", "7.71", "3.53", "31.37",
    "12.21", "24.42", "14.41", "27.72", "418.41", "643.43", "61.61", "0.0625", "19.91",
    "977.97", "274.26",
    # легаси WO 7002: тоталы верхнего уровня и массив blocks
    "853.35", "421.12", "432.23", "18.81", "733.37", "13.31", "29.92",
)
PART_MONEY = ("41.14", "52.25", "6.36", "4.47", "39.93", "5551")
# строка инвентаризации (average_cost), кор на руках и возврат кора (core_cost, credit_total)
STOCK_MONEY = ("46.64", "8.48", "16.96")


def _leaked(payload, values=WO_MONEY) -> list:
    """Засеянные суммы, которые видны в ответе (числом или внутри строки)."""
    dumped = json.dumps(payload, default=str)
    return [v for v in values if re.search(rf"(?<![\w.]){re.escape(v)}(?!\w)", dumped)]


@pytest.fixture(scope="module")
def money_shop(app, seed):
    client = MongoClient(TEST_MONGO_URI)
    shop_db = client[SHOP_A_DB]
    shop_id = ObjectId()
    created = datetime(2026, 8, 1, tzinfo=timezone.utc)
    modern = {
        "_id": ObjectId(), "shop_id": shop_id, "wo_number": 7001, "status": "open",
        "is_active": True, "created_at": created,
        "labors": [
            {"labor_id": "L1",
             "labor": {"description": "front brakes", "hours": "2", "rate_code": "standard",
                       "hourly_rate": 137.13, "labor_full_total": 589.59,
                       "issue_description": "squeal", "assigned_mechanics": []},
             "parts": [
                 {"part_number": "BP-7", "description": "pads", "qty": 2, "cost": 11.17,
                  "price": 23.29, "core_charge": 7.71, "misc_charge": 3.53,
                  "misc_charge_description": MISC_JSON, "one_time_part": False, "unit": "each"},
                 {"part_number": "BR-9", "description": "rotor", "qty": 1, "cost": 12.21,
                  "price": 24.42, "misc_charge_description": "", "one_time_part": False},
             ]},
            {"labor_id": "L2",
             "labor": {"description": "oil change", "hours": "1", "rate_code": "standard",
                       "hourly_rate": 141.14, "labor_full_total": 212.21},
             "parts": [{"part_number": "OF-1", "description": "oil filter", "qty": 1,
                        "cost": 14.41, "price": 27.72}]},
        ],
        "totals": {"labor": 418.41, "labor_total": 643.43, "parts_total": 61.61,
                   "sales_tax_rate": 0.0625, "sales_tax_total": 19.91, "grand_total": 977.97,
                   "labors": [{"labor": 274.26, "labor_full_total": 589.59}]},
    }
    # Легаси: тоталы верхнего уровня, плоский массив blocks, shop_id строкой.
    legacy = {
        "_id": ObjectId(), "shop_id": str(shop_id), "wo_number": 7002, "status": "paid",
        "is_active": True, "created_at": created,
        "grand_total": 853.35, "labor_total": 421.12, "parts_total": 432.23,
        "sales_tax_total": 18.81,
        "blocks": [{"labor_description": "legacy job", "labor_hours": "3",
                    "labor_rate_code": "standard", "labor_full_total": 733.37,
                    "parts": [{"part_number": "OLD-1", "description": "belt", "qty": 1,
                               "cost": 13.31, "price": 29.92}]}],
    }
    part = {
        "_id": ObjectId(), "shop_id": shop_id, "part_number": "MP-1", "description": "Air filter",
        "is_active": True, "in_stock": 3, "do_not_track_inventory": False,
        "average_cost": 41.14, "has_selling_price": True, "selling_price": 52.25,
        "core_has_charge": True, "core_cost": 6.36, "misc_has_charge": True,
        "misc_charges": [{"description": "Disposal", "price": 4.47, "taxable": True}],
        "last_price": 39.93, "last_price_order_number": 5551, "last_price_at": created,
        "created_at": created,
    }
    # Как их пишут parts/services/stocktake.py, inventory.apply_core_delta и
    # возврат кора в parts/routes.py.
    stock_item = {
        "_id": ObjectId(), "shop_id": shop_id, "stocktake_id": ObjectId(), "part_id": part["_id"],
        "part_number": "MP-1", "description": "Air filter", "location_id": None,
        "location_path": "Unassigned", "expected_initial": 3, "expected_at_count": 3,
        "counted_qty": 2, "variance": -1, "average_cost": 46.64, "status": "counted",
        "applied": False, "created_at": created,
    }
    core = {
        "_id": ObjectId(), "shop_id": shop_id, "part_id": part["_id"], "part_number": "MP-1",
        "description": "Air filter", "core_cost": 8.48, "quantity": 4, "is_active": True,
        "created_at": created,
    }
    core_return = {
        "_id": ObjectId(), "shop_id": shop_id, "core_id": core["_id"], "part_id": part["_id"],
        "part_number": "MP-1", "description": "Air filter", "quantity": 2, "core_cost": 8.48,
        "credit_total": 16.96, "vendor_id": None, "vendor_name": "Core Vendor", "notes": "",
        "returned_at": created, "is_active": True, "created_at": created,
    }
    shop_db.work_orders.insert_many([modern, legacy])
    shop_db.parts.insert_one(part)
    shop_db.stocktake_items.insert_one(stock_item)
    shop_db.cores.insert_one(core)
    shop_db.core_returns.insert_one(core_return)

    yield {"app": app, "shop_db": shop_db, "shop_id": shop_id}

    shop_db.work_orders.delete_many({"_id": {"$in": [modern["_id"], legacy["_id"]]}})
    shop_db.parts.delete_one({"_id": part["_id"]})
    shop_db.stocktake_items.delete_one({"_id": stock_item["_id"]})
    shop_db.cores.delete_one({"_id": core["_id"]})
    shop_db.core_returns.delete_one({"_id": core_return["_id"]})
    client.close()


def _run_money(money_shop, name, args, perms):
    # app context: неожиданная ошибка Mongo логируется и видна в assert как {"error": ...}
    with money_shop["app"].app_context():
        return data_tools.run_tool(
            name, args, shop_db=money_shop["shop_db"], shop_id=money_shop["shop_id"],
            permissions=perms,
        )


def _run_wo(money_shop, name, args, perms):
    return _run_money(money_shop, name, {"collection": "work_orders", **args}, perms)


# D8-01: ставки и себестоимость через переменные $map, переложенные под новые имена.
D8_PIPELINE = [{"$project": {
    "wo_number": 1,
    "r": {"$map": {"input": "$labors", "in": "$$this.labor.hourly_rate"}},
    "pc": {"$map": {"input": "$labors", "as": "b",
                    "in": {"$map": {"input": "$$b.parts", "as": "p", "in": "$$p.cost"}}}},
}}]
LABOR_SUM = {"$sum": {"$map": {"input": "$labors", "as": "l", "in": "$$l.labor.labor_full_total"}}}
# $ifNull: у легаси-WO нет labors, а $in требует массив
PART_COSTS = {"$reduce": {"input": {"$ifNull": ["$labors", []]}, "initialValue": [],
                          "in": {"$concatArrays": ["$$value", "$$this.parts.cost"]}}}
FIRST_PARTS = {"$ifNull": [{"$arrayElemAt": ["$labors.parts", 0]}, []]}

VARIABLE_PATH_PROBES = [
    pytest.param("db_aggregate", {"pipeline": D8_PIPELINE}, id="map-this-and-nested-p"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"x": {"$filter": {
        "input": FIRST_PARTS, "as": "p", "cond": {"$gt": ["$$p.price", 20]}}}}}]}, id="filter-cond"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"s": {"$reduce": {
        "input": "$labors", "initialValue": 0,
        "in": {"$add": ["$$value", "$$this.labor.labor_full_total"]}}}}}]}, id="reduce-value-this"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"r": {"$let": {
        "vars": {"l": {"$arrayElemAt": ["$labors", 0]}},
        "in": "$$l.labor.hourly_rate"}}}}]}, id="let-var"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"x": {"$sortArray": {
        "input": FIRST_PARTS, "sortBy": {"price": -1}}}}}]}, id="sortarray-sortby"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"m": {"$map": {
        "input": "$labors", "in": "$$this.parts.misc_charge_description"}}}}]}, id="misc-json-string"),
    pytest.param("db_aggregate", {"pipeline": [{"$group": {"_id": None, "s": {"$sum": LABOR_SUM}}}]},
                 id="group-sum-of-map"),
    pytest.param("db_aggregate", {"pipeline": [{"$match": {"$expr": {"$gt": [LABOR_SUM, 500]}}}]},
                 id="aggregate-match-expr"),
    pytest.param("db_count", {"filter": {"$expr": {"$gt": [LABOR_SUM, 500]}}}, id="count-expr-oracle"),
    pytest.param("db_find", {"filter": {"$expr": {"$in": [11.17, PART_COSTS]}}}, id="find-expr-reduce"),
    pytest.param("db_find", {"filter": {"$expr": {"$gt": [{"$let": {
        "vars": {"b": {"$arrayElemAt": ["$labors", 0]}}, "in": "$$b.labor.hourly_rate"}}, 100]}}},
        id="find-expr-let"),
    # ключ, собранный из строки, — оракул сравнения поддокумента целиком
    pytest.param("db_count", {"filter": {"$expr": {"$in": [
        {"$arrayToObject": [[["description", "front brakes"],
                             [{"$concat": ["hourly_", "rate"]}, 137.13]]]},
        {"$ifNull": ["$labors.labor", []]}]}}}, id="count-arraytoobject-oracle"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"x": {"$setField": {
        "field": {"$literal": "z"}, "input": {"$arrayElemAt": ["$labors.labor", 0]}, "value": 1}}}}]},
        id="setfield"),
    pytest.param("db_aggregate", {"pipeline": [{"$project": {"x": {"$unsetField": {
        "field": "description", "input": {"$arrayElemAt": ["$labors.labor", 0]}}}}}]},
        id="unsetfield"),
]


@pytest.mark.parametrize("tool,args", VARIABLE_PATH_PROBES)
def test_mechanic_variable_money_paths_rejected(money_shop, tool, args):
    result = _run_wo(money_shop, tool, args, MECHANIC_PERMS)
    assert "error" in result, result
    assert "cost" in result["error"]
    assert _leaked(result) == []


@pytest.mark.parametrize("tool,args", VARIABLE_PATH_PROBES)
def test_owner_variable_money_paths_still_run(money_shop, tool, args):
    """Те же запросы валидны для Mongo: отказ механику — не «просто синтаксис»."""
    result = _run_wo(money_shop, tool, args, FULL_PERMS)
    assert "error" not in result, result


def test_owner_sees_money_through_variable_paths(money_shop):
    result = _run_wo(money_shop, "db_aggregate",
                     {"pipeline": [{"$match": {"wo_number": 7001}}] + D8_PIPELINE}, FULL_PERMS)
    doc = result["docs"][0]
    assert doc["r"] == [137.13, 141.14]
    assert doc["pc"] == [[11.17, 12.21], [14.41]]

    assert _run_wo(money_shop, "db_count",
                   {"filter": {"$expr": {"$gt": [LABOR_SUM, 500]}}}, FULL_PERMS) == {"count": 1}
    found = _run_wo(money_shop, "db_find",
                    {"filter": {"$expr": {"$in": [11.17, PART_COSTS]}}}, FULL_PERMS)
    assert [d["wo_number"] for d in found["docs"]] == [7001]
    assert found["docs"][0]["totals"]["grand_total"] == 977.97
    assert found["docs"][0]["labors"][0]["parts"][0]["misc_charge_description"] == MISC_JSON


def test_mechanic_text_search_with_money_words_still_works(money_shop):
    """Литералы — не имена полей: поиск «balance», статус «due», $dateSubtract
    с аргументом amount не отклоняются (D8-02)."""
    cases = [
        ({"labors.labor.description": {"$regex": "balance"}}, 0),
        ({"labors.labor.description": {"$regex": "BRAKES", "$options": "i"}}, 1),
        ({"status": "due"}, 0),
        ({"status": {"$in": ["due", "open"]}}, 1),
        ({"$expr": {"$gte": ["$created_at", {"$dateSubtract": {
            "startDate": "$$NOW", "unit": "day", "amount": 36500}}]}}, 2),
    ]
    for flt, expected in cases:
        assert _run_wo(money_shop, "db_count", {"filter": flt}, MECHANIC_PERMS) == {"count": expected}, flt
        found = _run_wo(money_shop, "db_find", {"filter": flt}, MECHANIC_PERMS)
        assert found["returned"] == expected, (flt, found)
        assert _leaked(found) == []

    found = _run_wo(money_shop, "db_find",
                    {"filter": {"labors.labor.description": {"$regex": "brakes"}}}, MECHANIC_PERMS)
    labor = found["docs"][0]["labors"][0]
    assert labor["labor"]["description"] == "front brakes"
    assert labor["labor"]["issue_description"] == "squeal"
    assert [p["part_number"] for p in labor["parts"]] == ["BP-7", "BR-9"]

    grouped = _run_wo(money_shop, "db_aggregate", {"pipeline": [
        {"$group": {"_id": "$status", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]}, MECHANIC_PERMS)
    # paid механику виден как completed (test_mechanic_never_sees_paid_status)
    assert grouped["docs"] == [{"_id": "completed", "n": 1}, {"_id": "open", "n": 1}]


def test_mechanic_never_sees_paid_status(money_shop):
    """Механик-режим (CLAUDE.md): статус paid механику не отдаём — виден как
    completed, как в истории юнита; фильтр по "paid" ничего не находит, в том
    числе через $$ROOT. Владелец видит paid как есть."""
    for perms in (MECHANIC_PERMS, SENIOR_PERMS):
        found = _run_wo(money_shop, "db_find", {"filter": {"wo_number": 7002}}, perms)
        assert [d["status"] for d in found["docs"]] == ["completed"], found
        assert _run_wo(money_shop, "db_count", {"filter": {"status": "paid"}}, perms) == {"count": 0}
        assert _run_wo(money_shop, "db_count", {"filter": {"status": "completed"}}, perms) == {"count": 1}
        agg = _run_wo(money_shop, "db_aggregate", {"pipeline": [
            {"$match": {"status": {"$in": ["paid", "completed"]}}},
            {"$project": {"_id": 0, "wo_number": 1, "status": 1, "st": "$$ROOT.status"}}]}, perms)
        assert agg["docs"] == [{"wo_number": 7002, "status": "completed", "st": "completed"}], agg
        everything = _run_wo(money_shop, "db_find", {"filter": {}}, perms)
        assert everything["returned"] == 2 and '"paid"' not in json.dumps(everything), everything

    owner = _run_wo(money_shop, "db_find", {"filter": {"wo_number": 7002}}, FULL_PERMS)
    assert owner["docs"][0]["status"] == "paid"
    assert _run_wo(money_shop, "db_count", {"filter": {"status": "paid"}}, FULL_PERMS) == {"count": 1}


def _disable_cost_name_guard_and_strip(monkeypatch):
    """Остаётся только барьер _scoped_head: проверка имён/обходов ничего не
    отклоняет, результаты не чистятся."""
    monkeypatch.setattr(data_tools, "_reject_cost_probes", lambda *args, **kwargs: None)
    monkeypatch.setattr(data_tools, "_clean_docs",
                        lambda docs, *args, **kwargs: [data_tools._jsonable(d) for d in docs])


def test_cost_barrier_alone_hides_money(money_shop, monkeypatch):
    """Вторая линия: проверка имён и чистка результата выключены — механик
    всё равно не видит денег и не может фильтровать по ним, потому что
    _scoped_head вырезает их до стадий модели (в т.ч. поэлементно в массивах)."""
    _disable_cost_name_guard_and_strip(monkeypatch)

    reads = [
        ("db_aggregate", {"pipeline": D8_PIPELINE + [{"$sort": {"wo_number": 1}}]}),
        ("db_aggregate", {"pipeline": [{"$replaceRoot": {"newRoot": "$$ROOT"}}]}),
        ("db_aggregate", {"pipeline": [{"$project": {"kv": {"$objectToArray": "$$ROOT"}}}]}),
        # $$ROOT механику теперь разрешён: целые документы под «своими» именами модели
        ("db_aggregate", {"pipeline": [{"$group": {"_id": "$status", "total": {"$sum": 1},
                                                   "wos": {"$push": "$$ROOT"}}}]}),
        ("db_aggregate", {"pipeline": [{"$project": {
            "m": "$labors.parts.misc_charge_description", "t": "$totals", "g": "$grand_total",
            "b": "$blocks", "s": LABOR_SUM, "c": PART_COSTS}}]}),
        ("db_find", {"limit": 50}),
        ("db_find", {"projection": {"totals": 1, "labors": 1, "blocks": 1, "grand_total": 1}}),
    ]
    for tool, args in reads:
        mech = _run_wo(money_shop, tool, args, MECHANIC_PERMS)
        assert "error" not in mech, (tool, args, mech)
        assert mech["returned"] == 2, (tool, args, mech)
        assert _leaked(mech) == [], (tool, args, _leaked(mech))
        owner = _run_wo(money_shop, tool, args, FULL_PERMS)
        assert _leaked(owner), (tool, args)  # без барьера деньги видны — проверка не пустая

    # Поэлементно: у каждой строки и каждой запчасти поле вырезано, остальное на месте.
    mech = _run_wo(money_shop, "db_aggregate", {"pipeline": D8_PIPELINE + [{"$sort": {"wo_number": 1}}]},
                   MECHANIC_PERMS)
    assert mech["docs"][0]["r"] == [None, None]
    assert mech["docs"][0]["pc"] == [[None, None], [None]]
    whole = _run_wo(money_shop, "db_find", {"filter": {"wo_number": 7001}}, MECHANIC_PERMS)["docs"][0]
    assert "totals" not in whole
    assert whole["labors"][0]["labor"] == {
        "description": "front brakes", "hours": "2", "rate_code": "standard",
        "issue_description": "squeal", "assigned_mechanics": []}
    assert whole["labors"][0]["parts"][0] == {
        "part_number": "BP-7", "description": "pads", "qty": 2, "one_time_part": False, "unit": "each"}
    legacy = _run_wo(money_shop, "db_find", {"filter": {"wo_number": 7002}}, MECHANIC_PERMS)["docs"][0]
    assert not {"grand_total", "labor_total", "parts_total", "sales_tax_total"} & set(legacy)
    assert legacy["blocks"][0]["labor_description"] == "legacy job"
    assert legacy["blocks"][0]["parts"][0] == {"part_number": "OLD-1", "description": "belt", "qty": 1}

    # Фильтры по деньгам — в любой форме — ничего не находят (для владельца находят).
    oracles = [
        {"totals.grand_total": {"$gt": 0}},
        {"grand_total": {"$gt": 0}},
        {"labors.labor.hourly_rate": 137.13},
        {"labors.parts.cost": {"$gt": 0}},
        {"labors.parts.misc_charge_description": {"$regex": "31\\.37"}},
        {"blocks.parts.price": {"$gt": 0}},
        {"blocks.labor_full_total": {"$gt": 0}},
        {"$expr": {"$gt": [LABOR_SUM, 0]}},
        {"$expr": {"$in": [11.17, PART_COSTS]}},
        {"$expr": {"$gt": [{"$size": {"$objectToArray": {"$ifNull": ["$totals", {}]}}}, 0]}},
        {"$expr": {"$in": [137.13, {"$map": {"input": {"$ifNull": ["$labors.labor", []]}, "as": "l",
                                             "in": {"$getField": {"field": "hourly_rate", "input": "$$l"}}}}]}},
    ]
    for flt in oracles:
        assert _run_wo(money_shop, "db_count", {"filter": flt}, MECHANIC_PERMS) == {"count": 0}, flt
        assert _run_wo(money_shop, "db_find", {"filter": flt}, MECHANIC_PERMS)["returned"] == 0, flt
        agg = _run_wo(money_shop, "db_aggregate", {"pipeline": [{"$match": flt}]}, MECHANIC_PERMS)
        assert agg["returned"] == 0, (flt, agg)
        assert _run_wo(money_shop, "db_count", {"filter": flt}, FULL_PERMS)["count"] >= 1, flt


def test_misc_charges_and_last_price_hidden_without_costs(money_shop):
    """D8-A1: misc_charge_description (JSON с ценами misc-позиций) и
    last_price / last_price_order_number не уходят без view_costs."""
    wo = _run_wo(money_shop, "db_find", {"filter": {"wo_number": 7001}}, MECHANIC_PERMS)["docs"][0]
    assert all("misc_charge_description" not in p for labor in wo["labors"] for p in labor["parts"])
    owner_wo = _run_wo(money_shop, "db_find", {"filter": {"wo_number": 7001}}, FULL_PERMS)["docs"][0]
    assert owner_wo["labors"][0]["parts"][0]["misc_charge_description"] == MISC_JSON

    part_args = {"collection": "parts", "filter": {"part_number": "MP-1"}}
    senior = _run_money(money_shop, "db_find", part_args, SENIOR_PERMS)
    doc = senior["docs"][0]
    for key in ("last_price", "last_price_order_number", "average_cost", "selling_price",
                "core_cost", "misc_charges"):
        assert key not in doc, key
    assert doc["description"] == "Air filter" and doc["in_stock"] == 3
    assert _leaked(senior, PART_MONEY) == []
    dumped = _run_money(money_shop, "db_aggregate", {"collection": "parts", "pipeline": [
        {"$match": {"part_number": "MP-1"}}, {"$project": {"m": "$misc_charges.description"}}]}, SENIOR_PERMS)
    assert "cost" in dumped.get("error", ""), dumped
    for flt in ({"last_price": {"$gt": 0}}, {"last_price_order_number": 5551}):
        result = _run_money(money_shop, "db_count", {"collection": "parts", "filter": flt}, SENIOR_PERMS)
        assert "cost" in result.get("error", ""), (flt, result)

    owner = _run_money(money_shop, "db_find", part_args, FULL_PERMS)["docs"][0]
    assert owner["last_price"] == 39.93 and owner["last_price_order_number"] == 5551
    assert owner["average_cost"] == 41.14


def test_cost_barrier_covers_real_money_paths(app):
    """Барьер: явная карта контейнеров для каждой коллекции с costs_perm, пути
    не пересекаются (иначе Mongo отклонит $project), реальные денежные поля
    WO / запчастей (как их пишут сервисы) — в нём."""
    from app.blueprints.parts.routes import PART_COST_FIELDS
    from app.blueprints.work_orders.services.mechanic_view import (
        PART_SEARCH_MONEY_KEYS, WO_LIST_MONEY_KEYS,
    )

    with_costs = {name for name, cfg in data_tools.COLLECTIONS.items() if cfg["costs_perm"]}
    assert with_costs <= set(data_tools._COST_CONTAINERS)
    for name in with_costs:
        paths = list(data_tools._cost_barrier(name))
        assert [p for p in paths for q in paths if p.startswith(q + ".")] == [], name
        # На верхнем уровне не остаётся ни одного денежного имени — на этом
        # держится разрешение «своих» выходных полей модели (total, rate...).
        assert "" in data_tools._COST_CONTAINERS[name], name
        assert data_tools._COST_KEYS <= set(paths), name

    assert "average_cost" in data_tools._cost_barrier("stocktake_items")
    assert "core_cost" in data_tools._cost_barrier("cores")
    assert {"core_cost", "credit_total"} <= set(data_tools._cost_barrier("core_returns"))

    wo = data_tools._cost_barrier("work_orders")
    for path in ("totals", "grand_total", "labor_total", "parts_total", "sales_tax_total",
                 "labors.labor.hourly_rate", "labors.labor.labor_full_total", "labors.labor_full_total",
                 "labors.parts.cost", "labors.parts.price", "labors.parts.core_charge",
                 "labors.parts.misc_charge", "labors.parts.misc_charge_description",
                 "blocks.labor_full_total", "blocks.parts.cost", "blocks.parts.price"):
        assert path in wo, path
    assert "labors.labor.description" not in wo and "labors.parts.qty" not in wo

    parts = data_tools._cost_barrier("parts")
    money_flags = {"has_selling_price", "core_has_charge", "misc_has_charge"}  # флаги, не суммы
    for key in set(PART_COST_FIELDS) | (set(PART_SEARCH_MONEY_KEYS) - money_flags):
        assert key in parts and key in data_tools._COST_KEYS, key
    assert set(WO_LIST_MONEY_KEYS) <= data_tools._COST_KEYS


# ── Склад: инвентаризация и коры без parts.view_costs (red team) ─────


def _role_perms(key: str) -> set:
    from app.constants.permissions import build_default_roles

    return next(set(role["permissions"]) for role in build_default_roles() if role["key"] == key)


PARTS_SIDE_MONEY = [
    pytest.param("stocktake_items", ("average_cost",), id="stocktake_items"),
    pytest.param("cores", ("core_cost",), id="cores"),
    pytest.param("core_returns", ("core_cost", "credit_total"), id="core_returns"),
]


@pytest.mark.parametrize("role", ["senior_mechanic", "viewer"])
@pytest.mark.parametrize("collection,money_keys", PARTS_SIDE_MONEY)
def test_parts_side_money_hidden_without_parts_costs(money_shop, role, collection, money_keys):
    """average_cost строки инвентаризации и core_cost / credit_total коров
    уходили старшему механику и вьюеру обычным db_find (costs_perm None), хотя
    в UI эти колонки — только с parts.view_costs."""
    perms = _role_perms(role)
    assert "parts.view" in perms and "parts.view_costs" not in perms
    args = {"collection": collection, "filter": {"part_number": "MP-1"}}

    found = _run_money(money_shop, "db_find", args, perms)
    assert found["returned"] == 1, found
    doc = found["docs"][0]
    assert not set(money_keys) & set(doc), doc
    assert doc["part_number"] == "MP-1" and doc["description"] == "Air filter"
    assert _leaked(found, STOCK_MONEY) == []
    assert _run_money(money_shop, "db_count", args, perms) == {"count": 1}

    whole = _run_money(money_shop, "db_aggregate", {"collection": collection, "pipeline": [
        {"$match": {"part_number": "MP-1"}},
        {"$group": {"_id": "$part_number", "total": {"$sum": 1}, "rows": {"$push": "$$ROOT"}}}]}, perms)
    assert "error" not in whole, whole
    assert whole["docs"][0]["total"] == 1 and whole["docs"][0]["rows"][0]["part_number"] == "MP-1"
    assert _leaked(whole, STOCK_MONEY) == []

    for key in money_keys:
        for tool, extra in (
            ("db_count", {"filter": {key: {"$gt": 0}}}),
            ("db_find", {"sort": {key: -1}}),
            ("db_find", {"projection": {key: 1}}),
            ("db_aggregate", {"pipeline": [{"$group": {"_id": None, "s": {"$sum": f"${key}"}}}]}),
        ):
            result = _run_money(money_shop, tool, {"collection": collection, **extra}, perms)
            assert "cost" in result.get("error", ""), (tool, extra, result)

    owner = _run_money(money_shop, "db_find", args, FULL_PERMS)
    assert set(money_keys) <= set(owner["docs"][0])
    assert _leaked(owner, STOCK_MONEY)


@pytest.mark.parametrize("collection,money_keys", PARTS_SIDE_MONEY)
def test_parts_side_barrier_alone_hides_money(money_shop, monkeypatch, collection, money_keys):
    """Без проверки имён и чистки результата деньги строк инвентаризации и
    коров всё равно вырезаны барьером — и как поле, и как фильтр."""
    _disable_cost_name_guard_and_strip(monkeypatch)
    perms = _role_perms("senior_mechanic")
    for tool, args in (
        ("db_find", {"filter": {"part_number": "MP-1"}}),
        ("db_aggregate", {"pipeline": [{"$replaceRoot": {"newRoot": "$$ROOT"}}]}),
        ("db_aggregate", {"pipeline": [{"$project": {"kv": {"$objectToArray": "$$ROOT"}}}]}),
    ):
        mech = _run_money(money_shop, tool, {"collection": collection, **args}, perms)
        assert mech["returned"] == 1 and _leaked(mech, STOCK_MONEY) == [], (tool, args, mech)
        owner = _run_money(money_shop, tool, {"collection": collection, **args}, FULL_PERMS)
        assert _leaked(owner, STOCK_MONEY), (tool, args)
    for key in money_keys:
        flt = {"collection": collection, "filter": {key: {"$gt": 0}}}
        assert _run_money(money_shop, "db_count", flt, perms) == {"count": 0}, key
        assert _run_money(money_shop, "db_count", flt, FULL_PERMS) == {"count": 1}, key


# Суммы, которые и в UI видит всякий, кто может открыть эти данные (паритет —
# продуктовое решение, не утечка): Parts → Stocktakes показывает Shortage /
# Overage без parts.view_costs; заказы вендорам и их оплаты (/parts/api/
# orders/<id>, /payments) отдают цены, коры, оплачено и остаток с одним
# parts_orders.view. Любое другое денежное поле из db_schema.md в коллекции,
# которую читают без *.view_costs, — утечка (как average_cost / core_cost
# до фикса): закрыть costs_perm или осознанно добавить сюда.
UI_PARITY_MONEY = {
    "stocktakes": {"totals", "shortage_value", "overage_value"},
    "parts_orders": {"price", "core_charge", "non_inventory_amounts", "amount", "paid_amount",
                     "remaining_balance", "credit_total"},
    "parts_order_payments": {"amount"},
}


def test_registry_gates_every_documented_money_field():
    """Сверка реестра со схемой, которую видит модель: каждая коллекция
    описана, а денежные поля коллекций без costs_perm (и не закрытых
    *.view_costs целиком) — только из UI_PARITY_MONEY."""
    from pathlib import Path

    schema = (Path(data_tools.__file__).parent / "db_schema.md").read_text(encoding="utf-8")
    sections = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^### (\w+)\s*\n(.*?)(?=^### |\Z)", schema, re.M | re.S)
    }
    cost_perms = {cfg["costs_perm"] for cfg in data_tools.COLLECTIONS.values() if cfg["costs_perm"]}
    visible = {
        name for name, cfg in data_tools.COLLECTIONS.items()
        if not cfg["costs_perm"] and cfg["perm"] not in cost_perms
    }
    assert set(UI_PARITY_MONEY) <= visible

    leaks = {}
    for name in data_tools.COLLECTIONS:
        assert name in sections, f"collection {name} is not described in db_schema.md"
        if name not in visible:
            continue
        documented = set(re.findall(r"[A-Za-z_]\w*", " ".join(re.findall(r"`([^`]*)`", sections[name]))))
        extra = (documented & data_tools._COST_KEYS) - UI_PARITY_MONEY.get(name, set())
        if extra:
            leaks[name] = sorted(extra)
    assert leaks == {}


# ── Имена выходных полей модели: «total», «rate», $$ROOT (red team) ───
# Ложные отказы: механик называл счётчик «total» или код ставки «rate», брал
# документ целиком через $$ROOT — денег в этих запросах нет.

MONEY_FREE_PIPELINES = [
    pytest.param([{"$group": {"_id": "$status", "total": {"$sum": 1}}}], "total", id="group-total"),
    pytest.param([{"$match": {"status": "open"}}, {"$count": "total"}], "total", id="count-total"),
    pytest.param([{"$project": {"wo_number": 1, "rate": "$labors.labor.rate_code"}}], "rate",
                 id="project-rate-code"),
    pytest.param([{"$bucket": {"groupBy": "$wo_number", "boundaries": [0, 1000, 100000],
                               "output": {"total": {"$sum": 1}}}}], "total", id="bucket-output-total"),
    # дальше модель читает своё поле: фильтр, сортировка, проекция, $$ROOT.total
    pytest.param([{"$group": {"_id": "$status", "total": {"$sum": 1}}},
                  {"$match": {"total": {"$gte": 1}, "$or": [{"total": 1}, {"_id": "none"}]}},
                  {"$sort": {"total": -1, "_id": 1}},
                  {"$project": {"_id": 0, "status": "$_id", "total": 1, "same": "$$ROOT.total"}}],
                 "same", id="match-sort-project-own-total"),
    pytest.param([{"$sortByCount": "$status"}, {"$addFields": {"price": "$count"}}, {"$sort": {"price": -1}}],
                 "price", id="addfields-own-price"),
    pytest.param([{"$unwind": {"path": "$labors", "includeArrayIndex": "rate"}},
                  {"$project": {"_id": 0, "rate": 1, "job": "$labors.labor.description"}}], "rate",
                 id="unwind-index-rate"),
    pytest.param([{"$match": {"wo_number": 7001}}, {"$replaceWith": {"total": {"$size": "$labors"}}}],
                 "total", id="replacewith-literal-total"),
]


@pytest.mark.parametrize("pipeline,field", MONEY_FREE_PIPELINES)
def test_mechanic_money_like_output_names_work(money_shop, pipeline, field):
    """Своё поле модели с денежным именем — не деньги: запрос проходит,
    поле не вычищается, ответ механику тот же, что владельцу."""
    def as_set(result):  # порядок групп / равных при сортировке не задан
        return sorted(json.dumps(doc, sort_keys=True) for doc in result["docs"])

    def paid_as_completed(node):  # единственное намеренное отличие ответа механику
        if isinstance(node, dict):
            return {k: paid_as_completed(v) for k, v in node.items()}
        if isinstance(node, list):
            return [paid_as_completed(v) for v in node]
        return "completed" if node == "paid" else node

    mech = _run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, MECHANIC_PERMS)
    assert "error" not in mech, mech
    assert any(field in doc for doc in mech["docs"]), mech
    owner = paid_as_completed(_run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, FULL_PERMS))
    assert mech["returned"] == owner["returned"] and as_set(mech) == as_set(owner), (mech, owner)


def test_mechanic_money_like_output_names_values(money_shop):
    def run(pipeline):
        return _run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, MECHANIC_PERMS)["docs"]

    assert run([{"$match": {"status": "open"}}, {"$count": "total"}]) == [{"total": 1}]
    assert run([{"$match": {"wo_number": 7001}},
                {"$project": {"_id": 0, "wo_number": 1, "rate": "$labors.labor.rate_code"}}]) == [
        {"wo_number": 7001, "rate": ["standard", "standard"]}]
    assert run([{"$bucket": {"groupBy": "$wo_number", "boundaries": [0, 1000, 100000],
                             "output": {"total": {"$sum": 1}}}}]) == [{"_id": 1000, "total": 2}]
    assert run([{"$unwind": {"path": "$labors", "includeArrayIndex": "rate"}},
                {"$project": {"_id": 0, "rate": 1, "job": "$labors.labor.description"}}]) == [
        {"rate": 0, "job": "front brakes"}, {"rate": 1, "job": "oil change"}]
    assert sorted(doc["total"] for doc in run([{"$group": {"_id": "$status", "total": {"$sum": 1}}}])) == [1, 1]


def test_mechanic_whole_document_via_root(money_shop):
    """«Последний WO по юниту» и «WO по статусам» через $$ROOT: документ
    целиком, но после барьера — без денег."""
    for pipeline in (  # запросы ровно в том виде, в каком их отклоняли
        [{"$sort": {"created_at": -1}}, {"$group": {"_id": "$unit_id", "latest": {"$first": "$$ROOT"}}}],
        [{"$group": {"_id": "$status", "wos": {"$push": "$$ROOT"}}}],
    ):
        mech = _run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, MECHANIC_PERMS)
        assert "error" not in mech and mech["returned"] >= 1, (pipeline, mech)
        assert _leaked(mech) == [], (pipeline, _leaked(mech))

    latest = _run_wo(money_shop, "db_aggregate", {"pipeline": [
        {"$sort": {"created_at": -1, "wo_number": -1}},
        {"$group": {"_id": "$unit_id", "latest": {"$first": "$$ROOT"}}}]}, MECHANIC_PERMS)
    assert "error" not in latest, latest
    assert [doc["latest"]["wo_number"] for doc in latest["docs"]] == [7002]
    assert latest["docs"][0]["latest"]["blocks"][0]["labor_description"] == "legacy job"
    assert _leaked(latest) == []

    pipeline = [{"$group": {"_id": "$status", "wos": {"$push": "$$ROOT"}}}, {"$sort": {"_id": 1}}]
    grouped = _run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, MECHANIC_PERMS)
    assert "error" not in grouped, grouped
    assert sorted(wo["wo_number"] for doc in grouped["docs"] for wo in doc["wos"]) == [7001, 7002]
    modern = next(wo for doc in grouped["docs"] for wo in doc["wos"] if wo["wo_number"] == 7001)
    assert modern["labors"][0]["labor"]["description"] == "front brakes"
    assert "hourly_rate" not in modern["labors"][0]["labor"] and "totals" not in modern
    assert _leaked(grouped) == []
    # владельцу тот же запрос отдаёт деньги — проверка не пустая
    assert _leaked(_run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, FULL_PERMS))


# Свои имена модели не открывают деньги: денежное поле документа читается
# только по имени (отказ), компоненты после своего имени проверяются всегда,
# $replaceRoot на поддокумент сбрасывает свои имена, CURRENT не переназначить.
OWN_NAME_PROBES = [
    pytest.param([{"$addFields": {"total": "$totals"}}], id="define-from-totals"),
    pytest.param([{"$group": {"_id": None, "total": {"$sum": "$grand_total"}}}], id="sum-legacy-grand-total"),
    pytest.param([{"$sort": {"grand_total": -1}}], id="sort-by-document-money"),
    pytest.param([{"$project": {"total": 1}}], id="include-document-total"),
    pytest.param([{"$addFields": {"total": {"$arrayElemAt": ["$labors", 0]}}},
                  {"$project": {"r": "$total.labor.hourly_rate"}}], id="own-name-then-nested-money"),
    pytest.param([{"$group": {"_id": None, "n": {"$sum": 1}}}, {"$project": {"r": "$$ROOT.grand_total"}}],
                 id="root-var-document-money"),
    pytest.param([{"$addFields": {"price": 1}}, {"$unwind": "$labors"}, {"$unwind": "$labors.parts"},
                  {"$replaceRoot": {"newRoot": "$labors.parts"}}, {"$project": {"price": 1}}],
                 id="replaceroot-resets-own-names"),
    pytest.param([{"$addFields": {"price": 1}}, {"$project": {"x": {"$let": {
        "vars": {"CURRENT": {"$arrayElemAt": [{"$arrayElemAt": ["$labors.parts", 0]}, 0]}},
        "in": "$price"}}}}], id="let-rebinds-current"),
    pytest.param([{"$addFields": {"price": 1}}, {"$project": {"x": {"$map": {
        "input": {"$arrayElemAt": ["$labors.parts", 0]}, "as": "CURRENT", "in": "$price"}}}}],
        id="map-as-current"),
    pytest.param([{"$group": {"_id": None, "wos": {"$push": {"wo": "$wo_number", "total": "$wo_number"}}}}],
                 id="nested-output-money-name"),
]


@pytest.mark.parametrize("pipeline", OWN_NAME_PROBES)
def test_own_output_names_do_not_open_money(money_shop, pipeline):
    mech = _run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, MECHANIC_PERMS)
    assert "cost" in mech.get("error", ""), mech
    assert _leaked(mech) == []
    owner = _run_wo(money_shop, "db_aggregate", {"pipeline": pipeline}, FULL_PERMS)
    assert "error" not in owner, owner  # валидный для Mongo запрос, отказ — именно guard


def test_unknown_money_location_still_guarded(money_shop):
    """Барьер знает, где лежат деньги; денежное имя в НЕизвестном месте
    закрывают проверка имён и чистка результата — в т.ч. под своими именами
    модели, после $replaceRoot и при переназначенном CURRENT."""
    shop_db = money_shop["shop_db"]
    shop_id = ObjectId()
    shop_db.work_orders.insert_one({
        "_id": ObjectId(), "shop_id": shop_id, "wo_number": 7101, "status": "open", "is_active": True,
        "extra": {"price": 66.61, "note": "x"},
        "labors": [{"labor": {"description": "d", "warranty": {"price": 66.62}}}],
    })
    hidden = ("66.61", "66.62")

    def run(pipeline, perms):
        with money_shop["app"].app_context():
            return data_tools.run_tool(
                "db_aggregate", {"collection": "work_orders", "pipeline": pipeline},
                shop_db=shop_db, shop_id=shop_id, permissions=perms,
            )

    try:
        for pipeline in (
            [{"$group": {"_id": "$status", "total": {"$sum": 1}, "wos": {"$push": "$$ROOT"}}}],
            [{"$project": {"price": "$extra", "total": "$labors.labor.warranty"}}],
            [{"$addFields": {"price": 1}}, {"$replaceRoot": {"newRoot": "$extra"}}],
            [{"$replaceWith": {"price": "$extra"}}],
            [{"$unwind": "$labors"}, {"$replaceRoot": {"newRoot": "$labors.labor.warranty"}}],
            [{"$addFields": {"price": "$$ROOT"}}, {"$replaceWith": {"$mergeObjects": ["$price.extra", {}]}}],
            [{"$sortByCount": "$extra"}],
            [{"$bucketAuto": {"groupBy": "$extra", "buckets": 1}}],
        ):
            mech = run(pipeline, MECHANIC_PERMS)
            assert "error" not in mech and mech["returned"] == 1, (pipeline, mech)
            assert _leaked(mech, hidden) == [], (pipeline, mech)
            assert _leaked(run(pipeline, FULL_PERMS), hidden), pipeline

        for pipeline in (
            [{"$project": {"total": "$extra.price"}}],
            [{"$addFields": {"total": "$extra"}}, {"$project": {"x": "$total.price"}}],
            [{"$replaceRoot": {"newRoot": "$extra"}}, {"$project": {"price": 1}}],
            [{"$addFields": {"price": "$$ROOT"}}, {"$project": {"x": "$price.extra.price"}}],
            [{"$match": {"$expr": {"$eq": ["$extra", {"price": 66.61, "note": "x"}]}}}],
            [{"$addFields": {"price": 1}}, {"$project": {"x": {"$let": {
                "vars": {"CURRENT": "$extra"}, "in": "$price"}}}}],
            [{"$addFields": {"price": 1}}, {"$project": {"x": {"$map": {
                "input": ["$extra"], "as": "CURRENT", "in": "$price"}}}}],
        ):
            mech = run(pipeline, MECHANIC_PERMS)
            assert "cost" in mech.get("error", ""), (pipeline, mech)
            assert _leaked(run(pipeline, FULL_PERMS), hidden), pipeline  # обход реальный
    finally:
        shop_db.work_orders.delete_many({"shop_id": shop_id})


# ── Опасные операторы ────────────────────────────────────────────────


@pytest.mark.parametrize("bad_filter", [
    {"$where": "sleep(1000)"},
    {"status": {"$in": ["open"]}, "$where": "1"},
    {"$expr": {"$function": {"body": "x", "args": [], "lang": "js"}}},
])
def test_forbidden_filter_operators(shop_data, bad_filter):
    result = _run(shop_data, "db_find", {"collection": "work_orders", "filter": bad_filter})
    assert "not allowed" in result["error"]


@pytest.mark.parametrize("bad_stage", [
    {"$out": "pwned"},
    {"$merge": {"into": "pwned"}},
    {"$lookup": {"from": "customers", "as": "c", "localField": "x", "foreignField": "y"}},
])
def test_forbidden_pipeline_stages(shop_data, bad_stage):
    result = _run(shop_data, "db_aggregate", {
        "collection": "work_orders", "pipeline": [bad_stage],
    })
    assert "not allowed" in result["error"]


def test_aggregate_limit_clamped_and_appended(shop_data):
    result = _run(shop_data, "db_aggregate", {
        "collection": "work_orders",
        "pipeline": [{"$match": {}}, {"$limit": 100_000}],
    })
    assert "error" not in result
    assert result["returned"] <= data_tools.MAX_AGG_DOCS


def test_search_terms_always_stripped(shop_data):
    shop_data["shop_db"].customers.update_one(
        {"_id": shop_data["customer_id"]}, {"$set": {"search_terms": ["acme"]}}
    )
    result = _run(shop_data, "db_find", {"collection": "customers"})
    assert all("search_terms" not in d for d in result["docs"])


# ── Tool-use цикл чата (фейковый OpenAI) ─────────────────────────────


def _event(text=None, tool_calls=None, finish=None, usage=None):
    delta = SimpleNamespace(content=text, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice] if (text or tool_calls or finish) else [],
                           usage=usage)


def _tool_call(index, call_id, name, arguments):
    return SimpleNamespace(
        index=index, id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _FakeCompletions:
    def __init__(self, outer):
        self._outer = outer

    def create(self, **kwargs):
        self._outer.requests.append(kwargs)
        return iter(self._outer.batches.pop(0))


class _FakeClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.requests = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))


def test_chat_tool_loop_end_to_end(client, seed, shop_data, monkeypatch):
    """Модель просит db_count, получает результат и отвечает текстом."""
    import app.blueprints.assistant.services.chat as chat_service

    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    fake = _FakeClient([
        # раунд 1: модель зовёт инструмент (аргументы стримятся кусками)
        [
            _event(tool_calls=[_tool_call(0, "call_1", "db_count",
                                          '{"collection": "work_or')]),
            _event(tool_calls=[_tool_call(0, "", "",
                                          'ders", "filter": {"status": "open"}}')]),
            _event(finish="tool_calls"),
            _event(usage=usage),
        ],
        # раунд 2: финальный текст
        [
            _event(text="You have "),
            _event(text="2 open work orders.", finish="stop"),
            _event(usage=usage),
        ],
    ])

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(chat_service, "_make_client", lambda api_key: fake)

    login(client)
    token = get_csrf_token(client)
    resp = client.post(
        "/assistant/api/chat",
        data=json.dumps({"messages": [{"role": "user", "content": "how many open WOs?"}]}),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "You have 2 open work orders."

    # первый запрос ушёл с инструментами и схемой в промпте
    first = fake.requests[0]
    assert any(t["function"]["name"] == "db_count" for t in first["tools"])
    assert "db_find" in first["messages"][0]["content"]

    # во втором запросе модель получила результат инструмента
    second = fake.requests[1]
    tool_msgs = [m for m in second["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0]["content"]) == {"count": 2}

    # лог зафиксировал tool call и суммарные токены обоих раундов
    from app.extensions import get_master_db
    with client.application.app_context():
        log = get_master_db().assistant_logs.find_one(
            {"question": "how many open WOs?"}, sort=[("created_at", -1)]
        )
    assert log["tool_calls"][0]["tool"] == "db_count"
    assert log["tool_calls"][0]["collection"] == "work_orders"
    assert log["tokens_in"] == 200 and log["tokens_out"] == 40


def test_chat_tools_disabled_by_env(client, seed, monkeypatch):
    """ASSISTANT_DATA_TOOLS=0 — чат работает по-старому, без tools."""
    import app.blueprints.assistant.services.chat as chat_service

    fake = _FakeClient([
        [_event(text="Just docs answer.", finish="stop")],
    ])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ASSISTANT_DATA_TOOLS", "0")
    monkeypatch.setattr(chat_service, "_make_client", lambda api_key: fake)

    login(client)
    token = get_csrf_token(client)
    resp = client.post(
        "/assistant/api/chat",
        data=json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "Just docs answer."
    assert "tools" not in fake.requests[0]
