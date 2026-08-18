"""Bulk-платежи: распределение одного платежа по нескольким work orders.

Проверяет список клиентов с балансом, список неоплаченных инвойсов,
атомарность (всё или ничего), защиту от оверпеймента/эстимейтов/чужих клиентов.
"""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB, get_csrf_token, login


@pytest.fixture()
def logged_in(client):
    resp = login(client)
    assert resp.status_code == 302
    return client


@pytest.fixture()
def bulk_setup(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        shop_id = seed["shop_a"]["_id"]
        now = datetime.now(timezone.utc)

        customer = {
            "_id": ObjectId(), "shop_id": shop_id, "company_name": "Acme Fleet",
            "contacts": [], "is_active": True, "created_at": now,
        }
        other_customer = {
            "_id": ObjectId(), "shop_id": shop_id, "company_name": "Other Co",
            "contacts": [], "is_active": True, "created_at": now,
        }
        db.customers.insert_many([customer, other_customer])

        def make_wo(cust_id, total, wo_number, status="open", age_days=0):
            doc = {
                "_id": ObjectId(),
                "shop_id": shop_id,
                "customer_id": cust_id,
                "is_active": True,
                "status": status,
                "wo_number": wo_number,
                "totals": {"grand_total": float(total)},
                "created_at": now - timedelta(days=age_days),
            }
            db.work_orders.insert_one(doc)
            return doc

        wo_old = make_wo(customer["_id"], 100.0, 9101, age_days=3)
        wo_new = make_wo(customer["_id"], 200.0, 9102, age_days=1)
        estimate = make_wo(customer["_id"], 50.0, 9103, status="estimate", age_days=2)
        foreign_wo = make_wo(other_customer["_id"], 300.0, 9104, age_days=2)
        # WO в работе (не подтверждён менеджером) — в bulk-оплату не попадает.
        wo_progress = make_wo(customer["_id"], 400.0, 9105, status="in_progress", age_days=2)

        data = {
            "db": db,
            "customer": customer,
            "other_customer": other_customer,
            "wo_old": wo_old,
            "wo_new": wo_new,
            "estimate": estimate,
            "foreign_wo": foreign_wo,
            "wo_progress": wo_progress,
        }
        yield data

        wo_ids = [wo_old["_id"], wo_new["_id"], estimate["_id"], foreign_wo["_id"], wo_progress["_id"]]
        db.work_order_payments.delete_many({"work_order_id": {"$in": wo_ids}})
        db.work_orders.delete_many({"_id": {"$in": wo_ids}})
        db.customers.delete_many({"_id": {"$in": [customer["_id"], other_customer["_id"]]}})


def _bulk_pay(client, allocations, customer_id=None, **extra):
    token = get_csrf_token(client)
    payload = {"allocations": allocations, "payment_method": "check", **extra}
    if customer_id is not None:
        payload["customer_id"] = str(customer_id)
    return client.post(
        "/work_orders/api/bulk-payments",
        json=payload,
        headers={"X-CSRFToken": token},
    )


def _payments_count(setup):
    return setup["db"].work_order_payments.count_documents(
        {
            "work_order_id": {"$in": [setup["wo_old"]["_id"], setup["wo_new"]["_id"], setup["estimate"]["_id"]]},
            "is_active": True,
        }
    )


def test_customers_with_balance_listed(logged_in, bulk_setup):
    resp = logged_in.get("/work_orders/api/bulk-payments/customers")
    data = resp.get_json()
    assert data["ok"] is True

    entry = next((c for c in data["customers"] if c["id"] == str(bulk_setup["customer"]["_id"])), None)
    assert entry is not None
    assert entry["label"] == "Acme Fleet"
    # Эстимейт в долг не входит.
    assert entry["invoices_count"] == 2
    assert entry["balance_due"] == 300.0


def test_unpaid_list_oldest_first_and_reflects_partial_payment(logged_in, bulk_setup, app):
    cust_id = bulk_setup["customer"]["_id"]

    # Частично оплатим новый WO обычным (одиночным) платежом.
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/work_orders/{bulk_setup['wo_new']['_id']}/payment",
        json={"amount": 50, "payment_method": "cash"},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is True

    resp = logged_in.get(f"/work_orders/api/bulk-payments/customers/{cust_id}/unpaid")
    data = resp.get_json()
    assert data["ok"] is True

    rows = data["work_orders"]
    assert [r["wo_number"] for r in rows] == [9101, 9102]  # старые первыми, без эстимейта
    assert rows[0]["balance"] == 100.0
    assert rows[1]["paid_amount"] == 50.0
    assert rows[1]["balance"] == 150.0


def test_bulk_payment_pays_multiple_invoices(logged_in, bulk_setup, app):
    resp = _bulk_pay(
        logged_in,
        [
            {"work_order_id": str(bulk_setup["wo_old"]["_id"]), "amount": 100},
            {"work_order_id": str(bulk_setup["wo_new"]["_id"]), "amount": 200},
        ],
        customer_id=bulk_setup["customer"]["_id"],
        notes="Check #777",
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["applied_total"] == 300.0
    assert data["invoices_paid_in_full"] == 2
    assert all(r["is_fully_paid"] for r in data["results"])

    with app.app_context():
        db = bulk_setup["db"]
        for wo in (bulk_setup["wo_old"], bulk_setup["wo_new"]):
            assert db.work_orders.find_one({"_id": wo["_id"]})["status"] == "paid"

        payments = list(
            db.work_order_payments.find(
                {"work_order_id": {"$in": [bulk_setup["wo_old"]["_id"], bulk_setup["wo_new"]["_id"]]}}
            )
        )
    assert len(payments) == 2
    # Платежи одной пачки связаны общим bulk_group_id.
    group_ids = {p["bulk_group_id"] for p in payments}
    assert len(group_ids) == 1
    assert str(next(iter(group_ids))) == data["bulk_group_id"]
    assert all(p["payment_method"] == "check" and p["notes"] == "Check #777" for p in payments)


def test_bulk_partial_allocation_keeps_open(logged_in, bulk_setup, app):
    resp = _bulk_pay(
        logged_in,
        [{"work_order_id": str(bulk_setup["wo_new"]["_id"]), "amount": 50}],
        customer_id=bulk_setup["customer"]["_id"],
    )
    data = resp.get_json()
    assert data["ok"] is True
    result = data["results"][0]
    assert result["is_fully_paid"] is False
    assert result["status"] == "open"
    assert result["remaining_balance"] == 150.0


def test_bulk_overpayment_rejects_whole_request(logged_in, bulk_setup, app):
    resp = _bulk_pay(
        logged_in,
        [
            {"work_order_id": str(bulk_setup["wo_old"]["_id"]), "amount": 100},
            {"work_order_id": str(bulk_setup["wo_new"]["_id"]), "amount": 250},
        ],
        customer_id=bulk_setup["customer"]["_id"],
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "overpayment"
    assert data["work_order_id"] == str(bulk_setup["wo_new"]["_id"])

    # Всё или ничего: валидная первая строка тоже не записалась.
    with app.app_context():
        assert _payments_count(bulk_setup) == 0


def test_bulk_rejects_estimates(logged_in, bulk_setup, app):
    resp = _bulk_pay(
        logged_in,
        [{"work_order_id": str(bulk_setup["estimate"]["_id"]), "amount": 10}],
        customer_id=bulk_setup["customer"]["_id"],
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "not_payable"
    with app.app_context():
        assert _payments_count(bulk_setup) == 0


def test_bulk_excludes_and_rejects_in_progress(logged_in, bulk_setup, app):
    """WO в работе (не подтверждённый) не виден в списках и не принимает оплату."""
    cust_id = bulk_setup["customer"]["_id"]

    # Не в списке неоплаченных инвойсов клиента.
    rows = logged_in.get(f"/work_orders/api/bulk-payments/customers/{cust_id}/unpaid").get_json()["work_orders"]
    assert 9105 not in [r["wo_number"] for r in rows]

    # Не входит в баланс клиента (100 + 200, без in_progress 400).
    customers = logged_in.get("/work_orders/api/bulk-payments/customers").get_json()["customers"]
    entry = next(c for c in customers if c["id"] == str(cust_id))
    assert entry["invoices_count"] == 2
    assert entry["balance_due"] == 300.0

    # Прямая попытка оплатить — отказ всего запроса.
    resp = _bulk_pay(
        logged_in,
        [
            {"work_order_id": str(bulk_setup["wo_old"]["_id"]), "amount": 100},
            {"work_order_id": str(bulk_setup["wo_progress"]["_id"]), "amount": 10},
        ],
        customer_id=cust_id,
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "not_payable"
    assert "in progress" in data["message"]
    with app.app_context():
        assert _payments_count(bulk_setup) == 0


def test_bulk_rejects_foreign_customer_invoice(logged_in, bulk_setup, app):
    resp = _bulk_pay(
        logged_in,
        [{"work_order_id": str(bulk_setup["foreign_wo"]["_id"]), "amount": 10}],
        customer_id=bulk_setup["customer"]["_id"],
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "customer_mismatch"


def test_bulk_invalid_payloads_rejected(logged_in, bulk_setup):
    # Пустое распределение.
    data = _bulk_pay(logged_in, []).get_json()
    assert data["ok"] is False
    assert data["error"] == "no_allocations"

    # Нулевая/отрицательная сумма.
    for bad in (0, -5, "abc", None):
        data = _bulk_pay(
            logged_in,
            [{"work_order_id": str(bulk_setup["wo_old"]["_id"]), "amount": bad}],
        ).get_json()
        assert data["ok"] is False
        assert data["error"] == "invalid_amount"

    # Один и тот же инвойс дважды.
    data = _bulk_pay(
        logged_in,
        [
            {"work_order_id": str(bulk_setup["wo_old"]["_id"]), "amount": 10},
            {"work_order_id": str(bulk_setup["wo_old"]["_id"]), "amount": 20},
        ],
    ).get_json()
    assert data["ok"] is False
    assert data["error"] == "duplicate_work_order"
