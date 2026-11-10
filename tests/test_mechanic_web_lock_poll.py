"""
Веб-страница механика (/mechanic/work_orders/<id>, mechanic_wo.js): контракт
живого обновления для WO, закрытого механику.

Опрос страницы (pollRev) узнаёт замок из /rev — locked: true у WO,
подтверждённого менеджером, и у сметы — и тогда детали не перечитывает: они
механику отказывают (403 wo_confirmed / 400 estimate_locked_for_mechanics),
а раньше страница делала GET деталей каждые 20 с и каждый раз показывала окно
с ошибкой. Причину замка страница показывает баннером #mechLockNotice
(шаблон) и выключает Save; locked: false — сигнал перечитать WO.

Сама логика страницы — JS (в репозитории нет JS-раннера); здесь закреплены
серверные ответы, на которые она опирается, и элементы шаблона.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECH_EMAIL = "mech-lockpoll@test.local"
MECH_PASSWORD = "password123"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def lp(app, seed, mongo):
    """Механик и клиент/юнит в магазине A."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    user = {
        "_id": ObjectId(), "email": MECH_EMAIL, "password_hash": generate_password_hash(MECH_PASSWORD),
        "first_name": "Lena", "last_name": "Lockpoll", "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop["_id"])],
        "role": "mechanic", "created_at": now,
    }
    master.users.insert_one(user)
    customer = {
        "_id": ObjectId(), "shop_id": shop["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Lockpoll Fleet", "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)
    unit = {
        "_id": ObjectId(), "shop_id": shop["_id"], "customer_id": customer["_id"],
        "vin": "LOCKPOLL123456789", "unit_number": "LP-1", "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)

    yield {"user": user, "customer": customer, "unit": unit}

    shop_db.work_orders.update_many({"customer_id": customer["_id"]}, {"$set": {"is_active": False}})
    shop_db.wo_time_logs.delete_many({"user_id": user["_id"]})
    # Механик модуля не должен попадать в списки механиков следующих модулей.
    master.users.update_one({"_id": user["_id"]}, {"$set": {"is_active": False}})


def _post(client, url, payload):
    return client.post(url, json=payload, headers={"X-CSRFToken": get_csrf_token(client)})


def _mechanic(app):
    c = app.test_client()
    assert login(c, email=MECH_EMAIL, password=MECH_PASSWORD).status_code == 302
    return c


def _create_wo(mech, lp):
    resp = _post(mech, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(lp["customer"]["_id"]),
        "unit_id": str(lp["unit"]["_id"]),
        # Тесты заводят по WO на один юнит — явный обход «один юнит = один WO».
        "allow_duplicate_unit_wo": True,
        "labors": [{"description": "Lock poll job", "parts": []}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def test_rev_keeps_answering_locked_while_details_refuse_the_mechanic(app, mongo, lp):
    mech = _mechanic(app)
    owner = app.test_client()
    assert login(owner).status_code == 302
    wo_id = _create_wo(mech, lp)
    works = mongo[SHOP_A_DB].work_orders
    rev_url = f"/work_orders/api/mechanic/work_orders/{wo_id}/rev"
    details_url = f"/work_orders/api/mechanic/work_orders/{wo_id}?mp=1"
    confirm_url = f"/work_orders/api/work_orders/{wo_id}/confirm"

    def rev_now():
        return works.find_one({"_id": ObjectId(wo_id)})["labors_rev"]

    assert mech.get(rev_url).get_json() == {
        "ok": True, "labors_rev": rev_now(), "locked": False, "mechanic_done": False}

    # Менеджер подтвердил: /rev — 200 и locked, детали механику — 403 с
    # текстом причины (его страница показывает в баннере).
    assert _post(owner, confirm_url, {"confirmed": True}).status_code == 200
    j = mech.get(rev_url).get_json()
    assert j["ok"] is True and j["locked"] is True and j["labors_rev"] == rev_now()
    resp = mech.get(details_url)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "wo_confirmed" and resp.get_json()["message"]

    # Офис правит подтверждённый WO — ревизия двигается, а опрос всё так же
    # отвечает 200 + locked: страница по нему детали не перечитывает.
    works.update_one({"_id": ObjectId(wo_id)}, {"$inc": {"labors_rev": 1}})
    j = mech.get(rev_url).get_json()
    assert j["locked"] is True and j["labors_rev"] == rev_now()
    assert mech.get(details_url).status_code == 403

    # Подтверждение сняли — locked: false, детали открываются с той же ревизией.
    assert _post(owner, confirm_url, {"confirmed": False}).status_code == 200
    j = mech.get(rev_url).get_json()
    assert j["locked"] is False
    resp = mech.get(details_url)
    assert resp.status_code == 200 and resp.get_json()["labors_rev"] == j["labors_rev"]

    # Смета — тоже locked (без статуса в ответе), детали — 400.
    works.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "estimate"}})
    j = mech.get(rev_url).get_json()
    assert j["locked"] is True and "status" not in j
    resp = mech.get(details_url)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "estimate_locked_for_mechanics"


def test_mechanic_page_renders_the_hidden_lock_notice(app, mongo, lp):
    mech = _mechanic(app)
    wo_id = _create_wo(mech, lp)
    resp = mech.get(f"/mechanic/work_orders/{wo_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Баннер, которым mechanic_wo.js (applyLock) показывает причину замка:
    # есть на странице и скрыт, пока замка нет.
    assert 'id="mechLockNotice"' in html and 'id="mechLockNoticeText"' in html
    notice_tag = html[html.rindex("<div", 0, html.index('id="mechLockNotice"')):html.index('id="mechLockNotice"')]
    assert "alert alert-warning" in notice_tag and "d-none" in notice_tag
