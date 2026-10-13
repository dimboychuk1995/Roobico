"""
Неактивные парты: деактивация скрывает парт из обычного поиска; обратно в
оборот он возвращается только через парт-ордер — создание, сохранение,
приёмка, подтверждение email-заказа, матч в AI Order Reader. Слитые дубли
(merged_into) не возвращаются никогда. Плюс фильтр статуса на странице Parts.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def env(seed, mongo):
    db = mongo[SHOP_A_DB]
    shop_id = seed["shop_a"]["_id"]
    now = _now()

    vendor_id = db.vendors.insert_one({
        "shop_id": shop_id, "name": "Inactive Test Vendor", "is_active": True, "created_at": now,
    }).inserted_id

    def part(number, **extra):
        doc = {
            "_id": ObjectId(), "shop_id": shop_id, "part_number": number,
            "description": f"Desc {number}", "in_stock": 0, "average_cost": 10.0,
            "is_active": True, "created_at": now,
        }
        doc.update(extra)
        return doc

    active = part("INACTV-ACTIVE")
    inactive = part("INACTV-OFF", is_active=False, deactivated_at=now)
    merged = part("INACTV-MERGED", is_active=False, merged_into=active["_id"])
    db.parts.insert_many([active, inactive, merged])

    yield {"db": db, "shop_id": shop_id, "vendor_id": vendor_id,
           "active": active, "inactive": inactive, "merged": merged}

    ids = [active["_id"], inactive["_id"], merged["_id"]]
    db.parts_orders.delete_many({"shop_id": shop_id, "items.part_id": {"$in": ids}})
    db.inventory_movements.delete_many({"part_id": {"$in": ids}})
    db.part_location_stock.delete_many({"part_id": {"$in": ids}})
    db.parts.delete_many({"_id": {"$in": ids}})
    db.vendors.delete_one({"_id": vendor_id})


def _create_order(client, env, items, **extra):
    token = get_csrf_token(client)
    resp = client.post("/parts/api/orders/create", json={
        "vendor_id": str(env["vendor_id"]), "items": items, **extra,
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    return data


def _part(env, key):
    return env["db"].parts.find_one({"_id": env[key]["_id"]})


# ── Поиск ────────────────────────────────────────────────────────────────

def test_search_hides_inactive_by_default_and_shows_with_flag(client, env):
    login(client)
    resp = client.get("/parts/api/search?q=INACTV")
    numbers = {i["part_number"] for i in resp.get_json()["items"]}
    assert "INACTV-ACTIVE" in numbers
    assert "INACTV-OFF" not in numbers
    assert "INACTV-MERGED" not in numbers

    resp = client.get("/parts/api/search?q=INACTV&include_inactive=1")
    items = {i["part_number"]: i for i in resp.get_json()["items"]}
    assert items["INACTV-ACTIVE"]["is_active"] is True
    assert items["INACTV-OFF"]["is_active"] is False
    assert "INACTV-MERGED" not in items  # слитые дубли не ищутся никогда


# ── Создание заказа реактивирует ─────────────────────────────────────────

def test_create_order_reactivates_inactive_part(client, env):
    login(client)
    data = _create_order(client, env, [
        {"part_id": str(env["inactive"]["_id"]), "quantity": 2, "price": 12.5},
    ])
    assert data["items_count"] == 1
    assert data["reactivated_parts"] == 1

    part = _part(env, "inactive")
    assert part["is_active"] is True
    assert part["deactivated_at"] is None
    assert part["reactivated_by_order_id"] == ObjectId(data["order_id"])
    assert part["reactivated_by_order_number"] is not None

    order = env["db"].parts_orders.find_one({"_id": ObjectId(data["order_id"])})
    assert [i["part_id"] for i in order["items"]] == [env["inactive"]["_id"]]


def test_create_order_skips_merged_duplicate(client, env):
    login(client)
    token = get_csrf_token(client)
    resp = client.post("/parts/api/orders/create", json={
        "vendor_id": str(env["vendor_id"]),
        "items": [{"part_id": str(env["merged"]["_id"]), "quantity": 1, "price": 1}],
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 400
    assert "No valid items" in resp.get_json()["error"]
    assert _part(env, "merged")["is_active"] is False


# ── Сохранение заказа / приёмка ──────────────────────────────────────────

def test_update_order_reactivates_part_deactivated_after_ordering(client, env):
    login(client)
    data = _create_order(client, env, [
        {"part_id": str(env["active"]["_id"]), "quantity": 1, "price": 5},
    ])
    env["db"].parts.update_one({"_id": env["active"]["_id"]}, {"$set": {"is_active": False}})

    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{data['order_id']}/update", json={
        "vendor_id": str(env["vendor_id"]),
        "items": [{"part_id": str(env["active"]["_id"]), "quantity": 3, "price": 5}],
    }, headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _part(env, "active")["is_active"] is True


def test_receive_order_reactivates_and_adds_stock(client, env):
    login(client)
    data = _create_order(client, env, [
        {"part_id": str(env["active"]["_id"]), "quantity": 4, "price": 7},
    ])
    env["db"].parts.update_one({"_id": env["active"]["_id"]}, {"$set": {"is_active": False}})

    token = get_csrf_token(client)
    resp = client.post(f"/parts/api/orders/{data['order_id']}/receive", json={"vendor_bill": "INV-1"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["updated_parts"] == 1

    part = _part(env, "active")
    assert part["is_active"] is True
    assert part["in_stock"] == 4
    assert part["reactivated_by_order_id"] == ObjectId(data["order_id"])


# ── Email-заказ: матчинг и подтверждение ─────────────────────────────────

def test_email_order_matches_inactive_part_and_confirm_reactivates(app, env):
    from app.blueprints.parts.services.email_orders import confirm_email_order, match_part

    db = env["db"]
    assert match_part(db.parts, env["shop_id"], "inactv-off")["_id"] == env["inactive"]["_id"]
    assert match_part(db.parts, env["shop_id"], "INACTV-MERGED") is None

    order_id = db.parts_orders.insert_one({
        "shop_id": env["shop_id"], "vendor_id": env["vendor_id"], "order_number": 999001,
        "items": [{"part_id": env["inactive"]["_id"], "part_number": "INACTV-OFF",
                   "quantity": 1, "price": 3.0, "core_charge": 0.0}],
        "non_inventory_amounts": [], "status": "ordered", "is_active": True,
        "needs_confirmation": True, "source": {}, "created_at": _now(),
    }).inserted_id
    db.parts.update_one({"_id": env["inactive"]["_id"]}, {"$set": {"is_active": False}})

    order = db.parts_orders.find_one({"_id": order_id})
    ok, _msg = confirm_email_order(db, {"_id": env["shop_id"]}, order, actor_user_id=None)
    assert ok is True
    part = _part(env, "inactive")
    assert part["is_active"] is True
    assert part["reactivated_by_order_number"] == 999001


# ── AI Order Reader матчит неактивный парт и отдаёт сверенную цену ───────

def test_parse_invoice_matches_inactive_part_with_reconciled_price(client, env, monkeypatch):
    import app.utils.invoice_parser as parser

    def fake_parse(file_bytes, content_type, *, max_pages=None):
        raw = {
            "vendor_name": "Inactive Test Vendor", "invoice_number": "A1", "invoice_date": "01/02/2026",
            "document_kind": "invoice", "total": 300.0,
            "items": [{"part_number": "INACTV-OFF", "description": "x", "quantity": 3,
                       "price": 150.0, "line_total": 150.0}],
        }
        return {**raw, "items": [parser.normalize_line_item(i) for i in raw["items"]]}

    monkeypatch.setattr(parser, "parse_invoice", fake_parse)
    env["db"].parts.update_one({"_id": env["inactive"]["_id"]}, {"$set": {"is_active": False}})

    login(client)
    token = get_csrf_token(client)
    resp = client.post(
        "/parts/api/orders/parse-invoice",
        data={"invoice": (io.BytesIO(b"%PDF-1.4 fake"), "inv.pdf", "application/pdf")},
        headers={"X-CSRFToken": token},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["vendor_match"]["vendor_id"] == str(env["vendor_id"])
    line = data["items"][0]
    assert line["price"] == 50.0 and line["price_note"] == "extended_used_as_unit"
    assert line["matched_part"]["part_id"] == str(env["inactive"]["_id"])
    assert line["matched_part"]["is_active"] is False


# ── Страница Parts: фильтр статуса, нет кнопки Restore ───────────────────

def test_parts_page_status_filter(client, env):
    login(client)
    env["db"].parts.update_one({"_id": env["inactive"]["_id"]}, {"$set": {"is_active": False}})

    html = client.get("/parts/?tab=parts&q=INACTV").get_data(as_text=True)
    assert "INACTV-ACTIVE" in html and "INACTV-OFF" not in html

    html = client.get("/parts/?tab=parts&q=INACTV&status=inactive").get_data(as_text=True)
    assert "INACTV-OFF" in html and "INACTV-ACTIVE" not in html
    assert "INACTV-MERGED" not in html
    assert "Add to a parts order to reactivate" in html
    assert "/restore" not in html

    html = client.get("/parts/?tab=parts&q=INACTV&status=all").get_data(as_text=True)
    assert "INACTV-OFF" in html and "INACTV-ACTIVE" in html and "INACTV-MERGED" not in html


def test_duplicate_message_points_to_parts_order(client, env):
    login(client)
    env["db"].parts.update_one({"_id": env["inactive"]["_id"]}, {"$set": {"is_active": False}})
    token = get_csrf_token(client)
    resp = client.post("/parts/api/create", json={"part_number": "inactv-off", "description": "dup"},
                       headers={"X-CSRFToken": token})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert "Add it to a parts order" in resp.get_json()["error"]
