"""Mobile API: JSON-логин (cookie-сессия + CSRF в ответе) и списки."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB


def _mobile_login(client, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    return client.post(
        "/api/mobile/login",
        json={"email": email, "password": password},
        environ_base={"REMOTE_ADDR": "127.0.0.5"},
    )


def test_mobile_login_ok(client):
    resp = _mobile_login(client)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["csrf_token"]
    assert data["user"]["email"] == OWNER_EMAIL
    assert data["active_shop_id"]
    assert isinstance(data["shops"], list) and len(data["shops"]) == 1
    assert data["shops"][0]["name"] == "Shop A"


def test_mobile_login_wrong_password(client):
    resp = _mobile_login(client, password="nope-nope-nope")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_credentials"


def test_mobile_session_requires_auth(client):
    resp = client.get("/api/mobile/session")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"


def test_mobile_session_after_login(client):
    _mobile_login(client)
    resp = client.get("/api/mobile/session")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["user"]["email"] == OWNER_EMAIL


def test_mobile_session_recomputes_permissions(app, client, seed):
    """Права в /api/mobile/session пересчитываются на каждый запрос:
    смена прав роли видна без перелогина (кука не замораживает старые права)."""
    from werkzeug.security import generate_password_hash

    with app.app_context():
        from app.extensions import get_master_db, get_mongo_client
        master = get_master_db()
        tenant_db = get_mongo_client()[seed["tenant_a"]["db_name"]]

        tenant_db.roles.update_one(
            {"key": "mech_test"},
            {"$set": {"name": "Mech Test", "permissions": ["work_orders.view", "work_orders.view_costs"]}},
            upsert=True,
        )
        master.users.insert_one({
            "_id": ObjectId(),
            "email": "mobile-mech@test.local",
            "password_hash": generate_password_hash("password123"),
            "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"],
            "shop_ids": [str(seed["shop_a"]["_id"])],
            "role": "mech_test",
        })

    login_data = _mobile_login(client, email="mobile-mech@test.local", password="password123").get_json()
    assert "work_orders.view_costs" in login_data["permissions"]

    # Урезаем роль после логина — сессия должна отдать свежие права.
    with app.app_context():
        from app.extensions import get_mongo_client
        get_mongo_client()[seed["tenant_a"]["db_name"]].roles.update_one(
            {"key": "mech_test"},
            {"$set": {"permissions": ["work_orders.view"]}},
        )

    data = client.get("/api/mobile/session").get_json()
    assert "work_orders.view_costs" not in data["permissions"]
    assert "work_orders.view" in data["permissions"]


def test_mobile_logout_requires_csrf_header(client):
    login_data = _mobile_login(client).get_json()
    # Без токена — CSRF должен отбить запрос.
    resp = client.post("/api/mobile/logout")
    assert resp.status_code == 400

    resp = client.post("/api/mobile/logout", headers={"X-CSRFToken": login_data["csrf_token"]})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    resp = client.get("/api/mobile/session")
    assert resp.status_code == 401


def test_mobile_active_shop_validates_access(client):
    login_data = _mobile_login(client).get_json()
    headers = {"X-CSRFToken": login_data["csrf_token"]}

    resp = client.post("/api/mobile/active-shop", json={"shop_id": str(ObjectId())}, headers=headers)
    assert resp.status_code == 403

    shop_id = login_data["shops"][0]["id"]
    resp = client.post("/api/mobile/active-shop", json={"shop_id": shop_id}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["active_shop_id"] == shop_id


@pytest.fixture()
def mobile_seed(app, seed):
    """Клиент + юнит + вендор + запчасть + WO с лейбором в магазине A."""
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        now = datetime.now(timezone.utc)
        shop_id = seed["shop_a"]["_id"]

        customer = {
            "_id": ObjectId(), "shop_id": shop_id, "company_name": "Mobile Test Co",
            "contacts": [{"first_name": "Ann", "last_name": "Lee", "phone": "555-0101", "is_main": True}],
            "is_active": True, "created_at": now,
        }
        unit = {
            "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer["_id"],
            "unit_number": "T-42", "vin": "1FUJA6CK14LM12345", "year": "2020",
            "make": "Freightliner", "model": "Cascadia", "mileage": 120000,
            "is_active": True, "created_at": now,
        }
        vendor = {
            "_id": ObjectId(), "shop_id": shop_id, "name": "Mobile Vendor",
            "is_active": True, "created_at": now,
        }
        part = {
            "_id": ObjectId(), "shop_id": shop_id, "part_number": "MOB-001",
            "description": "Mobile test part", "in_stock": 5, "average_cost": 12.5,
            "is_active": True, "created_at": now,
        }
        wo = {
            "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer["_id"],
            "unit_id": unit["_id"],
            "wo_number": 90001, "status": "open", "is_active": True,
            "labors": [{
                "labor": {"description": "Brake job", "hours": "2", "rate_code": "standard"},
                "parts": [{"part_number": "MOB-001", "part_id": str(part["_id"]),
                           "qty": 2, "price": 25.0, "cost": 12.5, "one_time_part": False}],
            }],
            "totals": {
                "grand_total": 250.0, "labor_total": 200.0, "parts_total": 50.0,
                "labors": [{"labor": 200.0, "labor_total": 200.0, "parts_total": 50.0,
                            "labor_full_total": 250.0}],
            },
            "created_at": now,
        }
        db.customers.insert_one(customer)
        db.units.insert_one(unit)
        db.vendors.insert_one(vendor)
        db.parts.insert_one(part)
        db.work_orders.insert_one(wo)

        yield {"db": db, "customer": customer, "unit": unit, "vendor": vendor, "part": part, "wo": wo}

        db.customers.delete_one({"_id": customer["_id"]})
        db.units.delete_one({"_id": unit["_id"]})
        db.vendors.delete_one({"_id": vendor["_id"]})
        db.parts.delete_one({"_id": part["_id"]})
        db.work_orders.delete_one({"_id": wo["_id"]})


def test_mobile_lists(client, mobile_seed):
    _mobile_login(client)

    data = client.get("/api/mobile/work_orders").get_json()
    assert data["ok"] is True
    numbers = [x["wo_number"] for x in data["items"]]
    assert 90001 in numbers
    row = next(x for x in data["items"] if x["wo_number"] == 90001)
    assert row["customer"].startswith("Mobile Test Co")
    assert row["grand_total"] == 250.0

    data = client.get("/api/mobile/customers?q=Mobile Test").get_json()
    assert data["ok"] is True
    assert any(x["company_name"] == "Mobile Test Co" for x in data["items"])
    row = next(x for x in data["items"] if x["company_name"] == "Mobile Test Co")
    assert row["contact_name"] == "Ann Lee"
    assert row["phone"] == "555-0101"

    data = client.get("/api/mobile/vendors").get_json()
    assert data["ok"] is True
    assert any(x["name"] == "Mobile Vendor" for x in data["items"])

    data = client.get("/api/mobile/parts?q=MOB-001").get_json()
    assert data["ok"] is True
    assert any(x["part_number"] == "MOB-001" and x["in_stock"] == 5 for x in data["items"])


def test_mobile_work_order_details(client, mobile_seed):
    _mobile_login(client)
    wo_id = str(mobile_seed["wo"]["_id"])

    resp = client.get(f"/api/mobile/work_orders/{wo_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["id"] == wo_id
    assert data["status"] == "open"
    assert data["wo_number"] == "90001"
    assert data["cust_name"].startswith("Mobile Test Co")
    assert "T-42" in data["unit_label"] or data["unit_number"] == "T-42"
    assert len(data["labors"]) == 1
    labor = data["labors"][0]
    assert labor["labor_desc"] == "Brake job"
    assert labor["parts"][0]["part_number"] == "MOB-001"
    assert labor["parts"][0]["qty"] == 2
    assert data["t"]["grand_total"] == 250.0
    assert data["t"]["remaining_balance"] == 250.0

    resp = client.get(f"/api/mobile/work_orders/{ObjectId()}")
    assert resp.status_code == 404


def test_mobile_work_orders_filter_by_customer_and_unit(client, mobile_seed):
    _mobile_login(client)
    cid = str(mobile_seed["customer"]["_id"])
    uid = str(mobile_seed["unit"]["_id"])

    data = client.get(f"/api/mobile/work_orders?customer_id={cid}").get_json()
    assert [x["wo_number"] for x in data["items"]] == [90001]

    data = client.get(f"/api/mobile/work_orders?unit_id={uid}").get_json()
    assert [x["wo_number"] for x in data["items"]] == [90001]

    data = client.get(f"/api/mobile/work_orders?customer_id={ObjectId()}").get_json()
    assert data["items"] == []


def test_mobile_customer_details(client, mobile_seed):
    _mobile_login(client)
    cid = str(mobile_seed["customer"]["_id"])

    resp = client.get(f"/api/mobile/customers/{cid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["company_name"] == "Mobile Test Co"
    assert data["contacts"][0]["name"] == "Ann Lee"
    assert len(data["units"]) == 1
    assert data["units"][0]["unit_number"] == "T-42"

    assert client.get(f"/api/mobile/customers/{ObjectId()}").status_code == 404


def test_mobile_customer_create_and_unit_crud(client, seed, app):
    login_data = _mobile_login(client).get_json()
    headers = {"X-CSRFToken": login_data["csrf_token"]}

    # Валидация: без имени и адреса
    resp = client.post("/api/mobile/customers", json={}, headers=headers)
    assert resp.status_code == 400

    resp = client.post("/api/mobile/customers", json={
        "company_name": "Mobile Created LLC",
        "address": "123 Main Street, Springfield",
        "taxable": True,
        "contacts": [{"first_name": "Bob", "last_name": "Ray", "phone": "555-1", "is_main": True}],
    }, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    customer_id = resp.get_json()["id"]

    # Документ получил search_terms и legacy-поля
    from app.extensions import get_mongo_client
    from bson import ObjectId as OID
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        doc = db.customers.find_one({"_id": OID(customer_id)})
    assert doc["search_terms"]
    assert doc["taxable"] is True
    assert doc["contacts"][0]["first_name"] == "Bob"

    # Юнит: создать
    resp = client.post("/api/mobile/units", json={
        "customer_id": customer_id, "unit_number": "M-7", "vin": "",
        "make": "Volvo", "model": "VNL", "year": "2021", "mileage": "50000",
    }, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    unit_id = resp.get_json()["id"]

    with app.app_context():
        u = db.units.find_one({"_id": OID(unit_id)})
    assert u["unit_number"] == "M-7"
    assert u["year"] == 2021
    assert u["search_terms"]

    # Юнит: обновить (смена номера пересобирает search_terms)
    resp = client.post(f"/api/mobile/units/{unit_id}/update", json={
        "unit_number": "M-8", "make": "Volvo", "model": "VNL", "year": "2021",
    }, headers=headers)
    assert resp.status_code == 200
    with app.app_context():
        u = db.units.find_one({"_id": OID(unit_id)})
    assert u["unit_number"] == "M-8"
    # search_terms — компактные триграммы ("M-8" → "m8")
    assert any("m8" in t for t in u["search_terms"])

    # Юнит без customer_id / с чужим customer_id
    resp = client.post("/api/mobile/units", json={"unit_number": "X"}, headers=headers)
    assert resp.status_code == 400
    resp = client.post("/api/mobile/units", json={
        "customer_id": str(OID()), "unit_number": "X",
    }, headers=headers)
    assert resp.status_code == 404

    with app.app_context():
        db.units.delete_one({"_id": OID(unit_id)})
        db.customers.delete_one({"_id": OID(customer_id)})


def test_mobile_vendor_create(client, seed, app):
    login_data = _mobile_login(client).get_json()
    headers = {"X-CSRFToken": login_data["csrf_token"]}

    resp = client.post("/api/mobile/vendors", json={}, headers=headers)
    assert resp.status_code == 400

    resp = client.post("/api/mobile/vendors", json={
        "name": "Mobile Created Vendor",
        "website": "https://vendor.example",
        "contacts": [{"first_name": "Sue", "phone": "555-2", "is_main": True}],
    }, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    vendor_id = resp.get_json()["id"]

    from app.extensions import get_mongo_client
    from bson import ObjectId as OID
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        v = db.vendors.find_one({"_id": OID(vendor_id)})
    assert v["name"] == "Mobile Created Vendor"
    assert v["contacts"][0]["first_name"] == "Sue"
    with app.app_context():
        db.vendors.delete_one({"_id": OID(vendor_id)})


def test_mobile_work_order_create_and_edit(client, mobile_seed, app, seed):
    """Создание WO с мобилки: серверный расчёт тоталов, списание склада;
    редактирование: пересчёт и возврат разницы."""
    login_data = _mobile_login(client).get_json()
    headers = {"X-CSRFToken": login_data["csrf_token"]}
    part = mobile_seed["part"]

    # Отключаем shop supply для предсказуемой математики
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        db.shop_supply_amount_rules.update_one(
            {"shop_id": seed["shop_a"]["_id"]},
            {"$set": {"shop_supply_procentage": 0}},
            upsert=True,
        )
        db.parts.update_one({"_id": part["_id"]}, {"$set": {"in_stock": 10}})

    payload = {
        "customer_id": str(mobile_seed["customer"]["_id"]),
        "unit_id": str(mobile_seed["unit"]["_id"]),
        "status": "open",
        "labors": [{
            "description": "Oil change",
            "hours": "2",
            "rate_code": "standard",  # 100/hr из conftest
            "parts": [{
                "part_id": str(part["_id"]),
                "part_number": part["part_number"],
                "qty": 3, "price": 20.0, "cost": 12.5,
            }],
        }],
    }
    resp = client.post("/api/mobile/work_orders", json=payload, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    created = resp.get_json()
    wo_id = created["id"]
    # labor 2h × $100 + parts 3 × $20 = 260 (без налога — customer.taxable нет)
    assert created["grand_total"] == 260.0

    with app.app_context():
        stock = db.parts.find_one({"_id": part["_id"]})["in_stock"]
        wo_doc = db.work_orders.find_one({"_id": __import__("bson").ObjectId(wo_id)})
    assert stock == 7  # 10 - 3
    assert wo_doc["totals"]["labor_total"] == 200.0
    assert wo_doc["totals"]["parts_total"] == 60.0
    assert wo_doc["labors"][0]["labor"]["description"] == "Oil change"

    # Редактирование: qty 3 → 1, лейбор с ручной суммой
    edit_payload = {
        "labors": [{
            "description": "Oil change",
            "hours": "",
            "rate_code": "standard",
            "labor_total": 150.0,
            "parts": [{
                "part_id": str(part["_id"]),
                "part_number": part["part_number"],
                "qty": 1, "price": 20.0, "cost": 12.5,
            }],
        }],
    }
    resp = client.post(f"/api/mobile/work_orders/{wo_id}", json=edit_payload, headers=headers)
    assert resp.status_code == 200, resp.get_json()
    edited = resp.get_json()
    assert edited["grand_total"] == 170.0  # 150 + 20

    with app.app_context():
        stock = db.parts.find_one({"_id": part["_id"]})["in_stock"]
    assert stock == 9  # вернулось 2

    # Paid WO редактировать нельзя
    with app.app_context():
        db.work_orders.update_one(
            {"_id": __import__("bson").ObjectId(wo_id)}, {"$set": {"status": "paid"}}
        )
    resp = client.post(f"/api/mobile/work_orders/{wo_id}", json=edit_payload, headers=headers)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "paid_cannot_edit"

    with app.app_context():
        db.work_orders.delete_one({"_id": __import__("bson").ObjectId(wo_id)})
        db.cores.delete_many({"shop_id": seed["shop_a"]["_id"]})


def test_mobile_labor_rates(client, seed):
    _mobile_login(client)
    data = client.get("/api/mobile/labor_rates").get_json()
    assert data["ok"] is True
    assert any(r["code"] == "standard" and r["hourly_rate"] == 100.0 for r in data["items"])


def test_mobile_unit_details(client, mobile_seed):
    _mobile_login(client)
    uid = str(mobile_seed["unit"]["_id"])

    resp = client.get(f"/api/mobile/units/{uid}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["unit_number"] == "T-42"
    assert data["vin"] == "1FUJA6CK14LM12345"
    assert data["customer_id"] == str(mobile_seed["customer"]["_id"])
    assert data["annual_inspection"] is None

    assert client.get(f"/api/mobile/units/{ObjectId()}").status_code == 404


# ── постоянная мобильная сессия (не выкидывает из приложения) ───────


def _session_set_cookie(resp):
    """Заголовок Set-Cookie, который реально ставит session-куку.

    В ответе может быть и кука-удалялка легаси-сессии (session=; Expires=1970
    из after_request) — берём ту, где у session непустое значение."""
    for h in resp.headers.getlist("Set-Cookie"):
        if h.startswith("session=") and not h.startswith("session=;"):
            return h
    return ""


def test_mobile_login_sets_permanent_cookie(client):
    """Кука мобильной сессии постоянная (с Expires): переживает перезапуск
    приложения, срок продлевается каждым запросом."""
    resp = _mobile_login(client)
    cookie = _session_set_cookie(resp)
    assert cookie
    assert "Expires=" in cookie


def test_mobile_session_upgrades_existing_session_to_permanent(client):
    """Сессии, залогиненные до введения permanent-кук, апгрейдятся первым же
    вызовом /api/mobile/session — без перелогина."""
    from tests.conftest import login

    login(client)  # веб-логин permanent не ставит — имитация старой сессии
    resp = client.get("/api/mobile/session")
    assert resp.status_code == 200
    cookie = _session_set_cookie(resp)
    assert cookie
    assert "Expires=" in cookie


def test_web_login_cookie_not_permanent(client):
    """Веб не затронут: браузерная кука остаётся сессионной (без Expires)."""
    from tests.conftest import login

    resp = login(client)
    cookie = _session_set_cookie(resp)
    assert cookie
    assert "Expires=" not in cookie
