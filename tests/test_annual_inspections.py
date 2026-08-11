"""AVIR (годовая инспекция): чеклист компонентов, история и срок действия.

- create сохраняет отметки чеклиста (мусорные ключи/статусы отбрасываются);
- история юнита не затирается — вторая инспекция не удаляет первую;
- PDF собирается с отметками (X / NA / дата ремонта);
- delete-эндпоинт удаляет запись (нужен work_orders.delete);
- срок действия: +12 месяцев, статусы valid / expiring / expired.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId
from pymongo import MongoClient

from tests.conftest import SHOP_A_DB, TEST_MONGO_URI, get_csrf_token, login


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture()
def avi_unit(seed, mongo):
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]

    customer = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "company_name": "AVIR Fleet LLC",
        "is_active": True,
    }
    unit = {
        "_id": ObjectId(),
        "shop_id": shop_a["_id"],
        "customer_id": customer["_id"],
        "vin": "1FUJA6CK14LM99999",
        "unit_number": "AVIR-1",
        "type": "semi_truck",
        "is_active": True,
    }
    shop_db.customers.insert_one(customer)
    shop_db.units.insert_one(unit)

    yield {"customer": customer, "unit": unit, "shop_db": shop_db}

    shop_db.annual_inspections.delete_many({"unit_id": unit["_id"]})
    shop_db.units.delete_one({"_id": unit["_id"]})
    shop_db.customers.delete_one({"_id": customer["_id"]})


def _create_inspection(client, unit, components=None, date="2026-03-01"):
    payload = {
        "unit_id": str(unit["_id"]),
        "customer_id": str(unit["customer_id"]),
        "date": date,
        "motor_carrier_operator": "AVIR Fleet LLC",
        "inspector_name": "Test Inspector",
        "vin": unit["vin"],
        "vehicle_type": "semi_truck",
    }
    if components is not None:
        payload["components"] = components
    resp = client.post(
        "/work_orders/api/annual_inspections/create",
        json=payload,
        headers={"X-CSRFToken": get_csrf_token(client)},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True, data
    return data["id"]


def test_create_stores_sanitized_components_and_keeps_history(client, avi_unit):
    login(client)
    shop_db = avi_unit["shop_db"]
    unit = avi_unit["unit"]

    first_id = _create_inspection(client, unit, components={
        "1a": {"status": "ok"},
        "7c": {"status": "repair", "repaired_date": "2026-03-02"},
        "12": {"status": "na"},
        "zzz": {"status": "ok"},              # неизвестный ключ — отброшен
        "2a": {"status": "great"},            # неизвестный статус — отброшен
        "3a": "ok",                            # не dict — отброшен
    })

    doc = shop_db.annual_inspections.find_one({"_id": ObjectId(first_id)})
    assert doc["components"] == {
        "1a": {"status": "ok"},
        "7c": {"status": "repair", "repaired_date": "2026-03-02"},
        "12": {"status": "na"},
    }

    # Вторая инспекция не затирает первую — история сохраняется
    second_id = _create_inspection(client, unit, components={"1a": {"status": "ok"}}, date="2026-04-01")
    assert second_id != first_id
    assert shop_db.annual_inspections.count_documents({"unit_id": unit["_id"]}) == 2

    # PDF последней инспекции скачивается
    resp = client.get(f"/work_orders/api/annual_inspections/{second_id}/download-pdf")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"


def test_preview_pdf_post_with_components(client, avi_unit):
    login(client)
    resp = client.post(
        "/work_orders/api/annual_inspections/preview-pdf",
        json={
            "unit_id": str(avi_unit["unit"]["_id"]),
            "date": "2026-03-01",
            "inspector_name": "Test Inspector",
            "vin": avi_unit["unit"]["vin"],
            "vehicle_type": "semi_truck",
            "components": {"1a": {"status": "ok"}},
        },
        headers={"X-CSRFToken": get_csrf_token(client)},
    )
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"


def test_delete_inspection(client, avi_unit):
    login(client)
    shop_db = avi_unit["shop_db"]
    inspection_id = _create_inspection(client, avi_unit["unit"])

    resp = client.post(
        f"/work_orders/api/annual_inspections/{inspection_id}/delete",
        headers={"X-CSRFToken": get_csrf_token(client)},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert shop_db.annual_inspections.find_one({"_id": ObjectId(inspection_id)}) is None

    # Повторное удаление — inspection_not_found
    resp = client.post(
        f"/work_orders/api/annual_inspections/{inspection_id}/delete",
        headers={"X-CSRFToken": get_csrf_token(client)},
    )
    assert resp.get_json()["ok"] is False


def test_marked_columns_render_states():
    from app.blueprints.work_orders.services.pdf_contexts import _annual_inspection_marked_columns

    cols = _annual_inspection_marked_columns({
        "1a": {"status": "ok"},
        "1b": {"status": "repair", "repaired_date": "2026-03-02"},
        "1c": {"status": "na"},
    })
    by_letter = {i["text"][0]: i for i in cols[0][0]["items"]}
    # OK — галочка в 1-й колонке
    assert by_letter["a"]["ok_mark"] == "✔" and by_letter["a"]["repair_mark"] == ""
    # Needs repair — X во 2-й + дата в 3-й
    assert by_letter["b"]["repair_mark"] == "X" and by_letter["b"]["repaired_date"] == "03/02/26"
    # N/A — отметка в 3-й колонке, 1-я и 2-я пустые
    assert by_letter["c"]["ok_mark"] == "" and by_letter["c"]["repair_mark"] == ""
    assert by_letter["c"]["repaired_date"] == "N/A"
    # Неотмеченный пункт — пустые клетки
    assert by_letter["d"]["ok_mark"] == "" and by_letter["d"]["repair_mark"] == ""
    assert by_letter["d"]["repaired_date"] == ""


def test_type_defaults_cover_full_checklist():
    from app.blueprints.work_orders.services.inspections import (
        VEHICLE_TYPE_COMPONENT_DEFAULTS,
        annual_inspection_component_keys,
        default_components_for_type,
    )

    # Каждый пресет покрывает все пункты формы валидными статусами
    all_keys = annual_inspection_component_keys()
    for vehicle_type, defaults in VEHICLE_TYPE_COMPONENT_DEFAULTS.items():
        assert set(defaults.keys()) == all_keys, vehicle_type
        assert set(defaults.values()) <= {"ok", "na"}, vehicle_type

    trailer = default_components_for_type("semi_trailer")
    assert trailer["1g"] == {"status": "na"}   # tractor protection valve
    assert trailer["7a"] == {"status": "na"}   # steering — трейлеру не применимо
    assert trailer["10a"] == {"status": "na"}  # steering-axle tires

    truck = default_components_for_type("semi_truck")
    assert truck["1g"] == {"status": "ok"}
    assert truck["7a"] == {"status": "ok"}
    assert truck["6a"] == {"status": "na"}     # safe loading — не про тягач
    assert truck["12"] == {"status": "ok"}

    # Hot shot: electric и hydraulic различаются только тормозами 1j
    electric = default_components_for_type("hot_shot_electric")
    hydraulic = default_components_for_type("hot_shot_hydraulic")
    assert electric["1i"] == {"status": "ok"} and electric["1j"] == {"status": "na"}
    assert hydraulic["1i"] == {"status": "ok"} and hydraulic["1j"] == {"status": "ok"}
    diff = {k for k in electric if electric[k] != hydraulic[k]}
    assert diff == {"1j"}

    pickup = default_components_for_type("pickup_truck")
    assert pickup["1k"] == {"status": "ok"}    # vacuum systems
    assert pickup["9c"] == {"status": "na"}

    # Для неизвестного типа пресета нет
    assert default_components_for_type("bus") == {}


def test_create_stores_report_number(client, avi_unit):
    login(client)
    shop_db = avi_unit["shop_db"]
    unit = avi_unit["unit"]

    resp = client.post(
        "/work_orders/api/annual_inspections/create",
        json={
            "unit_id": str(unit["_id"]),
            "customer_id": str(unit["customer_id"]),
            "date": "2026-05-01",
            "vin": unit["vin"],
            "vehicle_type": "semi_trailer",
            "report_number": "LTS-000123",
        },
        headers={"X-CSRFToken": get_csrf_token(client)},
    )
    data = resp.get_json()
    assert data["ok"] is True
    doc = shop_db.annual_inspections.find_one({"_id": ObjectId(data["id"])})
    assert doc["report_number"] == "LTS-000123"

    # Вписанный номер попадает в PDF-контекст вместо хвоста _id
    from app.blueprints.work_orders.services.pdf_contexts import _build_annual_inspection_pdf_context
    ctx = _build_annual_inspection_pdf_context(shop_db, doc)
    assert ctx["report_number"] == "LTS-000123"


def test_inspection_expiry_and_status():
    from app.blueprints.work_orders.services.inspections import (
        inspection_expiry,
        inspection_expiry_status,
    )

    base = datetime(2026, 3, 1, 12, 0, 0)
    expires = inspection_expiry({"inspection_date": base})
    assert expires == datetime(2027, 3, 1, 12, 0, 0)

    now = datetime(2026, 8, 1)
    assert inspection_expiry_status(expires, now=now) == "valid"
    assert inspection_expiry_status(expires, now=datetime(2027, 2, 15)) == "expiring"
    assert inspection_expiry_status(expires, now=datetime(2027, 3, 2)) == "expired"
    assert inspection_expiry_status(None) == ""

    # 29 февраля не ломает расчёт
    leap = inspection_expiry({"inspection_date": datetime(2028, 2, 29)})
    assert leap == datetime(2029, 2, 28)


def test_inspection_modal_available_on_customer_pages(client, avi_unit):
    """Модалка и кнопка запуска есть на странице юнита и в списке юнитов."""
    login(client)
    unit = avi_unit["unit"]
    cid, uid = str(unit["customer_id"]), str(unit["_id"])

    resp = client.get(f"/customers/{cid}/units/{uid}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="annualInspectionModal"' in html
    assert "data-avi-open" in html
    assert f'data-avi-unit-id="{uid}"' in html

    resp = client.get(f"/customers/{cid}?tab=units")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="annualInspectionModal"' in html
    assert f'data-avi-unit-id="{uid}"' in html


def test_component_keys_are_unique_and_stable():
    from app.blueprints.work_orders.services.inspections import (
        annual_inspection_checklist,
        annual_inspection_component_keys,
    )

    keys = [
        item["key"]
        for column in annual_inspection_checklist()
        for section in column
        for item in section["items"]
    ]
    assert len(keys) == len(set(keys)), "component keys must be unique"
    known = annual_inspection_component_keys()
    for expected in ("1a", "1k", "2f", "5", "7j", "12", "13"):
        assert expected in known
