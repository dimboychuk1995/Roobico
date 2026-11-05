"""
Приёмка парт-ордера с фактическими количествами (частичная приёмка) и
откат приёмки / удаление принятого заказа «в минус».

- receive принимает item_quantities {part_id: принято}: заказ приводится к
  принятому (quantity = принято, ordered_quantity = заказывали — пишется один
  раз), 0 выкидывает строку (и last_price парта пересчитывается), все нули —
  отказ, платёж больше нового итога — отказ, мусор в количестве — 400;
- без item_quantities — полная приёмка, как раньше (мобильное приложение);
- unreceive / DELETE принятого заказа работают всегда: остаток и строка
  локации уходят в минус, ответ перечисляет такие парты в negative_stock;
- receive-context отдаёт все строки, включая неучитываемые и неактивные парты.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MASTER, TEST_MONGO_URI, get_csrf_token, login

PREFIX = "RCVQ-"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def env(client, seed, mongo):
    assert login(client).status_code == 302
    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = _now()
    loc = {"_id": ObjectId(), "shop_id": shop_id, "name": "RCVQ shelf", "parent_id": None,
           "is_active": True, "created_at": now}
    db.parts_locations.insert_one(loc)
    vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "RCVQ Vendor", "is_active": True, "created_at": now,
    }).inserted_id

    yield {"client": client, "db": db, "shop_id": shop_id, "loc": loc, "vendor_id": vendor_id,
           "journal": mongo[TEST_MASTER].audit_journal}

    part_ids = [p["_id"] for p in db.parts.find({"part_number": {"$regex": f"^{PREFIX}"}}, {"_id": 1})]
    order_ids = [o["_id"] for o in db.parts_orders.find(
        {"shop_id": shop_id, "$or": [{"items.part_id": {"$in": part_ids}}, {"vendor_id": vendor_id}]},
        {"_id": 1},
    )]
    db.parts_order_payments.delete_many({"parts_order_id": {"$in": order_ids}})
    db.parts_orders.delete_many({"_id": {"$in": order_ids}})
    db.parts.delete_many({"_id": {"$in": part_ids}})
    db.part_location_stock.delete_many({"part_id": {"$in": part_ids}})
    db.inventory_movements.delete_many({"part_id": {"$in": part_ids}})
    db.parts_locations.delete_one({"_id": loc["_id"]})
    db.vendors.delete_one({"_id": vendor_id})


# ── helpers ──────────────────────────────────────────────────────────────

def _post(env, url, payload=None):
    client = env["client"]
    token = get_csrf_token(client)
    return client.post(url, json=payload if payload is not None else {}, headers={"X-CSRFToken": token})


def _create_part(env, number, in_stock=0, location=True, **extra):
    payload = {
        "part_number": number,
        "description": f"Receive qty test {number}",
        "in_stock": in_stock,
        "location_id": str(env["loc"]["_id"]) if location else "",
    }
    payload.update(extra)
    resp = _post(env, "/parts/api/create", payload)
    data = resp.get_json()
    assert resp.status_code == 200 and data.get("ok"), data
    return ObjectId(data["part_id"])


def _insert_order(env, lines, order_number=97001, non_inventory=None):
    """lines: [(part_id, qty, price)] — заказ «ordered» напрямую в базе."""
    now = _now()
    parts = {p["_id"]: p for p in env["db"].parts.find({"_id": {"$in": [line[0] for line in lines]}})}
    items = [{
        "part_id": pid,
        "part_number": parts[pid]["part_number"],
        "description": parts[pid].get("description") or "",
        "quantity": qty,
        "price": float(price),
        "core_charge": 0.0,
    } for pid, qty, price in lines]
    total = sum(q * p for _, q, p in lines) + sum(x["amount"] for x in (non_inventory or []))
    order = {
        "_id": ObjectId(), "shop_id": env["shop_id"], "vendor_id": env["vendor_id"],
        "order_number": order_number, "status": "ordered", "vendor_bill": "",
        "items": items, "non_inventory_amounts": non_inventory or [],
        "payment_status": "unpaid", "paid_amount": 0.0, "remaining_balance": float(total),
        "is_active": True, "order_date": now, "created_at": now, "updated_at": now,
    }
    env["db"].parts_orders.insert_one(order)
    return order


def _create_order_api(env, items):
    resp = _post(env, "/parts/api/orders/create", {"vendor_id": str(env["vendor_id"]), "items": items})
    data = resp.get_json()
    assert resp.status_code == 200 and data.get("ok"), data
    return ObjectId(data["order_id"])


def _receive(env, order_id, payload):
    return _post(env, f"/parts/api/orders/{order_id}/receive", payload)


def _unreceive(env, order_id):
    return _post(env, f"/parts/api/orders/{order_id}/unreceive")


def _delete(env, order_id):
    token = get_csrf_token(env["client"])
    return env["client"].delete(f"/parts/api/orders/{order_id}", headers={"X-CSRFToken": token})


def _part(env, part_id):
    return env["db"].parts.find_one({"_id": part_id})


def _order(env, order_id):
    return env["db"].parts_orders.find_one({"_id": order_id})


def _row_qty(env, part_id, location_id):
    row = env["db"].part_location_stock.find_one(
        {"shop_id": env["shop_id"], "part_id": part_id, "location_id": location_id}
    )
    return int(row["qty"]) if row else None


def _consume_stock(env, part_id, new_qty):
    """Имитация списания на work order: остаток и строка локации = new_qty."""
    env["db"].parts.update_one({"_id": part_id}, {"$set": {"in_stock": new_qty}})
    env["db"].part_location_stock.update_one(
        {"shop_id": env["shop_id"], "part_id": part_id, "location_id": env["loc"]["_id"]},
        {"$set": {"qty": new_qty}},
    )


# ── частичная приёмка ─────────────────────────────────────────────────────

def test_partial_receive_updates_order_to_received_quantity(env):
    pid = _create_part(env, "RCVQ-PART-1")
    order = _insert_order(env, [(pid, 15, 2.0)])

    # Неизвестный part_id в item_quantities игнорируется.
    resp = _receive(env, order["_id"], {
        "vendor_bill": "B-10",
        "item_quantities": {str(pid): 10, str(ObjectId()): 99},
    })
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["updated_parts"] == 1
    assert data["quantity_changes"] == [
        {"part_id": str(pid), "part_number": "RCVQ-PART-1", "ordered": 15, "received": 10}
    ]

    assert int(_part(env, pid)["in_stock"]) == 10
    assert _row_qty(env, pid, env["loc"]["_id"]) == 10
    mv = env["db"].inventory_movements.find_one({"part_id": pid, "type": "receive"})
    assert mv and mv["qty_delta"] == 10 and mv["stock_after"] == 10

    after = _order(env, order["_id"])
    assert after["status"] == "received" and after["vendor_bill"] == "B-10"
    assert after["items"][0]["quantity"] == 10
    assert after["items"][0]["ordered_quantity"] == 15
    assert after["remaining_balance"] == pytest.approx(20.0)
    assert after["payment_status"] == "unpaid"

    # Деталка заказа отдаёт обе величины и новый итог.
    got = env["client"].get(f"/parts/api/orders/{order['_id']}").get_json()["order"]
    assert got["items"][0]["quantity"] == 10 and got["items"][0]["ordered_quantity"] == 15
    assert got["payment_summary"]["total_amount"] == pytest.approx(20.0)

    # Журнал: событие receive с изменением количества по строке.
    events = list(env["journal"].find({
        "kind": "event", "entity_type": "parts_order", "entity_id": str(order["_id"]), "action": "receive",
    }))
    assert events, "receive event missing"
    changes = {c["field"]: c for c in events[0]["changes"]}
    assert changes["items.RCVQ-PART-1.quantity"]["old"] == 15
    assert changes["items.RCVQ-PART-1.quantity"]["new"] == 10
    # details сплющиваются журналом в строку (audit_log._plain).
    assert events[0]["details"]["quantity_changes"] == ["RCVQ-PART-1: ordered 15, received 10"]
    assert "1 line(s)" in events[0]["summary"]


def test_receive_more_than_ordered_grows_the_order(env):
    pid = _create_part(env, "RCVQ-MORE")
    order = _insert_order(env, [(pid, 15, 2.0)])

    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): 20}})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["quantity_changes"] == [
        {"part_id": str(pid), "part_number": "RCVQ-MORE", "ordered": 15, "received": 20}
    ]
    after = _order(env, order["_id"])
    assert after["items"][0]["quantity"] == 20 and after["items"][0]["ordered_quantity"] == 15
    assert after["remaining_balance"] == pytest.approx(40.0)
    assert int(_part(env, pid)["in_stock"]) == 20


def test_ordered_quantity_is_written_once(env):
    """Unreceive → повторная приёмка с другим числом: ordered_quantity остаётся
    исходным заказанным, изменение в ответе — от предыдущего количества."""
    pid = _create_part(env, "RCVQ-TWICE")
    order = _insert_order(env, [(pid, 15, 2.0)])
    assert _receive(env, order["_id"], {"item_quantities": {str(pid): 10}}).get_json()["ok"]
    assert _unreceive(env, order["_id"]).get_json()["ok"]
    assert int(_part(env, pid)["in_stock"]) == 0

    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): 8}})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["quantity_changes"] == [
        {"part_id": str(pid), "part_number": "RCVQ-TWICE", "ordered": 10, "received": 8}
    ]
    after = _order(env, order["_id"])
    assert after["items"][0]["quantity"] == 8 and after["items"][0]["ordered_quantity"] == 15
    assert int(_part(env, pid)["in_stock"]) == 8


def test_zero_received_drops_line_and_resets_last_price(env):
    keep = _create_part(env, "RCVQ-KEEP")
    drop = _create_part(env, "RCVQ-DROP")
    # Через API, чтобы refresh_last_price при создании привязал last_price к заказу.
    order_id = _create_order_api(env, [
        {"part_id": str(keep), "quantity": 5, "price": 3.0},
        {"part_id": str(drop), "quantity": 4, "price": 6.0},
    ])
    assert _part(env, drop)["last_price_order_id"] == order_id
    assert _part(env, drop)["last_price"] == 6.0

    resp = _receive(env, order_id, {"item_quantities": {str(keep): "5", str(drop): 0}})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["updated_parts"] == 1
    assert data["quantity_changes"] == [
        {"part_id": str(drop), "part_number": "RCVQ-DROP", "ordered": 4, "received": 0}
    ]

    after = _order(env, order_id)
    assert after["status"] == "received"
    assert [it["part_id"] for it in after["items"]] == [keep]
    assert after["items"][0]["quantity"] == 5 and "ordered_quantity" not in after["items"][0]
    assert after["remaining_balance"] == pytest.approx(15.0)

    assert int(_part(env, keep)["in_stock"]) == 5
    assert int(_part(env, drop)["in_stock"]) == 0
    assert env["db"].inventory_movements.count_documents({"part_id": drop, "type": "receive"}) == 0
    # Выкинутая строка: заказ больше не «последний» для этого парта.
    assert _part(env, drop)["last_price_order_id"] is None
    assert _part(env, drop)["last_price"] is None
    assert _part(env, keep)["last_price_order_id"] == order_id


def test_all_lines_zero_is_rejected_unless_non_inventory_amounts(env):
    pid = _create_part(env, "RCVQ-ZERO")
    order = _insert_order(env, [(pid, 3, 1.0)])

    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): 0}})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Nothing to receive: every line is 0."
    after = _order(env, order["_id"])
    assert after["status"] == "ordered" and after["items"][0]["quantity"] == 3
    assert "ordered_quantity" not in after["items"][0]
    assert int(_part(env, pid)["in_stock"]) == 0
    assert env["db"].inventory_movements.count_documents({"part_id": pid, "type": "receive"}) == 0

    # С non-inventory суммой заказ принимается без строк (счёт только за shop supply).
    pid2 = _create_part(env, "RCVQ-ZERO-2")
    order2 = _insert_order(env, [(pid2, 3, 1.0)], order_number=97002,
                           non_inventory=[{"type": "shop_supply", "description": "Rags", "amount": 12.5}])
    resp = _receive(env, order2["_id"], {"item_quantities": {str(pid2): 0}})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    after2 = _order(env, order2["_id"])
    assert after2["status"] == "received" and after2["items"] == []
    assert after2["remaining_balance"] == pytest.approx(12.5)
    assert int(_part(env, pid2)["in_stock"]) == 0


def test_received_quantity_validation(env):
    pid = _create_part(env, "RCVQ-VALID")
    order = _insert_order(env, [(pid, 15, 2.0)])

    for bad in ("abc", -1, 1.5, True, "", None, "1.5", [10]):
        resp = _receive(env, order["_id"], {"item_quantities": {str(pid): bad}})
        assert resp.status_code == 400, (bad, resp.get_json())
        assert resp.get_json()["error"] == "Received quantity must be a whole number (0 or more).", bad
    assert _order(env, order["_id"])["status"] == "ordered"
    assert int(_part(env, pid)["in_stock"]) == 0

    # Числовая строка принимается.
    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): "10"}})
    assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_json()
    assert int(_part(env, pid)["in_stock"]) == 10
    after = _order(env, order["_id"])
    assert after["items"][0]["quantity"] == 10 and after["items"][0]["ordered_quantity"] == 15


def test_payment_above_received_total_blocks_receive(env):
    pid = _create_part(env, "RCVQ-PAID")
    order = _insert_order(env, [(pid, 15, 2.0)])  # итог $30
    env["db"].parts_order_payments.insert_one({
        "parts_order_id": order["_id"], "shop_id": env["shop_id"], "tenant_id": None,
        "amount": 25.0, "payment_method": "cash", "notes": "", "payment_date": _now(),
        "is_active": True, "created_at": _now(), "created_by": None,
    })

    # 10 × $2 = $20 < $25 оплачено — отказ, ничего не изменилось.
    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): 10}})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == (
        "Paid amount is greater than the received order total. Reduce the payment first."
    )
    after = _order(env, order["_id"])
    assert after["status"] == "ordered"
    assert after["items"][0]["quantity"] == 15 and "ordered_quantity" not in after["items"][0]
    assert int(_part(env, pid)["in_stock"]) == 0
    assert env["db"].inventory_movements.count_documents({"part_id": pid, "type": "receive"}) == 0

    # 13 × $2 = $26 ≥ $25 — проходит, платёжное состояние пересчитано.
    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): 13}})
    assert resp.status_code == 200 and resp.get_json()["ok"], resp.get_json()
    after = _order(env, order["_id"])
    assert after["status"] == "received"
    assert after["paid_amount"] == pytest.approx(25.0)
    assert after["remaining_balance"] == pytest.approx(1.0)
    assert after["payment_status"] == "partially_paid"
    assert int(_part(env, pid)["in_stock"]) == 13


def test_receive_without_item_quantities_is_full_receive(env):
    pid = _create_part(env, "RCVQ-FULL")
    order = _insert_order(env, [(pid, 7, 2.0)])

    resp = _receive(env, order["_id"], {"vendor_bill": "B-7"})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["quantity_changes"] == [] and data["updated_parts"] == 1
    after = _order(env, order["_id"])
    assert after["status"] == "received"
    assert after["items"][0]["quantity"] == 7 and "ordered_quantity" not in after["items"][0]
    assert int(_part(env, pid)["in_stock"]) == 7
    got = env["client"].get(f"/parts/api/orders/{order['_id']}").get_json()["order"]
    assert got["items"][0]["quantity"] == 7 and got["items"][0]["ordered_quantity"] is None

    # Отмена приёмки при достаточном остатке — в минус никто не ушёл.
    data = _unreceive(env, order["_id"]).get_json()
    assert data["ok"] and data["updated_parts"] == 1 and data["negative_stock"] == []
    assert int(_part(env, pid)["in_stock"]) == 0
    assert _order(env, order["_id"])["status"] == "ordered"

    # Количества, равные заказанным, — строки не меняются.
    resp = _receive(env, order["_id"], {"item_quantities": {str(pid): 7}})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"] and data["quantity_changes"] == []
    assert "ordered_quantity" not in _order(env, order["_id"])["items"][0]
    assert int(_part(env, pid)["in_stock"]) == 7


# ── откат приёмки / удаление в минус ──────────────────────────────────────

def test_unreceive_goes_below_zero_when_stock_was_used(env):
    pid = _create_part(env, "RCVQ-NEG")
    order = _insert_order(env, [(pid, 6, 4.0)])
    assert _receive(env, order["_id"], {"vendor_bill": "B-6"}).get_json()["ok"]
    assert int(_part(env, pid)["in_stock"]) == 6

    _consume_stock(env, pid, 3)  # 3 из 6 уже ушли на work order

    resp = _unreceive(env, order["_id"])
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["updated_parts"] == 1
    assert data["negative_stock"] == [{"part_id": str(pid), "part_number": "RCVQ-NEG", "stock_after": -3}]
    assert int(_part(env, pid)["in_stock"]) == -3
    assert _row_qty(env, pid, env["loc"]["_id"]) == -3
    after = _order(env, order["_id"])
    assert after["status"] == "ordered" and "received_at" not in after
    mv = env["db"].inventory_movements.find_one({"part_id": pid, "type": "unreceive"})
    assert mv and mv["qty_delta"] == -6 and mv["stock_after"] == -3

    # Журнал: парты в минусе — читаемыми строками (список dict'ов журнал
    # сплющивает до лейблов, и stock_after терялся бы).
    event = env["journal"].find_one({
        "kind": "event", "entity_type": "parts_order", "entity_id": str(order["_id"]),
        "action": "update", "summary": {"$regex": "un-received"},
    })
    assert event, "unreceive event missing"
    assert event["details"]["negative_stock"] == ["RCVQ-NEG: -3"]
    assert "1 part(s) now below zero" in event["summary"]

    # Повторная приёмка возвращает остаток в плюс.
    assert _receive(env, order["_id"], {"vendor_bill": "B-6"}).get_json()["ok"]
    assert int(_part(env, pid)["in_stock"]) == 3
    assert _row_qty(env, pid, env["loc"]["_id"]) == 3


def test_delete_received_order_with_used_stock(env):
    pid = _create_part(env, "RCVQ-DEL")
    order = _insert_order(env, [(pid, 5, 1.0)])
    assert _receive(env, order["_id"], {}).get_json()["ok"]
    _consume_stock(env, pid, 2)

    resp = _delete(env, order["_id"])
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert data["updated_parts"] == 1
    assert data["negative_stock"] == [{"part_id": str(pid), "part_number": "RCVQ-DEL", "stock_after": -3}]
    assert _order(env, order["_id"])["is_active"] is False
    assert int(_part(env, pid)["in_stock"]) == -3
    assert _row_qty(env, pid, env["loc"]["_id"]) == -3
    event = env["journal"].find_one({
        "kind": "event", "entity_type": "parts_order", "entity_id": str(order["_id"]), "action": "delete",
    })
    assert event and event["details"]["negative_stock"] == ["RCVQ-DEL: -3"]


# ── откат по парту, деактивированному после приёмки ──────────────────────

def test_unreceive_rolls_back_a_part_deactivated_after_receiving(env):
    """Парт деактивировали после приёмки: откат всё равно снимает остаток
    (иначе повторная приёмка посчитала бы его дважды), парт остаётся
    неактивным; повторная приёмка реактивирует его и считает остаток один раз."""
    pid = _create_part(env, "RCVQ-INACT-UNRECV")
    order = _insert_order(env, [(pid, 5, 2.0)])
    assert _receive(env, order["_id"], {}).get_json()["ok"]
    assert int(_part(env, pid)["in_stock"]) == 5
    env["db"].parts.update_one({"_id": pid}, {"$set": {"is_active": False, "deactivated_at": _now()}})

    data = _unreceive(env, order["_id"]).get_json()
    assert data["ok"] and data["updated_parts"] == 1 and data["negative_stock"] == []
    part = _part(env, pid)
    assert int(part["in_stock"]) == 0 and part["is_active"] is False
    assert _row_qty(env, pid, env["loc"]["_id"]) == 0
    mv = env["db"].inventory_movements.find_one({"part_id": pid, "type": "unreceive"})
    assert mv and mv["qty_delta"] == -5 and mv["stock_after"] == 0
    assert _order(env, order["_id"])["status"] == "ordered"

    assert _receive(env, order["_id"], {}).get_json()["ok"]
    part = _part(env, pid)
    assert int(part["in_stock"]) == 5 and part["is_active"] is True
    assert _row_qty(env, pid, env["loc"]["_id"]) == 5
    assert env["db"].inventory_movements.count_documents({"part_id": pid, "type": "receive"}) == 2


def test_delete_rolls_back_a_part_deactivated_after_receiving(env):
    pid = _create_part(env, "RCVQ-INACT-DEL")
    order = _insert_order(env, [(pid, 4, 1.0)])
    assert _receive(env, order["_id"], {}).get_json()["ok"]
    env["db"].parts.update_one({"_id": pid}, {"$set": {"is_active": False, "deactivated_at": _now()}})

    data = _delete(env, order["_id"]).get_json()
    assert data["ok"] and data["updated_parts"] == 1 and data["negative_stock"] == []
    part = _part(env, pid)
    assert int(part["in_stock"]) == 0 and part["is_active"] is False
    assert _row_qty(env, pid, env["loc"]["_id"]) == 0
    assert _order(env, order["_id"])["is_active"] is False
    mv = env["db"].inventory_movements.find_one({"part_id": pid, "type": "unreceive"})
    assert mv and mv["qty_delta"] == -4 and mv["stock_after"] == 0

    # Новый заказ на тот же парт: остаток считается один раз, парт снова активен.
    order2 = _insert_order(env, [(pid, 2, 1.0)], order_number=97003)
    assert _receive(env, order2["_id"], {}).get_json()["ok"]
    part = _part(env, pid)
    assert int(part["in_stock"]) == 2 and part["is_active"] is True


def test_unreceive_skips_merged_duplicates(env):
    """Слитый дубль (merged_into) — не «заказываемый» парт: как и при
    приёмке, откат его не трогает."""
    pid = _create_part(env, "RCVQ-MERGED")
    order = _insert_order(env, [(pid, 3, 1.0)])
    assert _receive(env, order["_id"], {}).get_json()["ok"]
    env["db"].parts.update_one({"_id": pid}, {"$set": {"is_active": False, "merged_into": ObjectId()}})

    data = _unreceive(env, order["_id"]).get_json()
    assert data["ok"] and data["updated_parts"] == 0 and data["negative_stock"] == []
    assert int(_part(env, pid)["in_stock"]) == 3
    assert env["db"].inventory_movements.count_documents({"part_id": pid, "type": "unreceive"}) == 0


# ── receive-context ───────────────────────────────────────────────────────

def test_receive_context_lists_untracked_and_inactive_parts(env):
    tracked = _create_part(env, "RCVQ-CTX-T", in_stock=2)
    untracked = _create_part(env, "RCVQ-CTX-U", location=False, do_not_track_inventory=True)
    inactive = _create_part(env, "RCVQ-CTX-I", in_stock=1)
    env["db"].parts.update_one({"_id": inactive}, {"$set": {"is_active": False}})
    order = _insert_order(env, [(tracked, 4, 1.0), (untracked, 2, 5.0), (inactive, 3, 2.0)])

    resp = env["client"].get(f"/parts/api/orders/{order['_id']}/receive-context")
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    rows = {r["part_number"]: r for r in data["items"]}
    assert set(rows) == {"RCVQ-CTX-T", "RCVQ-CTX-U", "RCVQ-CTX-I"}
    assert rows["RCVQ-CTX-T"] == {
        "part_id": str(tracked), "part_number": "RCVQ-CTX-T",
        "description": "Receive qty test RCVQ-CTX-T", "quantity": 4,
        "tracked": True, "is_active": True,
        "default_location_id": str(env["loc"]["_id"]), "in_stock": 2,
    }
    untracked_row = rows["RCVQ-CTX-U"]
    assert untracked_row["tracked"] is False and untracked_row["is_active"] is True
    assert untracked_row["quantity"] == 2 and untracked_row["in_stock"] == 0
    assert untracked_row["default_location_id"] == ""
    inactive_row = rows["RCVQ-CTX-I"]
    assert inactive_row["is_active"] is False and inactive_row["tracked"] is True
    assert inactive_row["quantity"] == 3 and inactive_row["in_stock"] == 1
    assert any(loc["id"] == str(env["loc"]["_id"]) for loc in data["locations"])
    assert data["vendor_bill"] == ""
    assert data["has_non_inventory_amounts"] is False

    # Частичная приёмка по всем трём: неучитываемый — только средняя цена,
    # неактивный — реактивируется и получает остаток.
    resp = _receive(env, order["_id"], {"item_quantities": {
        str(tracked): 4, str(untracked): 1, str(inactive): 2,
    }})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ok"], data
    assert int(_part(env, tracked)["in_stock"]) == 6
    untracked_doc = _part(env, untracked)
    assert untracked_doc["average_cost"] == 5.0 and not untracked_doc.get("in_stock")
    inactive_doc = _part(env, inactive)
    assert inactive_doc["is_active"] is True and int(inactive_doc["in_stock"]) == 3
    after = _order(env, order["_id"])
    assert {it["part_number"]: (it["quantity"], it.get("ordered_quantity")) for it in after["items"]} == {
        "RCVQ-CTX-T": (4, None), "RCVQ-CTX-U": (1, 2), "RCVQ-CTX-I": (2, 3),
    }
    assert sorted(c["part_number"] for c in data["quantity_changes"]) == ["RCVQ-CTX-I", "RCVQ-CTX-U"]


def test_receive_context_reports_non_inventory_amounts_and_errors(env):
    """has_non_inventory_amounts — диалог разрешает нули по всем строкам
    (бэкенд такой заказ принимает); ошибки — JSON с error (клиент показывает
    его и не открывает диалог)."""
    pid = _create_part(env, "RCVQ-CTX-NI")
    order = _insert_order(env, [(pid, 2, 1.0)],
                          non_inventory=[{"type": "shop_supply", "description": "Rags", "amount": 5.0}])
    data = env["client"].get(f"/parts/api/orders/{order['_id']}/receive-context").get_json()
    assert data["ok"] and data["has_non_inventory_amounts"] is True
    assert [r["part_number"] for r in data["items"]] == ["RCVQ-CTX-NI"]

    resp = env["client"].get(f"/parts/api/orders/{ObjectId()}/receive-context")
    assert resp.status_code == 404
    assert resp.get_json() == {"ok": False, "error": "Order not found."}
    resp = env["client"].get("/parts/api/orders/not-an-id/receive-context")
    assert resp.status_code == 400
    assert resp.get_json() == {"ok": False, "error": "Invalid order id."}
