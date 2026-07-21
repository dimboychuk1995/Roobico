"""General Revenue: все суммы старого отчёта сохранены в новой структуре rows
(секции + пояснения + kind), а арифметика Bottom Line сходится с summary.

Payroll в тестах недоступен (нет date_from/date_to → available=False), поэтому
секция Payroll отсутствует, а labor_cost = 0.
"""
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from tests.conftest import SHOP_A_DB


@pytest.fixture(scope="module")
def report_data(app, seed):
    from app.extensions import get_mongo_client
    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        db.work_orders.delete_many({})
        db.parts_orders.delete_many({})
        db.parts_order_payments.delete_many({})

        shop_id = seed["shop_a"]["_id"]
        c1 = ObjectId()

        db.work_orders.insert_many([
            # Канонический WO: parts_pure = parts_total (без cores/misc),
            # механик Mike 100% от 2 часов.
            {
                "shop_id": shop_id, "is_active": True, "customer_id": c1,
                "work_order_date": datetime(2026, 3, 10, tzinfo=timezone.utc),
                "created_at": datetime(2026, 3, 10, tzinfo=timezone.utc),
                "totals": {
                    "labor_total": 200.0, "parts_total": 50.0,
                    "sales_tax_total": 10.0, "grand_total": 260.0,
                },
                "labors": [{
                    "labor": {
                        "hours": 2, "hourly_rate": 100,
                        "assigned_mechanics": [
                            {"user_id": "u1", "name": "Mike", "percent": 100},
                        ],
                    },
                    "parts": [{"qty": 2, "cost": 10.0}],
                }],
            },
            # WO с cores и misc в totals: parts_pure берётся из totals.parts,
            # 4 часа делятся между Mike и Anna пополам.
            {
                "shop_id": shop_id, "is_active": True, "customer_id": c1,
                "work_order_date": datetime(2026, 3, 15, tzinfo=timezone.utc),
                "created_at": datetime(2026, 3, 15, tzinfo=timezone.utc),
                "totals": {
                    "labor_total": 100.0, "parts_total": 80.0, "parts": 60.0,
                    "core_total": 15.0, "misc_total": 5.0,
                    "sales_tax_total": 8.0, "grand_total": 188.0,
                },
                "labors": [{
                    "labor": {
                        "hours": 4, "hourly_rate": 25,
                        "assigned_mechanics": [
                            {"user_id": "u1", "name": "Mike", "percent": 50},
                            {"user_id": "u2", "name": "Anna", "percent": 50},
                        ],
                    },
                    "parts": [{"qty": 1, "cost": 30.0}],
                }],
            },
        ])

        po_id = ObjectId()
        db.parts_orders.insert_one({
            "_id": po_id, "shop_id": shop_id, "is_active": True,
            "order_date": datetime(2026, 3, 12, tzinfo=timezone.utc),
            "created_at": datetime(2026, 3, 12, tzinfo=timezone.utc),
            "items": [{"quantity": 2, "price": 25.0, "core_charge": 5.0}],
            "non_inventory_amounts": [{"type": "shop_supply", "amount": 7.5}],
        })
        db.parts_order_payments.insert_one({
            "shop_id": shop_id, "parts_order_id": po_id,
            "amount": 40.0, "is_active": True,
        })

        return {"db": db, "shop_id": shop_id}


def _run(app, report_data):
    import app.blueprints.reports.audit.routes as R
    date_ctx = {"created_from": None, "created_to_exclusive": None}
    with app.app_context():
        return R._report_general_revenue(
            report_data["db"], report_data["shop_id"], date_ctx,
        )


def test_summary_totals(app, report_data):
    s = _run(app, report_data)["summary"]
    assert s["sales_revenue"] == 448.0        # 260 + 188
    assert s["sales_labor"] == 300.0
    assert s["parts_sale"] == 110.0           # 50 + totals.parts=60
    assert s["parts_cost"] == 50.0            # 2*10 + 1*30
    assert s["parts_profit"] == 60.0
    assert s["core_charges"] == 15.0
    assert s["po_total_spent"] == 67.5        # 2*25 + 2*5 + 7.5
    assert s["net_revenue"] == 380.5          # 448 - 67.5
    assert s["wo_count"] == 2
    assert s["po_count"] == 1
    assert s["invoiced_hours"] == 6.0
    assert s["total_mech_hours"] == 6.0       # Mike 2+2, Anna 2
    # Payroll недоступен → labor_cost 0, обе итоговые цифры без зарплат.
    assert s["labor_cost"] == 0.0
    assert s["net_after_labor"] == 380.5
    assert s["net_revenue_parts_orders"] == 380.5
    assert s["net_revenue_parts_cost"] == 398.0  # 448 - 50
    assert s["revenue_minus_parts_cost"] == 398.0  # 448 - 50, без зарплат


def test_rows_have_sections_and_descriptions(app, report_data):
    rows = _run(app, report_data)["rows"]

    sections = [r["category"] for r in rows if r.get("row_type") == "section"]
    assert sections == [
        "Money In — Sales (Work Orders)",
        "Money Out — Parts Orders (vendor purchases)",
        "Bottom Line — Cash view (Parts Orders basis)",
        "Bottom Line — Job view (Parts Cost basis)",
        "Mechanic Hours",
    ]

    for r in rows:
        if r.get("row_type") == "section":
            assert r["desc"], f"section without desc: {r['category']}"
            assert r["amount"] is None
        else:
            assert "desc" in r, f"row without desc: {r['category']}"
            assert r["kind"] in {"in", "out", "info", "result"}, r["category"]


def test_rows_preserve_all_amounts(app, report_data):
    rows = _run(app, report_data)["rows"]
    amounts = {
        r["category"]: r["amount"] for r in rows if r.get("row_type") != "section"
    }

    # Money In
    assert amounts["Labor billed"] == 300.0
    assert amounts["Parts billed (sale price)"] == 110.0
    assert amounts["Parts cost (reference)"] == 50.0
    assert amounts["Parts profit (sale - cost)"] == 60.0
    assert amounts["Core charges billed"] == 15.0
    assert amounts["Misc charges billed"] == 5.0
    assert amounts["Sales tax collected"] == 18.0
    assert amounts["Total Revenue"] == 448.0
    # Money Out (Parts Orders)
    assert amounts["Parts bought"] == 50.0
    assert amounts["Cores charged by vendors"] == 10.0
    assert amounts["Non-inventory purchases"] == 7.5
    assert amounts["Total spent at vendors"] == 67.5
    assert amounts["...of it already paid"] == 40.0
    assert amounts["...still owed to vendors"] == 27.5
    # Bottom Line
    assert amounts["- Parts Orders (total spent)"] == 67.5
    assert amounts["= Left after vendor purchases"] == 380.5
    assert amounts["= NET REVENUE — Cash view"] == 380.5
    assert amounts["- Parts cost on Work Orders"] == 50.0
    assert amounts["= Left after parts cost"] == 398.0
    assert amounts["= NET REVENUE — Job view"] == 398.0
    # Mechanic Hours
    assert amounts["All mechanics — total"] == 6.0
    assert amounts["Mike"] == 4.0
    assert amounts["Anna"] == 2.0


def test_bottom_line_math_matches_summary(app, report_data):
    result = _run(app, report_data)
    s = result["summary"]
    assert s["net_revenue"] == round(s["sales_revenue"] - s["po_total_spent"], 2)
    assert s["net_revenue_parts_orders"] == round(
        s["sales_revenue"] - s["po_total_spent"] - s["labor_cost"], 2
    )
    assert s["net_revenue_parts_cost"] == round(
        s["sales_revenue"] - s["parts_cost"] - s["labor_cost"], 2
    )


def test_hours_rows_flagged(app, report_data):
    rows = _run(app, report_data)["rows"]
    hours_rows = [r for r in rows if r.get("is_hours")]
    assert {r["category"] for r in hours_rows} == {
        "All mechanics — total", "Mike", "Anna",
    }
