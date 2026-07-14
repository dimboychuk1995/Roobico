"""
Возврат ядер вендору: POST /parts/api/cores/<id>/return + вкладка Cores Returns.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from tests.conftest import SHOP_A_DB, get_csrf_token, login


def _shop_db(app):
    from app.extensions import get_mongo_client
    return get_mongo_client()[SHOP_A_DB]


def _make_core(app, seed, quantity=5, core_cost=20.0):
    with app.app_context():
        db = _shop_db(app)
        doc = {
            "_id": ObjectId(),
            "shop_id": seed["shop_a"]["_id"],
            "part_id": ObjectId(),
            "part_number": "CORE-TEST-1",
            "description": "Alternator core",
            "core_cost": core_cost,
            "quantity": quantity,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        db.cores.insert_one(doc)
        return doc


def _cleanup(app, core_id):
    with app.app_context():
        db = _shop_db(app)
        db.cores.delete_one({"_id": core_id})
        db.core_returns.delete_many({"core_id": core_id})


def test_core_return_decrements_and_logs(client, app, seed):
    core = _make_core(app, seed, quantity=5, core_cost=20.0)
    try:
        login(client)
        token = get_csrf_token(client)
        resp = client.post(
            f"/parts/api/cores/{core['_id']}/return",
            json={"quantity": 3, "notes": "RMA-42"},
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["remaining_quantity"] == 2
        assert body["credit_total"] == 60.0

        with app.app_context():
            db = _shop_db(app)
            assert db.cores.find_one({"_id": core["_id"]})["quantity"] == 2
            ret = db.core_returns.find_one({"core_id": core["_id"]})
            assert ret is not None
            assert ret["quantity"] == 3
            assert ret["credit_total"] == 60.0
            assert ret["part_number"] == "CORE-TEST-1"
            assert ret["notes"] == "RMA-42"
            assert ret["shop_id"] == seed["shop_a"]["_id"]
    finally:
        _cleanup(app, core["_id"])


def test_core_return_rejects_over_quantity(client, app, seed):
    core = _make_core(app, seed, quantity=2)
    try:
        login(client)
        token = get_csrf_token(client)
        resp = client.post(
            f"/parts/api/cores/{core['_id']}/return",
            json={"quantity": 5},
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

        with app.app_context():
            db = _shop_db(app)
            # Ничего не списано и не записано.
            assert db.cores.find_one({"_id": core["_id"]})["quantity"] == 2
            assert db.core_returns.count_documents({"core_id": core["_id"]}) == 0
    finally:
        _cleanup(app, core["_id"])


def test_core_return_rejects_bad_quantity(client, app, seed):
    core = _make_core(app, seed, quantity=2)
    try:
        login(client)
        token = get_csrf_token(client)
        for bad in (0, -1, "abc"):
            resp = client.post(
                f"/parts/api/cores/{core['_id']}/return",
                json={"quantity": bad},
                headers={"X-CSRFToken": token},
            )
            assert resp.status_code == 400
    finally:
        _cleanup(app, core["_id"])


def test_cores_returns_tab_lists_returns(client, app, seed):
    core = _make_core(app, seed, quantity=4, core_cost=15.0)
    try:
        login(client)
        token = get_csrf_token(client)
        resp = client.post(
            f"/parts/api/cores/{core['_id']}/return",
            json={"quantity": 4, "notes": "credit memo 7"},
            headers={"X-CSRFToken": token},
        )
        assert resp.status_code == 200

        resp = client.get("/parts/?tab=cores_returns&date_preset=all_time")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "CORE-TEST-1" in html
        assert "$60.00" in html  # 4 × $15 credit
        assert "credit memo 7" in html
    finally:
        _cleanup(app, core["_id"])
