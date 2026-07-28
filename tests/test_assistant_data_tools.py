"""Guard-слой read-only доступа AI-помощника к базе шопа + tool-use цикл чата.

Проверяем ровно то, чему нельзя доверять модель: изоляцию по shop_id,
permissions по коллекциям, вычистку денежных полей (механик-режим),
запрет опасных операторов и клампы лимитов. Плюс сквозной тест цикла
«модель → инструмент → модель» с фейковым OpenAI-клиентом.
"""
import json
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
