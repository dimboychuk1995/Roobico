"""
Глубокая проверка подсчёта часов механиков (wo_time_logs):
несколько механиков на одном лейборе, переключения между лейборами,
сохранения менеджером из веба поверх затреканного времени, paid-инвариант,
целостность процентов и консистентность отчёта mechanic_hours.

Тесты утверждают ОЖИДАЕМО ПРАВИЛЬНОЕ поведение; упавший тест = найденный баг.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

M1_EMAIL = "deep.mech1@test.local"
M2_EMAIL = "deep.mech2@test.local"
M3_EMAIL = "deep.mech3@test.local"
PASSWORD = "password123"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def deep_seed(app, seed, mongo):
    """Три механика + клиент/юнит в магазине A."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    users = {}
    for email, first, last in (
        (M1_EMAIL, "Alfa", "Deep"),
        (M2_EMAIL, "Bravo", "Deep"),
        (M3_EMAIL, "Charlie", "Deep"),
    ):
        u = {
            "_id": ObjectId(),
            "email": email,
            "password_hash": generate_password_hash(PASSWORD),
            "first_name": first,
            "last_name": last,
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(shop_a["_id"])],
            "role": "mechanic",
            "created_at": now,
        }
        master.users.insert_one(u)
        users[email] = u

    customer = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Deep Time Fleet",
        "taxable": False,
        "is_active": True,
        "created_at": now,
    }
    shop_db.customers.insert_one(customer)

    unit = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "customer_id": customer["_id"],
        "vin": "DEEP1234567890000",
        "unit_number": "D-1",
        "is_active": True,
        "created_at": now,
    }
    shop_db.units.insert_one(unit)

    return {"users": users, "customer": customer, "unit": unit, "shop": shop_a}


def _login_as(app, email):
    c = app.test_client()
    resp = login(c, email=email, password=PASSWORD)
    assert resp.status_code in (200, 302)
    return c


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _create_wo(client, deep_seed, descriptions):
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(deep_seed["customer"]["_id"]),
        "unit_id": str(deep_seed["unit"]["_id"]),
        "labors": [{"description": d, "parts": []} for d in descriptions],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data


def _start(client, wo_id, labor_id):
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": wo_id, "labor_id": labor_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _stop(client):
    resp = _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _backdate_open_timer(shop_db, user_id, seconds):
    """Открытый таймер пользователя «идёт» уже `seconds` секунд."""
    log = shop_db.wo_time_logs.find_one({"user_id": user_id, "stopped_at": None})
    assert log, "no running timer to backdate"
    shop_db.wo_time_logs.update_one(
        {"_id": log["_id"]},
        {"$set": {"started_at": log["started_at"] - timedelta(seconds=seconds)}},
    )


def _wo(shop_db, wo_id):
    return shop_db.work_orders.find_one({"_id": ObjectId(wo_id)})


def _cleanup_wo(shop_db, wo_id):
    shop_db.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"is_active": False}})
    shop_db.wo_time_logs.delete_many({"work_order_id": ObjectId(wo_id)})


def _echo_web_save(owner_client, shop_db, wo_id, hours_override=None, drop_labor_ids=None,
                   hours_edited_ids=None):
    """Сохранение менеджером из веб-редактора: клиент шлёт ровно то, что
    отрисовано в форме (см. serializeBlocks в work_order_details.js)."""
    wo = _wo(shop_db, wo_id)
    labors = []
    for b in wo["labors"]:
        if drop_labor_ids and b["labor_id"] in drop_labor_ids:
            continue
        lab = b.get("labor") or {}
        hours = lab.get("hours") or 0
        if hours_override and b["labor_id"] in hours_override:
            hours = hours_override[b["labor_id"]]
        labors.append({
            "labor_id": b["labor_id"],
            "labor_description": lab.get("description") or "",
            "labor_hours": float(hours or 0),
            "hours_edited": bool(hours_edited_ids and b["labor_id"] in hours_edited_ids),
            "labor_rate_code": lab.get("rate_code") or "",
            "labor_full_total": 0,
            "assigned_mechanics": [
                {"user_id": str(a.get("user_id")), "name": a.get("name") or "",
                 "role": a.get("role") or "", "percent": a.get("percent") or 0}
                for a in (lab.get("assigned_mechanics") or [])
            ],
            "issue_description": lab.get("issue_description") or "",
            "parts": [],
        })
    resp = _post_json(owner_client, f"/work_orders/api/work_orders/{wo_id}/update",
                      {"labors": labors, "totals": {}, "save_status": "in_progress"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True, body
    return body


# ── сценарий пользователя: двое на одном лейборе, один переключается ──


def test_two_mechanics_one_labor_then_switch(app, deep_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    m2 = deep_seed["users"][M2_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    c2 = _login_as(app, M2_EMAIL)

    data = _create_wo(c1, deep_seed, ["Job One", "Job Two"])
    wo = _wo(shop_db, data["id"])
    l1, l2 = wo["labors"][0]["labor_id"], wo["labors"][1]["labor_id"]

    # Оба стартуют на L1. M1 работает час, M2 — 30 минут.
    _start(c1, data["id"], l1)
    _start(c2, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _backdate_open_timer(shop_db, m2["_id"], 1800)

    # M2 переключается на L2: его сессия на L1 закрывается auto_switch.
    body = _start(c2, data["id"], l2)
    assert body["stopped_previous"]["labor_id"] == l1
    assert 1800 <= body["stopped_previous"]["seconds"] <= 1830

    closed = shop_db.wo_time_logs.find_one(
        {"work_order_id": ObjectId(data["id"]), "labor_id": l1, "user_id": m2["_id"]})
    assert closed["stop_source"] == "auto_switch"

    # Промежуточное состояние: у L1 завершено только время M2 (M1 ещё идёт).
    wo = _wo(shop_db, data["id"])
    lab1 = wo["labors"][0]["labor"]
    assert lab1["hours"] == "0.5", f"L1 hours after switch: {lab1['hours']!r}"
    assert lab1["hours_source"] == "tracked"

    # M2 работает на L2 15 минут и стопается.
    _backdate_open_timer(shop_db, m2["_id"], 900)
    _stop(c2)
    wo = _wo(shop_db, data["id"])
    lab2 = wo["labors"][1]["labor"]
    assert lab2["hours"] == "0.25", f"L2 hours: {lab2['hours']!r}"
    assigned2 = lab2["assigned_mechanics"]
    assert [str(a["user_id"]) for a in assigned2] == [str(m2["_id"])]
    assert assigned2[0]["percent"] == 100.0

    # M1 стопается: L1 = 1ч (M1) + 0.5ч (M2) = 1.5ч, доли ~66.7/33.3.
    _stop(c1)
    wo = _wo(shop_db, data["id"])
    lab1 = wo["labors"][0]["labor"]
    assert float(lab1["hours"]) == pytest.approx(1.5, abs=0.02), lab1["hours"]
    assigned1 = {str(a["user_id"]): a["percent"] for a in lab1["assigned_mechanics"]}
    assert assigned1[str(m1["_id"])] == pytest.approx(66.67, abs=0.5)
    assert assigned1[str(m2["_id"])] == pytest.approx(33.33, abs=0.5)
    assert sum(assigned1.values()) == pytest.approx(100.0, abs=0.001)

    # Биллинг: labor base = часы × ставка (standard 100/ч).
    totals = wo["totals"]["labors"]
    assert float(totals[0]["labor"]) == pytest.approx(150.0, abs=2.5)
    assert float(totals[1]["labor"]) == pytest.approx(25.0, abs=1.0)

    _cleanup_wo(shop_db, data["id"])


def test_switch_to_other_wo_refreshes_previous_wo(app, deep_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)

    wo_a = _create_wo(c1, deep_seed, ["WO-A job"])
    wo_b = _create_wo(c1, deep_seed, ["WO-B job"])
    la = _wo(shop_db, wo_a["id"])["labors"][0]["labor_id"]
    lb = _wo(shop_db, wo_b["id"])["labors"][0]["labor_id"]

    _start(c1, wo_a["id"], la)
    _backdate_open_timer(shop_db, m1["_id"], 7200)
    # Переключение на другой WO должно закрыть сессию и обновить часы WO-A.
    _start(c1, wo_b["id"], lb)

    lab_a = _wo(shop_db, wo_a["id"])["labors"][0]["labor"]
    assert float(lab_a["hours"]) == pytest.approx(2.0, abs=0.02), lab_a["hours"]
    assert lab_a["hours_source"] == "tracked"
    assert [str(a["user_id"]) for a in lab_a["assigned_mechanics"]] == [str(m1["_id"])]

    _stop(c1)
    _cleanup_wo(shop_db, wo_a["id"])
    _cleanup_wo(shop_db, wo_b["id"])


# ── сохранение менеджером из веба поверх затреканных часов ──────────


def test_manager_web_save_preserves_hours_source(app, deep_seed, mongo):
    """Save из веб-редактора не должен терять маркер hours_source="tracked" —
    иначе последующие сессии механиков перестают обновлять часы."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Tracked job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)
    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert lab["hours"] == "1.0"
    assert lab["hours_source"] == "tracked"

    # Менеджер просто пересохраняет WO из веба (ничего не меняя).
    _echo_web_save(owner, shop_db, data["id"])

    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert lab.get("hours_source") == "tracked", (
        f"hours_source after web save: {lab.get('hours_source')!r} — "
        "маркер трекнутых часов потерян"
    )
    _cleanup_wo(shop_db, data["id"])


def test_tracked_hours_keep_accumulating_after_manager_save(app, deep_seed, mongo):
    """После Save менеджера новые сессии механика должны продолжать
    прибавляться к часам работы."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Long job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)
    assert _wo(shop_db, data["id"])["labors"][0]["labor"]["hours"] == "1.0"

    _echo_web_save(owner, shop_db, data["id"])

    # Механик работает ещё час.
    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)

    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert float(lab["hours"]) == pytest.approx(2.0, abs=0.02), (
        f"hours after 2nd session: {lab['hours']!r} — вторая сессия не учлась"
    )
    _cleanup_wo(shop_db, data["id"])


def test_manager_stale_save_does_not_regress_fresh_tracked_hours(app, deep_seed, mongo):
    """Менеджер открыл WO (в форме часы 1.0), механик тем временем натрекал
    до 2.0. Save устаревшей формы не должен откатывать трекнутые часы."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Stale form job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)
    assert _wo(shop_db, data["id"])["labors"][0]["labor"]["hours"] == "1.0"

    # Механик работает ещё час — в базе уже 2.0.
    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)
    assert float(_wo(shop_db, data["id"])["labors"][0]["labor"]["hours"]) == pytest.approx(2.0, abs=0.02)

    # Save формы со старым значением 1.0 (менеджер часы не трогал).
    _echo_web_save(owner, shop_db, data["id"], hours_override={l1: 1.0})

    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert float(lab["hours"]) == pytest.approx(2.0, abs=0.02), (
        f"hours after stale save: {lab['hours']!r} — трекнутые часы откатились"
    )
    _cleanup_wo(shop_db, data["id"])


def test_manager_deliberate_hours_edit_overrides_tracked(app, deep_seed, mongo):
    """Менеджер осознанно правит часы tracked-строки (hours_edited=true):
    его значение сохраняется, становится ручным и трекинг его больше
    не перетирает."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Adjusted job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)
    assert _wo(shop_db, data["id"])["labors"][0]["labor"]["hours"] == "1.0"

    # Менеджер ставит 3 часа руками.
    _echo_web_save(owner, shop_db, data["id"],
                   hours_override={l1: 3.0}, hours_edited_ids={l1})
    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert float(lab["hours"]) == 3.0
    assert (lab.get("hours_source") or "") == ""

    # Новая сессия механика ручные часы не трогает.
    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)
    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert float(lab["hours"]) == 3.0, lab["hours"]
    _cleanup_wo(shop_db, data["id"])


# ── новые лейборы из веб-редактора и трекинг ────────────────────────


def test_new_labor_added_from_web_gets_trackable_labor_id(app, deep_seed, mongo):
    """Блок, добавленный менеджером в веб-редакторе, приходит с labor_id=""
    — сервер обязан выдать ему стабильный labor_id, иначе на нём нельзя
    запустить таймер."""
    shop_db = mongo[SHOP_A_DB]
    c1 = _login_as(app, M1_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Original job"])
    wo = _wo(shop_db, data["id"])

    labors_payload = [{
        "labor_id": wo["labors"][0]["labor_id"],
        "labor_description": "Original job",
        "labor_hours": 0,
        "labor_rate_code": "standard",
        "labor_full_total": 0,
        "assigned_mechanics": [],
        "issue_description": "",
        "parts": [],
    }, {
        "labor_id": "",  # новый блок из веб-формы
        "labor_description": "Added from web",
        "labor_hours": 0,
        "labor_rate_code": "standard",
        "labor_full_total": 0,
        "assigned_mechanics": [],
        "issue_description": "",
        "parts": [],
    }]
    resp = _post_json(owner, f"/work_orders/api/work_orders/{data['id']}/update",
                      {"labors": labors_payload, "totals": {}, "save_status": "in_progress"})
    assert resp.get_json()["ok"] is True

    wo = _wo(shop_db, data["id"])
    new_block = wo["labors"][1]
    assert str(new_block.get("labor_id") or ""), (
        "у добавленного из веба лейбора нет labor_id — таймер на нём запустить нельзя"
    )

    # И таймер на нём реально стартует.
    body = _post_json(c1, "/work_orders/api/mechanic/timers/start", {
        "work_order_id": data["id"], "labor_id": str(new_block.get("labor_id") or "")})
    assert body.status_code == 200 and body.get_json()["ok"] is True, body.get_data(as_text=True)
    _stop(c1)
    _cleanup_wo(shop_db, data["id"])


# ── paid-инвариант ──────────────────────────────────────────────────


def test_stop_timer_after_paid_does_not_rewrite_paid_wo(app, deep_seed, mongo):
    """Таймер шёл, WO успели оплатить. Stop не должен менять часы/тоталы
    оплаченного WO (деньги зафиксированы оплатой)."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)

    data = _create_wo(c1, deep_seed, ["Paid race job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)

    wo_before = _wo(shop_db, data["id"])
    hours_before = wo_before["labors"][0]["labor"]["hours"]
    grand_before = (wo_before.get("totals") or {}).get("grand_total")
    assert hours_before == "1.0"

    # Вторая сессия идёт, офис в этот момент закрывает WO оплатой.
    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    shop_db.work_orders.update_one(
        {"_id": ObjectId(data["id"])}, {"$set": {"status": "paid"}})

    _stop(c1)  # время сессии должно записаться в логи…

    wo_after = _wo(shop_db, data["id"])
    # …но биллинг оплаченного WO меняться не должен.
    assert wo_after["labors"][0]["labor"]["hours"] == hours_before, (
        f"hours: {hours_before!r} -> {wo_after['labors'][0]['labor']['hours']!r} "
        "— оплаченный WO переписан после stop"
    )
    assert (wo_after.get("totals") or {}).get("grand_total") == grand_before, (
        f"grand_total: {grand_before!r} -> {(wo_after.get('totals') or {}).get('grand_total')!r}"
    )
    _cleanup_wo(shop_db, data["id"])


# ── проценты, аномалии, отчёт ───────────────────────────────────────


def test_three_way_split_percent_sums_to_100(app, deep_seed, mongo):
    from app.blueprints.work_orders.services.time_tracking import time_based_assignments

    shop_db = mongo[SHOP_A_DB]
    shop_id = deep_seed["shop"]["_id"]
    wo_id = ObjectId()
    now = _now()
    for u_email in (M1_EMAIL, M2_EMAIL, M3_EMAIL):
        u = deep_seed["users"][u_email]
        shop_db.wo_time_logs.insert_one({
            "shop_id": shop_id, "work_order_id": wo_id, "labor_id": "LX",
            "user_id": u["_id"], "user_name": u["first_name"],
            "started_at": now, "stopped_at": now, "seconds": 3600,
            "stop_source": "user", "created_at": now, "updated_at": now,
        })

    out = time_based_assignments(shop_db, shop_id, str(wo_id))
    percents = [a["percent"] for a in out["LX"]]
    assert sum(percents) == pytest.approx(100.0, abs=0.001), percents
    assert all(33.32 <= p <= 33.35 for p in percents), percents
    shop_db.wo_time_logs.delete_many({"work_order_id": wo_id})


def test_stop_closes_all_open_timers_of_user(app, deep_seed, mongo):
    """Гонка двух параллельных start могла оставить у юзера два открытых
    таймера. Stop обязан закрыть все — иначе «фантомный» таймер крутится
    вечно и раздувает часы."""
    shop_db = mongo[SHOP_A_DB]
    m3 = deep_seed["users"][M3_EMAIL]
    c3 = _login_as(app, M3_EMAIL)

    data = _create_wo(c3, deep_seed, ["Race job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c3, data["id"], l1)
    # Симуляция гонки: второй открытый лог того же юзера.
    open_log = shop_db.wo_time_logs.find_one({"user_id": m3["_id"], "stopped_at": None})
    dup = dict(open_log)
    dup["_id"] = ObjectId()
    shop_db.wo_time_logs.insert_one(dup)

    _stop(c3)
    still_open = list(shop_db.wo_time_logs.find({"user_id": m3["_id"], "stopped_at": None}))
    # cleanup до assert, чтобы не отравить другие тесты фантомным таймером
    shop_db.wo_time_logs.delete_many({"user_id": m3["_id"], "stopped_at": None})
    _cleanup_wo(shop_db, data["id"])
    assert not still_open, (
        f"после stop остались открытые таймеры: {len(still_open)} — "
        "фантомная сессия продолжит накручивать часы"
    )


def test_orphan_logs_after_labor_removed_from_web(app, deep_seed, mongo):
    """Менеджер удалил лейбор из веб-редактора, на котором было затрекано
    время: сводки/рефреш не должны падать, отчёт tracked-часов сохраняет
    время механика."""
    from app.blueprints.work_orders.services.mechanic_editor import refresh_time_derived_fields
    from app.blueprints.work_orders.services.time_tracking import (
        summarize_mechanic_hours, summarize_wo_time,
    )

    shop_db = mongo[SHOP_A_DB]
    shop = deep_seed["shop"]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Keep me", "Delete me"])
    wo = _wo(shop_db, data["id"])
    l_del = wo["labors"][1]["labor_id"]

    _start(c1, data["id"], l_del)
    _backdate_open_timer(shop_db, m1["_id"], 3600)
    _stop(c1)

    # Менеджер сохраняет WO без этого лейбора.
    _echo_web_save(owner, shop_db, data["id"], drop_labor_ids={l_del})
    wo = _wo(shop_db, data["id"])
    assert len(wo["labors"]) == 1

    # Ничего не падает; висячий бакет просто существует.
    summary = summarize_wo_time(shop_db, shop["_id"], data["id"])
    assert l_del in summary
    refresh_time_derived_fields(shop_db, shop, data["id"])

    # Механик своё время в отчёте не теряет.
    tracked = summarize_mechanic_hours(shop_db, shop["_id"])
    assert tracked[str(m1["_id"])]["seconds"] >= 3600
    _cleanup_wo(shop_db, data["id"])


def test_report_billed_vs_tracked_consistent_for_split_labor(app, deep_seed, mongo):
    """Отчёт mechanic_hours: billed = часы×процент, tracked = секунды из
    логов; для лейбора с двумя механиками цифры должны сходиться."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    m2 = deep_seed["users"][M2_EMAIL]
    c1 = _login_as(app, M1_EMAIL)
    c2 = _login_as(app, M2_EMAIL)
    owner = _login_as(app, "owner@test.local")

    data = _create_wo(c1, deep_seed, ["Split report job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 7200)
    _stop(c1)
    _start(c2, data["id"], l1)
    _backdate_open_timer(shop_db, m2["_id"], 3600)
    _stop(c2)

    # Переносим WO и логи в изолированное отчётное окно (май 2022).
    window = datetime(2022, 5, 10, 12, 0, 0)
    shop_db.work_orders.update_one(
        {"_id": ObjectId(data["id"])},
        {"$set": {"work_order_date": window, "created_at": window}})
    shop_db.wo_time_logs.update_many(
        {"work_order_id": ObjectId(data["id"])},
        {"$set": {"started_at": window, "stopped_at": window}})
    # Точные секунды без набежавших миллисекунд теста.
    shop_db.wo_time_logs.update_one(
        {"work_order_id": ObjectId(data["id"]), "user_id": m1["_id"]},
        {"$set": {"seconds": 7200}})
    shop_db.wo_time_logs.update_one(
        {"work_order_id": ObjectId(data["id"]), "user_id": m2["_id"]},
        {"$set": {"seconds": 3600}})
    from app.blueprints.work_orders.services.mechanic_editor import refresh_time_derived_fields
    refresh_time_derived_fields(shop_db, deep_seed["shop"], data["id"])

    resp = owner.get("/reports/api/standard/mechanic_hours"
                     "?date_preset=custom&date_from=2022-05-01&date_to=2022-05-31")
    assert resp.status_code == 200
    rd = resp.get_json()["report_data"]
    rows = {r["mechanic_name"]: r for r in rd["rows"]}

    assert rows["Alfa Deep"]["tracked_hours"] == 2.0
    assert rows["Bravo Deep"]["tracked_hours"] == 1.0
    # billed: 3ч × 66.67% = 2.0, 3ч × 33.33% = 1.0
    assert rows["Alfa Deep"]["total_hours"] == pytest.approx(2.0, abs=0.01)
    assert rows["Bravo Deep"]["total_hours"] == pytest.approx(1.0, abs=0.01)
    _cleanup_wo(shop_db, data["id"])


def test_restart_same_labor_accumulates_without_loss(app, deep_seed, mongo):
    """Start на том же лейборе при идущем таймере: старая сессия закрывается
    auto_switch, секунды не теряются, часы = сумма сессий."""
    shop_db = mongo[SHOP_A_DB]
    m1 = deep_seed["users"][M1_EMAIL]
    c1 = _login_as(app, M1_EMAIL)

    data = _create_wo(c1, deep_seed, ["Restart job"])
    l1 = _wo(shop_db, data["id"])["labors"][0]["labor_id"]

    _start(c1, data["id"], l1)
    _backdate_open_timer(shop_db, m1["_id"], 1800)
    body = _start(c1, data["id"], l1)  # повторный start на том же лейборе
    assert body["stopped_previous"]["labor_id"] == l1
    _backdate_open_timer(shop_db, m1["_id"], 1800)
    _stop(c1)

    lab = _wo(shop_db, data["id"])["labors"][0]["labor"]
    assert float(lab["hours"]) == pytest.approx(1.0, abs=0.02), lab["hours"]
    logs = list(shop_db.wo_time_logs.find({"work_order_id": ObjectId(data["id"])}))
    assert len(logs) == 2
    assert all(log["stopped_at"] is not None for log in logs)
    _cleanup_wo(shop_db, data["id"])
