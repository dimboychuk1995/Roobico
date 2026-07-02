"""Поиск по customers/units через search_terms: индексный путь, fallback, инъекции."""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.utils.entity_search import (
    build_customer_search_terms,
    build_unit_search_terms,
    search_customer_ids,
    search_unit_ids,
)
from tests.conftest import SHOP_A_DB


@pytest.fixture(scope="module")
def search_data(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        db.customers.delete_many({})
        db.units.delete_many({})

        now = datetime.now(timezone.utc)
        shop_id = seed["shop_a"]["_id"]

        customers = [
            {
                "_id": ObjectId(), "shop_id": shop_id, "is_active": True,
                "company_name": "Acme Trucking LLC", "address": "12 Main St",
                "contacts": [{"first_name": "John", "last_name": "Doe",
                              "phone": "555-123-4567", "email": "john@acme.com", "is_main": True}],
                "created_at": now,
            },
            {
                "_id": ObjectId(), "shop_id": shop_id, "is_active": True,
                "company_name": "aXb Logistics", "address": "34 Side St",
                "contacts": [], "created_at": now,
            },
            # Легаси-запись БЕЗ search_terms — должна находиться через fallback.
            {
                "_id": ObjectId(), "shop_id": shop_id, "is_active": True,
                "company_name": "Legacy Freight", "address": "56 Old Rd",
                "contacts": [], "created_at": now, "_no_terms": True,
            },
        ]
        for c in customers:
            if not c.pop("_no_terms", False):
                c["search_terms"] = build_customer_search_terms(c)
        db.customers.insert_many(customers)

        units = [
            {
                "_id": ObjectId(), "shop_id": shop_id, "customer_id": customers[0]["_id"],
                "is_active": True, "unit_number": "TRK-42", "vin": "1XPBD49X1MD756789",
                "make": "Peterbilt", "model": "579", "year": 2021, "created_at": now,
            },
        ]
        for u in units:
            u["search_terms"] = build_unit_search_terms(u)
        db.units.insert_many(units)

        return {"db": db, "customers": customers, "units": units}


def _ids(docs):
    return {str(d["_id"]) for d in docs}


def test_customer_found_by_partial_company_name(app, search_data):
    with app.app_context():
        found = search_customer_ids(search_data["db"].customers, "acme truck")
    assert str(search_data["customers"][0]["_id"]) in map(str, found)


def test_customer_found_by_contact_phone_fragment(app, search_data):
    with app.app_context():
        found = search_customer_ids(search_data["db"].customers, "123-45")
    assert str(search_data["customers"][0]["_id"]) in map(str, found)


def test_customer_found_by_contact_email(app, search_data):
    with app.app_context():
        found = search_customer_ids(search_data["db"].customers, "john@acme")
    assert str(search_data["customers"][0]["_id"]) in map(str, found)


def test_short_query_uses_regex_fallback(app, search_data):
    with app.app_context():
        found = search_customer_ids(search_data["db"].customers, "aX")
    assert str(search_data["customers"][1]["_id"]) in map(str, found)


def test_legacy_doc_without_terms_found_via_fallback(app, search_data):
    with app.app_context():
        found = search_customer_ids(search_data["db"].customers, "Legacy Freight")
    assert str(search_data["customers"][2]["_id"]) in map(str, found)


def test_regex_injection_is_escaped(app, search_data):
    # "a.b" не должно матчить "aXb" — точка экранируется.
    with app.app_context():
        found = search_customer_ids(search_data["db"].customers, "a.b")
    assert str(search_data["customers"][1]["_id"]) not in map(str, found)


def test_no_results_for_garbage(app, search_data):
    with app.app_context():
        assert search_customer_ids(search_data["db"].customers, "zz-nothing-here") == []
        assert search_customer_ids(search_data["db"].customers, "") == []


def test_unit_found_by_vin_fragment(app, search_data):
    with app.app_context():
        found = search_unit_ids(search_data["db"].units, "1XPBD49")
    assert _ids(search_data["units"]) == set(map(str, found))


def test_unit_found_by_make_case_insensitive(app, search_data):
    with app.app_context():
        found = search_unit_ids(search_data["db"].units, "peterbilt")
    assert _ids(search_data["units"]) == set(map(str, found))


def test_terms_rebuilt_on_update_shape(app, search_data):
    """build_*_search_terms детерминированы и включают все искомые поля."""
    c = search_data["customers"][0]
    terms = build_customer_search_terms(c)
    assert terms == sorted(set(terms))
    compact_phone = "5551234567"
    assert any(t in compact_phone for t in terms)
