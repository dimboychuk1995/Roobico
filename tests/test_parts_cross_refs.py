"""Cross references (взаимозаменяемые запчасти).

Модель: парты одной группы `interchange_group` взаимозаменяемы.
Проверяем: связывание, слияние групп, отвязку (с роспуском группы из
одного парта), выдачу кросс-рефов в API парта и альтернатив в поиске
(парт-ордера и work orders).
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.utils.parts_search import build_parts_search_terms
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SHOP_A_DB, get_csrf_token, login


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


def _make_part(shop_id, number, description="", in_stock=5):
    return {
        "_id": ObjectId(),
        "shop_id": shop_id,
        "part_number": number,
        "description": description,
        "reference": None,
        "search_terms": build_parts_search_terms(number, description, None),
        "in_stock": in_stock,
        "average_cost": 10.0,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }


@pytest.fixture()
def parts(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        shop_id = seed["shop_a"]["_id"]
        docs = {
            "p1": _make_part(shop_id, "XREF-AAA-1", "Oil filter brand A"),
            "p2": _make_part(shop_id, "XREF-BBB-2", "Oil filter brand B", in_stock=0),
            "p3": _make_part(shop_id, "XREF-CCC-3", "Oil filter brand C"),
            "p4": _make_part(shop_id, "XREF-DDD-4", "Oil filter brand D"),
        }
        db.parts.insert_many(docs.values())
        yield {"db": db, "shop_id": shop_id, **docs}
        db.parts.delete_many({"_id": {"$in": [d["_id"] for d in docs.values()]}})


def _group(parts, key):
    doc = parts["db"].parts.find_one({"_id": parts[key]["_id"]}, {"interchange_group": 1})
    return doc.get("interchange_group")


def _add(client, part_id, other_id):
    token = get_csrf_token(client)
    return client.post(
        f"/parts/api/{part_id}/cross-refs/add",
        json={"other_part_id": str(other_id)},
        headers={"X-CSRFToken": token},
    )


def _remove(client, part_id):
    token = get_csrf_token(client)
    return client.post(
        f"/parts/api/{part_id}/cross-refs/remove",
        json={},
        headers={"X-CSRFToken": token},
    )


def test_link_two_parts(logged_in, parts, app):
    with app.app_context():
        resp = _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])
        data = resp.get_json()
        assert resp.status_code == 200 and data["ok"] is True, data
        assert [x["part_number"] for x in data["items"]] == ["XREF-BBB-2"]

        g1, g2 = _group(parts, "p1"), _group(parts, "p2")
        assert g1 is not None and g1 == g2


def test_link_self_rejected(logged_in, parts, app):
    with app.app_context():
        resp = _add(logged_in, parts["p1"]["_id"], parts["p1"]["_id"])
        assert resp.status_code == 400


def test_link_merges_groups(logged_in, parts, app):
    with app.app_context():
        _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])
        _add(logged_in, parts["p3"]["_id"], parts["p4"]["_id"])
        assert _group(parts, "p1") != _group(parts, "p3")

        _add(logged_in, parts["p2"]["_id"], parts["p3"]["_id"])
        groups = {_group(parts, k) for k in ("p1", "p2", "p3", "p4")}
        assert len(groups) == 1 and None not in groups


def test_unlink_and_dissolve(logged_in, parts, app):
    with app.app_context():
        _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])
        _add(logged_in, parts["p1"]["_id"], parts["p3"]["_id"])

        resp = _remove(logged_in, parts["p3"]["_id"])
        assert resp.get_json()["ok"] is True
        assert _group(parts, "p3") is None
        assert _group(parts, "p1") is not None
        assert _group(parts, "p1") == _group(parts, "p2")

        # Отвязали предпоследний парт — группа из одного должна распуститься.
        _remove(logged_in, parts["p2"]["_id"])
        assert _group(parts, "p2") is None
        assert _group(parts, "p1") is None


def test_cross_refs_in_part_api(logged_in, parts, app):
    with app.app_context():
        _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])

        resp = logged_in.get(f"/parts/api/{parts['p1']['_id']}")
        data = resp.get_json()
        assert data["ok"] is True
        assert [x["part_number"] for x in data["item"]["cross_refs"]] == ["XREF-BBB-2"]

        resp = logged_in.get(f"/parts/api/{parts['p2']['_id']}/cross-refs")
        data = resp.get_json()
        assert data["ok"] is True
        assert [x["part_number"] for x in data["items"]] == ["XREF-AAA-1"]


def test_parts_search_returns_alternates(logged_in, parts, app):
    with app.app_context():
        _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])

        resp = logged_in.get("/parts/api/search?q=XREF-AAA")
        data = resp.get_json()
        assert data["ok"] is True
        items = {x["part_number"]: x for x in data["items"]}
        assert "XREF-AAA-1" in items
        alts = items["XREF-AAA-1"]["alternates"]
        assert [a["part_number"] for a in alts] == ["XREF-BBB-2"]
        assert alts[0]["in_stock"] == 0

        # Обе части группы в выдаче — альтернативы не дублируются.
        resp = logged_in.get("/parts/api/search?q=XREF")
        data = resp.get_json()
        items = {x["part_number"]: x for x in data["items"]}
        assert items["XREF-AAA-1"]["alternates"] == []
        assert items["XREF-BBB-2"]["alternates"] == []


def test_wo_parts_search_returns_alternates(logged_in, parts, app):
    with app.app_context():
        _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])

        resp = logged_in.get("/work_orders/api/parts/search?q=XREF-AAA")
        data = resp.get_json()
        items = {x["part_number"]: x for x in data["items"]}
        assert "XREF-AAA-1" in items
        assert [a["part_number"] for a in items["XREF-AAA-1"]["alternates"]] == ["XREF-BBB-2"]


def test_mobile_parts_include_cross_refs(logged_in, parts, app):
    with app.app_context():
        _add(logged_in, parts["p1"]["_id"], parts["p2"]["_id"])

        resp = logged_in.post(
            "/api/mobile/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            environ_base={"REMOTE_ADDR": "127.0.0.5"},
        )
        assert resp.get_json()["ok"] is True

        data = logged_in.get("/api/mobile/parts?q=XREF-AAA").get_json()
        assert data["ok"] is True
        items = {x["part_number"]: x for x in data["items"]}
        assert "XREF-AAA-1" in items
        refs = items["XREF-AAA-1"]["cross_refs"]
        assert [r["part_number"] for r in refs] == ["XREF-BBB-2"]
        assert refs[0]["in_stock"] == 0
