"""
Живые имена в общих местах WO (дизайн §2.2/§2.3, A2): после переименования
пользователя подписи берутся по user_id из master.users, снимок в документе
— лишь запас (удалённый аккаунт; id чужого тенанта не резолвится).

Покрыто: сводка времени WO (users / running_users) — страница WO офиса,
live_state, детали механика (веб и приложение), «Tracked» в приложении
менеджера; назначения строк на странице WO офиса; список сессий (модалка
Edit mechanic hours); карточки «Active now»; ответы таймеров (start /
current / stop); сводка трекнутых часов для отчёта; имя в назначениях по
времени (бывший механик) — и через stop, и через синк после Save офиса;
сообщение «WO уже в работе по этому юниту». Хук переименования в Settings
→ Users → Edit: снимки переписываются без updated_at / labors_rev, без смены
имени рефреш не зовётся, сбой рефреша правку не ломает.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MASTER, TEST_MONGO_URI, get_csrf_token, login

WO_NUMBER = 98301  # 98xxx — номера модулей test_user_names_*
PASSWORD = "password123"
OLEG_EMAIL = "oleg.shared-names@test.local"
NADIA_EMAIL = "nadia.shared-names@test.local"

OLEG_ID = ObjectId()   # механик, переименован: снимки «Oleg Oldname» / «Oleg Midname»
NADIA_ID = ObjectId()  # механик, переименована: снимок «Nadia Oldname», таймер идёт
RITA_ID = ObjectId()   # бывший механик: аккаунт неактивен, в назначаемых её нет
GONE_ID = ObjectId()   # аккаунта нет вовсе — только снимок «Gone Person»
YURI_ID = ObjectId()   # пользователь ЧУЖОГО тенанта: его имя не должно утечь

OLEG_LIVE = "Oleg Livename"
NADIA_LIVE = "Nadia Livename"
RITA_LIVE = "Rita Retired"
YURI_LIVE = "Yuri Foreignlive"
STALE = ("Oleg Oldname", "Oleg Midname", "Nadia Oldname", "Rita Oldname")
# updated_at WO «до»: pymongo отдаёт наивный UTC — сравниваем так же.
STAMP = datetime(2026, 1, 2, 3, 4, 5)
_PASSWORD_HASH = generate_password_hash(PASSWORD)
_ORIGINAL_NAMES = {
    OLEG_ID: ("Oleg", "Livename"),
    NADIA_ID: ("Nadia", "Livename"),
    RITA_ID: ("Rita", "Retired"),
}


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


def _user(_id, tenant, shop, email, first, last, *, active=True):
    return {
        "_id": _id, "email": email, "password_hash": _PASSWORD_HASH,
        "first_name": first, "last_name": last, "name": f"{first} {last}",
        "is_active": active, "tenant_id": tenant["_id"], "shop_ids": [str(shop["_id"])],
        "role": "mechanic", "created_at": datetime.now(timezone.utc),
    }


@pytest.fixture(scope="module")
def people(app, seed, mongo):
    """Роль mechanic; механики Oleg и Nadia, бывший механик Rita (неактивна),
    механик тенанта B; свой клиент с юнитом (проверка «юнит уже в работе»
    не цепляется за чужие WO)."""
    from app.constants.permissions import build_default_roles

    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = datetime.now(timezone.utc)

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    mongo[TENANT_A_DB].roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    users = [
        _user(OLEG_ID, seed["tenant_a"], shop_a, OLEG_EMAIL, "Oleg", "Livename"),
        _user(NADIA_ID, seed["tenant_a"], shop_a, NADIA_EMAIL, "Nadia", "Livename"),
        _user(RITA_ID, seed["tenant_a"], shop_a, "rita.shared-names@test.local", "Rita", "Retired", active=False),
        _user(YURI_ID, seed["tenant_b"], seed["shop_b"], "yuri.shared-names@test.local", "Yuri", "Foreignlive"),
    ]
    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Shared Names Fleet", "taxable": False, "is_active": True, "created_at": now,
    }
    unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"], "customer_id": customer["_id"],
        "unit_number": "SN-1", "vin": "SHAREDNAMES000001", "mileage": 1000, "is_active": True, "created_at": now,
    }
    # try/finally: упавшая на середине подготовка не оставляет данных.
    try:
        master.users.insert_many(users)
        shop_db.customers.insert_one(customer)
        shop_db.units.insert_one(unit)
        yield {"shop": shop_a, "tenant": seed["tenant_a"], "customer": customer, "unit": unit}
    finally:
        master.users.delete_many({"_id": {"$in": [u["_id"] for u in users]}})
        shop_db.units.delete_one({"_id": unit["_id"]})
        shop_db.customers.delete_one({"_id": customer["_id"]})


def _assigned(user_id, name, percent):
    return {"user_id": user_id, "name": name, "role": "mechanic", "percent": percent}


def _block(labor_id, description, hours, assigned):
    return {
        "labor_id": labor_id,
        "labor": {
            "description": description, "hours": hours, "rate_code": "standard",
            "assigned_mechanics": assigned, "issue_description": "",
        },
        # Блочное зеркало назначений (легаси-форма) — тоже снимок.
        "assigned_mechanics": [dict(a) for a in assigned],
        "parts": [],
    }


@pytest.fixture()
def wo(people, mongo):
    """WO в работе со снимками старых имён: назначения строк (id и строкой, и
    ObjectId) + блочное зеркало, отметка Done, тайм-логи (завершённые и
    идущий таймер Nadia), запись календаря с бригадой."""
    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    shop = people["shop"]
    now = datetime.now(timezone.utc)

    doc = {
        "_id": ObjectId(), "shop_id": shop["_id"], "wo_number": WO_NUMBER,
        "customer_id": people["customer"]["_id"], "unit_id": people["unit"]["_id"],
        "status": "in_progress", "is_active": True, "mechanic_done": False, "manager_confirmed": False,
        "work_order_date": now, "created_at": now, "updated_at": STAMP, "labors_rev": 3,
        "labors": [
            _block("L1", "Brake job", "1.5", [
                _assigned(str(OLEG_ID), "Oleg Oldname", 60), _assigned(NADIA_ID, "Nadia Oldname", 40),
            ]),
            _block("L2", "Legacy job", "1", [
                _assigned(GONE_ID, "Gone Person", 50), _assigned(YURI_ID, "Yuri Snapshot", 50),
            ]),
            _block("L3", "Retired job", "", []),
        ],
        "mechanic_done_marks": [{"user_id": NADIA_ID, "name": "Nadia Oldname", "at": now}],
        "totals": {
            "labor_total": 250.0, "parts_total": 0.0, "sales_tax_total": 0.0, "grand_total": 250.0,
            "is_taxable": False, "labors": [{"labor": 150.0}, {"labor": 100.0}, {"labor": 0.0}],
        },
    }

    def _log(labor_id, user_id, snapshot, started, seconds=None):
        running = seconds is None
        return {
            "shop_id": shop["_id"], "tenant_id": shop["tenant_id"], "work_order_id": doc["_id"],
            "wo_number": WO_NUMBER, "labor_id": labor_id, "user_id": user_id, "user_name": snapshot,
            "started_at": started, "stopped_at": None if running else started + timedelta(seconds=seconds),
            "seconds": None if running else seconds, "stop_source": None if running else "user",
            "created_at": started, "updated_at": started,
        }

    logs = [
        _log("L1", OLEG_ID, "Oleg Oldname", now - timedelta(hours=5), 3600),
        _log("L1", OLEG_ID, "Oleg Midname", now - timedelta(hours=3), 1800),
        _log("L1", NADIA_ID, "Nadia Oldname", now - timedelta(minutes=30)),  # таймер идёт
        _log("L2", GONE_ID, "Gone Person", now - timedelta(hours=4), 600),
        _log("L2", YURI_ID, "Yuri Snapshot", now - timedelta(hours=4), 900),
        _log("L3", RITA_ID, "Rita Oldname", now - timedelta(hours=6), 1200),
    ]
    # Запись календаря — далеко в будущем, чтобы не попасть в недели других модулей.
    event = {
        "_id": ObjectId(), "shop_id": shop["_id"], "title": "Shared names appointment", "status": "scheduled",
        "start_time": datetime(2032, 6, 7, 9, 0), "end_time": datetime(2032, 6, 7, 11, 0),
        "mechanics": [{"id": str(OLEG_ID), "name": "Oleg Oldname"}, {"id": str(NADIA_ID), "name": "Nadia Oldname"}],
        "mechanic_ids": [str(OLEG_ID), str(NADIA_ID)],
        "mechanic_id": str(OLEG_ID), "mechanic_name": "Oleg Oldname",
        "created_at": now, "updated_at": STAMP,
    }
    try:
        shop_db.work_orders.insert_one(doc)
        shop_db.wo_time_logs.insert_many(logs)
        shop_db.calendar_events.insert_one(event)
        yield {"_id": doc["_id"], "event_id": event["_id"]}
    finally:
        shop_db.work_orders.delete_one({"_id": doc["_id"]})
        shop_db.wo_time_logs.delete_many({"work_order_id": doc["_id"]})
        shop_db.calendar_events.delete_one({"_id": event["_id"]})
        shop_db.wo_edit_views.delete_many({"work_order_id": doc["_id"]})
        # Тесты переименовывают людей — вернуть исходные имена.
        for user_id, (first, last) in _ORIGINAL_NAMES.items():
            master.users.update_one(
                {"_id": user_id},
                {"$set": {"first_name": first, "last_name": last, "name": f"{first} {last}"}, "$unset": {"phone": ""}},
            )


# ── helpers ──────────────────────────────────────────────────

def _script_json(html: str, element_id: str):
    match = re.search(rf'<script type="application/json" id="{element_id}">(.*?)</script>', html, re.S)
    assert match, f"#{element_id} not found on the page"
    return json.loads(match.group(1))


def _names(people_list):
    return [p["name"] for p in people_list]


def _login_mechanic(app, email=OLEG_EMAIL):
    c = app.test_client()
    assert login(c, email=email, password=PASSWORD).status_code == 302
    return c


def _post_json(client, url, payload):
    return client.post(url, json=payload, headers={"X-CSRFToken": get_csrf_token(client)})


def _without_app_context(fn):
    """Вызов в отдельном потоке: контексты Flask потоколокальны, так что там
    гарантированно нет app context (голый вызов из скрипта/теста)."""
    box: dict = {}

    def _run():
        try:
            box["result"] = fn()
        except BaseException as exc:  # пробрасываем в тест
            box["error"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


# ── сводка времени WO: страница офиса, live_state, механик, приложение ──

def test_office_details_page_shows_live_names(client, wo):
    """Страница WO офиса: назначения строк («Mechanics: …», модалка
    стоимости) и сводка времени (разбивка по людям, «X working») — живые
    имена; удалённый аккаунт и id чужого тенанта — снимок."""
    login(client)
    resp = client.get(f"/work_orders/details?work_order_id={wo['_id']}")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    html = resp.get_data(as_text=True)

    labors = {b["labor_id"]: b["labor"] for b in _script_json(html, "workOrderInitialData")}
    assert _names(labors["L1"]["assigned_mechanics"]) == [OLEG_LIVE, NADIA_LIVE]
    assert [m["percent"] for m in labors["L1"]["assigned_mechanics"]] == [60, 40]
    assert [m["user_id"] for m in labors["L1"]["assigned_mechanics"]] == [str(OLEG_ID), str(NADIA_ID)]
    assert _names(labors["L2"]["assigned_mechanics"]) == ["Gone Person", "Yuri Snapshot"]

    summary = _script_json(html, "woTimeSummary")
    assert {uid: u["user_name"] for uid, u in summary["L1"]["users"].items()} == {
        str(OLEG_ID): OLEG_LIVE, str(NADIA_ID): NADIA_LIVE,
    }
    assert summary["L1"]["users"][str(OLEG_ID)]["seconds"] == 5400
    assert [u["user_name"] for u in summary["L1"]["running_users"]] == [NADIA_LIVE]
    assert summary["L2"]["users"][str(GONE_ID)]["user_name"] == "Gone Person"
    assert summary["L2"]["users"][str(YURI_ID)]["user_name"] == "Yuri Snapshot"
    # Неактивный (бывший) механик — тоже по живому имени: история не теряет людей.
    assert summary["L3"]["users"][str(RITA_ID)]["user_name"] == RITA_LIVE

    for stale in STALE + (YURI_LIVE,):
        assert stale not in html, stale


def test_live_state_poll_returns_live_names(client, wo):
    login(client)
    data = client.get(f"/work_orders/api/work_orders/{wo['_id']}/live_state").get_json()
    assert data["ok"] is True
    summary = data["time_summary"]
    assert summary["L1"]["users"][str(OLEG_ID)]["user_name"] == OLEG_LIVE
    assert [u["user_name"] for u in summary["L1"]["running_users"]] == [NADIA_LIVE]
    assert summary["L3"]["users"][str(RITA_ID)]["user_name"] == RITA_LIVE


def test_mechanic_details_show_live_names_of_who_is_working(app, wo):
    """«Nadia working» у механика — веб-страница и приложение (одна форма)."""
    oleg = _login_mechanic(app)
    for url in (f"/work_orders/api/mechanic/work_orders/{wo['_id']}", f"/api/mobile/work_orders/{wo['_id']}"):
        data = oleg.get(url).get_json()
        assert data["ok"] is True, data
        labors = {row["labor_id"]: row for row in data["labors"]}
        running = labors["L1"]["time"]["running_users"]
        assert [(u["user_id"], u["user_name"], u["mine"]) for u in running] == [(str(NADIA_ID), NADIA_LIVE, False)]


def test_mobile_manager_tracked_label_uses_live_names(client, wo):
    """Приложение менеджера: «Tracked 1:30 — Oleg … 1:30, Nadia …» — живые
    имена; удалённый аккаунт и чужой тенант — снимок."""
    login(client)
    data = client.get(f"/api/mobile/work_orders/{wo['_id']}").get_json()
    assert data["ok"] is True, data
    labels = {row["labor_desc"]: row.get("tracked_label") or "" for row in data["labors"]}
    assert OLEG_LIVE in labels["Brake job"] and NADIA_LIVE in labels["Brake job"]
    assert "Gone Person" in labels["Legacy job"] and "Yuri Snapshot" in labels["Legacy job"]
    for label in labels.values():
        for stale in STALE + (YURI_LIVE,):
            assert stale not in label, (stale, label)


# ── сессии, «Active now», ответы таймеров ────────────────────

def test_time_sessions_list_uses_live_names(client, wo):
    """Модалка Edit mechanic hours: механик каждой сессии — живое имя."""
    login(client)
    data = client.get(f"/work_orders/api/work_orders/{wo['_id']}/time_logs").get_json()
    assert data["ok"] is True
    by_user: dict = {}
    for session in data["sessions"]:
        by_user.setdefault(session["user_id"], set()).add(session["user_name"])
    assert by_user == {
        str(OLEG_ID): {OLEG_LIVE},          # две сессии под разными снимками — одно имя
        str(NADIA_ID): {NADIA_LIVE},
        str(RITA_ID): {RITA_LIVE},
        str(GONE_ID): {"Gone Person"},
        str(YURI_ID): {"Yuri Snapshot"},
    }


def test_active_timer_cards_use_live_names(client, wo):
    login(client)
    items = client.get("/work_orders/api/mechanic/timers/active").get_json()["items"]
    ours = [(i["labor_id"], i["user_id"], i["user_name"]) for i in items if i["work_order_id"] == str(wo["_id"])]
    assert ours == [("L1", str(NADIA_ID), NADIA_LIVE)]


def test_timer_responses_follow_a_rename_made_while_the_timer_runs(app, wo, mongo):
    """Таймер стартовал под одним именем, механика переименовали, пока он шёл
    (снимок в логе — прежнее имя): current / stop и сводка отдают новое. Пересчёт
    после stop назначает строку бывшему механику по живому имени, а не по
    снимку из тайм-лога."""
    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    oleg = _login_mechanic(app)

    resp = _post_json(oleg, "/work_orders/api/mechanic/timers/start", {"work_order_id": str(wo["_id"]), "labor_id": "L3"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["timer"]["user_name"] == OLEG_LIVE

    master.users.update_one({"_id": OLEG_ID}, {"$set": {"last_name": "Renamed", "name": "Oleg Renamed"}})
    assert oleg.get("/work_orders/api/mechanic/timers/current").get_json()["timer"]["user_name"] == "Oleg Renamed"

    body = _post_json(oleg, "/work_orders/api/mechanic/timers/stop", {}).get_json()
    assert body["ok"] is True, body
    assert body["timer"]["user_name"] == "Oleg Renamed"
    assert body["time_summary"]["L1"]["users"][str(OLEG_ID)]["user_name"] == "Oleg Renamed"
    # Снимок в самом логе не переписывался (переименование мимо Settings) —
    # новое имя приходит с чтения.
    log = shop_db.wo_time_logs.find_one({"work_order_id": wo["_id"], "labor_id": "L3", "user_id": OLEG_ID})
    assert log["user_name"] == OLEG_LIVE

    # Пересчёт после stop: на L3 завершённое время Rita (бывший механик, её
    # нет в списке назначаемых) — имя живое, не «Rita Oldname» из тайм-лога.
    # (Секунды самого Oleg могут быть 0 или 1-2 — от них проверка не зависит.)
    stored = shop_db.work_orders.find_one({"_id": wo["_id"]})
    l3 = next(b for b in stored["labors"] if b["labor_id"] == "L3")
    assigned = {str(a["user_id"]): a["name"] for a in l3["labor"]["assigned_mechanics"]}
    assert assigned[str(RITA_ID)] == RITA_LIVE
    assert "Rita Oldname" not in assigned.values()


# ── сервисы: отчётная сводка, назначения по времени ──────────

def test_out_of_request_callers_pass_the_shop_tenant(app, wo, mongo, people):
    """Скрипт / крон (app context без запроса и сессии): тенант магазина
    передаётся явно — имена живые; без него резолвить не по чему — снимки,
    причём у человека — самый свежий, а не первой сессии."""
    from app.blueprints.work_orders.services.time_tracking import list_wo_time_sessions, summarize_wo_time

    shop_db = mongo[SHOP_A_DB]
    args = (shop_db, people["shop"]["_id"], wo["_id"])
    tenant_values = [people["tenant"]["_id"]]
    with app.app_context():
        summary = summarize_wo_time(*args, tenant_values=tenant_values)
        sessions = list_wo_time_sessions(*args, tenant_values=tenant_values)
        no_tenant = summarize_wo_time(*args)
    assert summary["L1"]["users"][str(OLEG_ID)]["user_name"] == OLEG_LIVE
    assert [u["user_name"] for u in summary["L1"]["running_users"]] == [NADIA_LIVE]
    assert {s["user_name"] for s in sessions if s["user_id"] == str(OLEG_ID)} == {OLEG_LIVE}
    assert no_tenant["L1"]["users"][str(OLEG_ID)]["user_name"] == "Oleg Midname"
    assert [u["user_name"] for u in no_tenant["L1"]["running_users"]] == ["Nadia Oldname"]


def test_tracked_hours_summary_uses_live_names(app, wo, mongo, people):
    from app.blueprints.work_orders.services.time_tracking import summarize_mechanic_hours

    shop_db = mongo[SHOP_A_DB]
    args = (shop_db, people["shop"]["_id"])
    tenant_values = [people["tenant"]["_id"]]
    with app.app_context():
        out = summarize_mechanic_hours(*args, tenant_values=tenant_values)
    assert out[str(OLEG_ID)] == {"user_name": OLEG_LIVE, "seconds": 5400}
    assert out[str(RITA_ID)] == {"user_name": RITA_LIVE, "seconds": 1200}
    assert out[str(GONE_ID)]["user_name"] == "Gone Person"
    assert out[str(YURI_ID)]["user_name"] == "Yuri Snapshot"
    assert str(NADIA_ID) not in out  # идущая сессия в отчётную сводку не входит

    # Голый вызов без приложения не падает — остаются снимки.
    bare = _without_app_context(lambda: summarize_mechanic_hours(*args, tenant_values=tenant_values))
    assert bare[str(OLEG_ID)]["user_name"] in ("Oleg Oldname", "Oleg Midname")
    assert bare[str(RITA_ID)]["user_name"] == "Rita Oldname"


def test_time_based_assignments_name_fallback_is_live(app, wo, mongo, people):
    from app.blueprints.work_orders.services.time_tracking import _log_payload, time_based_assignments

    shop_db = mongo[SHOP_A_DB]
    args = (shop_db, people["shop"]["_id"], wo["_id"])
    tenant_values = [people["tenant"]["_id"]]
    with app.app_context():
        live = time_based_assignments(*args, tenant_values=tenant_values)
    assert _names(live["L3"]) == [RITA_LIVE]
    assert _names(live["L1"]) == [OLEG_LIVE]  # идущий таймер Nadia в назначение не входит
    assert _names(live["L2"]) == ["Yuri Snapshot", "Gone Person"]  # по убыванию времени; снимки

    bare = _without_app_context(lambda: time_based_assignments(*args, tenant_values=tenant_values))
    assert _names(bare["L3"]) == ["Rita Oldname"]

    # Форматтер сессии: готовые имена — живое имя, без app context — снимок.
    log = shop_db.wo_time_logs.find_one({"work_order_id": wo["_id"], "user_id": RITA_ID})
    assert _log_payload(log, {str(RITA_ID): {"name": RITA_LIVE}})["user_name"] == RITA_LIVE
    assert _without_app_context(lambda: _log_payload(log))["user_name"] == "Rita Oldname"


def test_assignment_sync_after_office_save_keeps_live_names(app, wo, mongo, people):
    """Синк назначений по времени (после Save офиса): бывший механик
    получает живое имя; повторный синк ничего не пишет и ревизию не двигает."""
    from app.blueprints.work_orders.services import time_tracking
    from app.blueprints.work_orders.services.lookups import get_assignable_mechanics

    shop_db = mongo[SHOP_A_DB]
    shop = people["shop"]
    with app.app_context():
        mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
        assert str(RITA_ID) not in mechanics_by_id
        assert time_tracking.sync_labor_assignments_from_time(
            shop_db, shop, wo["_id"], mechanics_by_id=mechanics_by_id,
        ) is True
        rev = shop_db.work_orders.find_one({"_id": wo["_id"]})["labors_rev"]
        assert time_tracking.sync_labor_assignments_from_time(
            shop_db, shop, wo["_id"], mechanics_by_id=mechanics_by_id,
        ) is False

    stored = shop_db.work_orders.find_one({"_id": wo["_id"]})
    assert stored["labors_rev"] == rev
    assigned = {b["labor_id"]: b["labor"]["assigned_mechanics"] for b in stored["labors"]}
    assert [(str(a["user_id"]), a["name"], a["role"]) for a in assigned["L3"]] == [(str(RITA_ID), RITA_LIVE, "")]
    assert [(a["name"], a["role"]) for a in assigned["L1"]] == [(OLEG_LIVE, "mechanic")]
    assert _names(assigned["L2"]) == ["Yuri Snapshot", "Gone Person"]


# ── «юнит уже в работе» ──────────────────────────────────────

def test_unit_in_work_message_names_each_person_once_by_live_name(app, wo, mongo, people):
    shop_db = mongo[SHOP_A_DB]
    shop = people["shop"]
    now = datetime.now(timezone.utc)
    # Дубль открытого лога Nadia (гонка двух start) под другим снимком +
    # идущий таймер удалённого аккаунта.
    base = {"shop_id": shop["_id"], "work_order_id": wo["_id"], "wo_number": WO_NUMBER, "labor_id": "L2",
            "stopped_at": None, "seconds": None, "started_at": now, "created_at": now, "updated_at": now}
    shop_db.wo_time_logs.insert_many([
        {**base, "user_id": NADIA_ID, "user_name": "Nadia Stalename"},
        {**base, "user_id": GONE_ID, "user_name": "Gone Person"},
    ])

    oleg = _login_mechanic(app)
    data = oleg.get(f"/work_orders/api/mechanic/units/{people['unit']['_id']}/active_work_order").get_json()
    assert data["ok"] is True
    payload = data["work_order"]
    assert payload["id"] == str(wo["_id"])
    assert sorted(payload["working_now"]) == sorted([NADIA_LIVE, "Gone Person"])
    assert NADIA_LIVE in data["message"] and "Gone Person" in data["message"]
    for stale in ("Nadia Oldname", "Nadia Stalename"):
        assert stale not in data["message"]


# ── хук переименования в Settings → Users → Edit ─────────────

def _edit_form(**overrides):
    form = {
        "first_name": "Oleg", "last_name": "Livename", "email": OLEG_EMAIL, "phone": "",
        "role": "mechanic", "is_active": "on",
    }
    form.update(overrides)
    return form


def _post_edit(client, form):
    return client.post(f"/settings/users/{OLEG_ID}/edit", data={**form, "csrf_token": get_csrf_token(client)})


def _flashes(client):
    with client.session_transaction() as sess:
        return list(sess.get("_flashes") or [])


def test_rename_refreshes_name_snapshots_without_touching_the_wo(client, wo, mongo):
    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    before = shop_db.work_orders.find_one({"_id": wo["_id"]})

    login(client)
    resp = _post_edit(client, _edit_form(first_name="Olezhka", last_name="Newname"))
    assert resp.status_code == 302
    assert ("success", "User updated successfully.") in _flashes(client)
    user = master.users.find_one({"_id": OLEG_ID})
    assert (user["first_name"], user["last_name"], user["name"]) == ("Olezhka", "Newname", "Olezhka Newname")

    after = shop_db.work_orders.find_one({"_id": wo["_id"]})
    l1 = after["labors"][0]
    # Снимки переименованного — новые; коллеги (Nadia) не тронуты.
    assert _names(l1["labor"]["assigned_mechanics"]) == ["Olezhka Newname", "Nadia Oldname"]
    assert _names(l1["assigned_mechanics"]) == ["Olezhka Newname", "Nadia Oldname"]
    assert after["mechanic_done_marks"] == before["mechanic_done_marks"]
    oleg_logs = list(shop_db.wo_time_logs.find({"work_order_id": wo["_id"], "user_id": OLEG_ID}))
    assert len(oleg_logs) == 2 and {log["user_name"] for log in oleg_logs} == {"Olezhka Newname"}
    assert shop_db.wo_time_logs.find_one({"work_order_id": wo["_id"], "user_id": NADIA_ID})["user_name"] == "Nadia Oldname"
    event = shop_db.calendar_events.find_one({"_id": wo["event_id"]})
    assert _names(event["mechanics"]) == ["Olezhka Newname", "Nadia Oldname"]
    assert event["mechanic_name"] == "Olezhka Newname"
    # Не правка WO: updated_at и ревизия те же — живой опрос открытых страниц
    # не перезагружает их, CAS чужих сохранений не спотыкается.
    assert after["updated_at"] == before["updated_at"] == STAMP
    assert after["labors_rev"] == before["labors_rev"] == 3
    assert event["updated_at"] == STAMP


def test_edit_without_rename_skips_snapshot_refresh(client, wo, mongo, people, monkeypatch):
    from app.blueprints.settings.users import routes as users_routes

    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]
    calls = []
    monkeypatch.setattr(users_routes, "refresh_user_name_snapshots", lambda *args, **kwargs: calls.append(args) or {})

    login(client)
    assert _post_edit(client, _edit_form(phone="555-0100")).status_code == 302
    assert master.users.find_one({"_id": OLEG_ID})["phone"] == "555-0100"
    assert calls == []
    log = shop_db.wo_time_logs.find_one({"work_order_id": wo["_id"], "user_id": OLEG_ID, "seconds": 3600})
    assert log["user_name"] == "Oleg Oldname"

    # Переименование — рефреш зовётся: тенант пользователя, его id, новое имя.
    assert _post_edit(client, _edit_form(last_name="Newname")).status_code == 302
    assert len(calls) == 1
    _master, tenant_id, user_id, new_name = calls[0]
    assert (tenant_id, user_id, new_name) == (people["tenant"]["_id"], OLEG_ID, "Oleg Newname")


def test_snapshot_refresh_failure_does_not_break_the_edit(client, wo, mongo, monkeypatch, caplog):
    """Рефреш снимков упал — правка пользователя сохранена, ошибка в логе;
    экраны всё равно показывают новое имя (живое чтение не зависит от рефреша)."""
    from app.blueprints.settings.users import routes as users_routes

    def _boom(*args, **kwargs):
        raise RuntimeError("snapshot refresh exploded")

    monkeypatch.setattr(users_routes, "refresh_user_name_snapshots", _boom)
    master = mongo[TEST_MASTER]
    shop_db = mongo[SHOP_A_DB]

    login(client)
    with caplog.at_level(logging.ERROR):
        resp = _post_edit(client, _edit_form(first_name="Olezhka"))
    assert resp.status_code == 302
    assert ("success", "User updated successfully.") in _flashes(client)
    assert master.users.find_one({"_id": OLEG_ID})["name"] == "Olezhka Livename"
    assert any("failed to refresh name snapshots" in r.getMessage() for r in caplog.records)

    # Снимок в WO остался старым, но страница WO показывает живое имя.
    assert shop_db.work_orders.find_one({"_id": wo["_id"]})["labors"][0]["labor"]["assigned_mechanics"][0]["name"] == "Oleg Oldname"
    html = client.get(f"/work_orders/details?work_order_id={wo['_id']}").get_data(as_text=True)
    labors = {b["labor_id"]: b["labor"] for b in _script_json(html, "workOrderInitialData")}
    assert _names(labors["L1"]["assigned_mechanics"])[0] == "Olezhka Livename"
