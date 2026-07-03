"""
Bulk-платежи: один платёж клиента (например, чек от компании) распределяется
по нескольким work orders за раз. Все записи и пересчёт статусов WO — одной
логической операцией через run_atomically (см. app/utils/mongo_tx.py).
"""
from __future__ import annotations

from bson import ObjectId

from app.utils.mongo_tx import run_atomically
from app.blueprints.work_orders.services.common import (
    f64,
    format_preferred_date_label,
    oid,
    round2,
    utcnow,
)
from app.blueprints.work_orders.services.lookups import customer_label, unit_label
from app.blueprints.work_orders.services.payments import (
    _sum_active_work_order_payments,
    _sync_work_order_payment_state,
)
from app.blueprints.work_orders.services.totals import _work_order_grand_total

# Эстимейты — не инвойсы, их не оплачиваем (тот же список, что в listing.get_estimates_list).
ESTIMATE_STATUSES = ["estimate", "estimated", "quote", "quoted"]

MAX_BULK_ALLOCATIONS = 200

# Balance meньше цента считаем нулевым — совпадает с порогом в
# _build_work_order_payment_summary (remaining_balance <= 0.01 -> paid).
_BALANCE_EPSILON = 0.009


def _unpaid_invoices_match(shop_id) -> dict:
    return {
        "shop_id": shop_id,
        "is_active": True,
        "status": {"$nin": ESTIMATE_STATUSES + ["paid"]},
    }


def get_bulk_payment_customers(shop_db, shop_id) -> list[dict]:
    """Клиенты с открытым балансом: сколько неоплаченных инвойсов и общий долг."""
    pipeline = [
        {"$match": {**_unpaid_invoices_match(shop_id), "customer_id": {"$ne": None}}},
        {
            "$lookup": {
                "from": "work_order_payments",
                "let": {"wid": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$work_order_id", "$$wid"]},
                                    {"$eq": ["$is_active", True]},
                                ]
                            }
                        }
                    },
                    {"$group": {"_id": None, "paid": {"$sum": {"$ifNull": ["$amount", 0]}}}},
                ],
                "as": "_pay",
            }
        },
        {
            "$addFields": {
                "_grand": {"$ifNull": ["$totals.grand_total", {"$ifNull": ["$grand_total", 0]}]},
                "_paid": {"$ifNull": [{"$arrayElemAt": ["$_pay.paid", 0]}, 0]},
            }
        },
        {"$addFields": {"_balance": {"$max": [0, {"$subtract": ["$_grand", "$_paid"]}]}}},
        {"$match": {"_balance": {"$gt": _BALANCE_EPSILON}}},
        {
            "$group": {
                "_id": "$customer_id",
                "invoices_count": {"$sum": 1},
                "balance_due": {"$sum": "$_balance"},
            }
        },
    ]

    rows = list(shop_db.work_orders.aggregate(pipeline))
    if not rows:
        return []

    customer_ids = [r["_id"] for r in rows if r.get("_id")]
    labels: dict = {}
    for c in shop_db.customers.find(
        {"_id": {"$in": customer_ids}},
        {"company_name": 1, "first_name": 1, "last_name": 1, "contacts": 1},
    ):
        labels[c.get("_id")] = customer_label(c)

    items = [
        {
            "id": str(r["_id"]),
            "label": labels.get(r["_id"]) or "(no name)",
            "invoices_count": int(r.get("invoices_count") or 0),
            "balance_due": round2(r.get("balance_due") or 0),
        }
        for r in rows
        if r.get("_id")
    ]
    items.sort(key=lambda x: x["label"].lower())
    return items


def get_unpaid_work_orders_for_customer(shop_db, shop_id, customer_id: ObjectId) -> list[dict]:
    """Неоплаченные инвойсы клиента с балансами, старые первыми (для авто-распределения)."""
    rows = list(
        shop_db.work_orders.find({**_unpaid_invoices_match(shop_id), "customer_id": customer_id})
        .sort([("work_order_date", 1), ("created_at", 1)])
    )
    if not rows:
        return []

    wo_ids = [x["_id"] for x in rows]
    paid_map: dict = {}
    pay_pipeline = [
        {"$match": {"work_order_id": {"$in": wo_ids}, "is_active": True}},
        {"$group": {"_id": "$work_order_id", "paid": {"$sum": {"$ifNull": ["$amount", 0]}}}},
    ]
    for r in shop_db.work_order_payments.aggregate(pay_pipeline):
        paid_map[r.get("_id")] = float(r.get("paid") or 0)

    unit_ids = [x.get("unit_id") for x in rows if x.get("unit_id")]
    units_map: dict = {}
    if unit_ids:
        for u in shop_db.units.find({"_id": {"$in": unit_ids}}):
            units_map[u.get("_id")] = unit_label(u)

    items = []
    for x in rows:
        grand_total = round2(_work_order_grand_total(x))
        paid_amount = round2(paid_map.get(x["_id"], 0))
        balance = round2(max(0.0, grand_total - paid_amount))
        if balance <= _BALANCE_EPSILON:
            continue
        items.append(
            {
                "id": str(x["_id"]),
                "wo_number": x.get("wo_number"),
                "date": format_preferred_date_label(x.get("work_order_date"), x.get("created_at")),
                "unit": units_map.get(x.get("unit_id")) or "-",
                "grand_total": grand_total,
                "paid_amount": paid_amount,
                "balance": balance,
            }
        )
    return items


def apply_bulk_payment(
    shop_db,
    shop,
    mongo_client,
    *,
    allocations,
    payment_method: str,
    notes: str,
    payment_date,
    user_id,
    customer_id: ObjectId | None = None,
) -> dict:
    """Проверяет распределение и атомарно записывает платежи по нескольким WO.

    Всё или ничего: любая невалидная строка (несуществующий WO, оверпеймент,
    чужой клиент, эстимейт) отклоняет весь запрос без записи.
    """
    if not isinstance(allocations, list) or not allocations:
        return {"ok": False, "error": "no_allocations", "message": "Select at least one invoice to pay."}
    if len(allocations) > MAX_BULK_ALLOCATIONS:
        return {
            "ok": False,
            "error": "too_many_allocations",
            "message": f"Too many invoices in one bulk payment (max {MAX_BULK_ALLOCATIONS}).",
        }

    parsed: list[tuple[ObjectId, float]] = []
    seen_ids = set()
    for item in allocations:
        if not isinstance(item, dict):
            return {"ok": False, "error": "invalid_allocation", "message": "Invalid allocation entry."}
        wo_id = oid(item.get("work_order_id"))
        if not wo_id:
            return {"ok": False, "error": "invalid_work_order_id", "message": "Invalid work order id in allocations."}
        if wo_id in seen_ids:
            return {
                "ok": False,
                "error": "duplicate_work_order",
                "message": "The same invoice is listed more than once.",
            }
        seen_ids.add(wo_id)
        amount = f64(item.get("amount"))
        if amount is None or not (isinstance(amount, (int, float)) and amount > 0):
            return {
                "ok": False,
                "error": "invalid_amount",
                "message": "Each selected invoice needs a payment amount greater than zero.",
            }
        parsed.append((wo_id, round2(amount)))

    wos_map = {
        w["_id"]: w
        for w in shop_db.work_orders.find(
            {"_id": {"$in": list(seen_ids)}, "shop_id": shop["_id"], "is_active": True}
        )
    }

    prepared = []
    for wo_id, amount in parsed:
        wo = wos_map.get(wo_id)
        if not wo:
            return {"ok": False, "error": "work_order_not_found", "message": "One of the invoices was not found."}
        status = (wo.get("status") or "").strip().lower()
        if status in ESTIMATE_STATUSES:
            wo_number = wo.get("wo_number") or wo_id
            return {
                "ok": False,
                "error": "not_payable",
                "message": f"#{wo_number} is an estimate and cannot receive payments.",
            }
        if customer_id and wo.get("customer_id") != customer_id:
            wo_number = wo.get("wo_number") or wo_id
            return {
                "ok": False,
                "error": "customer_mismatch",
                "message": f"Invoice #{wo_number} belongs to a different customer.",
            }

        grand_total = round2(_work_order_grand_total(wo))
        paid_amount = _sum_active_work_order_payments(shop_db, wo_id)
        new_paid = round2(paid_amount + amount)
        if new_paid > grand_total:
            wo_number = wo.get("wo_number") or wo_id
            return {
                "ok": False,
                "error": "overpayment",
                "work_order_id": str(wo_id),
                "message": (
                    f"Payment for invoice #{wo_number} exceeds its balance "
                    f"(${round2(max(0.0, grand_total - paid_amount)):.2f} remaining)."
                ),
            }
        prepared.append({"wo": wo, "amount": amount})

    now = utcnow()
    bulk_group_id = ObjectId()
    payment_method = (payment_method or "").strip() or "cash"
    notes = (notes or "").strip()

    # Все платежи + пересчёт статусов WO — одна логическая операция:
    # на replica set — транзакция, на standalone — последовательно (session=None).
    def _apply(session):
        results = []
        for entry in prepared:
            wo = entry["wo"]
            payment_doc = {
                "work_order_id": wo["_id"],
                "shop_id": shop["_id"],
                "tenant_id": shop.get("tenant_id"),
                "amount": entry["amount"],
                "payment_method": payment_method,
                "notes": notes,
                "payment_date": payment_date,
                "bulk_group_id": bulk_group_id,
                "is_active": True,
                "created_at": now,
                "created_by": user_id,
            }
            inserted = shop_db.work_order_payments.insert_one(payment_doc, session=session)

            refreshed_wo = shop_db.work_orders.find_one(
                {"_id": wo["_id"], "shop_id": shop["_id"], "is_active": True}, session=session
            ) or wo
            summary = _sync_work_order_payment_state(
                shop_db, refreshed_wo, user_id, now, session=session
            ) or {"status": "open", "remaining_balance": None, "is_fully_paid": False}

            results.append(
                {
                    "work_order_id": str(wo["_id"]),
                    "payment_id": str(inserted.inserted_id),
                    "amount": entry["amount"],
                    "status": summary.get("status") or "open",
                    "remaining_balance": summary.get("remaining_balance"),
                    "is_fully_paid": bool(summary.get("is_fully_paid")),
                }
            )
        return results

    results = run_atomically(mongo_client, _apply)

    return {
        "ok": True,
        "bulk_group_id": str(bulk_group_id),
        "applied_total": round2(sum(entry["amount"] for entry in prepared)),
        "invoices_paid_in_full": sum(1 for r in results if r["is_fully_paid"]),
        "results": results,
    }
