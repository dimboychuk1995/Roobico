"""
Перенос WO на другого клиента (transfer) с compare-and-swap по labors_rev.

Кейс: механик завёл WO на нового клиента с новым юнитом, а WO должен
принадлежать существующему клиенту. Юнит следует за WO:
  - у целевого клиента уже есть юнит с тем же VIN → WO перевешивается на
    него (деактивированный дубль реактивируется);
  - нет → юнит «переезжает»: целевому клиенту создаётся копия со всеми полями
    (включая пробег), исходный деактивируется.
Деньги переоцениваются под нового клиента (reprice_wo_for_customer): ставки
работ, цены каталожных запчастей по его прайс-матрице, флаг налога.

transfer переписывает labors, поэтому запись WO — CAS по ревизии
(+ status != paid): сохранение механика, успевшее между чтением и записью,
не откатывается. Каждая попытка (≤ MAX_CAS_ATTEMPTS) заново выполняет всё:
чтение, guard'ы, поиск юнита, переоценку, новый _id копии юнита. Копия юнита
вставляется ДО записи WO и удаляется, если CAS не прошёл (на standalone-
Mongo без транзакций WO никогда не указывает на несуществующий юнит).
Попытки кончились — 409 concurrent_update.

    transfer_work_order(shop_db, shop, wo_id, data, *, user_id) -> (payload, status)
"""
from __future__ import annotations

from bson import ObjectId
from flask import current_app

from app.blueprints.work_orders.services import edit_views as ev
from app.blueprints.work_orders.services.common import oid, utcnow
from app.blueprints.work_orders.services.mechanic_editor import reprice_wo_for_customer
from app.extensions import get_mongo_client
from app.utils import audit_log
from app.utils.duplicates import find_duplicate_unit
from app.utils.entity_search import build_unit_search_terms
from app.utils.mongo_tx import run_atomically

_UNIT_COPY_SKIP = ("_id", "customer_id", "search_terms", "created_at", "created_by", "updated_at", "updated_by")


def _attempt(shop_db, shop, wo_id, target_id, user_id):
    """Одна попытка: (payload, status, info) — готовый ответ; None — CAS не прошёл."""
    shop_id = shop["_id"]
    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop_id, "is_active": True})
    if not wo:
        return {"ok": False, "error": "work_order_not_found"}, 404, None
    if (wo.get("status") or "open") == "paid":
        return {"ok": False, "error": "paid_cannot_edit"}, 400, None
    if not target_id:
        return {"ok": False, "error": "customer_required"}, 400, None
    target = shop_db.customers.find_one(
        {"_id": target_id, "shop_id": shop_id, "is_active": True},
        {"_id": 1, "company_name": 1, "first_name": 1, "last_name": 1, "contacts": 1},
    )
    if not target:
        return {"ok": False, "error": "customer_not_found"}, 400, None
    if wo.get("customer_id") == target_id:
        return {"ok": False, "error": "same_customer"}, 400, None

    unit = None
    if wo.get("unit_id"):
        unit = shop_db.units.find_one({"_id": wo["unit_id"], "shop_id": shop_id})

    now = utcnow()
    wo_set = {
        "customer_id": target_id,
        "updated_at": now,
        "updated_by": user_id,
        **reprice_wo_for_customer(shop_db, shop, wo, target_id),
    }
    cas_filter = {
        "_id": wo_id, "shop_id": shop_id, "is_active": True, "status": {"$ne": "paid"},
        **ev.rev_filter(ev.labors_rev(wo)),
    }
    client = get_mongo_client()
    info = {"wo": wo, "target": target, "unit_action": "none"}

    if unit is None:
        # WO без юнита (или юнит потерян) — просто меняем клиента.
        matched = run_atomically(
            client,
            lambda session: shop_db.work_orders.update_one(
                cas_filter, ev.bump_rev({"$set": wo_set}), session=session
            ).matched_count,
        )
        if not matched:
            return None
        return {"ok": True, "unit_action": "none"}, 200, info

    vin = str(unit.get("vin") or "").strip()
    existing = find_duplicate_unit(shop_db, shop_id, target_id, vin) if vin else None

    if existing is not None:
        # У целевого клиента этот юнит уже есть — перевешиваем WO на него.
        wo_set["unit_id"] = existing["_id"]
        reactivate = existing.get("is_active") is False

        def _tx(session):
            res = shop_db.work_orders.update_one(cas_filter, ev.bump_rev({"$set": wo_set}), session=session)
            if not res.matched_count:
                return False
            if reactivate:
                unit_set = {"is_active": True, "updated_at": now, "updated_by": user_id}
                # Реактивированному дублю подтягиваем пробег переносимого
                # юнита — он свежее (WO только что откатали).
                if unit.get("mileage"):
                    unit_set["mileage"] = unit.get("mileage")
                shop_db.units.update_one({"_id": existing["_id"]}, {"$set": unit_set}, session=session)
            return True

        if not run_atomically(client, _tx):
            return None
        info["unit_action"] = "reactivated_existing" if reactivate else "linked_existing"
    else:
        # Юнит «переезжает»: копия у целевого клиента (новый _id на каждой
        # попытке), исходник деактивируется после успешной записи WO.
        new_unit = {k: v for k, v in unit.items() if k not in _UNIT_COPY_SKIP}
        new_unit.update({
            "_id": ObjectId(),
            "customer_id": target_id,
            "is_active": True,
            "created_at": now,
            "created_by": user_id,
            "updated_at": now,
            "updated_by": user_id,
        })
        new_unit["search_terms"] = build_unit_search_terms(new_unit)
        wo_set["unit_id"] = new_unit["_id"]

        def _tx(session):
            shop_db.units.insert_one(new_unit, session=session)
            res = shop_db.work_orders.update_one(cas_filter, ev.bump_rev({"$set": wo_set}), session=session)
            if not res.matched_count:
                # WO изменили между чтением и записью — копия юнита не нужна.
                shop_db.units.delete_one({"_id": new_unit["_id"]}, session=session)
                return False
            shop_db.units.update_one(
                {"_id": unit["_id"]},
                {"$set": {"is_active": False, "updated_at": now, "updated_by": user_id}},
                session=session,
            )
            return True

        if not run_atomically(client, _tx):
            return None
        info["unit_action"] = "moved"

    return {
        "ok": True,
        "unit_action": info["unit_action"],
        "customer_id": str(target_id),
        "unit_id": str(wo_set.get("unit_id") or ""),
    }, 200, info


def transfer_work_order(shop_db, shop, wo_id, data, *, user_id):
    """Перенос WO на клиента data["customer_id"]; (payload, http_status)."""
    from app.blueprints.calendar.services.work_orders import (
        refresh_linked_appointments_identity,
        sync_linked_appointments_status,
    )

    target_id = oid((data or {}).get("customer_id"))
    for _attempt_no in range(ev.MAX_CAS_ATTEMPTS):
        outcome = _attempt(shop_db, shop, wo_id, target_id, user_id)
        if outcome is None:
            continue
        payload, status, info = outcome
        if info is None:
            return payload, status
        wo, target, unit_action = info["wo"], info["target"], info["unit_action"]
        # Привязанные записи календаря переезжают к новому клиенту/юниту вместе с WO.
        try:
            refresh_linked_appointments_identity(shop_db, shop, wo_id)
            sync_linked_appointments_status(shop_db, shop, wo_id)
        except Exception:
            current_app.logger.exception("WO %s transferred, but the calendar sync failed", wo_id)
        details = {"to_customer_id": str(target_id), "unit_action": unit_action}
        summary = f"{audit_log.wo_label(wo)} transferred to {audit_log.customer_label(target)}"
        if "unit_id" in payload:
            details["unit_id"] = payload["unit_id"]
            summary += f" (unit: {unit_action})"
        audit_log.log_event("transfer", "work_order", wo_id, label=audit_log.wo_label(wo),
                            summary=summary, details=details)
        return payload, status
    current_app.logger.warning("WO %s: transfer gave up after %d CAS attempts", wo_id, ev.MAX_CAS_ATTEMPTS)
    return dict(ev.CONCURRENT_UPDATE_RESPONSE), 409


__all__ = ["transfer_work_order"]
