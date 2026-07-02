"""
Платежи work orders: суммирование, статус оплаты, синхронизация статуса WO.
Аргумент session — pymongo-сессия для транзакций (см. app/utils/mongo_tx.py),
None на standalone.
"""
from __future__ import annotations

from app.blueprints.work_orders.services.common import round2
from app.blueprints.work_orders.services.totals import _work_order_grand_total


def _sum_active_work_order_payments(shop_db, wo_id, session=None) -> float:
    if shop_db is None or not wo_id:
        return 0.0

    payments = shop_db.work_order_payments.find(
        {"work_order_id": wo_id, "is_active": True}, session=session
    )
    return round2(sum(round2(payment.get("amount") or 0) for payment in payments))


def _build_work_order_payment_summary(wo: dict, paid_amount: float) -> dict:
    grand_total = _work_order_grand_total(wo or {})
    paid = round2(max(0.0, paid_amount or 0.0))
    remaining_balance = round2(max(0.0, grand_total - paid))
    status = "paid" if remaining_balance <= 0.01 else "open"
    return {
        "grand_total": grand_total,
        "paid_amount": paid,
        "remaining_balance": remaining_balance,
        "status": status,
        "is_fully_paid": status == "paid",
    }


def _sync_work_order_payment_state(shop_db, wo: dict, user_id, now, session=None):
    if shop_db is None or not isinstance(wo, dict):
        return None

    wo_id = wo.get("_id")
    if not wo_id:
        return None

    summary = _build_work_order_payment_summary(
        wo, _sum_active_work_order_payments(shop_db, wo_id, session=session)
    )
    # Preserve "in_progress" status when there's no payment yet (computed status would be "open")
    new_status = summary["status"]
    current_status = (wo.get("status") or "open").strip().lower()
    if new_status == "open" and current_status == "in_progress" and (summary.get("paid_amount") or 0) <= 0.01:
        new_status = "in_progress"
    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "status": new_status,
                "updated_at": now,
                "updated_by": user_id,
            }
        },
        session=session,
    )
    summary["status"] = new_status
    summary["is_in_progress"] = new_status == "in_progress"
    return summary
