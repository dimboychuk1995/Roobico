"""Возвраты вендору по принятым/оплаченным партс-ордерам: отдельная строка в
parts_orders (is_return), списание со склада, лимит возврата, восстановление
при удалении возврата, гарды."""
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

    shop_id = seed["shop_a"]["_id"]
    now = datetime.now(timezone.utc)
    loc = {"_id": ObjectId(), "shop_id": shop_id, "name": "Ret shelf", "parent_id": None,
           "is_active": True, "created_at": now}
    db.parts_locations.insert_one(loc)

    part = {
        "_id": ObjectId(),
        "shop_id": shop_id,
        "part_number": "VR-PART-1",
        "description": "Vendor return test part",
        "in_stock": 0,
        "average_cost": 0.0,
        "location_id": loc["_id"],
        "is_active": True,
        "created_at": now,
    }
    db.parts.insert_one(part)

    yield {"db": db, "shop_id": shop_id, "loc": loc, "part": part}

    db.parts_orders.delete_many({"shop_id": shop_id, "items.part_id": part["_id"]})
    db.parts.delete_one({"_id": part["_id"]})
    db.part_location_stock.delete_many({"part_id": part["_id"]})
    db.inventory_movements.delete_many({"part_id": part["_id"]})
    db.parts_locations.delete_one({"_id": loc["_id"]})


def _insert_order(env, qty=10, price=4.0):
    now = datetime.now(timezone.utc)
    order = {
        "_id": ObjectId(),
        "shop_id": env["shop_id"],
        "order_number": 98001,
        "status": "ordered",
        "items": [{
            "part_id": env["part"]["_id"],
            "part_number": env["part"]["part_number"],
            "quantity": qty,
            "price": price,
        }],
        "non_inventory_amounts": [],
        "is_active": True,
        "created_at": now,
        "order_date": now,
    }
    env["db"].parts_orders.insert_one(order)
    return order


def _receive(client, order_id):
    token = get_csrf_token(client)
    resp = client.post(
        f"/parts/api/orders/{order_id}/receive",
        json={"vendor_bill": "B-1"},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()


def _create_return(client, order_id, items, notes=""):
    token = get_csrf_token(client)
    return client.post(
        f"/parts/api/orders/{order_id}/returns",
        json={"items": items, "notes": notes},
        headers={"X-CSRFToken": token},
    )


def _stock(env):
    doc = env["db"].parts.find_one({"_id": env["part"]["_id"]}, {"in_stock": 1})
    return int(doc.get("in_stock") or 0)


def test_return_flow_from_received_order(logged_in, env):
    order = _insert_order(env, qty=10, price=4.0)
    _receive(logged_in, order["_id"])
    assert _stock(env) == 10

    # Возврат 3 штук
    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 3}], "damaged")
    data = resp.get_json()
    assert data.get("ok"), data
    assert data["credit_total"] == 12.0, "3 * $4.00"

    assert _stock(env) == 7, "возврат списал 3 со склада"
    row = env["db"].part_location_stock.find_one(
        {"part_id": env["part"]["_id"], "location_id": env["loc"]["_id"]}
    )
    assert int(row["qty"]) == 7

    ret = env["db"].parts_orders.find_one({"_id": ObjectId(data["return_id"])})
    assert ret["is_return"] is True
    assert ret["return_for_order_id"] == order["_id"]
    assert ret["status"] == "returned"
    assert ret["credit_total"] == 12.0
    assert ret["notes"] == "damaged"

    mv = env["db"].inventory_movements.find_one({"part_id": env["part"]["_id"], "type": "vendor_return"})
    assert mv and mv["qty_delta"] == -3

    # return-context: доступно к возврату стало 7
    resp = logged_in.get(f"/parts/api/orders/{order['_id']}/return-context")
    ctx = resp.get_json()
    assert ctx.get("ok")
    assert ctx["items"][0]["returnable"] == 7

    # Сверх лимита — отказ
    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 8}])
    assert resp.status_code == 400
    assert _stock(env) == 7

    # Строка возврата видна во вкладке Orders
    resp = logged_in.get("/parts?tab=orders", follow_redirects=True)
    assert resp.status_code == 200
    assert "Return ·".encode() in resp.data


def test_delete_return_restores_stock(logged_in, env):
    order = _insert_order(env, qty=6, price=2.0)
    _receive(logged_in, order["_id"])
    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 4}])
    data = resp.get_json()
    assert data.get("ok"), data
    assert _stock(env) == 2

    token = get_csrf_token(logged_in)
    resp = logged_in.delete(
        f"/parts/api/orders/{data['return_id']}",
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json().get("ok"), resp.get_json()

    assert _stock(env) == 6, "удаление возврата вернуло остаток"
    row = env["db"].part_location_stock.find_one(
        {"part_id": env["part"]["_id"], "location_id": env["loc"]["_id"]}
    )
    assert int(row["qty"]) == 6

    ret = env["db"].parts_orders.find_one({"_id": ObjectId(data["return_id"])})
    assert ret["is_active"] is False

    # После удаления возврата снова можно вернуть всё количество
    resp = logged_in.get(f"/parts/api/orders/{order['_id']}/return-context")
    assert resp.get_json()["items"][0]["returnable"] == 6


def test_unreceive_blocked_while_return_active(logged_in, env):
    order = _insert_order(env, qty=5, price=1.0)
    _receive(logged_in, order["_id"])
    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 1}])
    assert resp.get_json().get("ok")

    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/parts/api/orders/{order['_id']}/unreceive",
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 400
    assert "returns" in (resp.get_json().get("error") or "").lower()


def test_return_guards(logged_in, env):
    order = _insert_order(env, qty=5, price=1.0)
    token = get_csrf_token(logged_in)

    # Заказ не принят и не оплачен — возврат недоступен
    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 1}])
    assert resp.status_code == 400

    _receive(logged_in, order["_id"])
    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 2}])
    data = resp.get_json()
    assert data.get("ok")
    return_id = data["return_id"]

    # Возврат нельзя принять, оплатить или вернуть повторно
    resp = logged_in.post(
        f"/parts/api/orders/{return_id}/receive",
        json={},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 400

    resp = logged_in.post(
        f"/parts/api/orders/{return_id}/payment",
        json={"amount": 1},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 400

    resp = logged_in.get(f"/parts/api/orders/{return_id}/return-context")
    assert resp.status_code == 400


def test_paid_not_received_order_returns_without_stock(logged_in, env, app):
    """Оплачен, но не принят: возврат-кредит создаётся, склад не трогается."""
    order = _insert_order(env, qty=4, price=5.0)
    env["db"].parts_orders.update_one({"_id": order["_id"]}, {"$set": {"payment_status": "paid"}})

    resp = _create_return(logged_in, order["_id"], [{"part_id": str(env["part"]["_id"]), "quantity": 2}])
    data = resp.get_json()
    assert data.get("ok"), data
    assert _stock(env) == 0, "склад не менялся — товар не принимали"
    assert env["db"].inventory_movements.count_documents(
        {"part_id": env["part"]["_id"], "type": "vendor_return"}
    ) == 0

    ret = env["db"].parts_orders.find_one({"_id": ObjectId(data["return_id"])})
    assert ret["stock_deducted"] is False

    # Тоталы вкладки Orders: возврат уменьшает сумму закупок
    from app.blueprints.parts.routes import _get_parts_orders_totals
    with app.app_context():
        totals = _get_parts_orders_totals(
            env["db"].parts_orders,
            {"shop_id": env["shop_id"], "is_active": {"$ne": False},
             "items.part_id": env["part"]["_id"]},
        )
    assert totals["total"] == 10.0, "заказ $20 минус возврат-кредит $10"


def test_returns_filter_and_attachments_button(logged_in, env):
    """Фильтр Returns на Parts Orders показывает только возвраты; у строки
    возврата есть кнопка вложений (кредит-инвойс вендора), привязанная к
    самому возврату — на оригинальном заказе этих файлов нет."""
    now = datetime.now(timezone.utc)
    order = _insert_order(env)
    env["db"].parts_orders.update_one(
        {"_id": order["_id"]}, {"$set": {"vendor_bill": "BILL-ORIG-77"}})

    ret = {
        "_id": ObjectId(),
        "shop_id": env["shop_id"],
        "order_number": 98002,
        "is_return": True,
        "return_for_order_id": order["_id"],
        "return_for_order_number": order["order_number"],
        "vendor_id": None,
        "vendor_bill": "CREDIT-RET-77",
        "status": "returned",
        "payment_status": "credit",
        "items": [{
            "part_id": env["part"]["_id"],
            "part_number": env["part"]["part_number"],
            "description": "returned",
            "quantity": 2,
            "price": 4.0,
        }],
        "credit_total": 8.0,
        "non_inventory_amounts": [],
        "order_date": now,
        "is_active": True,
        "created_at": now,
    }
    env["db"].parts_orders.insert_one(ret)

    # Фильтр Returns: только возврат, без оригинала
    page = logged_in.get("/parts/?tab=orders&paid_status=returns").get_data(as_text=True)
    assert "CREDIT-RET-77" in page
    assert "BILL-ORIG-77" not in page
    # Кнопка вложений на строке возврата, entity = сам возврат
    assert f'data-entity-id="{ret["_id"]}"' in page

    # All: видны оба; у оригинального заказа кнопки файлов нет
    page_all = logged_in.get("/parts/?tab=orders").get_data(as_text=True)
    assert "BILL-ORIG-77" in page_all
    assert "CREDIT-RET-77" in page_all
    assert f'data-entity-id="{order["_id"]}"' not in page_all

    # Unpaid по-прежнему без возвратов
    page_unpaid = logged_in.get("/parts/?tab=orders&paid_status=unpaid").get_data(as_text=True)
    assert "CREDIT-RET-77" not in page_unpaid
