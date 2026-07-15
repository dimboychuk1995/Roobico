"""
Механик-режим: скрытие цен (view_costs), таймеры работ, форс in_progress,
merge по labor_id, автоцены сервером, синк дефолтных ролей.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

MECHANIC_EMAIL = "mechanic@test.local"
MECHANIC_PASSWORD = "password123"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def mech_seed(app, seed, mongo):
    """Механик + роль mechanic (новые дефолты) + клиент/юнит/парт в магазине A."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one(
        {"key": "mechanic"}, {"$set": mechanic_role}, upsert=True
    )

    mechanic_user = {
        "_id": ObjectId(),
        "email": MECHANIC_EMAIL,
        "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": "Mike",
        "last_name": "Wrench",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(shop_a["_id"])],
        "role": "mechanic",
        "created_at": now,
    }
    master.users.insert_one(mechanic_user)

    customer = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Mech Fleet LLC",
        "taxable": False,
        "is_active": True,
        "created_at": now,
    }
    shop_db.customers.insert_one(customer)

    unit = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "customer_id": customer["_id"],
        "vin": "MECH1234567890000",
        "unit_number": "M-1",
        "is_active": True,
        "created_at": now,
    }
    shop_db.units.insert_one(unit)

    part = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "part_number": "MECH-BRAKE-01",
        "description": "Brake pad set",
        "average_cost": 40.0,
        "has_selling_price": True,
        "selling_price": 99.5,
        "in_stock": 10,
        "is_active": True,
        "search_terms": ["mech", "brake", "01", "mechbrake01", "mech-brake-01", "pad", "set"],
        "created_at": now,
    }
    shop_db.parts.insert_one(part)

    return {"user": mechanic_user, "customer": customer, "unit": unit, "part": part}


def login_mechanic(client):
    return login(client, email=MECHANIC_EMAIL, password=MECHANIC_PASSWORD)


def _post_json(client, url, payload):
    token = get_csrf_token(client)
    return client.post(url, json=payload, headers={"X-CSRFToken": token})


def _create_wo_as_mechanic(client, mech_seed, description="Replace brakes"):
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(mech_seed["customer"]["_id"]),
        "unit_id": str(mech_seed["unit"]["_id"]),
        "labors": [{
            "description": description,
            "parts": [{"part_id": str(mech_seed["part"]["_id"]), "qty": 2}],
        }],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data


# ── роутинг и права ─────────────────────────────────────────────────


def test_mechanic_redirected_from_manager_pages(client, mech_seed):
    login_mechanic(client)
    resp = client.get("/work_orders")
    assert resp.status_code == 302
    assert "/mechanic/work_orders" in resp.headers["Location"]

    resp = client.get("/work_orders/details")
    assert resp.status_code == 302
    assert "/mechanic/work_orders/new" in resp.headers["Location"]


def test_owner_not_redirected(client, mech_seed):
    login(client)
    resp = client.get("/work_orders")
    assert resp.status_code == 200


def test_mechanic_login_lands_on_mechanic_page(client, mech_seed):
    resp = login_mechanic(client)
    assert resp.status_code == 302
    resp = client.get(resp.headers["Location"], follow_redirects=True)
    assert resp.status_code == 200
    assert b"mechListPage" in resp.data or b"Work Orders" in resp.data


def test_mechanic_pages_render(client, mech_seed):
    login_mechanic(client)
    assert client.get("/mechanic/work_orders").status_code == 200
    assert client.get("/mechanic/work_orders/new").status_code == 200


# ── стриппинг денег ─────────────────────────────────────────────────


def test_parts_search_stripped_for_mechanic(client, mech_seed):
    login_mechanic(client)
    resp = client.get("/work_orders/api/parts/search?q=MECH-BRAKE")
    assert resp.status_code == 200
    items = resp.get_json()["items"]
    assert items, "part should be found"
    for item in items:
        for key in ("average_cost", "selling_price", "core_cost", "misc_charges", "has_selling_price"):
            assert key not in item
        assert "part_number" in item and "in_stock" in item


def test_parts_search_full_for_owner(client, mech_seed):
    login(client)
    resp = client.get("/work_orders/api/parts/search?q=MECH-BRAKE")
    items = resp.get_json()["items"]
    assert items
    assert items[0]["average_cost"] == 40.0
    assert items[0]["selling_price"] == 99.5


def test_money_endpoints_forbidden_for_mechanic(client, mech_seed):
    login_mechanic(client)
    assert client.get("/api/mobile/part_price?part_id=" + str(mech_seed["part"]["_id"])).status_code == 403
    assert client.get("/work_orders/api/parts_pricing").status_code == 403
    assert client.get("/work_orders/api/estimates").status_code == 403
    assert client.get("/work_orders/api/work_orders/all-payments").status_code == 403
    assert client.get("/work_orders/api/bulk-payments/customers").status_code == 403


def test_mobile_labor_rates_stripped(client, mech_seed):
    login_mechanic(client)
    items = client.get("/api/mobile/labor_rates").get_json()["items"]
    assert items
    assert all("hourly_rate" not in r for r in items)
    assert all(r.get("code") for r in items)

    login(client)
    items = client.get("/api/mobile/labor_rates").get_json()["items"]
    assert any("hourly_rate" in r for r in items)


def test_mobile_wo_list_stripped(client, mech_seed):
    login_mechanic(client)
    _create_wo_as_mechanic(client, mech_seed, description="List strip test")
    data = client.get("/api/mobile/work_orders").get_json()
    assert data["ok"] is True
    assert data["items"]
    for item in data["items"]:
        for key in ("grand_total", "labor_total", "parts_total", "paid_amount", "balance"):
            assert key not in item
    assert data["totals"] == {}


# ── mechanic create: автоцены + форс статуса ────────────────────────


def test_mechanic_create_autofills_prices_and_forces_in_progress(client, mech_seed, mongo):
    login_mechanic(client)
    stock_before = mongo[SHOP_A_DB].parts.find_one({"_id": mech_seed["part"]["_id"]})["in_stock"]

    data = _create_wo_as_mechanic(client, mech_seed, description="Autofill test")
    assert data["status"] == "in_progress"
    assert "grand_total" not in data

    wo = mongo[SHOP_A_DB].work_orders.find_one({"wo_number": data["wo_number"]})
    assert wo["status"] == "in_progress"
    block = wo["labors"][0]
    assert block["labor_id"]
    part_line = block["parts"][0]
    assert float(part_line["price"]) == 99.5   # selling_price из каталога
    assert float(part_line["cost"]) == 40.0    # average_cost
    assert wo["totals"]["grand_total"] == pytest.approx(199.0)

    stock_after = mongo[SHOP_A_DB].parts.find_one({"_id": mech_seed["part"]["_id"]})["in_stock"]
    assert stock_after == stock_before - 2


def test_mechanic_create_ignores_client_prices_and_status(client, mech_seed, mongo):
    login_mechanic(client)
    resp = _post_json(client, "/work_orders/api/mechanic/work_orders", {
        "customer_id": str(mech_seed["customer"]["_id"]),
        "unit_id": str(mech_seed["unit"]["_id"]),
        "status": "open",
        "labors": [{
            "description": "Price injection attempt",
            "hours": "10",
            "rate_code": "standard",
            "parts": [{
                "part_id": str(mech_seed["part"]["_id"]),
                "qty": 1,
                "price": 1.0,
                "cost": 0.5,
            }],
        }],
    })
    data = resp.get_json()
    assert data["status"] == "in_progress"
    wo = mongo[SHOP_A_DB].work_orders.find_one({"wo_number": data["wo_number"]})
    part_line = wo["labors"][0]["parts"][0]
    assert float(part_line["price"]) == 99.5
    assert wo["labors"][0]["labor"]["hours"] == ""  # клиентские hours игнорируются


# ── mechanic update: merge по labor_id ──────────────────────────────


def test_mechanic_update_preserves_manager_fields(client, mech_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    # Менеджерский WO: часы, ставка, вручную поправленная цена парта.
    labor_id = str(ObjectId())
    wo_doc = {
        "shop_id": mech_seed["customer"]["shop_id"],
        "tenant_id": mech_seed["customer"]["tenant_id"],
        "wo_number": 900001,
        "customer_id": mech_seed["customer"]["_id"],
        "unit_id": mech_seed["unit"]["_id"],
        "status": "open",
        "labors": [{
            "labor_id": labor_id,
            "labor": {
                "description": "Manager job",
                "hours": "2",
                "rate_code": "standard",
                "hourly_rate": 100.0,
                "assigned_mechanics": [],
                "issue_description": "",
            },
            "parts": [{
                "part_id": str(mech_seed["part"]["_id"]),
                "part_number": "MECH-BRAKE-01",
                "description": "Brake pad set",
                "one_time_part": False,
                "qty": "1",
                "cost": "40",
                "price": "120",   # менеджер поднял цену вручную
                "core_charge": "",
                "misc_charge": "",
                "misc_charge_description": "",
            }],
        }],
        "totals": {"labors": [{"labor": 200.0}], "labor_total": 200.0, "sales_tax_rate": 0, "is_taxable": False},
        "is_active": True,
        "created_at": _now(),
        "updated_at": _now(),
    }
    shop_db.work_orders.insert_one(wo_doc)

    login_mechanic(client)
    resp = _post_json(
        client,
        f"/work_orders/api/mechanic/work_orders/{wo_doc['_id']}",
        {"labors": [{
            "labor_id": labor_id,
            "description": "Manager job — done",
            "parts": [
                # существующий парт: механик меняет qty
                {"part_id": str(mech_seed["part"]["_id"]), "qty": 3},
            ],
        }]},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "in_progress"

    wo = shop_db.work_orders.find_one({"_id": wo_doc["_id"]})
    assert wo["status"] == "in_progress"
    block = wo["labors"][0]
    assert block["labor_id"] == labor_id
    assert block["labor"]["hours"] == "2"                 # менеджерские часы сохранены
    assert block["labor"]["rate_code"] == "standard"
    assert block["labor"]["description"] == "Manager job — done"
    part_line = block["parts"][0]
    assert float(part_line["price"]) == 120.0             # менеджерская цена сохранена
    assert int(float(part_line["qty"])) == 3              # qty обновлён механиком


def test_mechanic_update_rejects_paid(client, mech_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo_doc = {
        "shop_id": mech_seed["customer"]["shop_id"],
        "wo_number": 900002,
        "customer_id": mech_seed["customer"]["_id"],
        "unit_id": mech_seed["unit"]["_id"],
        "status": "paid",
        "labors": [],
        "is_active": True,
        "created_at": _now(),
    }
    shop_db.work_orders.insert_one(wo_doc)

    login_mechanic(client)
    resp = _post_json(
        client,
        f"/work_orders/api/mechanic/work_orders/{wo_doc['_id']}",
        {"labors": [{"description": "x", "parts": []}]},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "paid_cannot_edit"


# ── форс in_progress на существующих эндпоинтах ─────────────────────


def test_web_update_forces_in_progress_for_mechanic(client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Force status test")
    wo_id = data["id"]

    resp = _post_json(client, f"/work_orders/api/work_orders/{wo_id}/update", {
        "labors": [{"labor_description": "Force status test", "labor_hours": 0,
                     "labor_rate_code": "", "labor_full_total": 0, "assigned_mechanics": [],
                     "issue_description": "", "parts": []}],
        "totals": {},
        "save_status": "open",
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "in_progress"

    wo = mongo[SHOP_A_DB].work_orders.find_one({"_id": ObjectId(wo_id)})
    assert wo["status"] == "in_progress"


def test_status_endpoint_restricted_for_mechanic(client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Status endpoint test")
    wo_id = data["id"]

    resp = _post_json(client, f"/work_orders/api/work_orders/{wo_id}/status", {"status": "open"})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "forbidden_status"

    resp = _post_json(client, f"/work_orders/api/work_orders/{wo_id}/status", {"status": "in_progress"})
    assert resp.status_code == 200

    # paid WO нельзя трогать: иначе delete_many снёс бы платежи.
    shop_db = mongo[SHOP_A_DB]
    shop_db.work_orders.update_one({"_id": ObjectId(wo_id)}, {"$set": {"status": "paid"}})
    payment_id = shop_db.work_order_payments.insert_one({
        "shop_id": mech_seed["customer"]["shop_id"],
        "work_order_id": ObjectId(wo_id),
        "amount": 100.0, "is_active": True, "created_at": _now(),
    }).inserted_id

    resp = _post_json(client, f"/work_orders/api/work_orders/{wo_id}/status", {"status": "in_progress"})
    assert resp.status_code == 403
    assert shop_db.work_order_payments.find_one({"_id": payment_id}) is not None


def test_mobile_edit_forces_in_progress_for_mechanic(client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Mobile force test")
    wo_id = data["id"]

    resp = _post_json(client, f"/api/mobile/work_orders/{wo_id}", {
        "status": "open",
        "labors": [{"labor_id": "", "description": "Mobile force test", "parts": []}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "in_progress"


# ── таймеры ─────────────────────────────────────────────────────────


def test_timer_start_stop_and_auto_switch(client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Timer test A")
    wo_id = data["id"]
    shop_db = mongo[SHOP_A_DB]
    wo = shop_db.work_orders.find_one({"_id": ObjectId(wo_id)})
    labor_id = wo["labors"][0]["labor_id"]

    # старт
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": wo_id, "labor_id": labor_id})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["timer"]["labor_id"] == labor_id
    assert body["stopped_previous"] is None
    assert body["time_summary"][labor_id]["my_running"] is True

    log = shop_db.wo_time_logs.find_one({"labor_id": labor_id, "stopped_at": None})
    assert log is not None
    assert log["user_name"] == "Mike Wrench"

    # второй WO: старт авто-стопит первый
    data2 = _create_wo_as_mechanic(client, mech_seed, description="Timer test B")
    wo2 = shop_db.work_orders.find_one({"_id": ObjectId(data2["id"])})
    labor2_id = wo2["labors"][0]["labor_id"]

    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": data2["id"], "labor_id": labor2_id})
    body = resp.get_json()
    assert body["stopped_previous"] is not None
    assert body["stopped_previous"]["labor_id"] == labor_id

    prev = shop_db.wo_time_logs.find_one({"labor_id": labor_id})
    assert prev["stopped_at"] is not None
    assert prev["stop_source"] == "auto_switch"
    assert prev["seconds"] >= 0

    # current
    resp = client.get("/work_orders/api/mechanic/timers/current")
    timer = resp.get_json()["timer"]
    assert timer is not None and timer["labor_id"] == labor2_id

    # стоп
    resp = _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    assert resp.status_code == 200
    assert resp.get_json()["timer"]["seconds"] >= 0

    resp = _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "no_running_timer"


def test_timer_start_flips_status_to_in_progress(client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Timer status flip")
    shop_db = mongo[SHOP_A_DB]
    shop_db.work_orders.update_one({"_id": ObjectId(data["id"])}, {"$set": {"status": "open"}})
    wo = shop_db.work_orders.find_one({"_id": ObjectId(data["id"])})
    labor_id = wo["labors"][0]["labor_id"]

    _post_json(client, "/work_orders/api/mechanic/timers/start",
               {"work_order_id": data["id"], "labor_id": labor_id})
    wo = shop_db.work_orders.find_one({"_id": ObjectId(data["id"])})
    assert wo["status"] == "in_progress"

    _post_json(client, "/work_orders/api/mechanic/timers/stop", {})


def test_timer_start_foreign_shop_wo_404(client, mech_seed, mongo):
    # WO чужого магазина (shop B) недоступен — изоляция тенантов.
    foreign_wo = {
        "_id": ObjectId(),
        "shop_id": ObjectId(),
        "wo_number": 900010,
        "status": "open",
        "labors": [{"labor_id": str(ObjectId()), "labor": {}, "parts": []}],
        "is_active": True,
    }
    mongo["roobico_test_shop_b"].work_orders.insert_one(foreign_wo)

    login_mechanic(client)
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start", {
        "work_order_id": str(foreign_wo["_id"]),
        "labor_id": foreign_wo["labors"][0]["labor_id"],
    })
    assert resp.status_code == 404


# ── labor_id / детали механика ──────────────────────────────────────


def test_mechanic_details_lazy_persists_labor_ids(client, mech_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    wo_doc = {
        "shop_id": mech_seed["customer"]["shop_id"],
        "wo_number": 900003,
        "customer_id": mech_seed["customer"]["_id"],
        "unit_id": mech_seed["unit"]["_id"],
        "status": "open",
        "labors": [{  # легаси-блок без labor_id
            "labor": {"description": "Legacy job", "hours": "1", "rate_code": "standard"},
            "parts": [],
        }],
        "is_active": True,
        "created_at": _now(),
    }
    shop_db.work_orders.insert_one(wo_doc)

    login_mechanic(client)
    resp = client.get(f"/work_orders/api/mechanic/work_orders/{wo_doc['_id']}")
    assert resp.status_code == 200
    body = resp.get_json()
    labor = body["labors"][0]
    assert labor["labor_id"]
    # без денег и часов
    assert "hours" not in labor and "price" not in str(body)

    persisted = shop_db.work_orders.find_one({"_id": wo_doc["_id"]})
    assert persisted["labors"][0]["labor_id"] == labor["labor_id"]


def test_mobile_details_mechanic_shape(client, mech_seed):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Mobile shape test")
    resp = client.get(f"/api/mobile/work_orders/{data['id']}")
    body = resp.get_json()
    assert body["ok"] is True
    assert "t" not in body                       # нет тоталов
    assert "raw_labors" not in body
    assert body["labors"][0]["labor_id"]
    assert "price" not in str(body)


def test_mobile_details_owner_keeps_full_shape(client, mech_seed):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Owner shape test")

    login(client)
    body = client.get(f"/api/mobile/work_orders/{data['id']}").get_json()
    assert "t" in body and "raw_labors" in body
    assert body["raw_labors"][0]["labor_id"]


def test_normalize_saved_labors_keeps_labor_id(app):
    from app.blueprints.work_orders.services.totals import normalize_saved_labors

    with app.app_context():
        out = normalize_saved_labors([
            {"labor_id": "abc123", "labor": {"description": "keep"}, "parts": []},
            {"labor": {"description": "generate"}, "parts": []},
        ])
    assert out[0]["labor_id"] == "abc123"
    assert out[1]["labor_id"]


# ── менеджерская сторона ────────────────────────────────────────────


def test_manager_details_page_contains_time_summary(client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Tracked display test")
    wo = mongo[SHOP_A_DB].work_orders.find_one({"_id": ObjectId(data["id"])})
    labor_id = wo["labors"][0]["labor_id"]
    _post_json(client, "/work_orders/api/mechanic/timers/start",
               {"work_order_id": data["id"], "labor_id": labor_id})
    _post_json(client, "/work_orders/api/mechanic/timers/stop", {})

    login(client)
    resp = client.get(f"/work_orders/details?work_order_id={data['id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "woTimeSummary" in html
    assert labor_id in html


def test_mechanic_hours_report_includes_tracked(app, client, mech_seed, mongo):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Report tracked test")
    wo = mongo[SHOP_A_DB].work_orders.find_one({"_id": ObjectId(data["id"])})
    labor_id = wo["labors"][0]["labor_id"]
    _post_json(client, "/work_orders/api/mechanic/timers/start",
               {"work_order_id": data["id"], "labor_id": labor_id})
    _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    # Лог с ненулевой длительностью для проверки суммирования.
    mongo[SHOP_A_DB].wo_time_logs.update_many(
        {"labor_id": labor_id}, {"$set": {"seconds": 3600}}
    )

    from app.blueprints.reports.audit.routes import _report_mechanic_hours

    with app.app_context():
        report = _report_mechanic_hours(
            mongo[SHOP_A_DB], mech_seed["customer"]["shop_id"], {}, chart_bucket="month"
        )

    assert "total_tracked_hours" in report["summary"]
    tracked_rows = [r for r in report["rows"] if r.get("tracked_hours", 0) > 0]
    assert tracked_rows, "mechanic tracked hours should appear in report"
    assert tracked_rows[0]["tracked_hours"] >= 1.0


# ── синк дефолтных ролей ────────────────────────────────────────────


def test_sync_default_roles_additive_and_idempotent(mongo):
    from app.scripts.sync_default_roles import sync_tenant_roles

    tdb = mongo["roobico_test_role_sync"]
    tdb.roles.delete_many({})
    tdb.roles.insert_many([
        {"key": "mechanic", "is_system": True,
         "permissions": ["calendar.view", "work_orders.view", "attachments.view"]},
        {"key": "viewer", "is_system": True, "permissions": ["work_orders.view"]},
        {"key": "custom_role", "is_system": False, "permissions": ["work_orders.view"]},
    ])

    changes = sync_tenant_roles(tdb)
    changed_keys = {k for k, _ in changes}
    assert "mechanic" in changed_keys and "viewer" in changed_keys

    mech = tdb.roles.find_one({"key": "mechanic"})
    assert "work_orders.create" in mech["permissions"]
    assert "attachments.upload" in mech["permissions"]
    assert "calendar.view" in mech["permissions"]  # ничего не удалено

    viewer = tdb.roles.find_one({"key": "viewer"})
    assert "work_orders.view_costs" in viewer["permissions"]

    custom = tdb.roles.find_one({"key": "custom_role"})
    assert custom["permissions"] == ["work_orders.view"]  # кастомная роль не тронута

    # идемпотентность
    assert sync_tenant_roles(tdb) == []
    tdb.client.drop_database("roobico_test_role_sync")


# ── несколько механиков на одной работе + «сейчас в работе» ─────────

MECHANIC2_EMAIL = "mechanic2@test.local"


@pytest.fixture(scope="module")
def second_mechanic(app, seed, mech_seed, mongo):
    master = mongo["roobico_test_master"]
    user = {
        "_id": ObjectId(),
        "email": MECHANIC2_EMAIL,
        "password_hash": generate_password_hash(MECHANIC_PASSWORD),
        "first_name": "Bob",
        "last_name": "Torque",
        "is_active": True,
        "tenant_id": seed["tenant_a"]["_id"],
        "shop_ids": [str(seed["shop_a"]["_id"])],
        "role": "mechanic",
        "created_at": _now(),
    }
    master.users.insert_one(user)
    return user


def test_two_mechanics_same_labor_and_active_list(app, client, mech_seed, second_mechanic, mongo):
    shop_db = mongo[SHOP_A_DB]

    # Первый механик стартует таймер.
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="Team job")
    wo = shop_db.work_orders.find_one({"_id": ObjectId(data["id"])})
    labor_id = wo["labors"][0]["labor_id"]
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": data["id"], "labor_id": labor_id})
    assert resp.status_code == 200

    # Второй механик стартует таймер на ТОЙ ЖЕ работе — таймер первого
    # не трогается, оба идут параллельно.
    client2 = app.test_client()
    login(client2, email=MECHANIC2_EMAIL, password=MECHANIC_PASSWORD)
    token2 = get_csrf_token(client2)
    resp = client2.post("/work_orders/api/mechanic/timers/start",
                        json={"work_order_id": data["id"], "labor_id": labor_id},
                        headers={"X-CSRFToken": token2})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stopped_previous"] is None  # чужой таймер не остановлен

    bucket = body["time_summary"][labor_id]
    assert bucket["my_running"] is True
    assert len(bucket["running_users"]) == 2
    names = {u["user_name"] for u in bucket["running_users"]}
    assert names == {"Mike Wrench", "Bob Torque"}
    mine_flags = {u["user_name"]: u["mine"] for u in bucket["running_users"]}
    assert mine_flags["Bob Torque"] is True and mine_flags["Mike Wrench"] is False

    running_logs = list(shop_db.wo_time_logs.find({"labor_id": labor_id, "stopped_at": None}))
    assert len(running_logs) == 2

    # «Сейчас в работе»: оба таймера видны в active-списке.
    resp = client.get("/work_orders/api/mechanic/timers/active")
    items = [i for i in resp.get_json()["items"] if i["labor_id"] == labor_id]
    assert len(items) == 2
    by_name = {i["user_name"]: i for i in items}
    assert by_name["Mike Wrench"]["mine"] is True      # для первого клиента
    assert by_name["Bob Torque"]["mine"] is False
    assert by_name["Mike Wrench"]["wo_number"] == data["wo_number"]
    assert by_name["Mike Wrench"]["labor_description"] == "Team job"

    # Работа по частям: первый стопает и стартует снова — сессии копятся.
    _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    shop_db.wo_time_logs.update_one(
        {"labor_id": labor_id, "user_id": mech_seed["user"]["_id"], "stopped_at": {"$ne": None}},
        {"$set": {"seconds": 600}},
    )
    resp = _post_json(client, "/work_orders/api/mechanic/timers/start",
                      {"work_order_id": data["id"], "labor_id": labor_id})
    bucket = resp.get_json()["time_summary"][labor_id]
    assert bucket["completed_seconds"] == 600          # завершённая сессия в базе
    assert len(bucket["running_users"]) == 2           # обе идущие: моя новая + Bob

    # cleanup: стопаем оба
    _post_json(client, "/work_orders/api/mechanic/timers/stop", {})
    client2.post("/work_orders/api/mechanic/timers/stop", json={},
                 headers={"X-CSRFToken": token2})


# ── пресеты и создание юнита механиком ──────────────────────────────


def test_preset_detail_stripped_for_mechanic(client, mech_seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    preset_id = shop_db.wo_presets.insert_one({
        "shop_id": mech_seed["customer"]["shop_id"],
        "name": "Brake service",
        "description": "Full brake job",
        "labor_hours": 2,
        "labor_rate_code": "standard",
        "parts": [{
            "part_id": str(mech_seed["part"]["_id"]),
            "part_number": "MECH-BRAKE-01",
            "description": "Brake pad set",
            "qty": 2,
            "cost": 40.0,
            "price": 99.5,
        }],
        "is_active": True,
        "created_at": _now(),
    }).inserted_id

    login_mechanic(client)
    presets = client.get("/work_orders/api/presets").get_json()
    assert any(p["id"] == str(preset_id) for p in presets)

    body = client.get(f"/work_orders/api/presets/{preset_id}").get_json()
    assert body["name"] == "Brake service"
    part = body["parts"][0]
    for key in ("cost", "price", "core_cost", "core_has_charge", "misc_charges"):
        assert key not in part
    assert part["part_number"] == "MECH-BRAKE-01"
    assert part["qty"] == 2

    # Владелец видит цены как раньше.
    login(client)
    body = client.get(f"/work_orders/api/presets/{preset_id}").get_json()
    assert body["parts"][0]["cost"] == 40.0
    assert body["parts"][0]["price"] == 99.5


def test_mechanic_can_create_unit(client, mech_seed, mongo):
    login_mechanic(client)
    resp = _post_json(client, "/api/mobile/units", {
        "customer_id": str(mech_seed["customer"]["_id"]),
        "unit_number": "M-NEW-7",
        "vin": "NEWVIN0000000007",
        "make": "Ford",
        "model": "F-350",
        "year": 2024,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True and body["id"]

    unit = mongo[SHOP_A_DB].units.find_one({"_id": ObjectId(body["id"])})
    assert unit["unit_number"] == "M-NEW-7"
    assert unit["customer_id"] == mech_seed["customer"]["_id"]
    assert unit["shop_id"] == mech_seed["customer"]["shop_id"]
    assert unit.get("search_terms")


def test_wo_pdf_forbidden_for_mechanic(client, mech_seed):
    login_mechanic(client)
    data = _create_wo_as_mechanic(client, mech_seed, description="PDF test")
    resp = client.get(f"/work_orders/api/work_orders/{data['id']}/download-pdf")
    assert resp.status_code == 403
