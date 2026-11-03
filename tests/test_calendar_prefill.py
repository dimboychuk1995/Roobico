"""
Старт работы из записи календаря: содержимое записи переезжает в новый WO.

Пресеты записи → строки работ с часами/ставкой/закреплёнными ценами
пресета (та же ценовая логика, что у пресета, применённого механиком
руками); бригада записи → assigned_mechanics каждой строки поровну в
сумме 100; заметка записи → описание проблемы первой строки, а без
пресетов — сама строка работы (длинная заметка: строка получает начало,
полный текст остаётся в описании проблемы). Запись привязывается к WO и
идёт за его статусом. Мобильный create и веб-эндпоинт механика ведут себя
одинаково.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "cal-prefill-mech@test.local"
MECHANIC2_EMAIL = "cal-prefill-mech-2@test.local"
MECHANIC_PASSWORD = "password123"
NOTE = "Grinding noise front left"
# Заметка длиннее строки работы (JOB_LINE_MAX_LEN = 200); на границах 200 и
# 300 — буквы, чтобы strip() ничего не отрезал.
LONG_NOTE = ("Grinding noise front left; " * 12)[:300]
PRESET_NAME = "Front brakes"
PRESET_DESCRIPTION = "Replace front brake pads"
PINNED_PRICE = 123.45


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _mechanic_doc(seed, shop_a, email, first, last):
    return {
        "_id": ObjectId(), "email": email,
        "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": first, "last_name": last, "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic", "created_at": _now(),
    }


@pytest.fixture(scope="module")
def cal_seed(app, seed, mongo):
    """Бригада из двух механиков, клиент, парт и пресет с закреплённой ценой
    парта в магазине A. Юниты — по одному на тест (один юнит = один WO)."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    mechanic = _mechanic_doc(seed, shop_a, MECHANIC_EMAIL, "Cal", "Prefill")
    second = _mechanic_doc(seed, shop_a, MECHANIC2_EMAIL, "Second", "Wrench")
    master.users.insert_many([mechanic, second])

    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Prefill Fleet LLC", "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)

    # Парт с selling price 77 — закреплённая цена пресета (123.45) должна победить.
    part = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "part_number": "CAL-PRE-01",
        "description": "Brake pad set", "average_cost": 40.0,
        "has_selling_price": True, "selling_price": 77.0, "in_stock": 50,
        "is_active": True, "created_at": now,
    }
    shop_db.parts.insert_one(part)

    # Форма документа — как пишет settings/wo_presets (_parse_preset_form): qty — float.
    preset = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "name": PRESET_NAME,
        "description": PRESET_DESCRIPTION, "labor_hours": 1.5, "labor_rate_code": "standard",
        "allow_discount": False,
        "parts": [{
            "part_id": str(part["_id"]), "part_number": "CAL-PRE-01", "description": "Brake pad set",
            "qty": 2.0, "cost": 40.0, "price": PINNED_PRICE, "price_overridden": True, "misc_charges": [],
        }],
        "is_active": True, "created_at": now, "updated_at": now,
    }
    shop_db.wo_presets.insert_one(preset)

    yield {"mechanic": mechanic, "second": second, "customer": customer, "part": part, "preset": preset, "shop": shop_a}

    shop_db.work_orders.delete_many({"customer_id": customer["_id"]})
    shop_db.wo_time_logs.delete_many({"user_id": {"$in": [mechanic["_id"], second["_id"]]}})
    shop_db.calendar_events.delete_many({"customer_id": customer["_id"]})
    shop_db.units.delete_many({"customer_id": customer["_id"]})
    shop_db.wo_presets.delete_one({"_id": preset["_id"]})
    shop_db.parts.delete_one({"_id": part["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})
    master.users.delete_many({"_id": {"$in": [mechanic["_id"], second["_id"]]}})


def _json(client, method, url, payload=None):
    token = get_csrf_token(client)
    fn = getattr(client, method)
    return fn(url, json=payload if payload is not None else {}, headers={"X-CSRFToken": token})


def _future_weekday(weeks_ahead: int, weekday: int = 1, hour: int = 12) -> datetime:
    """Будущий день недели через N недель — у каждого теста своя неделя,
    чтобы бригада не упиралась в проверку занятости слота."""
    today = _now().replace(hour=hour, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(weeks=weeks_ahead, days=weekday)


def _new_unit(mongo, cal_seed, number: str) -> dict:
    """Свой юнит на тест: find_active_unit_work_order не должен цепляться за чужие WO."""
    unit = {
        "_id": ObjectId(), "shop_id": cal_seed["shop"]["_id"], "customer_id": cal_seed["customer"]["_id"],
        "unit_number": number, "year": 2022, "make": "Peterbilt", "model": "579",
        "is_active": True, "created_at": _now(),
    }
    mongo[SHOP_A_DB].units.insert_one(unit)
    return unit


def _preset_ref(cal_seed):
    return [{"id": str(cal_seed["preset"]["_id"]), "name": PRESET_NAME}]


def _create_event(client, cal_seed, unit, weeks_ahead, mechanic_ids, presets=None, title="", hour=9) -> str:
    """Запись календаря от имени владельца; возвращает id строкой."""
    assert login(client).status_code == 302
    day = _future_weekday(weeks_ahead, weekday=2).strftime("%Y-%m-%d")
    resp = _json(client, "post", "/calendar/api/events", {
        "customer_id": str(cal_seed["customer"]["_id"]), "customer_label": "Prefill Fleet LLC",
        "unit_id": str(unit["_id"]), "unit_label": unit["unit_number"],
        "mechanic_ids": mechanic_ids, "presets": presets or [], "title": title,
        "start_time": f"{day}T{hour:02d}:00:00", "end_time": f"{day}T{hour + 1:02d}:00:00",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def _login_mechanic(client):
    assert login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD).status_code == 302


def _mobile_payload(cal_seed, unit, **extra):
    """То, что шлёт приложение механика по «Start work»: работ ещё нет."""
    payload = {
        "customer_id": str(cal_seed["customer"]["_id"]), "unit_id": str(unit["_id"]),
        "status": "in_progress", "labors": [], "mechanic_state": "in_progress", "unit_mileage": 120000,
    }
    payload.update(extra)
    return payload


def _crew(block) -> dict:
    """{user_id: percent} по назначениям строки."""
    return {a["user_id"]: a["percent"] for a in block["labor"]["assigned_mechanics"]}


def _assert_seeded_from_preset(wo, cal_seed):
    """Строка из пресета: описание, часы/ставка пресета, закреплённая цена парта, qty целое."""
    assert len(wo["labors"]) == 1, wo["labors"]  # один пресет — одна строка
    labor = wo["labors"][0]["labor"]
    assert labor["description"] == PRESET_DESCRIPTION
    assert float(labor["hours"]) == 1.5
    assert labor["hours_source"] == "preset"
    assert labor["rate_code"] == "standard"
    part_line = wo["labors"][0]["parts"][0]
    assert str(part_line["part_id"]) == str(cal_seed["part"]["_id"])
    assert int(part_line["qty"]) == 2
    assert float(part_line["price"]) == PINNED_PRICE  # а не selling price 77
    assert float(part_line["cost"]) == 40.0


def test_crew_shares_split_evenly_and_sum_to_100():
    from app.blueprints.work_orders.services.appointment_prefill import crew_shares

    assert crew_shares(0) == []
    assert crew_shares(1) == [100.0]
    assert crew_shares(2) == [50.0, 50.0]
    assert crew_shares(3) == [33.34, 33.33, 33.33]
    for n in range(1, 12):
        assert sum(crew_shares(n)) == pytest.approx(100)


def test_mobile_start_work_seeds_presets_crew_and_note(client, cal_seed, mongo):
    """(a) Механик стартует работу из записи с пресетом, бригадой и заметкой."""
    shop_db = mongo[SHOP_A_DB]
    unit = _new_unit(mongo, cal_seed, "PRE-A")
    m1, m2 = str(cal_seed["mechanic"]["_id"]), str(cal_seed["second"]["_id"])
    event_id = _create_event(client, cal_seed, unit, 30, [m1, m2], presets=_preset_ref(cal_seed), title=NOTE)

    _login_mechanic(client)
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit, calendar_event_id=event_id))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True and body["status"] == "in_progress"

    wo = shop_db.work_orders.find_one({"_id": ObjectId(body["id"])})
    _assert_seeded_from_preset(wo, cal_seed)
    labor = wo["labors"][0]["labor"]
    assert labor["issue_description"] == NOTE

    crew = _crew(wo["labors"][0])
    assert set(crew) == {cal_seed["mechanic"]["_id"], cal_seed["second"]["_id"]}
    assert all(isinstance(uid, ObjectId) for uid in crew)
    assert crew[cal_seed["mechanic"]["_id"]] == 50.0
    assert sum(crew.values()) == pytest.approx(100)
    names = {a["user_id"]: (a["name"], a["role"]) for a in labor["assigned_mechanics"]}
    assert names[cal_seed["second"]["_id"]] == ("Second Wrench", "mechanic")

    # Механик видит описание проблемы в деталях WO (безденежная форма).
    detail = client.get(f"/api/mobile/work_orders/{body['id']}")
    assert detail.status_code == 200, detail.get_data(as_text=True)
    detail_body = detail.get_json()
    assert detail_body["labors"][0]["issue_description"] == NOTE
    assert "grand_total" not in detail_body

    # Запись привязана к WO и пошла за работой.
    event = shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})
    assert event["work_order_id"] == wo["_id"]
    assert event["wo_number"] == wo["wo_number"]
    assert event["status"] == "in_progress"


def test_mobile_note_without_presets_becomes_job_line(client, cal_seed, mongo):
    """(b) Пресетов нет — заметка записи становится строкой работы (как note на вебе)."""
    shop_db = mongo[SHOP_A_DB]
    unit = _new_unit(mongo, cal_seed, "PRE-B")
    event_id = _create_event(client, cal_seed, unit, 31, [str(cal_seed["mechanic"]["_id"])], title=NOTE)

    _login_mechanic(client)
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit, calendar_event_id=event_id))
    assert resp.status_code == 200, resp.get_data(as_text=True)

    wo = shop_db.work_orders.find_one({"_id": ObjectId(resp.get_json()["id"])})
    assert len(wo["labors"]) == 1, wo["labors"]
    labor = wo["labors"][0]["labor"]
    assert labor["description"] == NOTE
    # Заметка уже стала строкой работы — в описание проблемы она не дублируется.
    assert labor["issue_description"] == ""
    assert labor["rate_code"] == "standard"  # дефолтная ставка магазина, не $0
    assert wo["labors"][0]["parts"] == []
    assert _crew(wo["labors"][0]) == {cal_seed["mechanic"]["_id"]: 100.0}

    event = shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})
    assert event["work_order_id"] == wo["_id"] and event["status"] == "in_progress"


def test_mobile_office_explicit_labors_get_crew_without_duplicates(client, cal_seed, mongo):
    """(c) Офисный пользователь шлёт свои работы: пресеты записи не добавляются
    (нет дублей), бригада — на каждую строку, заметка — в описание проблемы."""
    shop_db = mongo[SHOP_A_DB]
    unit = _new_unit(mongo, cal_seed, "PRE-C")
    m1, m2 = str(cal_seed["mechanic"]["_id"]), str(cal_seed["second"]["_id"])
    event_id = _create_event(client, cal_seed, unit, 32, [m1, m2], presets=_preset_ref(cal_seed), title=NOTE)

    # Владелец залогинен после _create_event; у него есть view_costs — это не механик-режим.
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit, calendar_event_id=event_id, labors=[
        {"description": "Oil change", "hours": "1", "rate_code": "standard", "parts": []},
        {"description": "Inspect belts", "hours": "0.5", "rate_code": "standard", "parts": []},
    ]))
    assert resp.status_code == 200, resp.get_data(as_text=True)

    wo = shop_db.work_orders.find_one({"_id": ObjectId(resp.get_json()["id"])})
    assert [b["labor"]["description"] for b in wo["labors"]] == ["Oil change", "Inspect belts"]
    for block in wo["labors"]:
        crew = _crew(block)
        assert set(crew) == {cal_seed["mechanic"]["_id"], cal_seed["second"]["_id"]}
        assert sum(crew.values()) == pytest.approx(100)
    assert wo["labors"][0]["labor"]["issue_description"] == NOTE
    assert wo["labors"][1]["labor"]["issue_description"] == ""
    assert wo["labors"][0]["labor"]["hours"] == "1"  # офисные часы сохранены
    assert shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})["work_order_id"] == wo["_id"]

    # Пустой список работ у офисного пользователя при записи без пресетов — по-прежнему 400.
    unit2 = _new_unit(mongo, cal_seed, "PRE-C2")
    event2 = _create_event(client, cal_seed, unit2, 32, [m1], title=NOTE, hour=13)
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit2, calendar_event_id=event2))
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "labors_required"
    assert shop_db.work_orders.count_documents({"unit_id": unit2["_id"]}) == 0


def test_deactivated_crew_member_is_dropped(client, cal_seed, mongo, seed):
    """(d) Уволенный после планирования член бригады молча выпадает, доли — оставшимся."""
    shop_db = mongo[SHOP_A_DB]
    master = mongo["roobico_test_master"]
    unit = _new_unit(mongo, cal_seed, "PRE-D")
    third = _mechanic_doc(seed, cal_seed["shop"], "cal-prefill-mech-3@test.local", "Gone", "Fisher")
    master.users.insert_one(third)
    try:
        m1, m3 = str(cal_seed["mechanic"]["_id"]), str(third["_id"])
        event_id = _create_event(client, cal_seed, unit, 33, [m1, m3], presets=_preset_ref(cal_seed), title=NOTE)
        master.users.update_one({"_id": third["_id"]}, {"$set": {"is_active": False}})

        _login_mechanic(client)
        resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit, calendar_event_id=event_id))
        assert resp.status_code == 200, resp.get_data(as_text=True)

        wo = shop_db.work_orders.find_one({"_id": ObjectId(resp.get_json()["id"])})
        _assert_seeded_from_preset(wo, cal_seed)
        assert _crew(wo["labors"][0]) == {cal_seed["mechanic"]["_id"]: 100.0}
    finally:
        master.users.delete_one({"_id": third["_id"]})


def test_web_mechanic_create_links_and_seeds_from_appointment(client, cal_seed, mongo):
    """(e) Веб-эндпоинт механика с calendar_event_id: те же пресеты/бригада/заметка,
    привязка после создания; по юниту WO уже идёт — 409 и привязка к нему."""
    shop_db = mongo[SHOP_A_DB]
    unit = _new_unit(mongo, cal_seed, "PRE-E")
    m1, m2 = str(cal_seed["mechanic"]["_id"]), str(cal_seed["second"]["_id"])
    event_id = _create_event(client, cal_seed, unit, 34, [m1, m2], presets=_preset_ref(cal_seed), title=NOTE)
    follow_up = _create_event(client, cal_seed, unit, 34, [m1], title="Follow-up", hour=14)

    _login_mechanic(client)
    web_payload = {
        "customer_id": str(cal_seed["customer"]["_id"]), "unit_id": str(unit["_id"]),
        "unit_mileage": 120000, "labors": [], "calendar_event_id": event_id,
    }
    resp = _json(client, "post", "/work_orders/api/mechanic/work_orders", web_payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True and body["status"] == "in_progress"
    assert "grand_total" not in body  # форма ответа веб-эндпоинта — без денег

    wo = shop_db.work_orders.find_one({"_id": ObjectId(body["id"])})
    _assert_seeded_from_preset(wo, cal_seed)
    assert wo["labors"][0]["labor"]["issue_description"] == NOTE
    crew = _crew(wo["labors"][0])
    assert set(crew) == {cal_seed["mechanic"]["_id"], cal_seed["second"]["_id"]}
    assert sum(crew.values()) == pytest.approx(100)

    event = shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})
    assert event["work_order_id"] == wo["_id"] and event["wo_number"] == wo["wo_number"]
    assert event["status"] == "in_progress"

    # По юниту WO уже идёт: 409, но вторая запись привязана к нему (как в мобильном).
    resp = _json(client, "post", "/work_orders/api/mechanic/work_orders", {**web_payload, "calendar_event_id": follow_up})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["work_order"]["id"] == body["id"]
    second_event = shop_db.calendar_events.find_one({"_id": ObjectId(follow_up)})
    assert second_event["work_order_id"] == wo["_id"] and second_event["status"] == "in_progress"

    # Без записи пустой список работ по-прежнему не принимается.
    unit2 = _new_unit(mongo, cal_seed, "PRE-E2")
    resp = _json(client, "post", "/work_orders/api/mechanic/work_orders",
                 {**web_payload, "unit_id": str(unit2["_id"]), "calendar_event_id": ""})
    assert resp.status_code == 400 and resp.get_json()["error"] == "labors_required"


def test_appointment_for_another_unit_is_linked_but_not_applied(client, cal_seed, mongo):
    """Запись сделана на другой юнит: её пресеты/бригада/заметка в WO не идут,
    но привязка к созданному WO всё равно ставится."""
    shop_db = mongo[SHOP_A_DB]
    planned_unit = _new_unit(mongo, cal_seed, "PRE-F1")
    actual_unit = _new_unit(mongo, cal_seed, "PRE-F2")
    m1, m2 = str(cal_seed["mechanic"]["_id"]), str(cal_seed["second"]["_id"])
    event_id = _create_event(client, cal_seed, planned_unit, 35, [m1, m2], presets=_preset_ref(cal_seed), title=NOTE)

    _login_mechanic(client)
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(
        cal_seed, actual_unit, calendar_event_id=event_id,
        labors=[{"description": "Tire rotation", "parts": []}],
    ))
    assert resp.status_code == 200, resp.get_data(as_text=True)

    wo = shop_db.work_orders.find_one({"_id": ObjectId(resp.get_json()["id"])})
    assert [b["labor"]["description"] for b in wo["labors"]] == ["Tire rotation"]
    assert wo["labors"][0]["labor"]["assigned_mechanics"] == []
    assert wo["labors"][0]["labor"]["issue_description"] == ""
    event = shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})
    assert event["work_order_id"] == wo["_id"] and event["status"] == "in_progress"


def test_mobile_office_note_as_job_line_is_not_duplicated(client, cal_seed, mongo):
    """Офисная форма без пресетов делает заметку строкой работы — сервер не
    кладёт её второй раз в описание проблемы (бригада назначается как обычно)."""
    shop_db = mongo[SHOP_A_DB]
    unit = _new_unit(mongo, cal_seed, "PRE-G")
    m1 = str(cal_seed["mechanic"]["_id"])
    event_id = _create_event(client, cal_seed, unit, 39, [m1], title=NOTE)
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit, calendar_event_id=event_id, labors=[
        {"description": NOTE, "hours": "1", "rate_code": "standard", "parts": []},
    ]))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    wo = shop_db.work_orders.find_one({"_id": ObjectId(resp.get_json()["id"])})
    assert wo["labors"][0]["labor"]["description"] == NOTE
    assert wo["labors"][0]["labor"]["issue_description"] == ""
    assert _crew(wo["labors"][0]) == {cal_seed["mechanic"]["_id"]: 100.0}
    assert shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})["work_order_id"] == wo["_id"]


def test_mobile_long_note_keeps_full_text_in_issue_description(client, cal_seed, mongo):
    """Заметка длиннее строки работы (JOB_LINE_MAX_LEN): строка получает её
    начало, полный текст — в описание проблемы, ничего не теряется."""
    from app.blueprints.work_orders.services.appointment_prefill import JOB_LINE_MAX_LEN

    assert len(LONG_NOTE) > JOB_LINE_MAX_LEN and LONG_NOTE == LONG_NOTE.strip()
    shop_db = mongo[SHOP_A_DB]
    unit = _new_unit(mongo, cal_seed, "PRE-H")
    event_id = _create_event(client, cal_seed, unit, 41, [str(cal_seed["mechanic"]["_id"])], title=LONG_NOTE)
    assert shop_db.calendar_events.find_one({"_id": ObjectId(event_id)})["title"] == LONG_NOTE

    _login_mechanic(client)
    resp = _json(client, "post", "/api/mobile/work_orders", _mobile_payload(cal_seed, unit, calendar_event_id=event_id))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    wo = shop_db.work_orders.find_one({"_id": ObjectId(resp.get_json()["id"])})
    assert len(wo["labors"]) == 1, wo["labors"]
    labor = wo["labors"][0]["labor"]
    assert labor["description"] == LONG_NOTE[:JOB_LINE_MAX_LEN]
    assert labor["issue_description"] == LONG_NOTE
    assert _crew(wo["labors"][0]) == {cal_seed["mechanic"]["_id"]: 100.0}
