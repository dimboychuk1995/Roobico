"""
Work orders на календаре.

Календарь показывает два вида событий:

  * appointments — записи calendar_events (ручное расписание). Запись может
    быть привязана к WO (work_order_id): тогда статус/механики/номер берутся
    с живого WO, а сама запись остаётся редактируемой (время, механик).
  * work orders — «виртуальные» события, собранные из самих WO на лету
    (ничего не пишется в базу): каждый активный WO (кроме смет) попадает в
    календарь сам, даже если его никогда не планировали. Так видно, кто что
    сделал и когда, и что ещё предстоит.

Где WO стоит на сетке:

  * есть тайм-логи механиков (wo_time_logs) — по одному блоку на каждую
    непрерывную рабочую сессию КАЖДОГО механика (соседние сессии одного
    механика на одном WO с паузой до SESSION_MERGE_GAP и без чужой работы
    между ними склеиваются: перекур/обед — один блок). Блок = ровно то
    время, когда этот механик реально работал над WO, поэтому у одного
    механика блоки никогда не пересекаются: таймер на другой работе
    закрывает предыдущий, а если длительность поправили руками и она
    наехала на следующую сессию — конец режется по её началу;
  * логов нет вообще (ни в этой неделе, ни в другой) и нет ни одной живой
    (не отменённой) записи календаря — WO «не расписан»: событие с
    unscheduled=True на дате WO (work_order_date, иначе created_at). Фронт
    показывает его чипом в полосе над сеткой, а не блоком с выдуманным
    временем; start/end в таком событии — лишь разумный дефолт для кнопки
    «Add to schedule» (08:00 или момент создания, длина = часы работ от 1
    до 8, с округлением до 15 минут).

Факты всегда важнее плана. Сессии таймеров показываются всегда. Запись
календаря, привязанная к WO (план), помечается superseded=True, если она
пересекается по времени с реальной работой по ЭТОМУ же WO того же
механика (план без механика — с работой любого) — план «сбылся», на сетке
его заменяют факты (фронт прячет такие записи, но показывает их в окне WO,
чтобы их можно было поправить или удалить). План другого механика
остаётся: он ещё должен сделать свою часть.
Чип «не расписан» не показывается, если у WO есть хоть одна живая
привязанная запись (WO уже в плане) или хоть один тайм-лог (над ним уже
работали — блоки стоят на днях работы).

Статус WO для календаря (wo_state):
  open          — WO создан, работа не начиналась
  in_progress   — в работе
  mechanic_done — механики отметили Done, ждёт проверки менеджера
  completed     — работа сделана (WO закрыт менеджером / есть отработанное
                  время), оплата ещё не прошла
  paid          — оплачен
Механик-режим денег не видит: paid маскируется как completed (как в истории
юнита), сметы не показываются никому — это не работа, а предложение.

Все времена в ответе — «настенные» часы магазина без таймзоны: так же
хранятся и ручные записи календаря, и фронт интерпретирует их как локальные.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from bson import ObjectId

from app.utils.contacts import get_main_contact_name
from app.utils.display_datetime import (
    _safe_tzinfo,
    get_active_shop_timezone_name,
)
from app.utils.tenant import oid

ESTIMATE_STATUSES = ("estimate", "estimated", "quote", "quoted")

WO_STATES = [
    {"key": "open", "label": "Not started", "color": "#f0a30a"},
    {"key": "in_progress", "label": "In progress", "color": "#1a73e8"},
    {"key": "mechanic_done", "label": "Mechanic done", "color": "#7b4de0"},
    {"key": "completed", "label": "Completed", "color": "#0f9d8a"},
    {"key": "paid", "label": "Paid", "color": "#0d904f"},
]

# Сетка календаря: 06:00–21:00. События за пределами сетки фронт не рисует,
# поэтому дефолтное время нерасписанного WO прижимаем к рабочему окну.
GRID_START_HOUR = 6
GRID_END_HOUR = 21
DEFAULT_START_HOUR = 8
MIN_DURATION = timedelta(hours=1)
MAX_DURATION = timedelta(hours=8)
MIN_SESSION = timedelta(minutes=5)
# Пауза между сессиями одного механика на одном WO, при которой они
# считаются одной непрерывной работой (перекур, обед, короткий стоп/старт)
# — если в этой паузе механик не работал над другим WO.
SESSION_MERGE_GAP = timedelta(minutes=60)
GRID_STEP = timedelta(minutes=15)


def customer_label(c: dict) -> str:
    """Лейбл клиента для календаря: компания, иначе главный контакт."""
    company = (c.get("company_name") or "").strip()
    if company:
        return company
    name = get_main_contact_name(c, entity_type="customer")
    return name or "(no name)"


def unit_label(u: dict) -> str:
    """Лейбл юнита для календаря: номер, год, марка, модель (без VIN — коротко)."""
    parts = []
    for key in ("unit_number", "year", "make", "model"):
        if u.get(key):
            parts.append(str(u[key]))
    return " ".join(parts) or "(unit)"


def _shop_tz():
    return _safe_tzinfo(get_active_shop_timezone_name())


def _as_utc(dt: datetime | None) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_local_naive(dt: datetime | None, tz) -> datetime | None:
    """UTC из Mongo → настенное время магазина без tzinfo."""
    dt = _as_utc(dt)
    if dt is None:
        return None
    return dt.astimezone(tz).replace(tzinfo=None)


def _naive_iso(dt: datetime | None) -> str:
    if not isinstance(dt, datetime):
        return ""
    return dt.replace(tzinfo=None, microsecond=0).isoformat()


def parse_range_param(raw: str, tz) -> tuple[datetime | None, datetime | None]:
    """
    Параметр start/end календаря → (настенное время магазина, UTC).

    Веб шлёт «YYYY-MM-DDTHH:MM:SS» без зоны — это локальные часы; мобильное
    приложение шлёт ISO с «Z». Оба приводим к настенным часам магазина.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None, None
    if parsed.tzinfo is not None:
        local = parsed.astimezone(tz).replace(tzinfo=None)
    else:
        local = parsed
    utc = local.replace(tzinfo=tz).astimezone(timezone.utc)
    return local, utc


def derive_wo_state(wo: dict, has_completed_logs: bool, mechanic_mode: bool = False) -> str:
    status = str(wo.get("status") or "open").strip().lower()
    if status == "paid":
        return "completed" if mechanic_mode else "paid"
    if status == "in_progress":
        return "mechanic_done" if wo.get("mechanic_done") else "in_progress"
    if status in ESTIMATE_STATUSES:
        return "estimate"
    if wo.get("manager_confirmed") or wo.get("mechanic_done") or has_completed_logs:
        return "completed"
    return "open"


def wo_assigned_mechanics(wo: dict) -> list[dict]:
    """Назначенные на строки механики WO: [{id, name}] без дублей."""
    out: list[dict] = []
    seen: set[str] = set()
    for block in wo.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        assigned = labor.get("assigned_mechanics") or block.get("assigned_mechanics") or []
        for a in assigned:
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


def wo_labor_summary(wo: dict, limit: int = 4) -> list[str]:
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
    return total


def planned_duration(wo: dict) -> timedelta:
    """Плановая длительность WO для расписания: часы работ, от 1 до 8 часов."""
    hours = wo_labor_hours(wo)
    duration = timedelta(hours=hours) if hours > 0 else MIN_DURATION
    return max(MIN_DURATION, min(duration, MAX_DURATION))


def _clamp_to_grid(start: datetime, duration: timedelta) -> tuple[datetime, datetime]:
    # Длительность — целое число шагов сетки (часы работ бывают 1.33):
    # иначе конец получает секунды, а фронт при дропе чипа строит кривое время.
    steps = -(-duration // GRID_STEP)
    duration = max(GRID_STEP, steps * GRID_STEP)
    day = start.date()
    grid_start = datetime.combine(day, time(GRID_START_HOUR, 0))
    grid_end = datetime.combine(day, time(GRID_END_HOUR, 0))
    if start < grid_start:
        start = grid_start
    if start >= grid_end:
        start = grid_end - MIN_DURATION
    start = start.replace(second=0, microsecond=0, minute=(start.minute // 15) * 15)
    end = min(start + duration, grid_end)
    if end <= start:
        end = min(start + MIN_DURATION, grid_end)
    return start, end


def _wo_base_local_date(wo: dict, tz):
    base = _to_local_naive(wo.get("work_order_date"), tz) or _to_local_naive(wo.get("created_at"), tz)
    return base.date() if base else None


def _work_intervals(logs: list[dict], tz) -> dict[ObjectId, dict[date, dict[str, list[dict]]]]:
    """
    Тайм-логи → непрерывные рабочие интервалы:
    {wo_id: {local_date: {user_id: [{start, end, seconds, running, name}, ...]}}}

    Инвариант: интервалы одного механика в пределах дня не пересекаются —
    механик не может быть на двух работах одновременно. Поэтому сначала все
    его сессии за день (по всем WO) сортируются по старту, затем:
      * соседние сессии на одном WO с паузой ≤ SESSION_MERGE_GAP (и без чужой
        работы между ними) склеиваются в один блок;
      * сессия, наехавшая на начало следующей (ручная правка длительности),
        режется по её началу;
      * короткие сессии растягиваются до MIN_SESSION только если это не
        задевает следующую.
    Сессия через полночь остаётся на дне старта.
    """
    now_utc = datetime.now(timezone.utc)
    by_user_day: dict[tuple[date, str], list[dict]] = {}
    for log in logs:
        wo_id = log.get("work_order_id")
        started = _as_utc(log.get("started_at"))
        if not wo_id or started is None:
            continue
        stopped = _as_utc(log.get("stopped_at"))
        running = stopped is None
        effective_end = stopped or now_utc
        if effective_end < started:
            effective_end = started
        local_start = started.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)
        local_end = effective_end.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)
        day = local_start.date()
        if local_end.date() != day:
            local_end = datetime.combine(day, time(23, 59))
        if local_end < local_start:
            local_end = local_start
        uid = str(log.get("user_id") or "")
        by_user_day.setdefault((day, uid), []).append({
            "wo_id": wo_id,
            "start": local_start,
            "end": local_end,
            "seconds": int(log.get("seconds") or 0) if not running else int((effective_end - started).total_seconds()),
            "running": running,
            "name": str(log.get("user_name") or "").strip() or "Mechanic",
        })

    result: dict[ObjectId, dict[date, dict[str, list[dict]]]] = {}
    for (day, uid), items in by_user_day.items():
        items.sort(key=lambda x: (x["start"], x["end"]))
        out: list[dict] = []
        for item in items:
            last = out[-1] if out else None
            if last and last["wo_id"] == item["wo_id"] and item["start"] - last["end"] <= SESSION_MERGE_GAP:
                last["end"] = max(last["end"], item["end"])
                last["seconds"] += item["seconds"]
                last["running"] = last["running"] or item["running"]
                last["name"] = last["name"] or item["name"]
                continue
            if last and item["start"] < last["end"]:
                last["end"] = item["start"]
            out.append(dict(item))
        for idx, item in enumerate(out):
            if item["end"] - item["start"] < MIN_SESSION:
                padded = item["start"] + MIN_SESSION
                nxt = out[idx + 1]["start"] if idx + 1 < len(out) else None
                if nxt is None or padded <= nxt:
                    item["end"] = padded
        for item in out:
            if item["end"] <= item["start"]:
                continue
            session = {k: v for k, v in item.items() if k != "wo_id"}
            result.setdefault(item["wo_id"], {}).setdefault(day, {}).setdefault(uid, []).append(session)
    return result


def _base_event(wo: dict, state: str, status: str, customers: dict, units: dict, mechanic_mode: bool) -> dict:
    return {
        "kind": "work_order",
        "is_virtual": True,
        "editable": False,
        "work_order_id": str(wo["_id"]),
        "wo_number": wo.get("wo_number"),
        "wo_status": "completed" if (mechanic_mode and status == "paid") else status,
        "wo_state": state,
        "status": state,
        "title": f"WO #{wo.get('wo_number') or ''}".strip(),
        "customer_id": str(wo.get("customer_id") or ""),
        "customer_label": customers.get(wo.get("customer_id")) or "",
        "unit_id": str(wo.get("unit_id") or ""),
        "unit_label": units.get(wo.get("unit_id")) or "",
        "labor_summary": wo_labor_summary(wo),
        "labor_hours": round(wo_labor_hours(wo), 2),
        "presets": [],
        "mechanic_id": "",
        "mechanic_name": "",
        "unscheduled": False,
    }


def load_work_order_events(
    shop_db,
    shop: dict,
    range_start_local: datetime,
    range_end_local: datetime,
    *,
    mechanic_mode: bool = False,
) -> list[dict]:
    """
    Виртуальные события WO за диапазон (настенные часы магазина, [start, end)):
    сессии таймеров (всегда) и чипы «не расписан» для WO без таймеров и без
    привязанных записей календаря.
    """
    tz = _shop_tz()
    shop_id = shop["_id"]

    range_start_utc = range_start_local.replace(tzinfo=tz).astimezone(timezone.utc)
    range_end_utc = range_end_local.replace(tzinfo=tz).astimezone(timezone.utc)

    # Тайм-логи за диапазон: сессия лежит на дне своего старта (через
    # полночь — обрезается 23:59), поэтому запас по датам не нужен.
    logs = list(
        shop_db.wo_time_logs.find(
            {
                "shop_id": shop_id,
                "started_at": {"$gte": range_start_utc, "$lt": range_end_utc},
            },
            {"work_order_id": 1, "user_id": 1, "user_name": 1, "started_at": 1, "stopped_at": 1, "seconds": 1},
        )
    )
    intervals_by_wo = _work_intervals(logs, tz)

    wo_query = {
        "shop_id": shop_id,
        "is_active": True,
        "status": {"$nin": list(ESTIMATE_STATUSES)},
        "$or": [
            {"work_order_date": {"$gte": range_start_utc - timedelta(days=1), "$lt": range_end_utc + timedelta(days=1)}},
            {"work_order_date": {"$exists": False}, "created_at": {"$gte": range_start_utc, "$lt": range_end_utc}},
            {"work_order_date": None, "created_at": {"$gte": range_start_utc, "$lt": range_end_utc}},
        ],
    }
    if intervals_by_wo:
        wo_query = {"$or": [wo_query, {"_id": {"$in": list(intervals_by_wo.keys())}, "shop_id": shop_id, "is_active": True}]}

    projection = {
        "wo_number": 1, "status": 1, "customer_id": 1, "unit_id": 1, "labors": 1,
        "work_order_date": 1, "created_at": 1, "mechanic_done": 1, "mechanic_done_marks": 1,
        "manager_confirmed": 1, "updated_at": 1,
    }
    work_orders = list(shop_db.work_orders.find(wo_query, projection))
    if not work_orders:
        return []

    # Логи WO вообще (не только в диапазоне): над WO уже работали — чип
    # «не расписан» ему не нужен; завершённые логи — для статуса completed
    # у WO без mechanic_done. Два запроса на все WO сразу.
    wo_ids = [w["_id"] for w in work_orders]
    logged_wo_ids = set(
        shop_db.wo_time_logs.distinct("work_order_id", {"shop_id": shop_id, "work_order_id": {"$in": wo_ids}})
    )
    completed_log_wo_ids = set(
        shop_db.wo_time_logs.distinct(
            "work_order_id",
            {"shop_id": shop_id, "work_order_id": {"$in": wo_ids}, "stopped_at": {"$ne": None}},
        )
    )

    customers = {
        c["_id"]: customer_label(c)
        for c in shop_db.customers.find(
            {"_id": {"$in": [w.get("customer_id") for w in work_orders if w.get("customer_id")]}},
            {"company_name": 1, "contacts": 1, "first_name": 1, "last_name": 1},
        )
    }
    units = {
        u["_id"]: unit_label(u)
        for u in shop_db.units.find(
            {"_id": {"$in": [w.get("unit_id") for w in work_orders if w.get("unit_id")]}},
            {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1},
        )
    }
    # WO, у которых есть хоть одна живая запись календаря, — уже в плане,
    # чип не нужен (отменённая запись слот не держит и план не означает).
    scheduled_wo_ids = set(
        shop_db.calendar_events.distinct(
            "work_order_id",
            {"shop_id": shop_id, "work_order_id": {"$in": wo_ids}, "status": {"$ne": "cancelled"}},
        )
    )

    events: list[dict] = []
    for wo in work_orders:
        status = str(wo.get("status") or "open").strip().lower()
        if status in ESTIMATE_STATUSES:
            continue
        wo_id = wo["_id"]
        state = derive_wo_state(wo, wo_id in completed_log_wo_ids, mechanic_mode=mechanic_mode)
        assigned = wo_assigned_mechanics(wo)
        base = _base_event(wo, state, status, customers, units, mechanic_mode)
        # Фактическая работа: блок на каждую сессию каждого механика.
        for day, users in sorted((intervals_by_wo.get(wo_id) or {}).items()):
            day_start = datetime.combine(day, time.min)
            if not (range_start_local <= day_start < range_end_local):
                continue
            for uid, sessions in users.items():
                for idx, s in enumerate(sessions):
                    events.append({
                        **base,
                        "id": f"wo:{wo_id}:{day.isoformat()}:{uid or 'x'}:{idx}",
                        "start_time": _naive_iso(s["start"]),
                        "end_time": _naive_iso(s["end"]),
                        "mechanics": [{"id": uid, "name": s["name"]}],
                        "mechanic_ids": [uid] if uid else [],
                        "mechanic_name": s["name"],
                        "worked": [{"id": uid, "name": s["name"], "seconds": s["seconds"], "running": s["running"]}],
                        "running": bool(s["running"]),
                        "source": "time_logs",
                    })

        # Нерасписанный WO: чип на дате WO, без выдуманного времени на сетке.
        # Только если над ним никто никогда не работал и его никто не планировал.
        if wo_id in logged_wo_ids or wo_id in scheduled_wo_ids:
            continue
        day = _wo_base_local_date(wo, tz)
        if day is None:
            continue
        day_start = datetime.combine(day, time.min)
        if not (range_start_local <= day_start < range_end_local):
            continue
        created_local = _to_local_naive(wo.get("created_at"), tz)
        if created_local and created_local.date() == day:
            start = created_local
        else:
            start = datetime.combine(day, time(DEFAULT_START_HOUR, 0))
        start, end = _clamp_to_grid(start, planned_duration(wo))
        events.append({
            **base,
            "id": f"wo:{wo_id}:{day.isoformat()}",
            "start_time": _naive_iso(start),
            "end_time": _naive_iso(end),
            "mechanics": assigned,
            "mechanic_ids": [m["id"] for m in assigned if m["id"]],
            "mechanic_name": ", ".join(m["name"] for m in assigned),
            "worked": [],
            "running": False,
            "source": "work_order_date",
            "unscheduled": True,
        })

    return events


def enrich_linked_appointments(
    shop_db,
    shop: dict,
    events: list[dict],
    raw_rows: list[dict],
    *,
    mechanic_mode: bool = False,
) -> dict[ObjectId, set]:
    """
    Дополнить записи календаря, привязанные к WO, живыми данными WO
    (номер, статус, механики, работы, отработанное в этот день время).
    Мутирует events (по индексу совпадают с raw_rows). Возвращает
    {wo_id: {local_date,...}} — дни, на которые есть привязанные записи
    (чтобы не дублировать виртуальные).
    """
    linked_days: dict[ObjectId, set] = {}
    wo_ids = [r.get("work_order_id") for r in raw_rows if r.get("work_order_id")]
    if not wo_ids:
        return linked_days

    shop_id = shop["_id"]
    tz = _shop_tz()
    wo_map = {
        w["_id"]: w
        for w in shop_db.work_orders.find(
            {"_id": {"$in": wo_ids}, "shop_id": shop_id, "is_active": True},
            {"wo_number": 1, "status": 1, "labors": 1, "mechanic_done": 1, "manager_confirmed": 1, "customer_id": 1, "unit_id": 1},
        )
    }
    completed_log_wo_ids = set(
        shop_db.wo_time_logs.distinct(
            "work_order_id",
            {"shop_id": shop_id, "work_order_id": {"$in": list(wo_map.keys())}, "stopped_at": {"$ne": None}},
        )
    ) if wo_map else set()

    # Фактическое время по дням привязанных записей — одним запросом.
    worked_by_wo_day: dict[tuple, dict[str, dict]] = {}
    days = [r.get("start_time") for r in raw_rows if r.get("work_order_id") and isinstance(r.get("start_time"), datetime)]
    if wo_map and days:
        lo = min(days).replace(hour=0, minute=0, second=0, microsecond=0)
        hi = max(days).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        logs = shop_db.wo_time_logs.find(
            {
                "shop_id": shop_id,
                "work_order_id": {"$in": list(wo_map.keys())},
                "started_at": {
                    "$gte": lo.replace(tzinfo=tz).astimezone(timezone.utc) - timedelta(days=1),
                    "$lt": hi.replace(tzinfo=tz).astimezone(timezone.utc),
                },
            },
            {"work_order_id": 1, "user_id": 1, "user_name": 1, "started_at": 1, "stopped_at": 1, "seconds": 1},
        )
        for wo_id, by_day in _work_intervals(list(logs), tz).items():
            for day, users in by_day.items():
                bucket = worked_by_wo_day.setdefault((wo_id, day), {})
                for uid, sessions in users.items():
                    entry = bucket.setdefault(uid, {"id": uid, "name": "", "seconds": 0, "running": False})
                    for s in sessions:
                        entry["name"] = entry["name"] or s["name"]
                        entry["seconds"] += s["seconds"]
                        entry["running"] = entry["running"] or s["running"]

    for row, event in zip(raw_rows, events):
        wo_id = row.get("work_order_id")
        wo = wo_map.get(wo_id) if wo_id else None
        if not wo:
            # WO удалён/не найден — запись остаётся обычной.
            event["work_order_id"] = ""
            event["wo_number"] = None
            event["wo_state"] = ""
            continue
        status = str(wo.get("status") or "open").strip().lower()
        state = derive_wo_state(wo, wo_id in completed_log_wo_ids, mechanic_mode=mechanic_mode)
        assigned = wo_assigned_mechanics(wo)
        event["work_order_id"] = str(wo_id)
        event["wo_number"] = wo.get("wo_number")
        event["wo_status"] = "completed" if (mechanic_mode and status == "paid") else status
        event["wo_state"] = state
        event["labor_summary"] = wo_labor_summary(wo)
        event["labor_hours"] = round(wo_labor_hours(wo), 2)
        # План — на конкретного механика записи; без него — на назначенных по WO.
        if event.get("mechanic_id"):
            mechanics = [{"id": event["mechanic_id"], "name": event.get("mechanic_name") or "Mechanic"}]
        else:
            mechanics = list(assigned)
        event["mechanics"] = mechanics
        event["mechanic_ids"] = [m["id"] for m in mechanics if m["id"]]
        event["superseded"] = False
        start = row.get("start_time")
        if isinstance(start, datetime):
            linked_days.setdefault(wo_id, set()).add(start.date())
            worked = worked_by_wo_day.get((wo_id, start.date())) or {}
            event["worked"] = sorted(worked.values(), key=lambda u: -u["seconds"])
            event["running"] = any(u["running"] for u in worked.values())

    return linked_days


def mark_superseded_plans(events: list[dict]) -> int:
    """
    План (запись, привязанная к WO) пересёкся с реальной работой по тому же
    WO того же механика → superseded=True: на сетке его заменяют факты.
    План без механика заменяется работой любого механика по этому WO.
    План другого механика не трогаем — его часть ещё не сделана.
    Возвращает число помеченных записей.
    """
    sessions: dict[str, list[tuple[datetime, datetime, str]]] = {}
    for ev in events:
        if ev.get("is_virtual") and ev.get("source") == "time_logs" and ev.get("work_order_id"):
            try:
                s = datetime.fromisoformat(ev["start_time"])
                e = datetime.fromisoformat(ev["end_time"])
            except (TypeError, ValueError):
                continue
            ids = ev.get("mechanic_ids") or []
            uid = str(ids[0]) if ids else ""
            sessions.setdefault(ev["work_order_id"], []).append((s, e, uid))
    count = 0
    for ev in events:
        if ev.get("is_virtual") or not ev.get("work_order_id"):
            continue
        try:
            s = datetime.fromisoformat(ev["start_time"])
            e = datetime.fromisoformat(ev["end_time"])
        except (TypeError, ValueError):
            continue
        plan_mechanics = {str(m) for m in (ev.get("mechanic_ids") or []) if m}
        for ss, se, uid in sessions.get(ev["work_order_id"], []):
            if ss >= e or se <= s:
                continue
            if plan_mechanics and uid and uid not in plan_mechanics:
                continue
            ev["superseded"] = True
            count += 1
            break
    return count


def find_mechanic_conflict(shop_db, shop: dict, mechanic_id, start_time: datetime, end_time: datetime, exclude_id=None, exclude_wo_id=None):
    """
    Чем занят механик в этот слот: другой записью календаря (отменённые не
    считаются) или реальной работой по таймеру на другом WO. Один механик не
    может быть на двух работах одновременно. Возвращает документ записи
    или псевдо-конфликт {"kind": "time_log", ...}; None — слот свободен.
    """
    mid = oid(mechanic_id)
    if not mid or not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        return None
    query = {
        "shop_id": shop["_id"],
        "mechanic_id": mid,
        "start_time": {"$lt": end_time},
        "end_time": {"$gt": start_time},
        "status": {"$ne": "cancelled"},
    }
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    tz = _shop_tz()
    now_utc = datetime.now(timezone.utc)
    for found in shop_db.calendar_events.find(query, sort=[("start_time", 1)]):
        # План, который уже «сбылся» (механик по таймеру работал над этим WO
        # в это время), на сетке спрятан — слот он не держит.
        if found.get("work_order_id") and _plan_already_worked(shop_db, shop["_id"], mid, found, tz, now_utc):
            continue
        return found

    # Фактическая работа по таймеру на другом WO в это время. Работающий
    # сейчас таймер занимает [старт, сейчас) — будущие слоты он не блокирует.
    start_utc = start_time.replace(tzinfo=tz).astimezone(timezone.utc)
    end_utc = end_time.replace(tzinfo=tz).astimezone(timezone.utc)
    busy_or = [{"stopped_at": {"$gt": start_utc}}]
    if start_utc < now_utc:
        busy_or.append({"stopped_at": None})
    log_query = {
        "shop_id": shop["_id"],
        "user_id": mid,
        "started_at": {"$lt": end_utc},
        "$or": busy_or,
    }
    wo_excl = oid(exclude_wo_id)
    if wo_excl:
        log_query["work_order_id"] = {"$ne": wo_excl}
    log = shop_db.wo_time_logs.find_one(log_query, sort=[("started_at", 1)])
    if not log:
        return None
    stopped = _as_utc(log.get("stopped_at")) or datetime.now(timezone.utc)
    return {
        "kind": "time_log",
        "wo_number": log.get("wo_number"),
        "work_order_id": log.get("work_order_id"),
        "mechanic_name": log.get("user_name"),
        "start_time": _to_local_naive(log.get("started_at"), tz),
        "end_time": stopped.astimezone(tz).replace(tzinfo=None),
        "running": log.get("stopped_at") is None,
    }


def _plan_already_worked(shop_db, shop_id, mechanic_id, plan: dict, tz, now_utc: datetime) -> bool:
    """Механик по таймеру работал над WO плана в окно самого плана."""
    ps, pe = plan.get("start_time"), plan.get("end_time")
    if not isinstance(ps, datetime) or not isinstance(pe, datetime):
        return False
    ps_utc = ps.replace(tzinfo=tz).astimezone(timezone.utc)
    pe_utc = pe.replace(tzinfo=tz).astimezone(timezone.utc)
    logs = shop_db.wo_time_logs.find(
        {"shop_id": shop_id, "user_id": mechanic_id, "work_order_id": plan["work_order_id"], "started_at": {"$lt": pe_utc}},
        {"started_at": 1, "stopped_at": 1},
    )
    for log in logs:
        ended = _as_utc(log.get("stopped_at")) or now_utc
        if ended > ps_utc:
            return True
    return False


def conflict_message(conflict: dict, mechanic_name: str = "") -> str:
    start = conflict.get("start_time")
    end = conflict.get("end_time")
    when = ""
    if isinstance(start, datetime) and isinstance(end, datetime):
        when = f" {start:%m/%d %H:%M}–{end:%H:%M}"
    who = mechanic_name or conflict.get("mechanic_name") or "This mechanic"
    if conflict.get("kind") == "time_log":
        what = f"WO #{conflict['wo_number']}" if conflict.get("wo_number") else "another work order"
        verb = "is working" if conflict.get("running") else "was actually working"
        return f"{who} {verb} on {what}{when} (timer). Choose another time or mechanic."
    what = f"WO #{conflict['wo_number']}" if conflict.get("wo_number") else (
        conflict.get("title") or conflict.get("customer_label") or "another appointment"
    )
    return f"{who} is already booked{when} ({what}). Choose another time or mechanic."


def work_order_snapshot_for_event(shop_db, shop: dict, work_order_id) -> dict | None:
    """Лейблы WO для новой записи календаря, привязанной к WO."""
    wo_id = oid(work_order_id)
    if not wo_id:
        return None
    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        {"wo_number": 1, "customer_id": 1, "unit_id": 1, "labors": 1},
    )
    if not wo:
        return None
    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}, {"company_name": 1, "contacts": 1, "first_name": 1, "last_name": 1}) if wo.get("customer_id") else None
    unit = shop_db.units.find_one({"_id": wo.get("unit_id")}, {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1}) if wo.get("unit_id") else None
    assigned = wo_assigned_mechanics(wo)
    return {
        "work_order_id": wo_id,
        "wo_number": wo.get("wo_number"),
        "customer_id": wo.get("customer_id"),
        "customer_label": customer_label(customer) if customer else "",
        "unit_id": wo.get("unit_id"),
        "unit_label": unit_label(unit) if unit else "",
        "mechanic": assigned[0] if assigned else None,
        "title": ", ".join(wo_labor_summary(wo, limit=3)),
    }
