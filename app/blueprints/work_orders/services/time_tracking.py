"""
Учёт фактического времени механиков на labor-строках WO (start/stop job).

Логи лежат в отдельной shop-коллекции wo_time_logs и привязаны к строке
через стабильный labor_id — сохранения WO переписывают массив labors целиком,
поэтому встраивать время в документ WO нельзя. Открытый таймер = запись с
stopped_at: null; у пользователя может быть только один открытый таймер,
start на другой строке атомарно закрывает предыдущий (stop_source:
"auto_switch"). Все таймстемпы — UTC, длительности считает сервер.

Старт фиксируется в WO одной атомарной записью ПОСЛЕ вставки открытого
лога (строка на месте + $inc labors_rev) — см. start_timer: идущий таймер
входит в merge сохранений WO (такую работу не удаляют, Done не ставят).
"""
from __future__ import annotations

import logging
from datetime import timezone

from flask import current_app, has_app_context

from app.blueprints.work_orders.services.common import oid, round2, utcnow
from app.utils.user_names import display_name_for, load_user_names, relabel_people, tenant_values_for_shop

_log = logging.getLogger(__name__)

# Статусы сметы (с легаси-синонимами — как в платежах и PDF). Старт таймера
# смету никогда не конвертирует: склад по ней не списан, в работу её
# переводит только офисный Convert (со списанием).
ESTIMATE_STATUSES = ("estimate", "estimated", "quote", "quoted")

# Человекочитаемые тексты отказов таймера: старые сборки приложения
# показывают в тосте message ответа, а без него — сырой код ошибки.
TIMER_ERROR_MESSAGES = {
    "invalid_arguments": "Save the job first, then start its timer.",
    "work_order_not_found": "This work order was deleted or is no longer available.",
    "labor_not_found": "This job is no longer on the work order. Refresh the work order and try again.",
    "paid_cannot_track": "This work order is paid. Time can't be tracked on it.",
    "estimate_cannot_track": (
        "This work order is an estimate. Time can be tracked after the office converts it to a work order."
    ),
    "wo_confirmed": "This work order was confirmed by a manager and is locked.",
    "concurrent_update": "This work order is being updated by someone else right now. Please try again.",
    "no_running_timer": "No timer is running.",
}
# Тексты для механик-режима поверх TIMER_ERROR_MESSAGES: статус paid механику
# не называется нигде (rev-опрос отдаёт locked, история юнита и календарь —
# Completed), поэтому оплаченный WO для него просто «закрыт».
MECHANIC_TIMER_ERROR_MESSAGES = {
    "paid_cannot_track": "This work order is locked. Time can't be tracked on it.",
}
TIMER_ERROR_FALLBACK = "The timer could not be updated. Please try again."


def timer_error_payload(error: str, *, mechanic_mode: bool) -> dict:
    """
    Тело отказа таймера: машинный код + текст для пользователя. Текст зависит
    от режима вызывающего (mechanic_mode — is_mechanic_mode()): параметр
    обязательный, чтобы новый вызов не показал механику «paid» по умолчанию.
    Код один для всех — по нему клиенты узнают замок.
    """
    message = (MECHANIC_TIMER_ERROR_MESSAGES.get(error) if mechanic_mode else None) or TIMER_ERROR_MESSAGES.get(error)
    return {"ok": False, "error": error, "message": message or TIMER_ERROR_FALLBACK}


def _live_names(user_ids, tenant_values=None) -> dict:
    """
    Живые имена людей по id ({str_id: {"name", ...}}, см. app/utils/user_names).
    user_name в логе — снимок на момент старта таймера и после переименования
    пользователя устаревает, поэтому подписи берутся из master.users, а снимок
    — лишь запас (удалённый аккаунт, чужой/легаси id). tenant_values=None —
    тенант сессии; вне app context (голый вызов без приложения) — пусто,
    остаются снимки.
    """
    if not has_app_context():
        return {}
    return load_user_names(user_ids, tenant_values=tenant_values)


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


def _log_payload(log: dict, names: dict | None = None) -> dict:
    """Сессия таймера для клиентов. user_name — живое имя по user_id (снимок
    лога — запас). names — уже загруженные имена (список сессий грузит их
    одним запросом); None — загрузить для этого лога (кэш на запрос)."""
    if names is None:
        names = _live_names([log.get("user_id")])
    return {
        "id": str(log.get("_id") or ""),
        "work_order_id": str(log.get("work_order_id") or ""),
        "wo_number": log.get("wo_number"),
        "labor_id": str(log.get("labor_id") or ""),
        "user_id": str(log.get("user_id") or ""),
        "user_name": display_name_for(names, log.get("user_id"), log.get("user_name"), default=""),
        "started_at": _fmt_iso(log.get("started_at")),
        "stopped_at": _fmt_iso(log.get("stopped_at")),
        "seconds": log.get("seconds"),
    }


def _stop_open_logs(shop_db, shop_id, user_id, stop_source: str):
    """
    Атомарно закрыть ВСЕ открытые таймеры пользователя. В норме открытый
    таймер один, но гонка двух параллельных start могла оставить несколько —
    незакрытый «фантомный» таймер копил бы часы вечно. Возвращает закрытые
    логи в порядке started_at (последний = актуальная сессия).
    """
    closed = []
    while True:
        now = utcnow()
        log = shop_db.wo_time_logs.find_one_and_update(
            {"shop_id": shop_id, "user_id": user_id, "stopped_at": None},
            {"$set": {"stopped_at": now, "stop_source": stop_source, "updated_at": now}},
            return_document=True,
        )
        if not log:
            break
        seconds = _elapsed_seconds(log.get("started_at"), now)
        shop_db.wo_time_logs.update_one({"_id": log["_id"]}, {"$set": {"seconds": seconds}})
        log["stopped_at"] = now
        log["seconds"] = seconds
        closed.append(log)
    closed.sort(key=lambda l: (l.get("started_at") is None, l.get("started_at")))
    return closed


# Чтение WO для старта таймера: проверки (_start_refusal) и снимок номера в лог.
_START_PROJECTION = {"labors.labor_id": 1, "status": 1, "wo_number": 1, "manager_confirmed": 1}


def _stored_labor_id(wo: dict | None, labor_id: str):
    """labor_id строки ровно в том виде, как он лежит в документе (фильтр
    записи сравнивает значения точно); None — такой строки нет."""
    for block in (wo or {}).get("labors") or []:
        if isinstance(block, dict) and str(block.get("labor_id") or "").strip() == labor_id:
            return block.get("labor_id")
    return None


def _start_refusal(wo: dict | None, labor_id: str, *, mechanic_mode: bool) -> str | None:
    """
    Почему на этой строке нельзя стартовать таймер (код ошибки) или None.
    Порядок — как раньше у роута и start_timer: нет WO → подтверждён
    менеджером (механику) → paid → смета (механику) → нет строки. Строже
    фильтра записи в _commit_start (статус без учёта регистра, синонимы
    сметы) — значит, после неудачной записи перечитанный WO либо даёт код,
    либо записи стоит повторить.
    """
    if not wo:
        return "work_order_not_found"
    status = str(wo.get("status") or "").strip().lower()
    if mechanic_mode and wo.get("manager_confirmed"):
        return "wo_confirmed"
    if status == "paid":
        return "paid_cannot_track"
    if mechanic_mode and status in ESTIMATE_STATUSES:
        return "estimate_cannot_track"
    if _stored_labor_id(wo, labor_id) is None:
        return "labor_not_found"
    return None


def _log_warning(message: str, *args) -> None:
    if has_app_context():
        current_app.logger.warning(message, *args)
    else:
        _log.warning(message, *args)


def _commit_start(shop_db, shop, wo: dict, labor_id: str, user_id, now, *, mechanic_mode: bool):
    """
    Фиксация старта в WO — одна атомарная запись ПОСЛЕ вставки открытого лога:
      - фильтр: строка ещё в WO, и WO можно трекать — те же условия, что у
        сохранения этого пользователя (CAS-фильтры concurrent_save: механику
        paid / смета / подтверждённый закрыты, менеджеру — только paid);
      - $inc labors_rev: сохранение, прочитавшее идущие таймеры или «кто
        работал» до вставки лога, не пройдёт CAS и пересоберёт merge уже с
        этим таймером;
      - той же записью снимается отметка Done этого механика и WO-уровневый
        «все закончили» (как release_mechanic_done_mark) — ревизия двигается
        ровно один раз на старт, а неудачный старт Done не трогает.
    Вариантов записи два (с отметкой Done и без неё), фильтры взаимоисключающие.
    Ни один не прошёл — WO перечитывается и проверяется заново: есть код
    отказа — он и возвращается; нет (между вариантами поменялся Done или сам
    labor_id в документе) — ещё попытка, не больше MAX_CAS_ATTEMPTS.
    Возвращает (before, released, error): before — WO до записи
    ({mechanic_done}), released — сняли ли отметку/флаг Done.
    """
    from pymongo import ReturnDocument

    from app.blueprints.work_orders.services import edit_views as ev
    from app.blueprints.work_orders.services.concurrent_save import (
        MECHANIC_CAS_FILTER,
        NON_MECHANIC_CAS_FILTER,
    )
    from app.blueprints.work_orders.services.mechanic_done import DONE_CLEAR_FIELDS

    guard = MECHANIC_CAS_FILTER if mechanic_mode else NON_MECHANIC_CAS_FILTER
    # Поля WO-уровневого Done; отметки остальных механиков остаются ($pull ниже).
    release_fields = {k: v for k, v in DONE_CLEAR_FIELDS.items() if k != "mechanic_done_marks"}
    my_done = {"$or": [{"mechanic_done": True}, {"mechanic_done_marks.user_id": user_id}]}
    not_done = {"mechanic_done": {"$ne": True}, "mechanic_done_marks.user_id": {"$ne": user_id}}
    projection = {"mechanic_done": 1}

    for _attempt in range(ev.MAX_CAS_ATTEMPTS):
        error = _start_refusal(wo, labor_id, mechanic_mode=mechanic_mode)
        if error:
            return None, False, error
        target = {
            "_id": wo["_id"], "shop_id": shop["_id"], **guard,
            "labors.labor_id": _stored_labor_id(wo, labor_id),
        }
        before = shop_db.work_orders.find_one_and_update(
            {**target, **my_done},
            ev.bump_rev({
                "$set": {**release_fields, "updated_at": now},
                "$pull": {"mechanic_done_marks": {"user_id": user_id}},
            }),
            projection=projection,
            return_document=ReturnDocument.BEFORE,
        )
        if before is not None:
            return before, True, None
        before = shop_db.work_orders.find_one_and_update(
            {**target, **not_done},
            ev.bump_rev({"$set": {"updated_at": now}}),
            projection=projection,
            return_document=ReturnDocument.BEFORE,
        )
        if before is not None:
            return before, False, None
        wo_id = wo["_id"]
        wo = shop_db.work_orders.find_one(
            {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, _START_PROJECTION,
        )
    error = _start_refusal(wo, labor_id, mechanic_mode=mechanic_mode)
    if error:
        return None, False, error
    _log_warning("start_timer: work order %s kept changing, timer start refused", wo_id)
    return None, False, "concurrent_update"


def start_timer(shop_db, shop, user_id, user_name, work_order_id, labor_id, *, mechanic_mode=False):
    """
    Стартует таймер на labor-строке. Возвращает (log, stopped_previous_logs,
    error): error — строка-код (тексты — timer_error_payload) или None;
    stopped_previous_logs — сессии, закрытые auto_switch (обычно 0 или 1).
    Они закрыты и тогда, когда старт сорвался уже после переключения (строку
    удалили / WO оплатили в ту же секунду) — вызывающий пересчитывает их WO
    в любом случае.

    mechanic_mode — пользователь без цен: подтверждённый менеджером WO и
    смета для него закрыты (wo_confirmed / estimate_cannot_track). Менеджер
    трекает время и на смете (диагностика), но конвертировать её старт не
    может ни у кого: в in_progress переводятся только не-сметы и не paid.

    Порядок: проверки → закрыть прежние таймеры → вставить открытый лог →
    _commit_start (строка на месте + $inc labors_rev, атомарно) → статус.
    Фиксация идёт после вставки, поэтому гонка с сохранением WO безопасна в
    обе стороны: сохранение, прочитавшее таймеры до вставки, повторит merge и
    работу с идущим таймером оставит; сохранение, удалившее строку раньше,
    срывает старт (лог удаляется, labor_not_found).
    """
    wo_id = oid(work_order_id)
    labor_id = str(labor_id or "").strip()
    if not wo_id or not labor_id:
        return None, [], "invalid_arguments"

    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, _START_PROJECTION,
    )
    error = _start_refusal(wo, labor_id, mechanic_mode=mechanic_mode)
    if error:
        return None, [], error

    stopped_prev_logs = _stop_open_logs(shop_db, shop["_id"], user_id, "auto_switch")

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

    try:
        before, released, error = _commit_start(
            shop_db, shop, wo, labor_id, user_id, now, mechanic_mode=mechanic_mode,
        )
    except Exception:
        # Старт не зафиксирован — открытого лога остаться не должно.
        shop_db.wo_time_logs.delete_one({"_id": doc["_id"]})
        raise
    if error:
        # Строку удалили / WO стал paid, сметой или подтверждённым после
        # проверки выше: таймера не было.
        shop_db.wo_time_logs.delete_one({"_id": doc["_id"]})
        return None, stopped_prev_logs, error

    # Смету (склад по ней не списан) и paid старт в работу не переводит —
    # и в фильтре: статус мог поменяться и после фиксации.
    res_status = shop_db.work_orders.update_one(
        {
            "_id": wo_id, "shop_id": shop["_id"], "is_active": True,
            "status": {"$nin": ["in_progress", "paid", *ESTIMATE_STATUSES]},
        },
        {"$set": {"status": "in_progress", "updated_at": now}},
    )

    # WO-уровневый переход «взят в работу» (не каждый старт таймера, а только
    # когда сам WO поменял состояние: статус или снятый флаг «все
    # закончили») — пуш офисным пользователям.
    if res_status.modified_count or (released and before.get("mechanic_done")):
        from app.blueprints.work_orders.services.push_events import notify_wo_event

        notify_wo_event(shop, wo_id, wo.get("wo_number"), user_id, "taken")

    # Запись календаря, привязанная к WO, становится In Progress вместе с ним.
    from app.blueprints.calendar.services.work_orders import sync_linked_appointments_status

    sync_linked_appointments_status(shop_db, shop, wo_id)

    return doc, stopped_prev_logs, None


def stop_timer(shop_db, shop, user_id):
    """Возвращает (logs, error): все закрытые сессии в порядке started_at."""
    logs = _stop_open_logs(shop_db, shop["_id"], user_id, "user")
    if not logs:
        return [], "no_running_timer"
    return logs, None


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


def summarize_wo_time(shop_db, shop_id, work_order_id, user_id=None, *, tenant_values=None):
    """
    Сводка времени по WO: {labor_id: bucket}. Несколько механиков могут
    работать над одной строкой одновременно — каждый идущий таймер попадает
    в running_users. Bucket:
      total_seconds      — завершённые + идущие (на момент ответа)
      completed_seconds  — только завершённые сессии (база для тикеров)
      my_seconds, my_running, my_started_at — для текущего пользователя
      running_users      — [{user_id, user_name, started_at, mine}]
      users              — {uid: {user_name, seconds}} накопленное по людям
    user_name — живое имя по user_id (снимок лога — запас), см. _live_names.
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
        user_bucket = bucket["users"].setdefault(uid, {"user_name": "", "seconds": 0})
        user_bucket["seconds"] += seconds
        # Снимок — запас на случай, когда живого имени нет: самый свежий
        # (логи идут по started_at), а не имя первой сессии человека.
        if log.get("user_name"):
            user_bucket["user_name"] = log["user_name"]

    _relabel_time_summary(summary, tenant_values)
    return summary


def _relabel_time_summary(summary: dict, tenant_values=None) -> None:
    """Подписи людей сводки (running_users и users) — живые имена одним
    запросом: после переименования механика «X working», разбивка
    трекнутого времени по людям и т.п. показывают новое имя и на старых
    сессиях. Неизвестный id — остаётся снимок."""
    ids = {uid for bucket in summary.values() for uid in bucket["users"]}
    ids.update(u["user_id"] for bucket in summary.values() for u in bucket["running_users"])
    names = _live_names(ids, tenant_values)
    if not names:
        return
    for bucket in summary.values():
        relabel_people(bucket["running_users"], names, id_keys=("user_id",), name_key="user_name")
        for uid, info in bucket["users"].items():
            info["user_name"] = display_name_for(names, uid, info["user_name"], default="")


def time_based_assignments(shop_db, shop_id, work_order_id, *, tenant_values=None):
    """
    Назначение механиков из фактического времени: {labor_id: [{user_id, name,
    role, percent}]} по завершённым сессиям таймеров. Один механик — 100%,
    несколько — пропорционально времени на строке. Строки без завершённого
    времени в результат не попадают (для них остаётся ручное назначение).
    name — живое имя по user_id (снимок лога — запас): вызывающие уточняют
    имя/роль по списку механиков магазина, а бывший механик (уволен, сменил
    роль) в этот список не входит — иначе в WO снова записался бы его
    старый снимок из тайм-лога.
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

    names = _live_names(
        [u["user_id"] for users in per_labor.values() for u in users.values()], tenant_values,
    )
    out: dict[str, list[dict]] = {}
    for labor_id, users in per_labor.items():
        total = sum(u["seconds"] for u in users.values())
        if total <= 0:
            continue
        entries = sorted(users.values(), key=lambda u: -u["seconds"])
        assigned = [
            {
                "user_id": u["user_id"],
                "name": display_name_for(names, u["user_id"], u["name"], default=""),
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


def _assignment_key(assigned) -> list[tuple]:
    """Сравнимая форма assigned_mechanics: (user_id, доля, имя, роль) по порядку."""
    out = []
    for a in assigned if isinstance(assigned, list) else []:
        if not isinstance(a, dict):
            continue
        out.append((
            str(a.get("user_id") or a.get("id") or ""),
            round2(a.get("percent")),
            str(a.get("name") or ""),
            str(a.get("role") or ""),
        ))
    return out


def sync_labor_assignments_from_time(shop_db, shop, work_order_id, mechanics_by_id=None):
    """
    Переписать assigned_mechanics строк WO по фактическому времени таймеров.
    mechanics_by_id ({user_id_str: {name, role}}) уточняет имя/роль из
    актуального списка механиков. Пишется (с $inc labors_rev) только строка,
    у которой назначение реально другое: пустая запись двигала бы ревизию
    после каждого Save офиса, и клиенты принимали бы свой же Save за чужую
    правку. Возвращает True, если что-то обновлено.
    """
    assignments = time_based_assignments(
        shop_db, shop["_id"], work_order_id, tenant_values=tenant_values_for_shop(shop),
    )
    if not assignments:
        return False

    wo_id = oid(work_order_id)
    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"]},
        {"labors.labor_id": 1, "labors.labor.assigned_mechanics": 1},
    )
    if not wo:
        return False
    stored: dict[str, list] = {}
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        lid = str(block.get("labor_id") or "").strip()
        if lid and lid not in stored:
            labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
            stored[lid] = labor.get("assigned_mechanics") or []

    now = utcnow()
    changed = False
    for labor_id, assigned in assignments.items():
        for a in assigned:
            m = (mechanics_by_id or {}).get(str(a["user_id"]))
            if m:
                a["name"] = m.get("name") or a["name"]
                a["role"] = m.get("role") or ""
        if labor_id not in stored or _assignment_key(stored[labor_id]) == _assignment_key(assigned):
            continue  # строки нет (время на удалённой работе) или назначение то же
        res = shop_db.work_orders.update_one(
            {"_id": wo_id, "shop_id": shop["_id"], "labors.labor_id": labor_id},
            {
                "$set": {"labors.$[b].labor.assigned_mechanics": assigned, "updated_at": now},
                "$inc": {"labors_rev": 1},
            },
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
    # Кто работает — живое имя по user_id (снимок лога — запас).
    names = _live_names([log.get("user_id") for log in logs], tenant_values_for_shop(shop))

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
            "user_name": display_name_for(names, log.get("user_id"), log.get("user_name"), default=""),
            "started_at": _fmt_iso(log.get("started_at")),
            "mine": user_id is not None and log.get("user_id") == user_id,
        })
    return items


# ─── Правка фактических часов (permission work_orders.edit_time_logs) ───────
# Менеджер корректирует завершённые сессии: механик забыл остановить таймер,
# случайный старт и т.п. Идущие сессии не правятся — сначала stop. started_at
# остаётся якорем (по нему сессия попадает в день для payroll-отчётов),
# stopped_at пересчитывается как started_at + seconds.

def list_wo_time_sessions(shop_db, shop_id, work_order_id, labor_id=None, *, tenant_values=None):
    """Сессии времени по WO (для модалки правки): завершённые + идущие.
    Имя механика — живое по user_id (одним запросом), снимок — запас."""
    wo_id = oid(work_order_id)
    if not wo_id:
        return []
    query: dict = {"shop_id": shop_id, "work_order_id": wo_id}
    if labor_id:
        query["labor_id"] = str(labor_id).strip()
    logs = list(shop_db.wo_time_logs.find(query).sort("started_at", 1))
    names = _live_names([log.get("user_id") for log in logs], tenant_values)
    out = []
    for log in logs:
        payload = _log_payload(log, names)
        payload["running"] = log.get("stopped_at") is None
        payload["edited"] = bool(log.get("edited_at"))
        out.append(payload)
    return out


def update_time_log_seconds(shop_db, shop_id, log_id, seconds, editor_id):
    """
    Установить длительность завершённой сессии. Возвращает (log, error).
    """
    lid = oid(log_id)
    if not lid:
        return None, "invalid_log_id"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None, "invalid_seconds"
    if seconds <= 0:
        return None, "invalid_seconds"

    log = shop_db.wo_time_logs.find_one({"_id": lid, "shop_id": shop_id})
    if not log:
        return None, "log_not_found"
    if log.get("stopped_at") is None:
        return None, "log_running"

    from datetime import timedelta

    now = utcnow()
    update = {
        "seconds": seconds,
        "stopped_at": (log.get("started_at") or now) + timedelta(seconds=seconds),
        "edited_at": now,
        "edited_by": editor_id,
        "updated_at": now,
    }
    # Исходную длительность сохраняем один раз — след для разбора «кто и что
    # поменял» (сам факт правки виден по edited_at/edited_by).
    if "original_seconds" not in log:
        update["original_seconds"] = int(log.get("seconds") or 0)
    shop_db.wo_time_logs.update_one({"_id": lid}, {"$set": update})
    log.update(update)
    return log, None


def delete_time_log(shop_db, shop_id, log_id):
    """Удалить завершённую сессию. Возвращает (log, error)."""
    lid = oid(log_id)
    if not lid:
        return None, "invalid_log_id"
    log = shop_db.wo_time_logs.find_one({"_id": lid, "shop_id": shop_id})
    if not log:
        return None, "log_not_found"
    if log.get("stopped_at") is None:
        return None, "log_running"
    shop_db.wo_time_logs.delete_one({"_id": lid})
    return log, None


def summarize_mechanic_hours(shop_db, shop_id, date_from=None, date_to_exclusive=None, *, tenant_values=None):
    """
    Для отчёта mechanic_hours: {user_id_str: {user_name, seconds}} по
    завершённым логам с started_at в заданном окне. user_name — живое имя
    по user_id (снимок лога — запас).
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
        bucket = out.setdefault(uid, {"user_name": "", "seconds": 0})
        bucket["seconds"] += int(log.get("seconds") or 0)
        if not bucket["user_name"] and log.get("user_name"):
            bucket["user_name"] = log["user_name"]

    names = _live_names(list(out), tenant_values)
    if names:
        for uid, bucket in out.items():
            bucket["user_name"] = display_name_for(names, uid, bucket["user_name"], default="")
    return out
