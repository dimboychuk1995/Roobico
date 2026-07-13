"""Customer portal: платежи (включая bulk) и maintenance-описания.

Платежи в work_order_payments не несут customer_id, поэтому портал обязан
резолвить их через work orders клиента — иначе вкладка Payments пустая.
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
def portal_setup(app, seed):
    from app.extensions import get_mongo_client

    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        shop_id = seed["shop_a"]["_id"]
        now = datetime.now(timezone.utc)

        customer = {
            "_id": ObjectId(), "shop_id": shop_id, "company_name": "Portal Fleet",
            "contacts": [], "is_active": True, "created_at": now,
        }
        db.customers.insert_one(customer)

        unit_1 = {
            "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer["_id"],
            "unit_number": "T-1", "make": "Volvo", "model": "VNL", "year": "2020",
            "vin": "VIN0001", "is_active": True, "created_at": now,
        }
        unit_2 = {
            "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer["_id"],
            "unit_number": "T-2", "make": "Freightliner", "model": "Cascadia",
            "year": "2021", "vin": "VIN0002", "is_active": True, "created_at": now,
        }
        db.units.insert_many([unit_1, unit_2])

        def make_wo(unit, total, wo_number, labors=None, wo_date=None):
            doc = {
                "_id": ObjectId(),
                "shop_id": shop_id,
                "customer_id": customer["_id"],
                "unit_id": unit["_id"],
                "is_active": True,
                "status": "open",
                "wo_number": wo_number,
                "labors": labors or [],
                "totals": {"grand_total": float(total)},
                "work_order_date": wo_date or (now - timedelta(days=1)).replace(tzinfo=None),
                "created_at": now,
            }
            db.work_orders.insert_one(doc)
            return doc

        wo_1 = make_wo(unit_1, 100.0, 8201)
        wo_2 = make_wo(unit_2, 200.0, 8202)

        data = {
            "db": db,
            "customer": customer,
            "unit_1": unit_1,
            "unit_2": unit_2,
            "wo_1": wo_1,
            "wo_2": wo_2,
        }
        yield data

        wo_ids = [wo_1["_id"], wo_2["_id"]]
        db.work_order_payments.delete_many({"work_order_id": {"$in": wo_ids}})
        db.work_orders.delete_many({"_id": {"$in": wo_ids}})
        db.units.delete_many({"_id": {"$in": [unit_1["_id"], unit_2["_id"]]}})
        db.customers.delete_one({"_id": customer["_id"]})
        from app.extensions import get_master_db
        get_master_db().customer_portal_tokens.delete_many(
            {"customer_id": customer["_id"]}
        )


def _portal_token(app, seed, customer_id) -> str:
    from app.blueprints.customer_portal.routes import get_or_create_portal_token
    with app.app_context():
        doc = get_or_create_portal_token(seed["shop_a"], customer_id)
        return doc["token"]


def test_portal_payments_shows_single_and_bulk(logged_in, portal_setup, app, seed):
    # Одиночный платёж по WO#8201.
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        f"/work_orders/api/work_orders/{portal_setup['wo_1']['_id']}/payment",
        json={"amount": 40, "payment_method": "cash"},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is True

    # Bulk-платёж: остаток WO#8201 + весь WO#8202 одним чеком.
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        "/work_orders/api/bulk-payments",
        json={
            "customer_id": str(portal_setup["customer"]["_id"]),
            "payment_method": "check",
            "allocations": [
                {"work_order_id": str(portal_setup["wo_1"]["_id"]), "amount": 60},
                {"work_order_id": str(portal_setup["wo_2"]["_id"]), "amount": 200},
            ],
        },
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is True

    portal = _portal_token(app, seed, portal_setup["customer"]["_id"])
    resp = logged_in.get(f"/portal/{portal}/tab/payments")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Все три платежа на месте — и одиночный, и оба из bulk-пачки.
    assert "$40.00" in html
    assert "$60.00" in html
    assert "$200.00" in html
    assert "#8201" in html
    assert "#8202" in html
    assert "No payments on file" not in html


def test_portal_payments_unit_filter(logged_in, portal_setup, app, seed):
    token = get_csrf_token(logged_in)
    resp = logged_in.post(
        "/work_orders/api/bulk-payments",
        json={
            "customer_id": str(portal_setup["customer"]["_id"]),
            "payment_method": "check",
            "allocations": [
                {"work_order_id": str(portal_setup["wo_1"]["_id"]), "amount": 100},
                {"work_order_id": str(portal_setup["wo_2"]["_id"]), "amount": 200},
            ],
        },
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is True

    portal = _portal_token(app, seed, portal_setup["customer"]["_id"])
    resp = logged_in.get(
        f"/portal/{portal}/tab/payments",
        query_string={"unit_id": str(portal_setup["unit_2"]["_id"])},
    )
    html = resp.get_data(as_text=True)
    assert "#8202" in html
    assert "$200.00" in html
    assert "#8201" not in html


def test_authorizations_sorted_chronologically_across_years(app, seed, portal_setup):
    db = portal_setup["db"]
    # 12/31/2025 vs 01/01/2026: строковая сортировка "%m/%d/%Y" ставила бы
    # декабрь выше января — проверяем настоящий хронологический порядок.
    db.work_orders.update_one(
        {"_id": portal_setup["wo_1"]["_id"]},
        {"$set": {"authorizations": [
            {"scope": "work_order", "status": "approved",
             "responded_at": datetime(2025, 12, 31, 12, 0)},
        ]}},
    )
    db.work_orders.update_one(
        {"_id": portal_setup["wo_2"]["_id"]},
        {"$set": {"authorizations": [
            {"scope": "work_order", "status": "declined",
             "responded_at": datetime(2026, 1, 1, 12, 0)},
            {"scope": "labor", "labor_index": 0, "status": "pending"},
        ]}},
    )

    from app.blueprints.customer_portal.routes import _list_customer_authorizations
    with app.app_context():
        rows, has_more = _list_customer_authorizations(
            db, portal_setup["customer"]["_id"],
        )

    assert has_more is False
    assert [r["responded_at"] for r in rows] == ["01/01/2026", "12/31/2025", ""]
    assert rows[-1]["status"] == "pending"


def test_unit_filter_does_not_leak_foreign_unit_label(app, seed, portal_setup):
    db = portal_setup["db"]
    foreign_customer = {
        "_id": ObjectId(), "shop_id": seed["shop_a"]["_id"],
        "company_name": "Foreign Co", "contacts": [], "is_active": True,
    }
    foreign_unit = {
        "_id": ObjectId(), "shop_id": seed["shop_a"]["_id"],
        "customer_id": foreign_customer["_id"], "unit_number": "SECRET-9",
        "make": "Kenworth", "model": "T680", "is_active": True,
    }
    db.customers.insert_one(foreign_customer)
    db.units.insert_one(foreign_unit)
    try:
        portal = _portal_token(app, seed, portal_setup["customer"]["_id"])
        resp = app.test_client().get(
            f"/portal/{portal}/tab/work-orders",
            query_string={"unit_id": str(foreign_unit["_id"])},
        )
        html = resp.get_data(as_text=True)
        assert "SECRET-9" not in html
        assert "Kenworth" not in html
    finally:
        db.units.delete_one({"_id": foreign_unit["_id"]})
        db.customers.delete_one({"_id": foreign_customer["_id"]})


def test_maintenance_pdf_rejects_bad_year_and_quarter(app, seed, portal_setup):
    portal = _portal_token(app, seed, portal_setup["customer"]["_id"])
    unit_id = portal_setup["unit_1"]["_id"]
    client = app.test_client()

    for query in ({"year": 99999}, {"year": 1800}, {"quarter": 7}, {"year": "abc"}):
        resp = client.get(
            f"/portal/{portal}/maintenance/{unit_id}/pdf", query_string=query,
        )
        assert resp.status_code == 400, query


def test_maintenance_description_lists_labors(app, portal_setup):
    from app.blueprints.customer_portal.routes import _maintenance_rows

    db = portal_setup["db"]
    # Норм. формат (labor.description) + legacy-формат (labor_description).
    db.work_orders.update_one(
        {"_id": portal_setup["wo_1"]["_id"]},
        {"$set": {
            "labors": [
                {"labor": {"description": "Oil change"}, "parts": []},
                {"labor_description": "Brake pads"},
            ],
            "work_order_date": datetime(2026, 5, 10),
        }},
    )
    # WO без лейборов — остаётся фолбэк на номер WO.
    db.work_orders.update_one(
        {"_id": portal_setup["wo_2"]["_id"]},
        {"$set": {"unit_id": portal_setup["unit_1"]["_id"],
                  "work_order_date": datetime(2026, 5, 20)}},
    )

    with app.app_context():
        rows, total = _maintenance_rows(
            db, portal_setup["customer"]["_id"],
            portal_setup["unit_1"]["_id"], 2026, 2,
        )

    assert [r["description"] for r in rows] == [
        "Oil change, Brake pads",
        "Work Order #8202",
    ]
    assert total == 300.0
