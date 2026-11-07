"""
Default Labor Rate при создании клиента: селект в диалоге Add Customer
(дефолт магазина предвыбран, без пустой опции; без активных ставок —
disabled placeholder, подсказка про Settings и никакого сидинга на GET),
POST /customers/create с выбранной ставкой, отказ по мусорному /
неизвестному id, неактивной и чужой (shop_id другого магазина) ставке,
дефолт магазина без поля; резолвер сеет дефолтные ставки только для базы
без единого документа labor_rates; тот же хелпер в customer_details_update
и в мобильном POST /api/mobile/customers (400 invalid_labor_rate).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from flask import url_for
from pymongo import MongoClient

from tests.conftest import (
    OWNER_EMAIL,
    OWNER_PASSWORD,
    SHOP_A_DB,
    SHOP_B_DB,
    TEST_MONGO_URI,
    get_csrf_token,
    login,
)

PREFIX = "LRC "  # все клиенты модуля — с этим префиксом, чистятся по нему
ADDRESS = "10 Rate Street, Springfield"
INVALID_MSG = "Selected default labor rate is invalid."


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def rates(seed, mongo):
    """Вторая активная ставка магазина A, неактивная ставка и ставка с
    shop_id магазина B в той же базе (проверяет фильтр shop_id)."""
    shop_db = mongo[SHOP_A_DB]
    fleet = {"_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "code": "fleet",
             "name": "Fleet", "hourly_rate": 85.0, "is_active": True}
    inactive = {"_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "code": "retired",
                "name": "Retired", "hourly_rate": 70.0, "is_active": False}
    foreign = {"_id": ObjectId(), "shop_id": seed["shop_b"]["_id"], "code": "standard",
               "name": "Shop B Standard", "hourly_rate": 120.0, "is_active": True}
    shop_db.labor_rates.insert_many([fleet, inactive, foreign])
    yield {"standard": seed["labor_rate"], "fleet": fleet, "inactive": inactive, "foreign": foreign}
    shop_db.labor_rates.delete_many({"_id": {"$in": [fleet["_id"], inactive["_id"], foreign["_id"]]}})
    shop_db.customers.delete_many({"company_name": {"$regex": "^" + re.escape(PREFIX)}})


@pytest.fixture()
def customer(seed, rates, mongo):
    """Клиент для customer_details_update, со стандартной ставкой."""
    shop_db = mongo[SHOP_A_DB]
    now = datetime.now(timezone.utc)
    doc = {
        "_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": PREFIX + "Details Fleet", "contacts": [], "address": ADDRESS,
        "taxable": True, "default_labor_rate": rates["standard"]["_id"], "is_active": True,
        "created_at": now, "updated_at": now,
    }
    shop_db.customers.insert_one(doc)
    yield doc
    shop_db.customers.delete_one({"_id": doc["_id"]})


def _post(client, url, data):
    data = dict(data)
    data["csrf_token"] = get_csrf_token(client)
    return client.post(url, data=data)


def _flashes(client):
    """Flash-сообщения из сессии после редиректа (и сброс, чтобы следующий
    запрос проверялся с чистого листа)."""
    with client.session_transaction() as sess:
        msgs = [m for _, m in (sess.get("_flashes") or [])]
        sess.pop("_flashes", None)
    return msgs


def _find(mongo, company_name):
    return mongo[SHOP_A_DB].customers.find_one({"company_name": company_name})


def _rate_of(mongo, customer_id):
    return mongo[SHOP_A_DB].customers.find_one({"_id": customer_id})["default_labor_rate"]


def _mobile_login(client):
    resp = client.post(
        "/api/mobile/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        environ_base={"REMOTE_ADDR": "127.0.0.13"},
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# ── диалог Add Customer ─────────────────────────────────────────────────


def test_add_customer_dialog_lists_rates_with_shop_default_preselected(client, rates):
    assert login(client).status_code == 302
    body = client.get("/customers").get_data(as_text=True)
    m = re.search(
        r'<select id="customerCreateLaborRate" name="default_labor_rate"[^>]*>(.*?)</select>',
        body, re.S,
    )
    assert m, "Default Labor Rate select missing in the Add Customer dialog"
    select = m.group(1)

    std_id = str(rates["standard"]["_id"])
    assert re.search(r'<option value="%s"[^>]*\bselected\b' % std_id, select)
    assert "Standard ($100.00/hr)" in select

    fleet_id = str(rates["fleet"]["_id"])
    assert fleet_id in select and "Fleet ($85.00/hr)" in select
    assert not re.search(r'<option value="%s"[^>]*\bselected\b' % fleet_id, select)

    # Неактивные и чужие ставки не предлагаются, пустой опции "- Not set -" нет.
    assert str(rates["inactive"]["_id"]) not in select
    assert str(rates["foreign"]["_id"]) not in select
    assert 'value=""' not in select


# ── без активных ставок / сидинг для пустой базы ────────────────────────


def test_dialog_without_active_rates_shows_placeholder_and_does_not_seed(app, client, seed, mongo):
    """Все ставки магазина A деактивированы: в селекте один disabled
    placeholder, подсказка ведёт в Settings → Work Order Settings, а GET
    /customers ничего не сеет — число документов labor_rates не меняется."""
    shop_db = mongo[SHOP_A_DB]
    shop_filter = {"shop_id": seed["shop_a"]["_id"]}
    # Деактивируем все ставки магазина, а вернём ровно те, что были активны.
    active_ids = [
        d["_id"] for d in shop_db.labor_rates.find({**shop_filter, "is_active": True}, {"_id": 1})
    ]
    assert seed["labor_rate"]["_id"] in active_ids
    before = shop_db.labor_rates.count_documents(shop_filter)
    shop_db.labor_rates.update_many(shop_filter, {"$set": {"is_active": False}})
    try:
        assert login(client).status_code == 302
        resp = client.get("/customers")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        m = re.search(
            r'<select id="customerCreateLaborRate" name="default_labor_rate"[^>]*>(.*?)</select>',
            body, re.S,
        )
        assert m, "Default Labor Rate select missing in the Add Customer dialog"
        select = m.group(1)
        assert '<option value="" disabled selected>No active labor rates</option>' in select
        assert str(seed["labor_rate"]["_id"]) not in select

        with app.test_request_context():
            settings_url = url_for("settings.work_orders_index")
        assert "Add a labor rate in" in body
        assert f'<a href="{settings_url}">Settings → Work Order Settings</a> first.' in body

        # Сидинга не было: ни одного нового документа labor_rates у магазина A.
        assert shop_db.labor_rates.count_documents(shop_filter) == before
    finally:
        shop_db.labor_rates.update_many({"_id": {"$in": active_ids}}, {"$set": {"is_active": True}})


def test_resolver_seeds_defaults_for_shop_without_any_rates(app, seed, mongo):
    """База магазина B без единого документа labor_rates: резолвер сеет
    дефолтные ставки и возвращает активную ставку "standard"."""
    from app.blueprints.customers.routes import _resolve_default_labor_rate_id

    shop_db = mongo[SHOP_B_DB]
    shop_b_id = seed["shop_b"]["_id"]
    assert shop_db.labor_rates.count_documents({"shop_id": shop_b_id}) == 0
    try:
        with app.app_context():
            rate_id = _resolve_default_labor_rate_id(shop_db, shop_b_id)
        assert isinstance(rate_id, ObjectId)
        doc = shop_db.labor_rates.find_one({"_id": rate_id})
        assert doc["shop_id"] == shop_b_id
        assert doc["code"] == "standard" and doc["is_active"] is True
    finally:
        shop_db.labor_rates.delete_many({"shop_id": shop_b_id})


# ── POST /customers/create ──────────────────────────────────────────────


def test_create_with_chosen_rate_stores_it(client, rates, mongo):
    login(client)
    name = PREFIX + "Chosen Fleet"
    resp = _post(client, "/customers/create", {
        "company_name": name, "address": ADDRESS,
        "default_labor_rate": str(rates["fleet"]["_id"]),
    })
    assert resp.status_code == 302
    doc = _find(mongo, name)
    assert doc, _flashes(client)
    assert doc["default_labor_rate"] == rates["fleet"]["_id"]
    assert "Customer created successfully." in _flashes(client)


def test_create_without_field_uses_shop_default(client, rates, mongo):
    login(client)
    name = PREFIX + "Default Rate"
    resp = _post(client, "/customers/create", {"company_name": name, "address": ADDRESS})
    assert resp.status_code == 302
    doc = _find(mongo, name)
    assert doc, _flashes(client)
    assert doc["default_labor_rate"] == rates["standard"]["_id"]

    # Пустое значение — то же, что отсутствие поля.
    name2 = PREFIX + "Empty Rate"
    resp = _post(client, "/customers/create",
                 {"company_name": name2, "address": ADDRESS, "default_labor_rate": ""})
    assert resp.status_code == 302
    doc2 = _find(mongo, name2)
    assert doc2, _flashes(client)
    assert doc2["default_labor_rate"] == rates["standard"]["_id"]


@pytest.mark.parametrize("which", ["garbage", "unknown", "inactive", "foreign"])
def test_create_rejects_invalid_rate(client, rates, mongo, which):
    login(client)
    raw = {
        "garbage": "not-an-object-id",
        "unknown": str(ObjectId()),
        "inactive": str(rates["inactive"]["_id"]),
        "foreign": str(rates["foreign"]["_id"]),
    }[which]
    name = PREFIX + "Rejected " + which
    resp = _post(client, "/customers/create",
                 {"company_name": name, "address": ADDRESS, "default_labor_rate": raw})
    assert resp.status_code == 302
    assert "/customers" in resp.headers["Location"]
    assert INVALID_MSG in _flashes(client)
    assert _find(mongo, name) is None


# ── хелпер и customer_details_update ────────────────────────────────────


def test_validate_helper_current_fallback(app, seed, rates, mongo):
    from app.blueprints.customers.routes import _validate_customer_labor_rate_id as validate

    shop_db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    # Пусто → текущая ставка клиента, если это ObjectId.
    assert validate(shop_db, shop_id, "", current=rates["fleet"]["_id"]) == (rates["fleet"]["_id"], None)
    # Пусто и legacy-строка вместо ObjectId → дефолт магазина ("standard").
    assert validate(shop_db, shop_id, None, current="standart") == (rates["standard"]["_id"], None)
    assert validate(shop_db, shop_id, "   ") == (rates["standard"]["_id"], None)
    # Явно выбранная ставка проверяется даже при валидной текущей.
    assert validate(shop_db, shop_id, str(rates["inactive"]["_id"]), current=rates["fleet"]["_id"]) == (None, INVALID_MSG)
    assert validate(shop_db, shop_id, str(rates["foreign"]["_id"])) == (None, INVALID_MSG)
    assert validate(shop_db, shop_id, "garbage") == (None, INVALID_MSG)
    assert validate(shop_db, shop_id, str(rates["fleet"]["_id"])) == (rates["fleet"]["_id"], None)


def test_details_update_sets_and_validates_rate(client, rates, customer, mongo):
    login(client)
    url = f"/customers/{customer['_id']}/update"
    base = {"company_name": customer["company_name"], "address": ADDRESS, "taxable": "1"}

    resp = _post(client, url, {**base, "default_labor_rate": str(rates["fleet"]["_id"])})
    assert resp.status_code == 302 and "tab=details" in resp.headers["Location"]
    assert "Customer updated successfully." in _flashes(client)
    assert _rate_of(mongo, customer["_id"]) == rates["fleet"]["_id"]

    # Пустое значение — ставка остаётся прежней (никогда не пустая).
    resp = _post(client, url, {**base, "default_labor_rate": ""})
    assert resp.status_code == 302
    assert "Customer updated successfully." in _flashes(client)
    assert _rate_of(mongo, customer["_id"]) == rates["fleet"]["_id"]

    for raw in ("garbage", str(ObjectId()), str(rates["inactive"]["_id"]), str(rates["foreign"]["_id"])):
        resp = _post(client, url, {**base, "default_labor_rate": raw})
        assert resp.status_code == 302 and "tab=details" in resp.headers["Location"]
        assert INVALID_MSG in _flashes(client)
        assert _rate_of(mongo, customer["_id"]) == rates["fleet"]["_id"]


# ── мобильный API ───────────────────────────────────────────────────────


def test_mobile_create_accepts_rate_and_rejects_invalid(client, rates, mongo):
    headers = {"X-CSRFToken": _mobile_login(client)["csrf_token"]}

    name = PREFIX + "Mobile Chosen"
    resp = client.post("/api/mobile/customers", json={
        "company_name": name, "address": ADDRESS,
        "default_labor_rate": str(rates["fleet"]["_id"]),
    }, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    doc = mongo[SHOP_A_DB].customers.find_one({"_id": ObjectId(resp.get_json()["id"])})
    assert doc["company_name"] == name
    assert doc["default_labor_rate"] == rates["fleet"]["_id"]

    # Без поля — поведение прежнее: дефолтная ставка магазина.
    name2 = PREFIX + "Mobile Default"
    resp = client.post("/api/mobile/customers",
                       json={"company_name": name2, "address": ADDRESS}, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    assert _find(mongo, name2)["default_labor_rate"] == rates["standard"]["_id"]

    name3 = PREFIX + "Mobile Rejected"
    for raw in ("garbage", str(ObjectId()), str(rates["inactive"]["_id"]), str(rates["foreign"]["_id"])):
        resp = client.post("/api/mobile/customers", json={
            "company_name": name3, "address": ADDRESS, "default_labor_rate": raw,
        }, headers=headers)
        assert resp.status_code == 400, resp.get_json()
        body = resp.get_json()
        assert body["ok"] is False
        assert body["error"] == "invalid_labor_rate"
        assert body["message"] == INVALID_MSG
    assert _find(mongo, name3) is None
