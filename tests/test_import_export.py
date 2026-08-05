"""
Import/Export: импорт CSV с реальными кейсами (пробелы в заголовках,
привязка юнитов к клиентам, остатки через сервис склада, work orders
с тоталами и платежами), экспорт CSV/XLSX, отказ .xls.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

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
def ie_seed(seed, mongo):
    """Клиент + юнит для привязок; подчистка всего импортированного."""
    shop_db = mongo[SHOP_A_DB]
    shop_a = seed["shop_a"]
    now = datetime.now(timezone.utc)
    customer = {
        "_id": ObjectId(), "shop_id": shop_a["_id"],
        "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Import Fleet LLC", "contacts": [],
        "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_one(customer)
    unit = {
        "_id": ObjectId(), "shop_id": shop_a["_id"],
        "customer_id": customer["_id"], "unit_number": "IMP-7",
        "vin": "IMPVIN00000000007", "is_active": True, "created_at": now,
    }
    shop_db.units.insert_one(unit)

    yield {"customer": customer, "unit": unit, "shop_id": shop_a["_id"]}

    shop_db.customers.delete_many({"company_name": {"$in": [
        "Import Fleet LLC", "Acme Hauling"]}})
    shop_db.units.delete_many({"customer_id": customer["_id"]})
    shop_db.parts.delete_many({"part_number": {"$in": ["IMP-P1", "IMP-P2"]}})
    shop_db.part_location_stock.delete_many({"part_number": {"$in": ["IMP-P1", "IMP-P2"]}})
    shop_db.inventory_movements.delete_many({"part_number": {"$in": ["IMP-P1", "IMP-P2"]}})
    wo_ids = [w["_id"] for w in shop_db.work_orders.find({"imported": True}, {"_id": 1})]
    shop_db.work_orders.delete_many({"_id": {"$in": wo_ids}})
    shop_db.work_order_payments.delete_many({"work_order_id": {"$in": wo_ids}})


def _post_import(client, entity, csv_text, mapping, filename="data.csv"):
    token = get_csrf_token(client)
    resp = client.post(
        "/import-export/import",
        data={
            "csrf_token": token,
            "entity_type": entity,
            "mapping": json.dumps(mapping),
            "file": (io.BytesIO(csv_text.encode("utf-8")), filename),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# ── импорт ──────────────────────────────────────────────────────────


def test_import_customers_csv_with_spaces_in_headers(client, ie_seed, mongo):
    """Регресс strip-бага: 'Company Name, Phone' (пробелы после запятых)
    раньше давал «0 imported» — поля молча терялись."""
    login(client)
    csv_text = ("Company Name, Phone, Address\n"
                "Acme Hauling, +1 555 0100, 742 Evergreen Terrace\n")
    data = _post_import(client, "customers", csv_text, {
        "Company Name": "company_name", "Phone": "phone", "Address": "address",
    })
    assert data["imported"] == 1, data
    doc = mongo[SHOP_A_DB].customers.find_one({"company_name": "Acme Hauling"})
    assert doc is not None
    assert doc["phone"] == "+1 555 0100"


def test_import_units_links_customer_and_reports_reasons(client, ie_seed, mongo):
    login(client)
    csv_text = ("Customer Name,Unit Number,VIN\n"
                "Import Fleet LLC,IMP-8,IMPVIN00000000008\n"   # ок
                "Ghost Fleet,IMP-9,IMPVIN00000000009\n"        # клиент не найден
                "Import Fleet LLC,,\n"                          # нет идентичности
                "Import Fleet LLC,IMP-7X,IMPVIN00000000007\n")  # дубль VIN
    data = _post_import(client, "units", csv_text, {
        "Customer Name": "customer_name", "Unit Number": "unit_number", "VIN": "vin",
    })
    assert data["imported"] == 1
    assert data["skipped"] == 3
    joined = " | ".join(data["errors"])
    assert 'customer "Ghost Fleet" not found' in joined
    assert "unit number and VIN are both empty" in joined
    assert "already exists for customer" in joined

    unit = mongo[SHOP_A_DB].units.find_one({"unit_number": "IMP-8"})
    assert unit is not None
    assert unit["customer_id"] == ie_seed["customer"]["_id"]


def test_import_parts_uses_stock_service(client, ie_seed, mongo):
    login(client)
    csv_text = ("Part Number,Description,In Stock,Average Cost\n"
                "IMP-P1,Imported filter,5,12.50\n"
                "IMP-P2,Imported hose,,3\n")
    data = _post_import(client, "parts", csv_text, {
        "Part Number": "part_number", "Description": "description",
        "In Stock": "in_stock", "Average Cost": "average_cost",
    })
    assert data["imported"] == 2, data

    shop_db = mongo[SHOP_A_DB]
    p1 = shop_db.parts.find_one({"part_number": "IMP-P1"})
    assert p1["in_stock"] == 5
    assert p1["average_cost"] == 12.50
    # Остаток проведён через сервис: есть строка локации и движение initial
    row = shop_db.part_location_stock.find_one({"part_id": p1["_id"]})
    assert row is not None and row["qty"] == 5
    move = shop_db.inventory_movements.find_one({"part_id": p1["_id"]})
    assert move is not None and move["type"] == "initial" and move["qty_delta"] == 5

    p2 = shop_db.parts.find_one({"part_number": "IMP-P2"})
    assert p2["in_stock"] == 0


def test_import_work_orders(client, ie_seed, mongo):
    login(client)
    csv_text = (
        "WO Number,Date,Customer Name,Unit Number,Status,Description,Labor Total,Parts Total,Sales Tax\n"
        "77001,2024-05-10,Import Fleet LLC,IMP-7,paid,Brake job,100,50,7.50\n"
        "77002,05/12/2024,Import Fleet LLC,,completed,Oil change,80,,\n"
        "77003,2024-05-13,Ghost Fleet,,completed,Nothing,10,,\n"
        "77001,2024-05-14,Import Fleet LLC,,completed,Dup number,10,,\n"
    )
    mapping = {
        "WO Number": "wo_number", "Date": "date", "Customer Name": "customer_name",
        "Unit Number": "unit_number", "Status": "status", "Description": "description",
        "Labor Total": "labor_total", "Parts Total": "parts_total",
        "Sales Tax": "sales_tax_total",
    }
    data = _post_import(client, "work_orders", csv_text, mapping)
    assert data["imported"] == 2, data
    assert data["skipped"] == 2
    joined = " | ".join(data["errors"])
    assert 'customer "Ghost Fleet" not found' in joined
    assert "WO #77001 already exists" in joined

    shop_db = mongo[SHOP_A_DB]
    wo = shop_db.work_orders.find_one({"wo_number": 77001, "shop_id": ie_seed["shop_id"]})
    assert wo is not None
    assert wo["customer_id"] == ie_seed["customer"]["_id"]
    assert wo["unit_id"] == ie_seed["unit"]["_id"]
    assert wo["status"] == "paid"
    totals = wo["totals"]
    assert totals["labor_total"] == 100.0
    assert totals["parts_total"] == 50.0
    assert totals["sales_tax_total"] == 7.50
    assert totals["grand_total"] == 157.50
    # Строка описания сохранена в labors
    assert wo["labors"][0]["labor"]["description"] == "Brake job"
    # paid → платёж на полную сумму
    pay = shop_db.work_order_payments.find_one({"work_order_id": wo["_id"]})
    assert pay is not None
    assert pay["amount"] == 157.50
    assert pay["is_active"] is True

    wo2 = shop_db.work_orders.find_one({"wo_number": 77002, "shop_id": ie_seed["shop_id"]})
    assert wo2["status"] == "completed"
    assert wo2["unit_id"] is None
    assert wo2["totals"]["grand_total"] == 80.0
    assert shop_db.work_order_payments.count_documents({"work_order_id": wo2["_id"]}) == 0


def test_upload_headers_rejects_legacy_xls(client, ie_seed):
    login(client)
    token = get_csrf_token(client)
    resp = client.post(
        "/import-export/upload-headers",
        data={"csrf_token": token, "file": (io.BytesIO(b"junk"), "old.xls")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert ".xlsx or CSV" in resp.get_json()["error"]


# ── экспорт ─────────────────────────────────────────────────────────


def test_export_customers_csv(client, ie_seed):
    login(client)
    resp = client.get("/import-export/export/customers?fmt=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["Content-Type"]
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    text = resp.get_data(as_text=True)
    first_line = text.lstrip("﻿").splitlines()[0]
    assert first_line.startswith("Company Name,First Name,Last Name,Phone,Email,Address")
    assert "Import Fleet LLC" in text


def test_export_units_csv_has_customer_name(client, ie_seed):
    login(client)
    resp = client.get("/import-export/export/units?fmt=csv")
    text = resp.get_data(as_text=True)
    lines = text.lstrip("﻿").splitlines()
    assert lines[0].startswith("Customer Name,Unit Number,VIN")
    row = next(line for line in lines if "IMP-7" in line)
    assert "Import Fleet LLC" in row


def test_export_work_orders_xlsx(client, ie_seed, mongo):
    import openpyxl

    login(client)
    resp = client.get("/import-export/export/work_orders?fmt=xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["Content-Type"]
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    assert headers[:4] == ["WO Number", "Date", "Status", "Customer Name"]


def test_export_invalid_entity_rejected(client, ie_seed):
    login(client)
    resp = client.get("/import-export/export/nonsense?fmt=csv")
    assert resp.status_code == 400
