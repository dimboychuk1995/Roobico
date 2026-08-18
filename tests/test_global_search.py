"""Глобальный поиск: статус оплаты парт-ордеров считается по parts_order_id."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def order_seed(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = datetime.now(timezone.utc)

    vendor = {"_id": ObjectId(), "shop_id": shop_id, "name": "Search Vendor", "is_active": True, "created_at": now}
    shop_db.vendors.insert_one(vendor)

    def make_order(number, total):
        doc = {
            "_id": ObjectId(),
            "shop_id": shop_id,
            "vendor_id": vendor["_id"],
            "order_number": number,
            "vendor_bill": f"BILL-{number}",
            "status": "received",
            "items": [{"quantity": 1, "price": float(total)}],
            "non_inventory_amounts": [],
            "is_active": True,
            "created_at": now,
        }
        shop_db.parts_orders.insert_one(doc)
        return doc

    paid_order = make_order(93310, 120.0)
    unpaid_order = make_order(93311, 80.0)
    shop_db.parts_order_payments.insert_one({
        "_id": ObjectId(),
        "shop_id": shop_id,
        "parts_order_id": paid_order["_id"],
        "amount": 120.0,
        "is_active": True,
        "created_at": now,
    })

    yield {"paid": paid_order, "unpaid": unpaid_order}

    shop_db.parts_order_payments.delete_many({"parts_order_id": paid_order["_id"]})
    shop_db.parts_orders.delete_many({"_id": {"$in": [paid_order["_id"], unpaid_order["_id"]]}})
    shop_db.vendors.delete_one({"_id": vendor["_id"]})


def _order_labels(client, q):
    data = client.get(f"/api/global-search?q={q}").get_json()
    group = next((g for g in data["results"] if g["category"] == "Part Orders"), None)
    return [i["label"] for i in (group["items"] if group else [])]


def test_global_search_part_order_payment_status(client, order_seed):
    login(client)

    labels = _order_labels(client, "93310")
    assert labels, "paid order should be found"
    assert "Paid" in labels[0] and "Unpaid" not in labels[0]

    labels = _order_labels(client, "93311")
    assert labels, "unpaid order should be found"
    assert "Unpaid" in labels[0]
