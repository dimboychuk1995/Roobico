"""
Estimates: сохранение WO как сметы (без списания склада), смета остаётся
сметой при обычном Save, конверсия в рабочий WO со списанием, запрет
платежей, отправка сметы на авторизацию клиенту (тот же флоу, что WO).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB, get_csrf_token, login


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


@pytest.fixture()
def env(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        customer_id = ObjectId()
        db.customers.insert_one({
            "_id": customer_id, "shop_id": seed["shop_a"]["_id"],
            "company_name": "Estimate Fleet", "contacts": [],
            "is_active": True, "created_at": datetime.now(timezone.utc),
        })
        unit_id = ObjectId()
        part = {
            "_id": ObjectId(),
            "shop_id": seed["shop_a"]["_id"],
            "part_number": "EST-PART-1",
            "description": "Estimate test part",
            "in_stock": 10,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        db.parts.insert_one(part)
        yield {"db": db, "part": part, "customer_id": customer_id, "unit_id": unit_id,
               "shop_id": seed["shop_a"]["_id"]}
        db.parts.delete_one({"_id": part["_id"]})
        db.work_orders.delete_many({"customer_id": customer_id})
        db.customers.delete_one({"_id": customer_id})
        db.cores.delete_many({"part_id": part["_id"]})
        db.inventory_movements.delete_many({"part_id": part["_id"]})
        db.part_location_stock.delete_many({"part_id": part["_id"]})


def _stock(env):
    return int(env["db"].parts.find_one({"_id": env["part"]["_id"]})["in_stock"])


def _create_estimate(client, env):
    token = get_csrf_token(client)
    resp = client.post("/work_orders/create", data={
        "csrf_token": token,
        "customer_id": str(env["customer_id"]),
        "unit_id": str(env["unit_id"]),
        "create_status": "estimate",
        "labors[0][labor_description]": "Estimate job",
        "labors[0][labor_full_total]": "100",
        "labors[0][parts][0][part_number]": env["part"]["part_number"],
        "labors[0][parts][0][part_id]": str(env["part"]["_id"]),
        "labors[0][parts][0][qty]": "2",
        "labors[0][parts][0][price]": "5",
    })
    assert resp.status_code in (200, 302)
    wo = env["db"].work_orders.find_one({"customer_id": env["customer_id"], "is_active": True})
    assert wo is not None
    return wo


def _flat_labor(parts=None):
    return {
        "labor_description": "Estimate job", "labor_hours": 0, "labor_rate_code": "",
        "labor_full_total": 100, "assigned_mechanics": [], "issue_description": "",
        "parts": parts if parts is not None else [{
            "part_id": None, "one_time_part": False, "part_number": "EST-PART-1",
            "description": "Estimate test part", "qty": 2, "cost": 0, "price": 5,
            "core_charge": 0, "misc_charge": 0, "misc_charge_description": "",
        }],
    }


def _update(client, wo_id, save_status):
    token = get_csrf_token(client)
    return client.post(
        f"/work_orders/api/work_orders/{wo_id}/update",
        json={"labors": [_flat_labor()], "totals": {}, "save_status": save_status},
        headers={"X-CSRFToken": token},
    )


def test_create_estimate_no_deduction(logged_in, env):
    wo = _create_estimate(logged_in, env)
    assert wo["status"] == "estimate"
    assert _stock(env) == 10, "смета не списывает склад"
    assert wo.get("inventory_deducted") is False
    assert wo.get("inventory_deductions") == []
    assert env["db"].cores.count_documents({"part_id": env["part"]["_id"]}) == 0

    # В API эстимейтов есть, в основном списке WO — нет
    api = logged_in.get("/work_orders/api/estimates?date_preset=all_time").get_json()
    numbers = [e.get("wo_number") for e in api.get("estimates") or []]
    assert wo["wo_number"] in numbers

    page = logged_in.get("/work_orders/?date_preset=all_time").get_data(as_text=True)
    assert str(wo["wo_number"]) not in page


def test_save_keeps_estimate_and_stock(logged_in, env):
    wo = _create_estimate(logged_in, env)
    resp = _update(logged_in, wo["_id"], "estimate")
    data = resp.get_json()
    assert data["ok"] is True, data
    assert data["status"] == "estimate"
    assert _stock(env) == 10
    assert env["db"].work_orders.find_one({"_id": wo["_id"]})["status"] == "estimate"


def test_convert_estimate_deducts_stock(logged_in, env):
    wo = _create_estimate(logged_in, env)
    resp = _update(logged_in, wo["_id"], "open")
    data = resp.get_json()
    assert data["ok"] is True, data
    assert data["status"] == "open"
    assert _stock(env) == 8, "конверсия списывает все парты сметы"
    fresh = env["db"].work_orders.find_one({"_id": wo["_id"]})
    assert fresh["status"] == "open"
    assert fresh.get("inventory_deducted") is True
    assert len(fresh.get("inventory_deductions") or []) == 1


def test_normal_wo_cannot_become_estimate(logged_in, env):
    wo = _create_estimate(logged_in, env)
    _update(logged_in, wo["_id"], "open")          # конверсия (склад 8)
    resp = _update(logged_in, wo["_id"], "estimate")  # попытка обратно
    data = resp.get_json()
    assert data["ok"] is True
    fresh = env["db"].work_orders.find_one({"_id": wo["_id"]})
    assert fresh["status"] == "open", "обратной конверсии WO → estimate нет"
    assert _stock(env) == 8, "склад не двинулся"


def test_estimate_rejects_payments(logged_in, env):
    wo = _create_estimate(logged_in, env)
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/work_orders/{wo['_id']}/payment",
        json={"amount": 50, "payment_method": "cash"},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "estimate_cannot_receive_payments"
    assert env["db"].work_order_payments.count_documents({"work_order_id": wo["_id"]}) == 0


def _download_pdf_html(client, wo_id, monkeypatch):
    """GET download-pdf с перехватом HTML, из которого рендерится PDF."""
    import app.blueprints.work_orders.routes as wo_routes

    captured = {}

    def fake_render(html):
        captured["html"] = html
        return b"%PDF-fake"

    monkeypatch.setattr(wo_routes, "render_html_to_pdf", fake_render)
    resp = client.get(f"/work_orders/api/work_orders/{wo_id}/download-pdf")
    assert resp.status_code == 200
    return resp, captured["html"]


def test_estimate_pdf_says_estimate_and_shows_issue(logged_in, env, monkeypatch):
    """PDF сметы подписан Estimate (не Work Order) и содержит описание
    проблемы клиента (issue_description) из лейбора."""
    wo = _create_estimate(logged_in, env)

    labor = _flat_labor()
    labor["issue_description"] = "Grinding noise when braking"
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/work_orders/{wo['_id']}/update",
        json={"labors": [labor], "totals": {}, "save_status": "estimate"},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is True

    resp, html = _download_pdf_html(logged_in, wo["_id"], monkeypatch)
    assert "Estimate-" in resp.headers["Content-Disposition"]
    assert "Estimate:" in html
    assert "Work Order:" not in html
    assert "Reported issue: Grinding noise when braking" in html


def test_wo_pdf_says_work_order_and_shows_issue(logged_in, env, monkeypatch):
    """После конверсии в WO PDF снова подписан Work Order; described issue
    печатается и там."""
    wo = _create_estimate(logged_in, env)

    labor = _flat_labor()
    labor["issue_description"] = "Check engine light on"
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/work_orders/{wo['_id']}/update",
        json={"labors": [labor], "totals": {}, "save_status": "open"},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is True

    resp, html = _download_pdf_html(logged_in, wo["_id"], monkeypatch)
    assert "WorkOrder-" in resp.headers["Content-Disposition"]
    assert "Work Order:" in html
    assert "Reported issue: Check engine light on" in html


def test_estimate_authorization_flow(logged_in, env, app, monkeypatch):
    """Смета отправляется клиенту тем же флоу, что WO; аппрув пишет
    authorizations[] — на странице это тот же бейдж Authorized."""
    import app.blueprints.work_orders.routes as wo_routes

    sent = {}

    def fake_send_email(*args, **kwargs):
        sent["args"] = args
        return True

    monkeypatch.setattr(wo_routes, "send_email", fake_send_email)
    # PDF сметы для письма не рендерим в тесте
    monkeypatch.setattr(
        wo_routes, "render_html_to_pdf", lambda *a, **k: b"%PDF-fake", raising=False)

    wo = _create_estimate(logged_in, env)
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/work_orders/{wo['_id']}/send-authorization",
        json={"emails": ["client@estimate.example"], "scope": "work_order"},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data.get("ok") is True, data
    assert sent, "письмо должно быть отправлено"

    from app.extensions import get_master_db
    with app.app_context():
        auth = get_master_db().work_order_authorizations.find_one(
            {"work_order_id": wo["_id"], "recipient_email": "client@estimate.example"})
    assert auth is not None and auth["status"] == "pending"

    # Клиент открывает публичную страницу и жмёт Approve
    resp = logged_in.post(f"/authorize/{auth['token']}", data={"decision": "approve"})
    assert resp.status_code in (200, 302)

    fresh = env["db"].work_orders.find_one({"_id": wo["_id"]})
    entries = fresh.get("authorizations") or []
    assert entries and entries[-1]["status"] == "approved"
    assert fresh["status"] == "estimate", "аппрув не меняет статус сметы"
    assert _stock(env) == 10, "аппрув не трогает склад"
