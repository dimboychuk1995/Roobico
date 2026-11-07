"""
Записи календаря ↔ work orders.

Запись может быть привязана к WO (work_order_id, wo_number). За WO следуют:
  * статус записи — по факту работы (sync_linked_appointments_status): WO
    взят в работу → in_progress, механик отметил Done / менеджер закрыл /
    оплачен → completed. Назад (open) записи НЕ откатываются, отменённые
    (cancelled) не трогаются;
  * клиент/юнит записи — когда их поменяли на WO (перенос, смена юнита в
    офисе: refresh_linked_appointments_identity).
Календарь сам в work orders никогда не пишет.

Состояние WO для календаря (wo_state) считается только по документу WO,
без обращений к базе (derive_wo_state):
  open          — создан, работа не начиналась
  in_progress   — в работе
  mechanic_done — механики отметили Done, ждёт проверки менеджера
  completed     — закрыт менеджером (manager_confirmed) или отмечен Done
  paid          — оплачен; механик-режим денег не видит → completed
  estimate      — смета: не синкается

Модуль импортируют work_orders/routes.py, services/time_tracking.py,
services/mechanic_view.py и mobile_api/routes.py — имена и сигнатуры
публичных функций здесь — контракт.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import current_app

from app.blueprints.calendar.services.events import (
    CUSTOMER_PROJECTION,
    UNIT_PROJECTION,
    appointment_note,
    customer_label,
    event_mechanics,
    identity_from_snapshot,
    naive_iso,
    unit_label,
)
from app.blueprints.calendar.services.statuses import status_keys
from app.utils.tenant import oid

ESTIMATE_STATUSES = ("estimate", "estimated", "quote", "quoted")

WO_STATES = [
    {"key": "open", "label": "Not started", "color": "#f0a30a"},
    {"key": "in_progress", "label": "In progress", "color": "#1a73e8"},
    {"key": "mechanic_done", "label": "Mechanic done", "color": "#7b4de0"},
    {"key": "completed", "label": "Completed", "color": "#0f9d8a"},
    {"key": "paid", "label": "Paid", "color": "#0d904f"},
]

# Статус записи по состоянию WO. open / estimate / неизвестное — записи не
# трогаем (никаких откатов в scheduled).
WO_STATE_TO_APPOINTMENT_STATUS = {
    "in_progress": "in_progress",
    "mechanic_done": "completed",
    "completed": "completed",
    "paid": "completed",
}

WO_STATE_FIELDS = {"status": 1, "mechanic_done": 1, "manager_confirmed": 1}
WO_ENRICH_PROJECTION = {"wo_number": 1, "labors": 1, **WO_STATE_FIELDS}
WO_SNAPSHOT_PROJECTION = {"customer_id": 1, "unit_id": 1, **WO_ENRICH_PROJECTION}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── состояние WO ─────────────────────────────────────────────

def derive_wo_state(wo: dict, mechanic_mode: bool = False) -> str:
    """Состояние WO для календаря по документу (см. докстринг модуля)."""
    status = str(wo.get("status") or "open").strip().lower()
    if status == "paid":
        return "completed" if mechanic_mode else "paid"
    if status == "in_progress":
        return "mechanic_done" if wo.get("mechanic_done") else "in_progress"
    if status in ESTIMATE_STATUSES:
        return "estimate"
    if wo.get("manager_confirmed") or wo.get("mechanic_done"):
        return "completed"
    return "open"


def wo_assigned_mechanics(wo: dict) -> list[dict]:
    """Механики, назначенные на строки WO: [{id: str, name}] без дублей."""
    out: list[dict] = []
    seen: set[str] = set()
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        for a in labor.get("assigned_mechanics") or block.get("assigned_mechanics") or []:
            if not isinstance(a, dict):
                continue
            uid = str(a.get("user_id") or a.get("id") or "").strip()
            name = str(a.get("name") or "").strip()
            key = uid or name
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"id": uid, "name": name or "Mechanic"})
    return out


def wo_labor_summary(wo: dict, limit: int = 3) -> list[str]:
    """Первые описания работ WO (не больше limit)."""
    descriptions: list[str] = []
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        text = str(labor.get("description") or block.get("description") or "").strip()
        if text:
            descriptions.append(text)
        if len(descriptions) >= limit:
            break
    return descriptions


def wo_labor_hours(wo: dict) -> float:
    total = 0.0
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        try:
            total += float(labor.get("hours") or block.get("hours") or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


# ── снимок WO для записи ─────────────────────────────────────

def work_order_snapshot_for_event(shop_db, shop: dict, work_order_id) -> dict | None:
    """
    Что запись берёт с WO при привязке: id/лейблы клиента и юнита, бригада
    по строкам, заголовок (первые 3 работы), часы. None — WO нет, удалён
    или чужой. Ключ "wo" — поля состояния для derive_wo_state.
    """
    wo_id = oid(work_order_id)
    if shop_db is None or not wo_id:
        return None
    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, WO_SNAPSHOT_PROJECTION
    )
    if not wo:
        return None
    customer = unit = None
    if wo.get("customer_id"):
        customer = shop_db.customers.find_one({"_id": wo["customer_id"], "shop_id": shop["_id"]}, CUSTOMER_PROJECTION)
    if wo.get("unit_id"):
        unit = shop_db.units.find_one({"_id": wo["unit_id"], "shop_id": shop["_id"]}, UNIT_PROJECTION)
    return {
        "work_order_id": wo_id,
        "wo_number": wo.get("wo_number"),
        "customer_id": wo.get("customer_id"),
        "customer_label": customer_label(customer) if customer else "",
        "unit_id": wo.get("unit_id"),
        "unit_label": unit_label(unit) if unit else "",
        "mechanics": wo_assigned_mechanics(wo),
        "title": ", ".join(wo_labor_summary(wo, limit=3)),
        "hours": wo_labor_hours(wo),
        "wo": {key: wo.get(key) for key in WO_STATE_FIELDS},
    }


def snapshot_payload(snapshot: dict, mechanic_mode: bool = False) -> dict:
    """Снимок → JSON для GET /calendar/api/work_orders/<id>/snapshot."""
    return {
        "work_order_id": str(snapshot["work_order_id"]),
        "wo_number": snapshot.get("wo_number"),
        "customer_id": str(snapshot["customer_id"]) if snapshot.get("customer_id") else "",
        "customer_label": snapshot.get("customer_label") or "",
        "unit_id": str(snapshot["unit_id"]) if snapshot.get("unit_id") else "",
        "unit_label": snapshot.get("unit_label") or "",
        "mechanics": [{"id": m["id"], "name": m["name"]} for m in snapshot.get("mechanics") or []],
        "title": snapshot.get("title") or "",
        "hours": float(snapshot.get("hours") or 0),
        "wo_state": derive_wo_state(snapshot.get("wo") or {}, mechanic_mode),
    }


# Клиент/юнит записи по снимку WO — общее правило с POST/PUT привязанной
# записи живёт в events.identity_from_snapshot (без фильтра is_active).


# ── отдача: живые данные WO у привязанных записей ────────────

def enrich_linked_appointments(shop_db, shop: dict, items: list[dict], rows: list[dict], mechanic_mode: bool = False) -> None:
    """
    Дополнить сериализованные записи (items, по индексу = rows) живыми
    данными WO одним $in: wo_number, wo_state, labor_summary (≤3). WO
    удалён/неактивен → work_order_id "", wo_number null, wo_state "".
    """
    wo_ids = {r["work_order_id"] for r in rows if isinstance(r.get("work_order_id"), ObjectId)}
    if not wo_ids:
        return
    wo_map = {
        w["_id"]: w
        for w in shop_db.work_orders.find(
            {"_id": {"$in": list(wo_ids)}, "shop_id": shop["_id"], "is_active": True}, WO_ENRICH_PROJECTION
        )
    }
    for item, row in zip(items, rows):
        wo_id = row.get("work_order_id")
        if not wo_id:
            continue
        wo = wo_map.get(wo_id)
        if not wo:
            item.update({"work_order_id": "", "wo_number": None, "wo_state": "", "labor_summary": []})
            continue
        item.update({
            "work_order_id": str(wo_id),
            "wo_number": wo.get("wo_number"),
            "wo_state": derive_wo_state(wo, mechanic_mode),
            "labor_summary": wo_labor_summary(wo, limit=3),
        })


# ── хуки из work orders ──────────────────────────────────────

def sync_linked_appointments_status(shop_db, shop: dict, work_order_id) -> int:
    """
    Статус всех не отменённых записей WO := статус по состоянию WO
    (WO_STATE_TO_APPOINTMENT_STATUS). open / estimate / нет WO → 0, назад не
    откатываем; целевого ключа нет среди статусов магазина → 0 с warning.
    Зовётся после любой записи WO; никогда не бросает (лог).
    """
    try:
        wo_id = oid(work_order_id)
        if shop_db is None or not wo_id:
            return 0
        wo = shop_db.work_orders.find_one(
            {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, WO_STATE_FIELDS
        )
        if not wo:
            return 0
        target = WO_STATE_TO_APPOINTMENT_STATUS.get(derive_wo_state(wo))
        if not target:
            return 0
        if target not in status_keys(shop_db):
            current_app.logger.warning(
                "Calendar: appointment status '%s' is missing from the shop's statuses — "
                "appointments linked to WO %s are not synced", target, wo_id,
            )
            return 0
        res = shop_db.calendar_events.update_many(
            {"shop_id": shop["_id"], "work_order_id": wo_id, "status": {"$nin": ["cancelled", target]}},
            {"$set": {"status": target, "updated_at": _utcnow()}},
        )
        return res.modified_count
    except Exception:
        current_app.logger.exception("Calendar: failed to sync appointment status for WO %s", work_order_id)
        return 0


def refresh_linked_appointments_identity(shop_db, shop: dict, work_order_id) -> int:
    """Клиент/юнит (id + живые лейблы) не отменённых записей WO := клиент/юнит
    WO (перенос WO, смена юнита в офисе). Никогда не бросает."""
    try:
        wo_id = oid(work_order_id)
        if shop_db is None or not wo_id:
            return 0
        snapshot = work_order_snapshot_for_event(shop_db, shop, wo_id)
        if not snapshot:
            return 0
        fields = identity_from_snapshot(snapshot)
        res = shop_db.calendar_events.update_many(
            {
                "shop_id": shop["_id"], "work_order_id": wo_id, "status": {"$ne": "cancelled"},
                "$or": [{key: {"$ne": value}} for key, value in fields.items()],
            },
            {"$set": {**fields, "updated_at": _utcnow()}},
        )
        return res.modified_count
    except Exception:
        current_app.logger.exception("Calendar: failed to refresh customer/unit of appointments linked to WO %s", work_order_id)
        return 0


def link_event_to_work_order(shop_db, shop: dict, event_id, work_order_id, wo_number) -> bool:
    """
    Привязать запись к WO (кнопка «Create Work Order» в календаре, «Start
    work» механика): ссылка + клиент/юнит с WO, затем статус по факту работы.
    Запись, уже привязанная к ДРУГОМУ живому WO, не перепривязывается
    (warning, False). True — запись найдена и обновлена.
    """
    eid, wo_id = oid(event_id), oid(work_order_id)
    if shop_db is None or not eid or not wo_id:
        return False
    event = shop_db.calendar_events.find_one({"_id": eid, "shop_id": shop["_id"]}, {"work_order_id": 1})
    if not event:
        return False
    current = event.get("work_order_id")
    if current and current != wo_id:
        other = shop_db.work_orders.find_one(
            {"_id": current, "shop_id": shop["_id"], "is_active": True}, {"wo_number": 1}
        )
        if other:
            current_app.logger.warning(
                "Calendar: appointment %s is already linked to active WO #%s — not relinking to WO #%s",
                eid, other.get("wo_number"), wo_number,
            )
            return False
    updates = {"work_order_id": wo_id, "wo_number": wo_number, "updated_at": _utcnow()}
    snapshot = work_order_snapshot_for_event(shop_db, shop, wo_id)
    if snapshot:
        updates["wo_number"] = snapshot.get("wo_number") or wo_number
        updates.update(identity_from_snapshot(snapshot))
    res = shop_db.calendar_events.update_one({"_id": eid, "shop_id": shop["_id"]}, {"$set": updates})
    sync_linked_appointments_status(shop_db, shop, wo_id)
    return res.modified_count > 0


def unlink_work_order_events(shop_db, shop: dict, work_order_id) -> int:
    """WO удалён: записи остаются обычными — ссылка и номер снимаются,
    статус не трогаем. Возвращает число изменённых записей."""
    wo_id = oid(work_order_id)
    if shop_db is None or not wo_id:
        return 0
    res = shop_db.calendar_events.update_many(
        {"shop_id": shop["_id"], "work_order_id": wo_id},
        {"$set": {"work_order_id": None, "wo_number": None, "updated_at": _utcnow()}},
    )
    return res.modified_count


def linked_appointments_for_wo(shop_db, shop: dict, work_order_id) -> list[dict]:
    """
    Не отменённые записи WO для страницы WO, механика и мобильного
    приложения: время — настенные часы магазина без зоны, note — заметка
    (title, если это не имя клиента). Денег нет — годится механик-режиму.
    """
    try:
        wo_id = oid(work_order_id)
        if shop_db is None or not wo_id:
            return []
        rows = shop_db.calendar_events.find(
            {"shop_id": shop["_id"], "work_order_id": wo_id, "status": {"$ne": "cancelled"}}
        ).sort("start_time", 1)
        return [
            {
                "id": str(row["_id"]),
                "start_time": naive_iso(row.get("start_time")),
                "end_time": naive_iso(row.get("end_time")),
                "status": row.get("status") or "scheduled",
                "title": str(row.get("title") or ""),
                "note": appointment_note(row),
                "mechanics": [{"id": str(m["id"]), "name": m["name"]} for m in event_mechanics(row)],
                "presets": [
                    {"id": str(p.get("id") or ""), "name": str(p.get("name") or "")}
                    for p in (row.get("presets") or [])
                    if isinstance(p, dict) and p.get("id")
                ],
            }
            for row in rows
        ]
    except Exception:
        current_app.logger.exception("Calendar: failed to list appointments linked to WO %s", work_order_id)
        return []
