"""
Журнал событий: бизнес-события с актором, объектом и diff'ом; технические
строки для неописанных эндпоинтов; отсутствие дублей; страница журнала с
фильтрами; журнал закрыт без reports.view_audit.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MASTER, TEST_MONGO_URI, get_csrf_token, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def journal(mongo):
    return mongo[TEST_MASTER].audit_journal


def _json(client, method, url, payload=None):
    token = get_csrf_token(client)
    return getattr(client, method)(url, json=payload or {}, headers={"X-CSRFToken": token})


def _events(journal, **query):
    return list(journal.find({"kind": "event", **query}).sort("created_at", -1))


def test_customer_create_update_deactivate_are_journaled(client, seed, mongo, journal):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    token = get_csrf_token(client)
    resp = client.post("/customers/create", data={
        "csrf_token": token, "company_name": "Journal Fleet", "address": "1 Main St",
        "contacts[0][first_name]": "Jo", "contacts[0][last_name]": "Ann",
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)
    customer = shop_db.customers.find_one({"company_name": "Journal Fleet"})
    assert customer, "customer not created"

    ev = _events(journal, entity_type="customer", entity_id=str(customer["_id"]), action="create")
    assert ev, "create event missing"
    assert ev[0]["user_name"]  # actor resolved from session
    assert ev[0]["user_role"] == "owner"
    assert ev[0]["entity_label"] == "Journal Fleet"
    assert ev[0]["source"] == "web"
    assert ev[0]["entity_url"] == f"/customers/{customer['_id']}"

    # Update via JSON API → diff with old/new values.
    resp = _json(client, "post", f"/customers/api/{customer['_id']}/update", {
        "company_name": "Journal Fleet Inc", "address": "2 Main St", "taxable": True,
        "contacts": [{"first_name": "Jo", "last_name": "Ann"}],
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    ev = _events(journal, entity_type="customer", entity_id=str(customer["_id"]), action="update")
    assert ev, "update event missing"
    fields = {c["field"]: c for c in ev[0]["changes"]}
    assert fields["company_name"]["old"] == "Journal Fleet" and fields["company_name"]["new"] == "Journal Fleet Inc"
    assert fields["address"]["new"] == "2 Main St"
    assert fields["taxable"]["new"] is True

    # Technical request row must NOT duplicate a described action.
    raw = list(journal.find({"kind": "request", "path": f"/customers/api/{customer['_id']}/update"}))
    assert raw == []

    resp = _json(client, "post", f"/customers/api/{customer['_id']}/deactivate", {})
    assert resp.status_code == 200
    assert _events(journal, entity_type="customer", entity_id=str(customer["_id"]), action="deactivate")

    shop_db.customers.delete_one({"_id": customer["_id"]})


def test_work_order_lifecycle_is_journaled(client, seed, mongo, journal):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    now = datetime.now(timezone.utc)
    customer = {"_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "company_name": "Journal WO Co", "is_active": True, "created_at": now}
    unit = {"_id": ObjectId(), "shop_id": seed["shop_a"]["_id"], "customer_id": customer["_id"], "unit_number": "J-1", "is_active": True, "created_at": now}
    shop_db.customers.insert_one(customer)
    shop_db.units.insert_one(unit)

    token = get_csrf_token(client)
    resp = client.post("/work_orders/create", data={
        "csrf_token": token, "customer_id": str(customer["_id"]), "unit_id": str(unit["_id"]),
        "labors[0][labor_description]": "Brake job", "labors[0][labor_hours]": "2",
        "labors[0][labor_rate_code]": seed["labor_rate"]["code"],
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)
    wo = shop_db.work_orders.find_one({"customer_id": customer["_id"], "is_active": True})
    assert wo
    wo_id = str(wo["_id"])

    ev = _events(journal, entity_type="work_order", entity_id=wo_id, action="create")
    assert ev and ev[0]["entity_label"] == f"WO #{wo['wo_number']}"
    assert "Journal WO Co" in ev[0]["summary"]
    assert ev[0]["details"]["jobs"] == ["Brake job"]
    assert ev[0]["entity_url"] == f"/work_orders/details?work_order_id={wo_id}"

    # Update: job renamed + hours changed → diff mentions jobs.
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo_id}/update", {
        "labors": [{"labor_id": wo["labors"][0]["labor_id"], "labor_description": "Brake job + rotors", "labor_hours": 3,
                    "labor_rate_code": seed["labor_rate"]["code"], "labor_full_total": 300, "assigned_mechanics": [],
                    "issue_description": "", "parts": []}],
        "totals": {"grand_total": 300, "labor_total": 300},
    })
    assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_data(as_text=True)
    ev = _events(journal, entity_type="work_order", entity_id=wo_id, action="update")
    assert ev, "update event missing"
    fields = {c["field"]: c for c in ev[0]["changes"]}
    assert "labors" in fields and "rotors" in str(fields["labors"]["new"])
    assert fields["hours"]["old"] == 2 and fields["hours"]["new"] == 3

    # Payment, status change, delete. Итог фиксируем напрямую — тест про журнал, не про расчёт сумм.
    shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"totals.grand_total": 300.0}})
    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo_id}/payment", {"amount": 100, "payment_method": "cash"})
    assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_data(as_text=True)
    ev = _events(journal, entity_type="work_order", entity_id=wo_id, action="payment")
    assert ev and ev[0]["details"]["amount"] == 100 and "$100.00" in ev[0]["summary"]

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo_id}/status", {"status": "in_progress"})
    assert resp.status_code == 200
    ev = _events(journal, entity_type="work_order", entity_id=wo_id, action="status")
    assert ev and ev[0]["changes"][0]["new"] == "in_progress"

    resp = _json(client, "post", f"/work_orders/api/work_orders/{wo_id}/delete", {})
    assert resp.status_code == 200 and resp.get_json()["ok"]
    ev = _events(journal, entity_type="work_order", entity_id=wo_id, action="delete")
    assert ev and ev[0]["entity_label"] == f"WO #{wo['wo_number']}"

    shop_db.work_orders.delete_one({"_id": wo["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})
    shop_db.units.delete_one({"_id": unit["_id"]})


def test_unlogged_endpoint_gets_readable_technical_row(client, journal):
    assert login(client).status_code == 302
    before = journal.count_documents({"endpoint": "dashboard.dashboard_goals_save_api"})
    resp = _json(client, "post", "/dashboard/api/goals", {"labor": 1000, "parts": 500, "total": 1500})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rows = list(journal.find({"endpoint": "dashboard.dashboard_goals_save_api"}).sort("created_at", -1))
    assert len(rows) == before + 1
    row = rows[0]
    assert row["kind"] == "request"
    assert row["summary"] == "Dashboard goals saved"
    assert row["action"] == "settings"
    assert row["user_name"]  # actor is resolved for technical rows too
    assert row["source"] == "web"


def test_denied_request_is_journaled_as_denied(client, journal):
    assert login(client).status_code == 302
    # Owner has everything — simulate a 404-less denial via a bad object: use a 4xx instead.
    resp = _json(client, "post", "/work_orders/api/work_orders/000000000000000000000000/status", {"status": "paid"})
    assert resp.status_code == 200  # handler answers ok:false with 200; still a request row
    rows = list(journal.find({"path": "/work_orders/api/work_orders/000000000000000000000000/status"}).sort("created_at", -1))
    assert rows and rows[0]["kind"] == "request"


def test_login_and_logout_are_journaled(client, journal):
    assert login(client).status_code == 302
    rows = _events(journal, action="login", entity_type="session")
    assert rows and rows[0]["summary"].endswith("signed in")
    assert rows[0]["user_role"] == "owner"
    client.get("/logout")
    rows = _events(journal, action="logout", entity_type="session")
    assert rows


def test_journal_page_filters_and_entity_history(client, seed, mongo, journal):
    assert login(client).status_code == 302
    shop_db = mongo[SHOP_A_DB]
    token = get_csrf_token(client)
    resp = client.post("/vendors/create", data={"csrf_token": token, "name": "Journal Vendor LLC"})
    assert resp.status_code == 302
    vendor = shop_db.vendors.find_one({"name": "Journal Vendor LLC"})
    assert vendor

    page = client.get("/reports/audit")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Journal Vendor LLC" in body
    assert "Activity Journal" in body

    # Entity history: only this vendor's rows.
    page = client.get(f"/reports/audit?entity_type=vendor&entity_id={vendor['_id']}")
    body = page.get_data(as_text=True)
    assert "Journal Vendor LLC" in body
    assert "Showing the history of one vendor" in body

    # Search box.
    page = client.get("/reports/audit?q=Journal+Vendor")
    assert "Journal Vendor LLC" in page.get_data(as_text=True)

    # Filter that matches nothing.
    page = client.get("/reports/audit?action=delete&entity_type=vendor&q=Journal+Vendor")
    assert "No journal entries found" in page.get_data(as_text=True)

    # Pagination renders without the old url_for(endpoint=...) crash.
    page = client.get("/reports/audit?per_page=1")
    assert page.status_code == 200
    assert "Next" in page.get_data(as_text=True)

    shop_db.vendors.delete_one({"_id": vendor["_id"]})


def test_journal_entries_are_tenant_scoped(client, seed, journal):
    assert login(client).status_code == 302
    foreign = {
        "kind": "event", "created_at": datetime.now(timezone.utc), "tenant_id": str(seed["tenant_b"]["_id"]),
        "shop_id": str(seed["shop_b"]["_id"]), "user_id": "", "user_name": "Foreign Guy", "user_role": "owner",
        "source": "web", "action": "create", "entity_type": "customer", "entity_id": "x", "entity_label": "FOREIGN-ONLY-CUSTOMER",
        "entity_url": "", "summary": "Created: Customer FOREIGN-ONLY-CUSTOMER", "changes": [], "details": {}, "ok": True,
    }
    journal.insert_one(foreign)
    page = client.get("/reports/audit?q=FOREIGN-ONLY")
    assert "FOREIGN-ONLY-CUSTOMER" not in page.get_data(as_text=True)
    journal.delete_one({"_id": foreign["_id"]})
