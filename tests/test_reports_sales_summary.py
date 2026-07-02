"""Sales Summary: агрегация должна давать ровно те же числа, что и ручной расчёт.

Кейсы кодируют исторические формы данных:
  - канонический WO: totals-словарь + вложенные labor-блоки с hours;
  - легаси WO: суммы на верхнем уровне, плоские labor-поля, часы через
    fallback totals.labors[i].labor / hourly_rate;
  - WO без customer_id и неактивные — исключаются.
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

        shop_id = seed["shop_a"]["_id"]
        c1, c2 = ObjectId(), ObjectId()

        wos = [
            # Канонический: totals полный, 2 часа по 100, детали 2шт*10.
            {
                "shop_id": shop_id, "is_active": True, "customer_id": c1,
                "work_order_date": datetime(2026, 1, 15, tzinfo=timezone.utc),
                "created_at": datetime(2026, 1, 15, tzinfo=timezone.utc),
                "totals": {
                    "labor_total": 200.0, "parts_total": 50.0,
                    "sales_tax_total": 10.0, "grand_total": 260.0,
                    "labors": [{"labor": 200.0}],
                },
                "labors": [{
                    "labor": {"hours": 2, "rate_code": "standard", "hourly_rate": 100},
                    "parts": [{"qty": 2, "cost": 10.0}],
                }],
            },
            # Легаси: totals без labor_total (fallback на верхний уровень),
            # часы = 150 / 100 (ставка по rate_code), отрицательный qty клампится.
            {
                "shop_id": shop_id, "is_active": True, "customer_id": c1,
                "work_order_date": datetime(2026, 2, 3, tzinfo=timezone.utc),
                "created_at": datetime(2026, 2, 3, tzinfo=timezone.utc),
                "labor_total": 150.0, "parts_total": 25.0,
                "sales_tax_total": 0.0, "grand_total": 175.0,
                "totals": {"labors": [{"labor": 150.0}]},
                "labors": [{
                    "labor_hours": 0, "labor_rate_code": "standard",
                    "parts": [{"qty": 1, "cost": 5.5}, {"qty": -1, "cost": 3.0}],
                }],
            },
            # Второй клиент, февраль.
            {
                "shop_id": shop_id, "is_active": True, "customer_id": c2,
                "work_order_date": datetime(2026, 2, 20, tzinfo=timezone.utc),
                "created_at": datetime(2026, 2, 20, tzinfo=timezone.utc),
                "totals": {
                    "labor_total": 0.0, "parts_total": 100.0,
                    "sales_tax_total": 8.25, "grand_total": 108.25,
                },
                "labors": [],
            },
            # Без customer_id — игнорируется целиком (и в графике тоже).
            {
                "shop_id": shop_id, "is_active": True, "customer_id": None,
                "created_at": datetime(2026, 2, 21, tzinfo=timezone.utc),
                "totals": {"grand_total": 999.0},
            },
            # Неактивный — игнорируется.
            {
                "shop_id": shop_id, "is_active": False, "customer_id": c1,
                "created_at": datetime(2026, 2, 22, tzinfo=timezone.utc),
                "totals": {"grand_total": 555.0},
            },
        ]
        db.work_orders.insert_many(wos)
        return {"db": db, "shop_id": shop_id, "c1": c1, "c2": c2}


def _run(app, report_data, chart_bucket="month"):
    import app.blueprints.reports.audit.routes as R
    date_ctx = {"created_from": None, "created_to_exclusive": None}
    with app.app_context():
        return R._report_sales_summary(
            report_data["db"], report_data["shop_id"], date_ctx,
            None, None, {}, chart_bucket=chart_bucket,
        )


def test_per_customer_rows(app, report_data):
    result = _run(app, report_data)
    rows = {r["customer_id"]: r for r in result["rows"]}
    assert set(rows) == {str(report_data["c1"]), str(report_data["c2"])}

    r1 = rows[str(report_data["c1"])]
    assert r1["orders_count"] == 2
    assert r1["labor_total"] == 350.0       # 200 + 150 (fallback на верхний уровень)
    assert r1["parts_total"] == 75.0        # 50 + 25
    assert r1["parts_cost_total"] == 25.5   # 2*10 + 1*5.5 (qty=-1 отброшен)
    assert r1["sales_tax_total"] == 10.0
    assert r1["grand_total"] == 435.0
    assert r1["invoiced_hours"] == 3.5      # 2 (stored) + 150/100 (fallback)

    r2 = rows[str(report_data["c2"])]
    assert r2["orders_count"] == 1
    assert r2["grand_total"] == 108.25
    assert r2["invoiced_hours"] == 0.0


def test_summary_totals(app, report_data):
    s = _run(app, report_data)["summary"]
    assert s["orders_count"] == 3
    assert s["revenue_total"] == 543.25     # 260 + 175 + 108.25; 999/555 исключены
    assert s["labor_total"] == 350.0
    assert s["parts_total"] == 175.0
    assert s["parts_cost_total"] == 25.5
    assert s["sales_tax_total"] == 18.25
    assert s["invoiced_hours"] == 3.5
    assert s["avg_ticket"] == round(543.25 / 3, 2)


def test_chart_month_buckets(app, report_data):
    chart = _run(app, report_data)["chart_data"]
    assert chart["labels"] == ["2026-01", "2026-02"]
    data = {d["label"]: d["data"] for d in chart["datasets"]}
    assert data["Revenue"] == [260.0, 283.25]   # фев: 175 + 108.25 (999 без клиента исключён)
    assert data["Labor"] == [200.0, 150.0]
    assert data["Parts"] == [50.0, 125.0]


def test_chart_week_buckets_are_mondays(app, report_data):
    chart = _run(app, report_data, chart_bucket="week")["chart_data"]
    # 2026-01-15 — четверг, его понедельник — 2026-01-12.
    assert "2026-01-12" in chart["labels"]
    for label in chart["labels"]:
        assert datetime.strptime(label, "%Y-%m-%d").weekday() == 0


def test_rows_sorted_by_grand_total_desc(app, report_data):
    rows = _run(app, report_data)["rows"]
    totals = [r["grand_total"] for r in rows]
    assert totals == sorted(totals, reverse=True)
