"""Рекаллы NHTSA по юниту: выдача списка и подсветка новых кампаний.

Снапшот просмотренных кампаний хранится на юните (`recalls_seen`):
при первом просмотре ничего не помечается новым, при последующих — новыми
считаются кампании, которых не было в снапшоте.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, login


RECALL_ROW = {
    "NHTSACampaignNumber": "19V066000",
    "ReportReceivedDate": "07/02/2019",
    "Component": "AIR BAGS:FRONTAL",
    "Summary": "The driver's frontal air bag may deploy unexpectedly.",
    "Consequence": "Unexpected deployment can increase the risk of a crash.",
    "Remedy": "Dealers will remove the air bag free of charge.",
    "parkIt": False,
    "parkOutSide": False,
}


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


@pytest.fixture()
def unit(seed):
    mongo = MongoClient(TEST_MONGO_URI)
    units = mongo[SHOP_A_DB].units
    doc = {
        "_id": ObjectId(),
        "shop_id": seed["shop_a"]["_id"],
        "customer_id": ObjectId(),
        "unit_number": "42",
        "vin": "3AKJHHDR0LSLU1234",
        "year": 2020,
        "make": "FREIGHTLINER",
        "model": "CASCADIA",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    units.insert_one(doc)
    yield doc
    units.delete_one({"_id": doc["_id"]})
    mongo.close()


def _mock_recalls(monkeypatch, rows, catalog=()):
    """Подменяет NHTSA: каталог моделей и выдачу рекаллов.

    rows — либо список строк (одна выдача на любой запрос), либо dict
    {model: rows} для проверки объединения по вариантам модели.
    Пустой catalog означает «модель в каталоге не нашлась» — эндпоинт
    запрашивает исходное имя модели как есть.
    """
    from app.blueprints.work_orders import recalls_api

    calls = {"models": []}

    def fake_recalls(make, model, year):
        calls["args"] = (make, model, year)
        calls["models"].append(model)
        picked = rows.get(model, []) if isinstance(rows, dict) else rows
        return {"Count": len(picked), "Message": "Results returned successfully", "results": picked}

    monkeypatch.setattr(recalls_api, "_fetch_recalls", fake_recalls)
    monkeypatch.setattr(recalls_api, "_fetch_model_catalog", lambda make, year: list(catalog))
    return calls


def _get_unit_doc(unit):
    mongo = MongoClient(TEST_MONGO_URI)
    try:
        return mongo[SHOP_A_DB].units.find_one({"_id": unit["_id"]})
    finally:
        mongo.close()


def test_first_check_lists_recalls_without_new_flags(logged_in, unit, monkeypatch):
    calls = _mock_recalls(monkeypatch, [RECALL_ROW])

    resp = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["ok"] is True
    assert calls["args"] == ("FREIGHTLINER", "CASCADIA", "2020")
    assert data["count"] == 1
    assert data["new_count"] == 0
    assert data["prev_checked"] == ""

    recall = data["recalls"][0]
    assert recall["campaign_number"] == "19V066000"
    assert recall["report_date"] == "02/07/2019"  # DD/MM/YYYY -> MM/DD/YYYY
    assert recall["is_new"] is False

    # Снапшот просмотренного сохранён на юните.
    doc = _get_unit_doc(unit)
    assert doc["recalls_seen"]["campaigns"] == ["19V066000"]
    assert doc["recalls_seen"]["checked_at"] is not None


def test_second_check_marks_only_fresh_campaigns_as_new(logged_in, unit, monkeypatch):
    _mock_recalls(monkeypatch, [RECALL_ROW])
    assert logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()["ok"] is True

    fresh = dict(RECALL_ROW, NHTSACampaignNumber="25V999000", Component="BRAKES")
    _mock_recalls(monkeypatch, [RECALL_ROW, fresh])

    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["new_count"] == 1
    assert data["prev_checked"]  # дата прошлой проверки

    flags = {r["campaign_number"]: r["is_new"] for r in data["recalls"]}
    assert flags == {"19V066000": False, "25V999000": True}

    doc = _get_unit_doc(unit)
    assert doc["recalls_seen"]["campaigns"] == ["19V066000", "25V999000"]


def test_unit_without_ymm_and_vin_returns_error(logged_in, unit, monkeypatch):
    mongo = MongoClient(TEST_MONGO_URI)
    mongo[SHOP_A_DB].units.update_one(
        {"_id": unit["_id"]},
        {"$set": {"make": None, "model": None, "year": None, "vin": None}},
    )
    mongo.close()

    _mock_recalls(monkeypatch, [RECALL_ROW])
    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is False
    assert data["error"] == "unit_missing_info"


def test_missing_ymm_is_filled_from_vin_decode(logged_in, unit, monkeypatch):
    mongo = MongoClient(TEST_MONGO_URI)
    mongo[SHOP_A_DB].units.update_one(
        {"_id": unit["_id"]},
        {"$set": {"make": None, "model": None, "year": None}},
    )
    mongo.close()

    from app.blueprints.work_orders import recalls_api

    monkeypatch.setattr(
        recalls_api,
        "_fetch_vpic",
        lambda vin: {"Results": [{"Make": "FREIGHTLINER", "Model": "CASCADIA", "ModelYear": "2020"}]},
    )
    calls = _mock_recalls(monkeypatch, [])

    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is True
    assert calls["args"] == ("FREIGHTLINER", "CASCADIA", "2020")
    assert data["count"] == 0


def test_model_name_is_resolved_via_nhtsa_catalog(logged_in, unit, monkeypatch):
    """vPIC даёт «F-450», каталог рекаллов NHTSA — «F-450 SD»."""
    mongo = MongoClient(TEST_MONGO_URI)
    mongo[SHOP_A_DB].units.update_one({"_id": unit["_id"]}, {"$set": {"make": "FORD", "model": "F-450", "year": 2024}})
    mongo.close()

    calls = _mock_recalls(
        monkeypatch,
        {"F-450 SD": [RECALL_ROW]},
        catalog=["BRONCO SPORT", "F-450 SD", "F-550 SD"],
    )

    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is True
    assert calls["models"] == ["F-450 SD"]
    assert data["nhtsa_models"] == ["F-450 SD"]
    assert data["count"] == 1


def test_variants_are_merged_and_deduped_by_campaign(logged_in, unit, monkeypatch):
    mongo = MongoClient(TEST_MONGO_URI)
    mongo[SHOP_A_DB].units.update_one({"_id": unit["_id"]}, {"$set": {"make": "FORD", "model": "F-150", "year": 2024}})
    mongo.close()

    other = dict(RECALL_ROW, NHTSACampaignNumber="24V111000")
    calls = _mock_recalls(
        monkeypatch,
        {
            "F-150 (REGULAR CAB) GAS": [RECALL_ROW, other],
            "F-150 (SUPER CAB) GAS": [RECALL_ROW],
        },
        catalog=["F-150 (REGULAR CAB) GAS", "F-150 (SUPER CAB) GAS", "MAVERICK"],
    )

    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is True
    assert sorted(calls["models"]) == ["F-150 (REGULAR CAB) GAS", "F-150 (SUPER CAB) GAS"]
    assert data["count"] == 2  # общая кампания не задублирована
    assert sorted(r["campaign_number"] for r in data["recalls"]) == ["19V066000", "24V111000"]


def test_http_400_with_json_body_is_not_a_failure(logged_in, unit, monkeypatch):
    """api.nhtsa.gov отвечает 400 с телом {"Count": 0, ...} на неизвестные
    make/model — это пустой результат, а не отказ сервиса."""
    import io
    import urllib.error
    import urllib.request as urlreq

    from app.blueprints.work_orders import recalls_api

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        raise urllib.error.HTTPError(
            url, 400, "Bad Request", None,
            io.BytesIO(b'{"Count":0,"Message":"Results returned successfully","results":[]}'),
        )

    monkeypatch.setattr(recalls_api, "_fetch_model_catalog", lambda make, year: [])
    monkeypatch.setattr(urlreq, "urlopen", fake_urlopen)

    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is True
    assert data["count"] == 0


def test_lookup_failure_returns_friendly_error(logged_in, unit, monkeypatch):
    from app.blueprints.work_orders import recalls_api

    def boom(make, model, year):
        raise OSError("network down")

    monkeypatch.setattr(recalls_api, "_fetch_recalls", boom)
    monkeypatch.setattr(recalls_api, "_fetch_model_catalog", lambda make, year: [])

    data = logged_in.get(f"/work_orders/api/units/{unit['_id']}/recalls").get_json()
    assert data["ok"] is False
    assert data["error"] == "recalls_lookup_failed"

    # Снапшот не должен появиться после неудачного запроса.
    doc = _get_unit_doc(unit)
    assert "recalls_seen" not in doc


def test_unknown_unit_returns_error(logged_in, monkeypatch):
    _mock_recalls(monkeypatch, [RECALL_ROW])
    data = logged_in.get(f"/work_orders/api/units/{ObjectId()}/recalls").get_json()
    assert data["ok"] is False
    assert data["error"] == "unit_not_found"


def test_requires_login(client, unit):
    resp = client.get(f"/work_orders/api/units/{unit['_id']}/recalls")
    assert resp.status_code in (302, 401)


def test_recalls_ui_present_on_work_order_page(logged_in):
    resp = logged_in.get("/work_orders/details")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "unitRecallsModal" in html
    assert "js-open-recalls-modal" in html
    assert "unit_recalls.js" in html


def test_recalls_ui_present_on_unit_details_page(logged_in, unit, seed):
    mongo = MongoClient(TEST_MONGO_URI)
    customers = mongo[SHOP_A_DB].customers
    customer = {
        "_id": unit["customer_id"],
        "shop_id": seed["shop_a"]["_id"],
        "first_name": "Recall",
        "last_name": "Tester",
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    customers.insert_one(customer)
    try:
        resp = logged_in.get(f"/customers/{customer['_id']}/units/{unit['_id']}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "unitRecallsModal" in html
        assert "js-open-recalls-modal" in html
        assert "unit_recalls.js" in html
    finally:
        customers.delete_one({"_id": customer["_id"]})
        mongo.close()
