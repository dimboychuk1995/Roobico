"""Платёжный флоу work order: запись, статус paid, оверпеймент, удаление.

Также фиксирует контракт mongo_tx: на standalone (тестовый Mongo) операции
выполняются последовательно (session=None), на replica set — транзакцией.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB, get_csrf_token, login


@pytest.fixture()
def logged_in(client):
    resp = login(client)
    assert resp.status_code == 302
    return client


@pytest.fixture()
def work_order(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        wo = {
            "_id": ObjectId(),
            "shop_id": seed["shop_a"]["_id"],
            "customer_id": ObjectId(),
            "is_active": True,
            "status": "open",
            "totals": {"grand_total": 300.0},
            "created_at": datetime.now(timezone.utc),
        }
        db.work_orders.insert_one(wo)
        yield {"db": db, "wo": wo}
        db.work_order_payments.delete_many({"work_order_id": wo["_id"]})
        db.work_orders.delete_one({"_id": wo["_id"]})


def _pay(client, wo_id, amount, **extra):
    token = get_csrf_token(client)
    return client.post(
        f"/work_orders/api/work_orders/{wo_id}/payment",
        json={"amount": amount, "payment_method": "cash", **extra},
        headers={"X-CSRFToken": token},
    )


def test_partial_payment_keeps_open(logged_in, work_order):
    resp = _pay(logged_in, work_order["wo"]["_id"], 100)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["amount_paid"] == 100.0
    assert data["remaining_balance"] == 200.0
    assert data["status"] == "open"
    assert data["is_fully_paid"] is False


def test_full_payment_marks_paid(logged_in, work_order, app):
    _pay(logged_in, work_order["wo"]["_id"], 100)
    resp = _pay(logged_in, work_order["wo"]["_id"], 200)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["remaining_balance"] == 0.0
    assert data["status"] == "paid"
    assert data["is_fully_paid"] is True

    with app.app_context():
        wo = work_order["db"].work_orders.find_one({"_id": work_order["wo"]["_id"]})
    assert wo["status"] == "paid"


def test_overpayment_rejected(logged_in, work_order, app):
    _pay(logged_in, work_order["wo"]["_id"], 250)
    resp = _pay(logged_in, work_order["wo"]["_id"], 100)
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "overpayment"
    # Платёж не записался.
    with app.app_context():
        count = work_order["db"].work_order_payments.count_documents(
            {"work_order_id": work_order["wo"]["_id"], "is_active": True}
        )
    assert count == 1


def test_delete_payment_reopens_work_order(logged_in, work_order, app):
    _pay(logged_in, work_order["wo"]["_id"], 100)
    resp = _pay(logged_in, work_order["wo"]["_id"], 200)
    assert resp.get_json()["status"] == "paid"

    with app.app_context():
        first = work_order["db"].work_order_payments.find_one(
            {"work_order_id": work_order["wo"]["_id"], "amount": 100.0}
        )
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/payments/{first['_id']}/delete",
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["status"] == "open"
    assert data["amount_paid"] == 200.0
    assert data["remaining_balance"] == 100.0

    with app.app_context():
        wo = work_order["db"].work_orders.find_one({"_id": work_order["wo"]["_id"]})
    assert wo["status"] == "open"


def test_invalid_amount_rejected(logged_in, work_order):
    for bad in (0, -5, "abc", None):
        data = _pay(logged_in, work_order["wo"]["_id"], bad).get_json()
        assert data["ok"] is False


def test_run_atomically_fallback_on_standalone(app):
    from app.extensions import get_mongo_client
    from app.utils.mongo_tx import run_atomically, transactions_supported

    with app.app_context():
        client = get_mongo_client()
        # Тестовый Mongo — standalone: транзакции не поддерживаются,
        # callback выполняется с session=None.
        assert transactions_supported(client) is False
        seen = {}
        result = run_atomically(client, lambda s: seen.setdefault("session", s) or "done")
        assert result == "done"
        assert seen["session"] is None
