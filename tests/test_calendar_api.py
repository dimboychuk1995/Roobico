"""
API календаря записей (appointments): CRUD и форма ответа, валидация,
бригада/пресеты/статус и legacy-документы, лейблы из базы (переименованный
клиент), фильтры диапазона и механика, изоляция тенантов, права, настройки
(статусы + рабочие часы и алиас /statuses), снимок WO, привязка к WO при
создании и живые данные WO, механик-режим без paid, правила привязанной
записи (клиент/юнит — 400, отвязка).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import template_rendered
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, SHOP_B_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "cal-api-mech@test.local"
MECHANIC2_EMAIL = "cal-api-mech-2@test.local"
OWNER_B_EMAIL = "cal-api-owner-b@test.local"
PASSWORD = "password123"
CUSTOMER_NAME = "API Fleet LLC"
OTHER_CUSTOMER_NAME = "API Second LLC"
UNIT_LABEL = "API-1 2022 Freightliner Cascadia"
TENANT_B_DB = "roobico_test_tenant_b"
# Номера WO модуля — 96001…96099 (94xxx — test_calendar_wo_sync, 95001 —
# test_permissions_enforcement, 97xxx/99xxx — другие модули).
WO_NUMBER_RANGE = {"$gte": 96000, "$lt": 96100}
# Недели модуля — январь 2031 и дальше: далеко от «текущих» недель других
# модулей, у каждого теста своя неделя, чтобы выборки диапазона не пересекались.
BASE_MONDAY = date(2031, 1, 6)
SHAPE = {
    "id", "kind", "is_virtual", "editable", "title", "start_time", "end_time", "status",
    "customer_id", "customer_label", "unit_id", "unit_label",
    "mechanics", "mechanic_ids", "mechanic_id", "mechanic_name",
    "presets", "note", "work_order_id", "wo_number", "wo_state", "labor_summary",
}
_EVENT_IDS: list[ObjectId] = []


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _user(tenant, shop, email, first, last, role):
    return {
        "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
        "first_name": first, "last_name": last, "is_active": True,
        "tenant_id": tenant["_id"], "shop_ids": [str(shop["_id"])], "role": role, "created_at": _now(),
    }


@pytest.fixture(scope="module")
def api_seed(app, seed, mongo):
    """Два механика, клиент с юнитом, второй клиент со своим юнитом, пресет в
    магазине A; владелец тенанта B (роль owner в базе тенанта B) — для изоляции."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)
    mongo[TENANT_B_DB].roles.update_one(
        {"key": "owner"}, {"$set": {"key": "owner", "name": "Owner", "permissions": []}}, upsert=True
    )

    mechanic = _user(seed["tenant_a"], shop_a, MECHANIC_EMAIL, "Api", "Mechanic", "mechanic")
    second = _user(seed["tenant_a"], shop_a, MECHANIC2_EMAIL, "Second", "Spanner", "mechanic")
    owner_b = _user(seed["tenant_b"], seed["shop_b"], OWNER_B_EMAIL, "Owner", "Bee", "owner")
    master.users.insert_many([mechanic, second, owner_b])

    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": CUSTOMER_NAME, "taxable": False, "is_active": True, "created_at": now,
    }
    other = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": OTHER_CUSTOMER_NAME, "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_many([customer, other])
    unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
        "unit_number": "API-1", "year": 2022, "make": "Freightliner", "model": "Cascadia",
        "vin": "APIVIN00000000001", "mileage": 10000, "is_active": True, "created_at": now,
    }
    other_unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": other["_id"],
        "unit_number": "API-2", "year": 2019, "make": "Kenworth", "model": "T680",
        "vin": "APIVIN00000000002", "mileage": 20000, "is_active": True, "created_at": now,
    }
    shop_db.units.insert_many([unit, other_unit])
    preset = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "name": "Oil change", "description": "Engine oil and filter",
        "labor_hours": 1.0, "labor_rate_code": "standard", "allow_discount": False, "parts": [],
        "is_active": True, "created_at": now, "updated_at": now,
    }
    shop_db.wo_presets.insert_one(preset)

    yield {
        "mechanic": mechanic, "second": second, "owner_b": owner_b,
        "customer": customer, "other": other, "unit": unit, "other_unit": other_unit,
        "preset": preset, "shop": shop_a, "tenant": seed["tenant_a"], "owner": seed["owner"],
    }

    customer_ids = [customer["_id"], other["_id"]]
    wo_ids = [w["_id"] for w in shop_db.work_orders.find({"wo_number": WO_NUMBER_RANGE}, {"_id": 1})]
    shop_db.calendar_events.delete_many({"$or": [
        {"_id": {"$in": list(_EVENT_IDS)}}, {"customer_id": {"$in": customer_ids}}, {"work_order_id": {"$in": wo_ids}},
    ]})
    mongo[SHOP_B_DB].calendar_events.delete_many({"_id": {"$in": list(_EVENT_IDS)}})
    shop_db.work_orders.delete_many({"_id": {"$in": wo_ids}})
    shop_db.units.delete_many({"_id": {"$in": [unit["_id"], other_unit["_id"]]}})
    shop_db.customers.delete_many({"_id": {"$in": customer_ids}})
    shop_db.wo_presets.delete_one({"_id": preset["_id"]})
    shop_db.calendar_settings.delete_many({"key": {"$in": ["statuses", "hours"]}})
    master.users.delete_many({"_id": {"$in": [mechanic["_id"], second["_id"], owner_b["_id"]]}})
    mongo[TENANT_B_DB].roles.delete_one({"key": "owner"})


# ── helpers ──────────────────────────────────────────────────

def _json(client, method, url, payload=None):
    token = get_csrf_token(client)
    return getattr(client, method)(url, json=payload if payload is not None else {}, headers={"X-CSRFToken": token})


def _login_owner(client):
    assert login(client).status_code == 302


def _login_mechanic(client):
    assert login(client, email=MECHANIC_EMAIL, password=PASSWORD).status_code == 302


def _login_owner_b(client):
    assert login(client, email=OWNER_B_EMAIL, password=PASSWORD).status_code == 302


def _day(week: int, weekday: int = 2) -> str:
    """Дата (YYYY-MM-DD) дня недели (0=Пн) недели модуля № week."""
    return (BASE_MONDAY + timedelta(weeks=week, days=weekday)).isoformat()


def _range(day: str) -> tuple[str, str]:
    d = date.fromisoformat(day)
    monday = d - timedelta(days=d.weekday())
    return f"{monday.isoformat()}T00:00:00", f"{(monday + timedelta(days=7)).isoformat()}T00:00:00"


def _times(day: str, hour: int, minutes: int = 60) -> dict:
    start = datetime.fromisoformat(f"{day}T{hour:02d}:00:00")
    return {"start_time": start.isoformat(), "end_time": (start + timedelta(minutes=minutes)).isoformat()}


def _create(client, payload: dict) -> dict:
    resp = _json(client, "post", "/calendar/api/events", payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()
    _EVENT_IDS.append(ObjectId(created["id"]))
    return created


def _put(client, event_id, payload: dict):
    return _json(client, "put", f"/calendar/api/events/{event_id}", payload)


def _events(client, day: str, extra: str = "") -> list[dict]:
    start, end = _range(day)
    resp = client.get(f"/calendar/api/events?start={start}&end={end}{extra}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _page_context(app, client, path="/calendar") -> dict:
    """Контекст render_template страницы — флаги для фронта, не зависящие от разметки."""
    recorded = []

    def _record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(_record, app)
    try:
        resp = client.get(path)
    finally:
        template_rendered.disconnect(_record, app)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return next(ctx for name, ctx in recorded if name == "public/calendar.html")


def _labor(number, suffix="", description="Replace brakes", hours="2", assigned=None):
    return {
        "labor_id": f"L{number}{suffix}",
        "labor": {"description": description, "hours": hours, "rate_code": "standard",
                  "assigned_mechanics": assigned or []},
        "parts": [],
    }


def _assigned(user, percent=100.0):
    return {"user_id": user["_id"], "name": f"{user['first_name']} {user['last_name']}", "role": "mechanic", "percent": percent}


def _insert_wo(shop_db, api_seed, number, status="open", labors=None, **extra):
    now = _now()
    doc = {
        "_id": ObjectId(), "shop_id": api_seed["shop"]["_id"], "wo_number": number,
        "customer_id": api_seed["customer"]["_id"], "unit_id": api_seed["unit"]["_id"],
        "status": status, "is_active": True,
        "labors": labors if labors is not None else [_labor(number, assigned=[_assigned(api_seed["mechanic"])])],
        "totals": {"grand_total": 300.0, "labors": []},
        "work_order_date": now, "created_at": now, "updated_at": now,
    }
    doc.update(extra)
    shop_db.work_orders.insert_one(doc)
    return doc


# ── страница и справочники ───────────────────────────────────

def test_page_context_flags_and_lookups(app, client, api_seed):
    _login_owner(client)
    ctx = _page_context(app, client)
    for key in ("calendar_can_create", "calendar_can_edit", "calendar_can_delete",
                "calendar_can_manage_settings", "calendar_can_create_wo"):
        assert ctx[key] is True, key
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ctx["calendar_today"])

    customers = client.get("/calendar/api/customers").get_json()
    labels = [c["label"] for c in customers]
    assert labels == sorted(labels, key=str.lower)
    assert next(c for c in customers if c["id"] == str(api_seed["customer"]["_id"]))["label"] == CUSTOMER_NAME

    units = client.get(f"/calendar/api/units/{api_seed['customer']['_id']}").get_json()
    assert units == [{"id": str(api_seed["unit"]["_id"]), "label": UNIT_LABEL, "vin": "APIVIN00000000001"}]
    assert client.get("/calendar/api/units/not-an-id").get_json() == []
    assert client.get(f"/calendar/api/units/{ObjectId()}").get_json() == []

    mechanics = {m["id"]: m for m in client.get("/calendar/api/mechanics").get_json()}
    m1, m2 = str(api_seed["mechanic"]["_id"]), str(api_seed["second"]["_id"])
    assert mechanics[m1] == {"id": m1, "name": "Api Mechanic", "role": "mechanic"}
    assert mechanics[m2]["name"] == "Second Spanner"
    assert str(api_seed["owner"]["_id"]) not in mechanics  # владелец — не механик

    presets = client.get("/calendar/api/presets").get_json()
    assert {"id": str(api_seed["preset"]["_id"]), "name": "Oil change"} in presets
    names = [p["name"] for p in presets]
    assert names == sorted(names, key=str.lower)

    statuses = client.get("/calendar/api/statuses").get_json()
    assert [s["key"] for s in statuses] == ["scheduled", "confirmed", "in_progress", "completed", "cancelled"]
    assert statuses[0] == {"key": "scheduled", "label": "Scheduled", "color": "#1a73e8"}
    assert [s["key"] for s in client.get("/calendar/api/wo_states").get_json()] == [
        "open", "in_progress", "mechanic_done", "completed", "paid",
    ]
    assert client.get("/calendar/api/settings").get_json() == {
        "statuses": statuses, "start_hour": 6, "end_hour": 20, "slot_minutes": 15,
    }


# ── CRUD и форма ответа ──────────────────────────────────────

def test_crud_round_trip_and_shape(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(0)
    m1 = str(api_seed["mechanic"]["_id"])
    created = _create(client, {
        **_times(day, 7, 90), "title": "  555-0100 brakes + oil  ", "status": "confirmed",
        "customer_id": str(api_seed["customer"]["_id"]), "customer_label": "WRONG",
        "unit_id": str(api_seed["unit"]["_id"]), "unit_label": "WRONG",
        "mechanics": [{"id": m1, "name": "Wrong Name"}],
        "presets": [{"id": str(api_seed["preset"]["_id"]), "name": "Oil change"}],
    })
    assert set(created) == SHAPE
    assert created["kind"] == "appointment" and created["is_virtual"] is False and created["editable"] is True
    assert created["title"] == "555-0100 brakes + oil" and created["note"] == "555-0100 brakes + oil"
    assert created["start_time"] == f"{day}T07:00:00" and created["end_time"] == f"{day}T08:30:00"
    assert created["status"] == "confirmed"
    assert created["customer_id"] == str(api_seed["customer"]["_id"]) and created["customer_label"] == CUSTOMER_NAME
    assert created["unit_id"] == str(api_seed["unit"]["_id"]) and created["unit_label"] == UNIT_LABEL
    assert created["mechanics"] == [{"id": m1, "name": "Api Mechanic"}]
    assert created["mechanic_ids"] == [m1] and created["mechanic_id"] == m1 and created["mechanic_name"] == "Api Mechanic"
    assert created["presets"] == [{"id": str(api_seed["preset"]["_id"]), "name": "Oil change"}]
    assert created["work_order_id"] == "" and created["wo_number"] is None
    assert created["wo_state"] == "" and created["labor_summary"] == []

    doc = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert doc["shop_id"] == api_seed["shop"]["_id"] and doc["tenant_id"] == api_seed["tenant"]["_id"]
    assert doc["created_by"] == api_seed["owner"]["_id"] == doc["updated_by"]
    assert doc["start_time"] == datetime(2031, 1, 8, 7, 0) and doc["start_time"].tzinfo is None
    assert doc["title"] == "555-0100 brakes + oil" and doc["customer_label"] == CUSTOMER_NAME
    assert doc["mechanic_id"] == ObjectId(m1) and doc["mechanic_ids"] == [ObjectId(m1)]
    assert doc["mechanics"] == [{"id": ObjectId(m1), "name": "Api Mechanic"}] and doc["mechanic_name"] == "Api Mechanic"
    assert doc["work_order_id"] is None and doc["wo_number"] is None

    rows = _events(client, day, "&include_work_orders=1")
    assert isinstance(rows, list)
    assert next(r for r in rows if r["id"] == created["id"]) == created

    # Частичный PUT: меняются только присланные ключи.
    resp = _put(client, created["id"], {
        "title": "Renamed", "status": "in_progress", "start_time": f"{day}T08:00:00", "end_time": f"{day}T10:15:00",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body) == SHAPE
    assert body["title"] == "Renamed" and body["status"] == "in_progress"
    assert body["start_time"] == f"{day}T08:00:00" and body["end_time"] == f"{day}T10:15:00"
    assert body["customer_id"] == created["customer_id"] and body["unit_id"] == created["unit_id"]
    assert body["mechanic_ids"] == [m1] and body["presets"] == created["presets"]

    resp = _put(client, created["id"], {"mechanic_ids": [], "presets": []})
    assert resp.status_code == 200
    assert resp.get_json()["mechanics"] == [] and resp.get_json()["mechanic_id"] == "" and resp.get_json()["presets"] == []
    after = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert after["mechanic_id"] is None and after["mechanic_name"] == "" and after["mechanic_ids"] == []
    assert after["updated_at"] > doc["updated_at"] and after["title"] == "Renamed"

    token = get_csrf_token(client)
    resp = client.delete(f"/calendar/api/events/{created['id']}", headers={"X-CSRFToken": token})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])}) is None
    # Повторное удаление и кривой id — тоже ok; правка удалённой — 404.
    assert client.delete(f"/calendar/api/events/{created['id']}", headers={"X-CSRFToken": token}).get_json() == {"ok": True}
    assert client.delete("/calendar/api/events/not-an-id", headers={"X-CSRFToken": token}).get_json() == {"ok": True}
    resp = _put(client, created["id"], {"title": "x"})
    assert resp.status_code == 404 and resp.get_json() == {"error": "Not found"}
    assert created["id"] not in {r["id"] for r in _events(client, day)}


def test_validation_errors(client, api_seed):
    _login_owner(client)
    day, next_day = _day(1), _day(1, weekday=3)
    cases = [
        ({"start_time": f"{day}T10:00:00"}, "Start and end time are required"),
        ({"start_time": "", "end_time": f"{day}T10:00:00"}, "Start and end time are required"),
        ({"start_time": "nope", "end_time": f"{day}T10:00:00"}, "Invalid date format"),
        ({"start_time": f"{day}T10:00:00", "end_time": "2031-13-45T10:00:00"}, "Invalid date format"),
        ({"start_time": f"{day}T10:00:00", "end_time": f"{day}T10:00:00"}, "End time must be after start time"),
        ({"start_time": f"{day}T10:00:00", "end_time": f"{day}T09:00:00"}, "End time must be after start time"),
        ({"start_time": f"{day}T22:00:00", "end_time": f"{next_day}T01:00:00"}, "An appointment must start and end on the same day"),
    ]
    for payload, message in cases:
        resp = _json(client, "post", "/calendar/api/events", payload)
        assert resp.status_code == 400, (payload, resp.get_data(as_text=True))
        assert resp.get_json() == {"error": message}, payload

    # Полночь следующего дня — «до конца дня», можно.
    created = _create(client, {"start_time": f"{day}T22:00:00", "end_time": f"{next_day}T00:00:00", "title": "Night"})
    assert created["end_time"] == f"{next_day}T00:00:00"

    # PUT проверяет результирующий интервал.
    resp = _put(client, created["id"], {"end_time": f"{day}T21:00:00"})
    assert resp.status_code == 400 and resp.get_json()["error"] == "End time must be after start time"
    resp = _put(client, created["id"], {"end_time": f"{next_day}T00:15:00"})
    assert resp.status_code == 400 and resp.get_json()["error"] == "An appointment must start and end on the same day"
    resp = _put(client, created["id"], {"start_time": ""})
    assert resp.status_code == 400 and resp.get_json()["error"] == "Start and end time are required"
    resp = _put(client, created["id"], {"start_time": "garbage"})
    assert resp.status_code == 400 and resp.get_json()["error"] == "Invalid date format"
    resp = _put(client, created["id"], {"start_time": f"{day}T23:00:00"})
    assert resp.status_code == 200 and resp.get_json()["start_time"] == f"{day}T23:00:00"

    # Строка с зоной (мобильное приложение) → настенные часы магазина
    # (тестовый магазин без таймзоны → America/Chicago, январь = UTC-6).
    zoned = _create(client, {"start_time": f"{day}T15:00:00Z", "end_time": f"{day}T16:30:00.000Z"})
    assert zoned["start_time"] == f"{day}T09:00:00" and zoned["end_time"] == f"{day}T10:30:00"


def test_status_mechanics_presets_and_legacy_docs(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(2)
    m1, m2 = str(api_seed["mechanic"]["_id"]), str(api_seed["second"]["_id"])
    stranger = str(ObjectId())

    created = _create(client, {
        **_times(day, 9), "status": "bogus", "title": "x" * 600,
        "mechanics": [{"id": stranger, "name": "Nobody"}, {"id": m1, "name": "Client Name"}, {"id": m1, "name": "dup"}, "junk"],
        "presets": [
            {"id": str(api_seed["preset"]["_id"]), "name": "Oil change"}, {"id": "", "name": "no id"}, {"id": "x", "name": ""}, "junk",
            {"id": str(api_seed["preset"]["_id"]), "name": "Oil change"},  # дубль по id — один раз
        ],
    })
    assert created["status"] == "scheduled"
    assert created["mechanics"] == [{"id": m1, "name": "Api Mechanic"}] and created["mechanic_ids"] == [m1]
    assert created["presets"] == [{"id": str(api_seed["preset"]["_id"]), "name": "Oil change"}]
    assert len(created["title"]) == 500
    saved = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert saved["mechanic_ids"] == [ObjectId(m1)] and saved["mechanics"][0]["name"] == "Api Mechanic"

    # Legacy-клиент: одиночный mechanic_id, присланное имя игнорируется.
    legacy = _create(client, {**_times(day, 11), "mechanic_id": m2, "mechanic_name": "ignored"})
    assert legacy["mechanic_ids"] == [m2] and legacy["mechanics"] == [{"id": m2, "name": "Second Spanner"}]
    assert legacy["mechanic_name"] == "Second Spanner"
    # Чужой id в legacy-поле — бригады нет.
    ghost = _create(client, {**_times(day, 12), "mechanic_id": stranger, "mechanic_name": "Nobody"})
    assert ghost["mechanics"] == [] and ghost["mechanic_id"] == "" and ghost["mechanic_name"] == ""
    ghost_doc = shop_db.calendar_events.find_one({"_id": ObjectId(ghost["id"])})
    assert ghost_doc["mechanic_id"] is None and ghost_doc["mechanic_ids"] == [] and ghost_doc["mechanic_name"] == ""
    # Приоритет полей: mechanics → mechanic_ids → mechanic_id.
    first_wins = _create(client, {**_times(day, 13), "mechanics": [], "mechanic_ids": [m1], "mechanic_id": m2})
    assert first_wins["mechanic_ids"] == []

    # Legacy-документ в базе: только mechanic_id/mechanic_name и «New Customer» без клиента.
    legacy_doc = {
        "_id": ObjectId(), "shop_id": api_seed["shop"]["_id"], "tenant_id": api_seed["tenant"]["_id"],
        "title": "Legacy", "start_time": datetime.fromisoformat(f"{day}T14:00:00"),
        "end_time": datetime.fromisoformat(f"{day}T15:00:00"), "status": "scheduled",
        "customer_id": None, "customer_label": "New Customer", "unit_id": None, "unit_label": "",
        "mechanic_id": ObjectId(m1), "mechanic_name": "Old Name", "presets": [],
        "work_order_id": None, "wo_number": None, "created_at": _now(), "updated_at": _now(),
    }
    shop_db.calendar_events.insert_one(legacy_doc)
    _EVENT_IDS.append(legacy_doc["_id"])
    row = next(r for r in _events(client, day) if r["id"] == str(legacy_doc["_id"]))
    assert set(row) == SHAPE
    assert row["mechanics"] == [{"id": m1, "name": "Old Name"}] and row["mechanic_ids"] == [m1] and row["mechanic_id"] == m1
    assert row["customer_id"] == "" and row["customer_label"] == "" and row["title"] == "Legacy" and row["note"] == "Legacy"

    filtered = {r["id"] for r in _events(client, day, f"&mechanic_id={m1}")}
    assert str(legacy_doc["_id"]) in filtered and created["id"] in filtered and legacy["id"] not in filtered

    # PUT на legacy-документ дописывает все четыре поля бригады; неизвестный статус → scheduled.
    resp = _put(client, str(legacy_doc["_id"]), {"status": "nonsense", "mechanic_ids": [m2]})
    assert resp.status_code == 200 and resp.get_json()["status"] == "scheduled"
    updated = shop_db.calendar_events.find_one({"_id": legacy_doc["_id"]})
    assert updated["mechanics"] == [{"id": ObjectId(m2), "name": "Second Spanner"}]
    assert updated["mechanic_ids"] == [ObjectId(m2)] and updated["mechanic_id"] == ObjectId(m2)
    assert updated["mechanic_name"] == "Second Spanner"


def test_labels_come_from_db_and_follow_renames(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(3)
    customer, other = api_seed["customer"], api_seed["other"]

    # Лейблы клиента игнорируются, юнит чужого клиента отбрасывается.
    created = _create(client, {
        **_times(day, 9), "customer_id": str(customer["_id"]), "customer_label": "Wrong",
        "unit_id": str(api_seed["other_unit"]["_id"]), "unit_label": "Wrong",
    })
    assert created["customer_label"] == CUSTOMER_NAME and created["unit_id"] == "" and created["unit_label"] == ""
    assert created["title"] == "" and created["note"] == ""
    resp = _put(client, created["id"], {"unit_id": str(api_seed["unit"]["_id"]), "unit_label": "Wrong"})
    assert resp.status_code == 200 and resp.get_json()["unit_label"] == UNIT_LABEL
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["unit_label"] == UNIT_LABEL

    shop_db.customers.update_one({"_id": customer["_id"]}, {"$set": {"company_name": "API Fleet Renamed"}})
    try:
        row = next(r for r in _events(client, day) if r["id"] == created["id"])
        assert row["customer_label"] == "API Fleet Renamed"
        assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["customer_label"] == CUSTOMER_NAME
        # customer_label в теле PUT игнорируется; ответ — живой лейбл.
        resp = _put(client, created["id"], {"customer_label": "Hacked", "title": "Note"})
        assert resp.status_code == 200 and resp.get_json()["customer_label"] == "API Fleet Renamed"
        assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["customer_label"] == CUSTOMER_NAME
        # Повторный выбор клиента переписывает сохранённый лейбл живым.
        resp = _put(client, created["id"], {"customer_id": str(customer["_id"])})
        assert resp.status_code == 200
        assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["customer_label"] == "API Fleet Renamed"
    finally:
        shop_db.customers.update_one({"_id": customer["_id"]}, {"$set": {"company_name": CUSTOMER_NAME}})

    # Смена клиента: прежний юнит ему не принадлежит — уходит.
    resp = _put(client, created["id"], {"customer_id": str(other["_id"])})
    assert resp.status_code == 200
    assert resp.get_json()["customer_label"] == OTHER_CUSTOMER_NAME and resp.get_json()["unit_id"] == ""

    # Несуществующий / неактивный клиент — без клиента.
    ghost = _create(client, {**_times(day, 11), "customer_id": str(ObjectId()), "customer_label": "Ghost"})
    assert ghost["customer_id"] == "" and ghost["customer_label"] == ""
    shop_db.customers.update_one({"_id": other["_id"]}, {"$set": {"is_active": False}})
    try:
        inactive = _create(client, {**_times(day, 12), "customer_id": str(other["_id"]), "title": "Call"})
        assert inactive["customer_id"] == "" and inactive["customer_label"] == "" and inactive["note"] == "Call"
    finally:
        shop_db.customers.update_one({"_id": other["_id"]}, {"$set": {"is_active": True}})

    # Старая запись без клиента с лейблом «New Customer»: лейбл пустой, заметки нет.
    legacy_doc = {
        "_id": ObjectId(), "shop_id": api_seed["shop"]["_id"], "title": "New Customer",
        "start_time": datetime.fromisoformat(f"{day}T13:00:00"), "end_time": datetime.fromisoformat(f"{day}T14:00:00"),
        "status": "scheduled", "customer_id": None, "customer_label": "New Customer", "unit_id": None, "unit_label": "",
        "mechanics": [], "mechanic_ids": [], "mechanic_id": None, "mechanic_name": "", "presets": [],
        "work_order_id": None, "wo_number": None, "created_at": _now(), "updated_at": _now(),
    }
    shop_db.calendar_events.insert_one(legacy_doc)
    _EVENT_IDS.append(legacy_doc["_id"])
    row = next(r for r in _events(client, day) if r["id"] == str(legacy_doc["_id"]))
    assert row["customer_id"] == "" and row["customer_label"] == "" and row["note"] == "" and row["title"] == "New Customer"


def test_range_and_mechanic_filters(client, api_seed):
    _login_owner(client)
    day = _day(4)
    m1, m2 = str(api_seed["mechanic"]["_id"]), str(api_seed["second"]["_id"])
    a = _create(client, {**_times(day, 8), "mechanic_ids": [m1], "title": "A"})["id"]
    b = _create(client, {**_times(day, 9), "mechanic_ids": [m2], "title": "B"})["id"]
    c = _create(client, {**_times(day, 10), "title": "C"})["id"]
    d = _create(client, {**_times(day, 11), "mechanic_ids": [m1, m2], "title": "D"})["id"]
    e = _create(client, {**_times(_day(5), 9), "mechanic_ids": [m1], "title": "E next week"})["id"]
    start, end = _range(day)
    # Границы: start_time >= start включается, start_time == end — нет.
    at_start = _create(client, {"start_time": start, "end_time": f"{_day(4, 0)}T01:00:00", "title": "At start"})["id"]
    at_end = _create(client, {"start_time": end, "end_time": f"{_day(5, 0)}T01:00:00", "title": "At end"})["id"]

    rows = _events(client, day)
    ids = {r["id"] for r in rows}
    assert {a, b, c, d, at_start} <= ids and e not in ids and at_end not in ids
    assert [r["start_time"] for r in rows] == sorted(r["start_time"] for r in rows)

    assert {r["id"] for r in _events(client, day, f"&mechanic_id={m1}")} & {a, b, c, d, at_start} == {a, d}
    assert {r["id"] for r in _events(client, day, f"&mechanic_id={m2}")} & {a, b, c, d} == {b, d}
    unassigned = {r["id"] for r in _events(client, day, "&mechanic_id=unassigned")}
    assert {c, at_start} <= unassigned and not ({a, b, d} & unassigned)
    assert _events(client, day, f"&mechanic_id={ObjectId()}") == []

    # Диапазон с «Z» (мобильное приложение): ISO в UTC переводится в часы магазина.
    resp = client.get(f"/calendar/api/events?start={start}Z&end={end}Z")
    assert resp.status_code == 200
    assert {a, b, c, d} <= {r["id"] for r in resp.get_json()}
    # Без диапазона — тоже массив.
    resp = client.get("/calendar/api/events")
    assert resp.status_code == 200 and isinstance(resp.get_json(), list)


# ── изоляция и права ─────────────────────────────────────────

def test_tenant_isolation(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(6)
    created = _create(client, {**_times(day, 9), "customer_id": str(api_seed["customer"]["_id"]), "title": "Tenant A"})
    wo = _insert_wo(shop_db, api_seed, 96001)

    _login_owner_b(client)
    assert client.get("/calendar").status_code == 200
    rows = _events(client, day)
    assert created["id"] not in {r["id"] for r in rows}
    assert not [r for r in rows if shop_db.calendar_events.find_one({"_id": ObjectId(r["id"])})]
    assert _put(client, created["id"], {"title": "Hijack"}).status_code == 404
    token = get_csrf_token(client)
    assert client.delete(f"/calendar/api/events/{created['id']}", headers={"X-CSRFToken": token}).get_json() == {"ok": True}
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["title"] == "Tenant A"
    assert client.get(f"/calendar/api/units/{api_seed['customer']['_id']}").get_json() == []
    assert client.get(f"/calendar/api/work_orders/{wo['_id']}/snapshot").status_code == 404
    # Чужой WO не привязывается, чужой клиент/юнит/механик не сохраняются.
    resp = _json(client, "post", "/calendar/api/events", {**_times(day, 10), "work_order_id": str(wo["_id"])})
    assert resp.status_code == 404 and resp.get_json() == {"error": "Work order not found"}
    mine = _create(client, {
        **_times(day, 11), "customer_id": str(api_seed["customer"]["_id"]), "unit_id": str(api_seed["unit"]["_id"]),
        "mechanic_ids": [str(api_seed["mechanic"]["_id"])], "title": "Tenant B",
    })
    assert mine["customer_id"] == "" and mine["unit_id"] == "" and mine["mechanics"] == []
    b_doc = mongo[SHOP_B_DB].calendar_events.find_one({"_id": ObjectId(mine["id"])})
    assert b_doc and b_doc["shop_id"] == api_seed["owner_b"]["shop_ids"][0] or b_doc["shop_id"] == ObjectId(api_seed["owner_b"]["shop_ids"][0])
    assert shop_db.calendar_events.find_one({"_id": ObjectId(mine["id"])}) is None

    _login_owner(client)
    assert mine["id"] not in {r["id"] for r in _events(client, day)}
    assert _put(client, mine["id"], {"title": "x"}).status_code == 404


def test_permissions_by_key(app, client, api_seed):
    _login_mechanic(client)  # роль mechanic: только calendar.view
    day = _day(7)
    ctx = _page_context(app, client)
    assert ctx["calendar_can_create"] is False and ctx["calendar_can_edit"] is False
    assert ctx["calendar_can_delete"] is False and ctx["calendar_can_manage_settings"] is False
    assert ctx["calendar_can_create_wo"] is False  # механик-режим
    assert isinstance(_events(client, day), list)
    assert client.get("/calendar/api/settings").status_code == 200
    assert client.get("/calendar/api/mechanics").status_code == 200

    resp = _json(client, "post", "/calendar/api/events", _times(day, 9))
    assert resp.status_code == 403 and resp.get_json()["error"] == "forbidden"
    assert _put(client, str(ObjectId()), {"title": "x"}).status_code == 403
    token = get_csrf_token(client)
    assert client.delete(f"/calendar/api/events/{ObjectId()}", headers={"X-CSRFToken": token}).status_code == 403
    assert _json(client, "put", "/calendar/api/settings", {"start_hour": 7}).status_code == 403
    assert _json(client, "put", "/calendar/api/statuses", {"statuses": []}).status_code == 403


# ── настройки ────────────────────────────────────────────────

def test_settings_get_put_and_statuses_alias(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    system = [
        {"key": "scheduled", "label": "Booked", "color": "#111111"},
        {"key": "in_progress", "label": "Working", "color": "#222222"},
        {"key": "completed", "label": "Done", "color": "#333333"},
        {"key": "cancelled", "label": "Cancelled", "color": "#444444"},
    ]
    try:
        default = client.get("/calendar/api/settings").get_json()
        assert (default["start_hour"], default["end_hour"], default["slot_minutes"]) == (6, 20, 15)

        for bad in ({"start_hour": 8, "end_hour": 8}, {"start_hour": -1}, {"end_hour": 25}, {"start_hour": "x"},
                    {"start_hour": 7.5}, {"start_hour": True}, {}):
            resp = _json(client, "put", "/calendar/api/settings", bad)
            assert resp.status_code == 400 and resp.get_json()["error"], bad
        assert client.get("/calendar/api/settings").get_json() == default

        resp = _json(client, "put", "/calendar/api/settings", {"statuses": []})
        assert resp.status_code == 400 and resp.get_json()["error"] == "At least one status is required"
        resp = _json(client, "put", "/calendar/api/settings", {"statuses": system[:2] + system[3:]})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "System statuses cannot be removed: completed"
        resp = _json(client, "put", "/calendar/api/settings", {"statuses": system + [{"key": "scheduled", "label": "Again", "color": "#555555"}]})
        assert resp.status_code == 400 and resp.get_json()["error"] == "Duplicate status key: scheduled"
        resp = _json(client, "put", "/calendar/api/settings", {"statuses": system + [{"key": "x", "label": "", "color": "#555555"}]})
        assert resp.status_code == 400 and resp.get_json()["error"] == "Each status needs a label"
        assert shop_db.calendar_settings.count_documents({}) == 0

        resp = _json(client, "put", "/calendar/api/settings", {
            "statuses": system + [{"key": "Waiting Parts!", "label": " Waiting parts ", "color": "nope"}],
            "start_hour": "7", "end_hour": 19,
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert [s["key"] for s in body["statuses"]] == ["scheduled", "in_progress", "completed", "cancelled", "waiting_parts_"]
        assert body["statuses"][-1] == {"key": "waiting_parts_", "label": "Waiting parts", "color": "#888888"}
        assert (body["start_hour"], body["end_hour"], body["slot_minutes"]) == (7, 19, 15)
        assert client.get("/calendar/api/settings").get_json() == body
        assert client.get("/calendar/api/statuses").get_json() == body["statuses"]
        assert shop_db.calendar_settings.find_one({"key": "hours"})["start_hour"] == 7

        # Кастомный статус принимается записью; системные — можно только переименовать.
        created = _create(client, {**_times(_day(8), 9), "status": "waiting_parts_"})
        assert created["status"] == "waiting_parts_"

        # Алиас только для статусов: отдаёт список, часы не трогает.
        resp = _json(client, "put", "/calendar/api/statuses", {"statuses": system})
        assert resp.status_code == 200 and [s["key"] for s in resp.get_json()] == [s["key"] for s in system]
        assert client.get("/calendar/api/statuses").get_json() == resp.get_json()
        assert client.get("/calendar/api/settings").get_json()["start_hour"] == 7
        resp = _json(client, "put", "/calendar/api/statuses", {"statuses": system[1:]})
        assert resp.status_code == 400 and "scheduled" in resp.get_json()["error"]
        resp = _json(client, "put", "/calendar/api/statuses", {})
        assert resp.status_code == 400

        # Только часы — статусы остаются.
        resp = _json(client, "put", "/calendar/api/settings", {"start_hour": 0, "end_hour": 24})
        assert resp.status_code == 200 and (resp.get_json()["start_hour"], resp.get_json()["end_hour"]) == (0, 24)
        assert resp.get_json()["statuses"] == system
    finally:
        shop_db.calendar_settings.delete_many({"key": {"$in": ["statuses", "hours"]}})
    assert client.get("/calendar/api/settings").get_json()["statuses"][0]["label"] == "Scheduled"


# ── work orders ──────────────────────────────────────────────

def test_wo_snapshot_endpoint(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    m1, m2 = api_seed["mechanic"], api_seed["second"]
    wo = _insert_wo(shop_db, api_seed, 96002, labors=[
        _labor(96002, "a", "Replace brakes", "1.5", assigned=[_assigned(m1)]),
        _labor(96002, "b", "Oil change", "2", assigned=[_assigned(m1, 50.0), _assigned(m2, 50.0)]),
        _labor(96002, "c", "Inspect belts", "0.5"),
        _labor(96002, "d", "Rotate tires", "1"),
    ])
    url = f"/calendar/api/work_orders/{wo['_id']}/snapshot"
    resp = client.get(url)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json() == {
        "work_order_id": str(wo["_id"]), "wo_number": 96002,
        "customer_id": str(api_seed["customer"]["_id"]), "customer_label": CUSTOMER_NAME,
        "unit_id": str(api_seed["unit"]["_id"]), "unit_label": UNIT_LABEL,
        "mechanics": [{"id": str(m1["_id"]), "name": "Api Mechanic"}, {"id": str(m2["_id"]), "name": "Second Spanner"}],
        "title": "Replace brakes, Oil change, Inspect belts", "hours": 5.0, "wo_state": "open",
    }
    for fields, state in (
        ({"status": "in_progress", "mechanic_done": False}, "in_progress"),
        ({"status": "in_progress", "mechanic_done": True}, "mechanic_done"),
        ({"status": "open", "mechanic_done": True}, "completed"),
        ({"status": "open", "mechanic_done": False, "manager_confirmed": True}, "completed"),
        ({"status": "paid", "manager_confirmed": False}, "paid"),
        ({"status": "estimate"}, "estimate"),
    ):
        shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": fields})
        assert client.get(url).get_json()["wo_state"] == state, fields

    assert client.get(f"/calendar/api/work_orders/{ObjectId()}/snapshot").status_code == 404
    assert client.get("/calendar/api/work_orders/garbage/snapshot").get_json() == {"error": "Work order not found"}
    shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"is_active": False}})
    assert client.get(url).status_code == 404


def test_linking_on_post_fills_empty_fields_and_enriches(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(9)
    m1, m2 = api_seed["mechanic"], api_seed["second"]
    wo = _insert_wo(shop_db, api_seed, 96003, labors=[
        _labor(96003, "a", "Replace brakes", "2", assigned=[_assigned(m1)]),
        _labor(96003, "b", "Oil change", "1"),
        _labor(96003, "c", "Inspect belts", "0.5"),
        _labor(96003, "d", "Rotate tires", "1"),
    ])
    created = _create(client, {**_times(day, 9, 120), "work_order_id": str(wo["_id"])})
    assert created["work_order_id"] == str(wo["_id"]) and created["wo_number"] == 96003
    assert created["wo_state"] == "open" and created["labor_summary"] == ["Replace brakes", "Oil change", "Inspect belts"]
    assert created["customer_id"] == str(api_seed["customer"]["_id"]) and created["customer_label"] == CUSTOMER_NAME
    assert created["unit_id"] == str(api_seed["unit"]["_id"]) and created["unit_label"] == UNIT_LABEL
    assert created["mechanics"] == [{"id": str(m1["_id"]), "name": "Api Mechanic"}]
    assert created["title"] == "Replace brakes, Oil change, Inspect belts" and created["note"] == created["title"]
    assert created["status"] == "scheduled"
    doc = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert doc["work_order_id"] == wo["_id"] and doc["wo_number"] == 96003

    # Явно присланное важнее WO; юнит WO принадлежит другому клиенту — отбрасывается.
    explicit = _create(client, {
        **_times(day, 11), "work_order_id": str(wo["_id"]), "title": "Call first",
        "mechanic_ids": [str(m2["_id"])], "customer_id": str(api_seed["other"]["_id"]),
    })
    assert explicit["title"] == "Call first" and explicit["mechanic_ids"] == [str(m2["_id"])]
    assert explicit["customer_id"] == str(api_seed["other"]["_id"]) and explicit["unit_id"] == ""
    assert explicit["wo_number"] == 96003

    # Привязка к идущему WO статус записи не трогает (новый визит по WO в работе —
    # всё равно «Scheduled»; факт работы виден в wo_state). За WO статус идёт
    # только по хукам work orders (таймер, Done, completed/paid).
    running = _insert_wo(shop_db, api_seed, 96004, status="in_progress")
    linked = _create(client, {**_times(day, 13), "work_order_id": str(running["_id"])})
    assert linked["status"] == "scheduled" and linked["wo_state"] == "in_progress"
    cancelled = _create(client, {**_times(day, 14), "work_order_id": str(running["_id"]), "status": "cancelled"})
    assert cancelled["status"] == "cancelled"

    before = shop_db.calendar_events.count_documents({"shop_id": api_seed["shop"]["_id"]})
    resp = _json(client, "post", "/calendar/api/events", {**_times(day, 15), "work_order_id": str(ObjectId())})
    assert resp.status_code == 404 and resp.get_json() == {"error": "Work order not found"}
    assert shop_db.calendar_events.count_documents({"shop_id": api_seed["shop"]["_id"]}) == before

    # WO удалён (неактивен): в выдаче связи нет, в базе ссылка остаётся.
    shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"is_active": False}})
    rows = {r["id"]: r for r in _events(client, day)}
    gone = rows[created["id"]]
    assert gone["work_order_id"] == "" and gone["wo_number"] is None and gone["wo_state"] == "" and gone["labor_summary"] == []
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["work_order_id"] == wo["_id"]
    assert rows[linked["id"]]["wo_state"] == "in_progress" and rows[linked["id"]]["labor_summary"] == ["Replace brakes"]
    shop_db.work_orders.update_one({"_id": running["_id"]}, {"$set": {"status": "estimate"}})
    assert {r["id"]: r for r in _events(client, day)}[linked["id"]]["wo_state"] == "estimate"


def test_mechanic_mode_masks_paid(app, client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(10)
    paid = _insert_wo(shop_db, api_seed, 96005, status="paid")
    created = _create(client, {**_times(day, 9), "work_order_id": str(paid["_id"])})
    # Статус новой записи — scheduled (привязка статус не трогает); состояние WO — в wo_state.
    assert created["wo_state"] == "paid" and created["status"] == "scheduled"
    assert client.get(f"/calendar/api/work_orders/{paid['_id']}/snapshot").get_json()["wo_state"] == "paid"

    _login_mechanic(client)
    start, end = _range(day)
    resp = client.get(f"/calendar/api/events?start={start}Z&end={end}Z")
    assert resp.status_code == 200
    row = next(r for r in resp.get_json() if r["id"] == created["id"])
    assert row["wo_state"] == "completed" and row["wo_number"] == 96005
    assert "paid" not in str(row).lower()
    assert "paid" not in {s["key"] for s in client.get("/calendar/api/wo_states").get_json()}
    assert client.get(f"/calendar/api/work_orders/{paid['_id']}/snapshot").get_json()["wo_state"] == "completed"
    assert _page_context(app, client)["calendar_can_create_wo"] is False
    _login_owner(client)
    assert [s["key"] for s in client.get("/calendar/api/wo_states").get_json()][-1] == "paid"


def test_linked_customer_unit_rules_and_unlink(client, api_seed, mongo):
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(11)
    customer, other = api_seed["customer"], api_seed["other"]
    wo = _insert_wo(shop_db, api_seed, 96006)
    created = _create(client, {**_times(day, 9), "work_order_id": str(wo["_id"])})
    message = ("This appointment is linked to WO #96006 — change the customer or unit on the work order "
               "or unlink it first")

    for payload in ({"customer_id": str(other["_id"]), "customer_label": OTHER_CUSTOMER_NAME},
                    {"unit_id": str(api_seed["other_unit"]["_id"])}, {"unit_id": ""}, {"customer_id": ""}):
        resp = _put(client, created["id"], payload)
        assert resp.status_code == 400, (payload, resp.get_data(as_text=True))
        assert resp.get_json() == {"error": message}
    saved = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert saved["customer_id"] == customer["_id"] and saved["unit_id"] == api_seed["unit"]["_id"]

    # Те же клиент/юнит, что у WO (модалка шлёт все поля) — обычная правка; статус — свободно.
    resp = _put(client, created["id"], {
        "customer_id": str(customer["_id"]), "unit_id": str(api_seed["unit"]["_id"]), "title": "Renamed", "status": "confirmed",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["title"] == "Renamed" and resp.get_json()["status"] == "confirmed"

    # Отвязка снимает только ссылку.
    resp = _put(client, created["id"], {"work_order_id": ""})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["work_order_id"] == "" and body["wo_number"] is None and body["wo_state"] == ""
    assert body["status"] == "confirmed" and body["title"] == "Renamed"
    assert body["customer_id"] == str(customer["_id"]) and body["unit_id"] == str(api_seed["unit"]["_id"])
    saved = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert saved["work_order_id"] is None and saved["wo_number"] is None and saved["status"] == "confirmed"

    # Свободная запись: клиент меняется, юнит другого клиента уходит.
    resp = _put(client, created["id"], {"customer_id": str(other["_id"])})
    assert resp.status_code == 200 and resp.get_json()["customer_id"] == str(other["_id"]) and resp.get_json()["unit_id"] == ""

    # Привязка и смена клиента в одном запросе — 400, ничего не записано.
    resp = _put(client, created["id"], {"work_order_id": str(wo["_id"]), "customer_id": str(customer["_id"]), "unit_id": ""})
    assert resp.status_code == 400 and resp.get_json() == {"error": message}
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["work_order_id"] is None

    # Привязка через PUT заполняет только пустое: бригада — с WO, клиент (не пустой) остаётся.
    assert _put(client, created["id"], {"mechanic_ids": []}).status_code == 200
    resp = _put(client, created["id"], {"work_order_id": str(wo["_id"])})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["work_order_id"] == str(wo["_id"]) and body["wo_number"] == 96006 and body["wo_state"] == "open"
    assert body["mechanic_ids"] == [str(api_seed["mechanic"]["_id"])]
    assert body["customer_id"] == str(other["_id"]) and body["title"] == "Renamed"

    resp = _put(client, created["id"], {"work_order_id": str(ObjectId())})
    assert resp.status_code == 404 and resp.get_json() == {"error": "Work order not found"}

    # WO удалён — привязка мёртвая, клиент меняется свободно.
    shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"is_active": False}})
    resp = _put(client, created["id"], {"customer_id": str(customer["_id"])})
    assert resp.status_code == 200 and resp.get_json()["customer_id"] == str(customer["_id"])


# ── привязанная запись: клиент/юнит следуют за WO ────────────

def _modal_payload(ev: dict) -> dict:
    """Ровно то, что веб-модалка шлёт на Save у не тронутой записи."""
    return {
        "title": ev["title"], "status": ev["status"], "start_time": ev["start_time"], "end_time": ev["end_time"],
        "customer_id": ev["customer_id"], "unit_id": ev["unit_id"],
        "mechanics": [{"id": m["id"], "name": m["name"]} for m in ev["mechanics"]],
        "presets": ev["presets"],
    }


def test_linked_identity_follows_wo_even_when_customer_or_unit_inactive(client, api_seed, mongo):
    """Клиента или юнит WO деактивировали (с открытыми WO это разрешено):
    привязанная запись остаётся его — и сохраняется из модалки, которая шлёт
    все поля без изменений; POST с WO берёт клиента/юнит с WO ровно как хук
    link_event_to_work_order — без фильтра is_active."""
    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(12)
    customer, unit = api_seed["customer"], api_seed["unit"]
    wo = _insert_wo(shop_db, api_seed, 96007)
    created = _create(client, {**_times(day, 9, 120), "work_order_id": str(wo["_id"])})
    modal = _modal_payload(created)

    shop_db.customers.update_one({"_id": customer["_id"]}, {"$set": {"is_active": False}})
    try:
        resp = _put(client, created["id"], {**modal, "title": "Call before arrival"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["title"] == "Call before arrival"
        assert body["customer_id"] == str(customer["_id"]) and body["customer_label"] == CUSTOMER_NAME
        assert body["unit_id"] == str(unit["_id"]) and body["unit_label"] == UNIT_LABEL

        posted = _create(client, {**_times(day, 13), "work_order_id": str(wo["_id"])})
        assert posted["customer_id"] == str(customer["_id"]) and posted["customer_label"] == CUSTOMER_NAME
        assert posted["unit_id"] == str(unit["_id"]) and posted["unit_label"] == UNIT_LABEL
        resp = _put(client, posted["id"], {**_modal_payload(posted), "status": "confirmed"})
        assert resp.status_code == 200 and resp.get_json()["status"] == "confirmed"
    finally:
        shop_db.customers.update_one({"_id": customer["_id"]}, {"$set": {"is_active": True}})

    shop_db.units.update_one({"_id": unit["_id"]}, {"$set": {"is_active": False}})
    try:
        resp = _put(client, created["id"], {**modal, "status": "confirmed"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["unit_id"] == str(unit["_id"]) and resp.get_json()["status"] == "confirmed"
    finally:
        shop_db.units.update_one({"_id": unit["_id"]}, {"$set": {"is_active": True}})

    # Чужой клиент у привязанной записи — по-прежнему 400.
    resp = _put(client, created["id"], {**modal, "customer_id": str(api_seed["other"]["_id"])})
    assert resp.status_code == 400
    saved = shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})
    assert saved["customer_id"] == customer["_id"] and saved["status"] == "confirmed"


def test_linked_row_with_drifted_identity_still_saves(app, client, api_seed, mongo):
    """refresh_linked_appointments_identity пропускает отменённые записи: после
    переноса WO такая запись хранит прежнего клиента. Save из модалки с этим
    прежним клиентом (поля read-only, ничего не меняли) — 200; клиент WO —
    тоже 200 (запись догоняет WO); третий клиент — 400."""
    from app.blueprints.calendar.services.work_orders import refresh_linked_appointments_identity

    _login_owner(client)
    shop_db = mongo[SHOP_A_DB]
    day = _day(13)
    customer, other, other_unit = api_seed["customer"], api_seed["other"], api_seed["other_unit"]
    wo = _insert_wo(shop_db, api_seed, 96008)
    created = _create(client, {**_times(day, 9), "work_order_id": str(wo["_id"]), "status": "cancelled"})

    shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"customer_id": other["_id"], "unit_id": other_unit["_id"]}})
    with app.app_context():
        assert refresh_linked_appointments_identity(shop_db, api_seed["shop"], wo["_id"]) == 0
    assert shop_db.calendar_events.find_one({"_id": ObjectId(created["id"])})["customer_id"] == customer["_id"]

    resp = _put(client, created["id"], {**_modal_payload(created), "status": "scheduled"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["status"] == "scheduled" and body["customer_id"] == str(customer["_id"])
    assert body["unit_id"] == str(api_seed["unit"]["_id"])

    # Без клиента/юнита в теле (так модалка шлёт привязанную запись) — тоже обычная правка.
    resp = _put(client, created["id"], {"title": "Back on", "status": "confirmed"})
    assert resp.status_code == 200 and resp.get_json()["title"] == "Back on"

    resp = _put(client, created["id"], {"customer_id": str(ObjectId())})
    assert resp.status_code == 400 and resp.get_json()["error"].startswith("This appointment is linked to WO #96008")

    resp = _put(client, created["id"], {"customer_id": str(other["_id"]), "unit_id": str(other_unit["_id"])})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["customer_id"] == str(other["_id"]) and resp.get_json()["unit_id"] == str(other_unit["_id"])
    assert resp.get_json()["customer_label"] == OTHER_CUSTOMER_NAME


def test_non_object_json_bodies_are_validation_errors(client, api_seed):
    """Массив/строка/число вместо объекта — 400 с {"error"}, не 500."""
    _login_owner(client)
    for url in ("/calendar/api/statuses", "/calendar/api/settings"):
        for raw in ("[1, 2]", '"str"', "42"):
            resp = client.put(url, data=raw, content_type="application/json", headers={"X-CSRFToken": get_csrf_token(client)})
            assert resp.status_code == 400, (url, raw, resp.get_data(as_text=True))
            assert resp.get_json()["error"]
    resp = client.post("/calendar/api/events", data="[1]", content_type="application/json", headers={"X-CSRFToken": get_csrf_token(client)})
    assert resp.status_code == 400 and resp.get_json() == {"error": "Start and end time are required"}
    created = _create(client, _times(_day(14), 9))
    resp = client.put(f"/calendar/api/events/{created['id']}", data='"str"', content_type="application/json",
                      headers={"X-CSRFToken": get_csrf_token(client)})
    assert resp.status_code == 200 and resp.get_json()["id"] == created["id"]  # пустое тело = ничего не менять
