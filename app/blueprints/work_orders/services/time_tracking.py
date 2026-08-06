"""
Учёт фактического времени механиков на labor-строках WO (start/stop job).

Логи лежат в отдельной shop-коллекции wo_time_logs и привязаны к строке
через стабильный labor_id — сохранения WO переписывают массив labors целиком,
поэтому встраивать время в документ WO нельзя. Открытый таймер = запись с
stopped_at: null; у пользователя может быть только один открытый таймер,
start на другой строке атомарно закрывает предыдущий (stop_source:
"auto_switch"). Все таймстемпы — UTC, длительности считает сервер.
"""
from __future__ import annotations

from datetime import timezone

from app.blueprints.work_orders.services.common import oid, round2, utcnow


def _fmt_iso(dt):
    if not dt:
        return ""
    try:
        # PyMongo возвращает naive UTC — без явного "Z" мобильный клиент
        # парсит строку как локальное время и идущий таймер считает нулём.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def _elapsed_seconds(started_at, now) -> int:
    try:
        if started_at.tzinfo is None and now.tzinfo is not None:
            started_at = started_at.replace(tzinfo=now.tzinfo)
        return max(0, int((now - started_at).total_seconds()))
    except Exception:
        return 0


def _log_payload(log: dict) -> dict:
    return {
        "id": str(log.get("_id") or ""),
        "work_order_id": str(log.get("work_order_id") or ""),
        "wo_number": log.get("wo_number"),
        "labor_id": str(log.get("labor_id") or ""),
        "user_id": str(log.get("user_id") or ""),
        "user_name": log.get("user_name") or "",
        "started_at": _fmt_iso(log.get("started_at")),
        "stopped_at": _fmt_iso(log.get("stopped_at")),
        "seconds": log.get("seconds"),
    }


def _stop_open_log(shop_db, shop_id, user_id, stop_source: str):
    """Атомарно закрыть открытый таймер пользователя (если есть)."""
    now = utcnow()
    log = shop_db.wo_time_logs.find_one_and_update(
        {"shop_id": shop_id, "user_id": user_id, "stopped_at": None},
        {"$set": {"stopped_at": now, "stop_source": stop_source, "updated_at": now}},
        return_document=True,
    )
    if not log:
        return None
    seconds = _elapsed_seconds(log.get("started_at"), now)
    shop_db.wo_time_logs.update_one({"_id": log["_id"]}, {"$set": {"seconds": seconds}})
    log["stopped_at"] = now
    log["seconds"] = seconds
    return log


def start_timer(shop_db, shop, user_id, user_name, work_order_id, labor_id):
    """
    Возвращает (log, stopped_previous, error). error — строка-код или None.
    Стартует таймер на labor-строке, форсит WO в in_progress.
    """
    wo_id = oid(work_order_id)
    labor_id = str(labor_id or "").strip()
    if not wo_id or not labor_id:
        return None, None, "invalid_arguments"

    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        {"labors": 1, "status": 1, "wo_number": 1},
    )
    if not wo:
        return None, None, "work_order_not_found"
    if (wo.get("status") or "").strip().lower() == "paid":
        return None, None, "paid_cannot_track"

    labor_ids = {
        str(b.get("labor_id") or "").strip()
        for b in (wo.get("labors") or [])
        if isinstance(b, dict)
    }
    if labor_id not in labor_ids:
        return None, None, "labor_not_found"

    stopped_prev = _stop_open_log(shop_db, shop["_id"], user_id, "auto_switch")

    now = utcnow()
    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "work_order_id": wo_id,
        "wo_number": wo.get("wo_number"),
        "labor_id": labor_id,
        "user_id": user_id,
        "user_name": user_name or "",
        "started_at": now,
        "stopped_at": None,
        "seconds": None,
        "stop_source": None,
        "created_at": now,
        "updated_at": now,
    }
    res = shop_db.wo_time_logs.insert_one(doc)
    doc["_id"] = res.inserted_id

    res_status = shop_db.work_orders.update_one(
        {"_id": wo_id, "shop_id": shop["_id"], "status": {"$ne": "in_progress"}},
        {"$set": {"status": "in_progress", "updated_at": now}},
    )
    # Механик снова взялся за работу — флаг «закончил» больше не актуален.
    res_done = shop_db.work_orders.update_one(
        {"_id": wo_id, "shop_id": shop["_id"], "mechanic_done": True},
        {"$set": {"mechanic_done": False, "mechanic_done_at": None, "mechanic_done_by": None, "updated_at": now}},
    )

    # WO-уровневый переход «взят в работу» (не каждый старт таймера, а только
    # когда сам WO поменял состояние) — пуш офисным пользователям.
    if res_status.modified_count or res_done.modified_count:
        from app.blueprints.work_orders.services.push_events import notify_wo_event

        notify_wo_event(shop, wo_id, wo.get("wo_number"), user_id, "taken")

    return doc, stopped_prev, None


def stop_timer(shop_db, shop, user_id):
    """Возвращает (log, error)."""
    log = _stop_open_log(shop_db, shop["_id"], user_id, "user")
    if not log:
        return None, "no_running_timer"
    return log, None


def get_running_timer(shop_db, shop, user_id):
    return shop_db.wo_time_logs.find_one(
        {"shop_id": shop["_id"], "user_id": user_id, "stopped_at": None}
    )


def running_timer_payload(shop_db, shop, user_id):
    log = get_running_timer(shop_db, shop, user_id)
    if not log:
        return None
    payload = _log_payload(log)
    # Описание labor-строки — для sticky-бара «что сейчас в работе».
    wo = shop_db.work_orders.find_one(
        {"_id": log.get("work_order_id"), "shop_id": shop["_id"]},
        {"labors": 1, "wo_number": 1},
    )
    if wo:
        payload["wo_number"] = wo.get("wo_number")
        for b in wo.get("labors") or []:
            if isinstance(b, dict) and str(b.get("labor_id") or "") == payload["labor_id"]:
                payload["labor_description"] = str((b.get("labor") or {}).get("description") or "")
                break
    return payload


def summarize_wo_time(shop_db, shop_id, work_order_id, user_id=None):
    """
    Сводка времени по WO: {labor_id: bucket}. Несколько механиков могут
    работать над одной строкой одновременно — каждый идущий таймер попадает
    в running_users. Bucket:
      total_seconds      — завершённые + идущие (на момент ответа)
      completed_seconds  — только завершённые сессии (база для тикеров)
      my_seconds, my_running, my_started_at — для текущего пользователя
      running_users      — [{user_id, user_name, started_at, mine}]
      users              — {uid: {user_name, seconds}} накопленное по людям
    """
    wo_id = oid(work_order_id)
    if not wo_id:
        return {}

    now = utcnow()
    summary: dict[str, dict] = {}
    cursor = shop_db.wo_time_logs.find(
        {"shop_id": shop_id, "work_order_id": wo_id}
    ).sort("started_at", 1)

    for log in cursor:
        labor_id = str(log.get("labor_id") or "")
        bucket = summary.setdefault(
            labor_id,
            {
                "total_seconds": 0,
                "completed_seconds": 0,
                "my_seconds": 0,
                "my_running": False,
                "my_started_at": "",
                "running_users": [],
                "users": {},
            },
        )
        mine = user_id is not None and log.get("user_id") == user_id
        if log.get("stopped_at") is None:
            seconds = _elapsed_seconds(log.get("started_at"), now)
            bucket["running_users"].append({
                "user_id": str(log.get("user_id") or ""),
                "user_name": log.get("user_name") or "",
                "started_at": _fmt_iso(log.get("started_at")),
                "mine": mine,
            })
            if mine:
                bucket["my_running"] = True
                bucket["my_started_at"] = _fmt_iso(log.get("started_at"))
        else:
            seconds = int(log.get("seconds") or 0)
            bucket["completed_seconds"] += seconds

        bucket["total_seconds"] += seconds
        if mine:
            bucket["my_seconds"] += seconds

        uid = str(log.get("user_id") or "")
        user_bucket = bucket["users"].setdefault(
            uid, {"user_name": log.get("user_name") or "", "seconds": 0}
        )
        user_bucket["seconds"] += seconds

    return summary


def time_based_assignments(shop_db, shop_id, work_order_id):
    """
    Назначение механиков из фактического времени: {labor_id: [{user_id, name,
    role, percent}]} по завершённым сессиям таймеров. Один механик — 100%,
    несколько — пропорционально времени на строке. Строки без завершённого
    времени в результат не попадают (для них остаётся ручное назначение).
    """
    wo_id = oid(work_order_id)
    if not wo_id:
        return {}

    per_labor: dict[str, dict[str, dict]] = {}
    cursor = shop_db.wo_time_logs.find(
        {"shop_id": shop_id, "work_order_id": wo_id, "stopped_at": {"$ne": None}},
        {"labor_id": 1, "user_id": 1, "user_name": 1, "seconds": 1},
    )
    for log in cursor:
        labor_id = str(log.get("labor_id") or "")
        user_id = log.get("user_id")
        seconds = int(log.get("seconds") or 0)
        if not labor_id or not user_id or seconds <= 0:
            continue
        users = per_labor.setdefault(labor_id, {})
        u = users.setdefault(
            str(user_id), {"user_id": user_id, "name": "", "seconds": 0}
        )
        u["seconds"] += seconds
        if log.get("user_name"):
            u["name"] = log["user_name"]

    out: dict[str, list[dict]] = {}
    for labor_id, users in per_labor.items():
        total = sum(u["seconds"] for u in users.values())
        if total <= 0:
            continue
        entries = sorted(users.values(), key=lambda u: -u["seconds"])
        assigned = [
            {
                "user_id": u["user_id"],
                "name": u["name"],
                "role": "",
                "percent": round2(u["seconds"] * 100.0 / total),
            }
            for u in entries
        ]
        # Из-за округления сумма может отличаться от 100 — правим большую долю.
        drift = round2(100.0 - sum(a["percent"] for a in assigned))
        if drift:
            assigned[0]["percent"] = round2(assigned[0]["percent"] + drift)
        out[labor_id] = assigned
    return out


def sync_labor_assignments_from_time(shop_db, shop, work_order_id, mechanics_by_id=None):
    """
    Переписать assigned_mechanics строк WO по фактическому времени таймеров.
    mechanics_by_id ({user_id_str: {name, role}}) уточняет имя/роль из
    актуального списка механиков. Возвращает True, если что-то обновлено.
    """
    assignments = time_based_assignments(shop_db, shop["_id"], work_order_id)
    if not assignments:
        return False

    wo_id = oid(work_order_id)
    now = utcnow()
    changed = False
    for labor_id, assigned in assignments.items():
        for a in assigned:
            m = (mechanics_by_id or {}).get(str(a["user_id"]))
            if m:
                a["name"] = m.get("name") or a["name"]
                a["role"] = m.get("role") or ""
        res = shop_db.work_orders.update_one(
            {"_id": wo_id, "shop_id": shop["_id"], "labors.labor_id": labor_id},
            {"$set": {"labors.$[b].labor.assigned_mechanics": assigned, "updated_at": now}},
            array_filters=[{"b.labor_id": labor_id}],
        )
        changed = changed or res.modified_count > 0
    return changed


def active_timers(shop_db, shop, user_id=None):
    """
    Все идущие таймеры магазина — для блока «сейчас в работе» в списке WO.
    Возвращает [{work_order_id, wo_number, customer, labor_id,
    labor_description, user_id, user_name, started_at, mine}].
    """
    from app.blueprints.work_orders.services.lookups import customer_label

    logs = list(
        shop_db.wo_time_logs.find(
            {"shop_id": shop["_id"], "stopped_at": None}
        ).sort("started_at", 1)
    )
    if not logs:
        return []

    wo_ids = list({log.get("work_order_id") for log in logs if log.get("work_order_id")})
    wos = {
        wo["_id"]: wo
        for wo in shop_db.work_orders.find(
            {"_id": {"$in": wo_ids}, "shop_id": shop["_id"]},
            {"wo_number": 1, "customer_id": 1, "labors.labor_id": 1, "labors.labor.description": 1},
        )
    }
    customer_ids = list({wo.get("customer_id") for wo in wos.values() if wo.get("customer_id")})
    customers = {}
    if customer_ids:
        for c in shop_db.customers.find({"_id": {"$in": customer_ids}}):
            customers[c["_id"]] = customer_label(c)

    items = []
    for log in logs:
        wo = wos.get(log.get("work_order_id"))
        if not wo:
            continue
        labor_desc = ""
        for block in wo.get("labors") or []:
            if isinstance(block, dict) and str(block.get("labor_id") or "") == str(log.get("labor_id") or ""):
                labor_desc = str((block.get("labor") or {}).get("description") or "")
                break
        items.append({
            "work_order_id": str(wo["_id"]),
            "wo_number": wo.get("wo_number"),
            "customer": customers.get(wo.get("customer_id")) or "-",
            "labor_id": str(log.get("labor_id") or ""),
            "labor_description": labor_desc,
            "user_id": str(log.get("user_id") or ""),
            "user_name": log.get("user_name") or "",
            "started_at": _fmt_iso(log.get("started_at")),
            "mine": user_id is not None and log.get("user_id") == user_id,
        })
    return items


def summarize_mechanic_hours(shop_db, shop_id, date_from=None, date_to_exclusive=None):
    """
    Для отчёта mechanic_hours: {user_id_str: {user_name, seconds}} по
    завершённым логам с started_at в заданном окне.
    """
    query: dict = {"shop_id": shop_id, "stopped_at": {"$ne": None}}
    started_range = {}
    if date_from:
        started_range["$gte"] = date_from
    if date_to_exclusive:
        started_range["$lt"] = date_to_exclusive
    if started_range:
        query["started_at"] = started_range

    out: dict[str, dict] = {}
    for log in shop_db.wo_time_logs.find(query, {"user_id": 1, "user_name": 1, "seconds": 1}):
        uid = str(log.get("user_id") or "")
        bucket = out.setdefault(uid, {"user_name": log.get("user_name") or "", "seconds": 0})
        bucket["seconds"] += int(log.get("seconds") or 0)
    return out
