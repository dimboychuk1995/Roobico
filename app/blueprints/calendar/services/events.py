"""
Записи календаря (calendar_events): лейблы, бригада, даты, сериализация и
сборка документа из запроса (POST/PUT).

Времена записи — НАИВНЫЕ настенные часы магазина (так календарь хранит их
с первого дня; существующие данные менять нельзя). Клиент шлёт
"YYYY-MM-DDTHH:MM:SS" без зоны = те же настенные часы; строку с зоной
(Z/смещение — так шлёт мобильное приложение) переводим в зону магазина и
отбрасываем tzinfo. Наружу отдаём тот же формат без зоны.

Модуль не знает о work orders (их знает services.work_orders, который
импортирует отсюда лейблы и бригаду); единственное исключение — снимок WO
для привязки, который берётся ленивым импортом внутри функций сборки.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from bson import ObjectId
from flask import current_app

from app.blueprints.calendar.services.statuses import status_keys
from app.utils.contacts import get_main_contact_name
from app.utils.display_datetime import _safe_tzinfo, get_active_shop_timezone_name
from app.utils.tenant import oid

TITLE_MAX_LEN = 500

# Старые записи без клиента несли customer_label «New Customer» — при
# отдаче такой лейбл (без customer_id) считается пустым.
LEGACY_EMPTY_CUSTOMER_LABELS = {"new customer"}

CUSTOMER_PROJECTION = {"company_name": 1, "contacts": 1, "first_name": 1, "last_name": 1}
UNIT_PROJECTION = {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1, "customer_id": 1}

# Подписи полей для audit_log.diff_fields в PUT.
AUDIT_FIELD_LABELS = {
    "start_time": "Start", "end_time": "End", "status": "Status", "mechanic_name": "Mechanic",
    "customer_label": "Customer", "unit_label": "Unit", "title": "Title", "wo_number": "Work order",
}


class CalendarError(Exception):
    """Ошибка валидации/поиска: роут отдаёт {"error": message} со status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _text(value) -> str:
    return str(value if value is not None else "").strip()


# ── лейблы ───────────────────────────────────────────────────

def customer_label(c: dict | None) -> str:
    """Компания, иначе главный контакт, иначе «(no name)»."""
    c = c or {}
    company = _text(c.get("company_name"))
    if company:
        return company
    return get_main_contact_name(c, entity_type="customer") or "(no name)"


def unit_label(u: dict | None) -> str:
    """Номер, год, марка, модель через пробел; без VIN — коротко."""
    u = u or {}
    parts = [str(u[key]) for key in ("unit_number", "year", "make", "model") if u.get(key)]
    return " ".join(parts) or "(unit)"


def display_customer_label(doc: dict) -> str:
    """Лейбл клиента для отдачи: legacy «New Customer» без customer_id → ''."""
    label = _text(doc.get("customer_label"))
    if not doc.get("customer_id") and label.lower() in LEGACY_EMPTY_CUSTOMER_LABELS:
        return ""
    return label


def appointment_note(doc: dict) -> str:
    """Заметка = title, если он не повторяет лейбл клиента (старые записи
    без заметки хранили в title имя клиента)."""
    title = _text(doc.get("title"))
    if not title or title == _text(doc.get("customer_label")):
        return ""
    return title


# ── бригада ──────────────────────────────────────────────────
# Документ хранит mechanics[{id,name}] и mechanic_ids[] (для запросов), а
# legacy mechanic_id/mechanic_name — первый механик. Пишем все четыре поля,
# читаем список, иначе legacy пару.

def event_mechanics(doc: dict) -> list[dict]:
    """[{id, name}] записи: список mechanics, иначе legacy mechanic_id/name."""
    raw = doc.get("mechanics")
    if isinstance(raw, list) and raw:
        return [
            {"id": m["id"], "name": _text(m.get("name")) or "Mechanic"}
            for m in raw
            if isinstance(m, dict) and m.get("id")
        ]
    if doc.get("mechanic_id"):
        return [{"id": doc["mechanic_id"], "name": _text(doc.get("mechanic_name")) or "Mechanic"}]
    return []


def mechanic_fields(mechanics: list[dict]) -> dict:
    """Поля документа для бригады (+ legacy первый механик)."""
    return {
        "mechanics": [{"id": m["id"], "name": m["name"]} for m in mechanics],
        "mechanic_ids": [m["id"] for m in mechanics],
        "mechanic_id": mechanics[0]["id"] if mechanics else None,
        "mechanic_name": mechanics[0]["name"] if mechanics else "",
    }


def build_mechanics(items, shop: dict) -> list[dict]:
    """
    [{id: ObjectId, name}] из сырых id / пар {id, name}, без дублей. Состав
    сверяется со списком назначаемых механиков магазина: имя берём оттуда,
    неизвестных (чужой/несуществующий id, уволенный, не механик) отбрасываем
    с логом — присланные клиентом id и имена в базу не попадают.
    """
    # Ленивый импорт: пакет work_orders тянет свой routes.py, а тот — календарь.
    from app.blueprints.work_orders.services.lookups import get_assignable_mechanics

    known = {m["id"]: m["name"] for m in get_assignable_mechanics(shop)}
    out: list[dict] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for item in items or []:
        raw_id = item.get("id") if isinstance(item, dict) else item
        mid = oid(_text(raw_id))
        if not mid or str(mid) in seen:
            continue
        seen.add(str(mid))
        if str(mid) not in known:
            dropped.append(str(mid))
            continue
        out.append({"id": mid, "name": known[str(mid)]})
    if dropped:
        current_app.logger.info(
            "Calendar: %s are not assignable mechanics of shop %s — dropped from the appointment crew",
            dropped, shop.get("_id"),
        )
    return out


def parse_mechanics(data: dict, shop: dict) -> list[dict] | None:
    """Бригада из запроса: первое из mechanics[{id,name}] / mechanic_ids[] /
    legacy mechanic_id. None — ни одно поле не передано."""
    if isinstance(data.get("mechanics"), list):
        return build_mechanics(data["mechanics"], shop)
    if isinstance(data.get("mechanic_ids"), list):
        return build_mechanics(data["mechanic_ids"], shop)
    if "mechanic_id" in data:
        raw = _text(data.get("mechanic_id"))
        return build_mechanics([raw], shop) if raw else []
    return None


def parse_presets(raw) -> list[dict]:
    """[{id, name}] с непустыми id и name, без дублей по id; остальное отбрасывается."""
    presets: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            pid, name = _text(item.get("id")), _text(item.get("name"))
            if pid and name and pid not in seen:
                seen.add(pid)
                presets.append({"id": pid, "name": name})
    return presets


# ── даты ─────────────────────────────────────────────────────

def shop_tz():
    return _safe_tzinfo(get_active_shop_timezone_name())


def parse_client_datetime(raw) -> datetime | None:
    """ISO-строка клиента → наивные настенные часы магазина; None — не разобрать."""
    value = _text(raw)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(shop_tz()).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def parse_range_param(raw) -> datetime | None:
    """start/end диапазона GET /calendar/api/events (веб — без зоны,
    мобильное — с Z): настенные часы магазина или None."""
    return parse_client_datetime(raw)


def naive_iso(dt) -> str:
    """Наивный ISO без микросекунд ("YYYY-MM-DDTHH:MM:SS") или ''."""
    if not isinstance(dt, datetime):
        return ""
    return dt.replace(tzinfo=None, microsecond=0).isoformat()


def parse_required_datetime(raw) -> datetime:
    if not _text(raw):
        raise CalendarError("Start and end time are required")
    parsed = parse_client_datetime(raw)
    if parsed is None:
        raise CalendarError("Invalid date format")
    return parsed


def check_span(start, end) -> None:
    """Конец после начала и в тот же календарный день (00:00 следующего дня
    — можно: это «до полуночи»)."""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise CalendarError("Start and end time are required")
    if end <= start:
        raise CalendarError("End time must be after start time")
    next_midnight = datetime.combine(start.date() + timedelta(days=1), time.min)
    if end.date() != start.date() and end != next_midnight:
        raise CalendarError("An appointment must start and end on the same day")


def parse_times(start_raw, end_raw) -> tuple[datetime, datetime]:
    if not _text(start_raw) or not _text(end_raw):
        raise CalendarError("Start and end time are required")
    start, end = parse_client_datetime(start_raw), parse_client_datetime(end_raw)
    if start is None or end is None:
        raise CalendarError("Invalid date format")
    check_span(start, end)
    return start, end


# ── клиент / юнит / статус / заголовок ───────────────────────

def resolve_customer(shop_db, shop: dict, raw_id) -> tuple[ObjectId | None, str]:
    """(customer_id, живой лейбл) активного клиента магазина, иначе (None, '')."""
    cid = oid(_text(raw_id))
    if not cid:
        return None, ""
    doc = shop_db.customers.find_one(
        {"_id": cid, "shop_id": shop["_id"], "is_active": True}, CUSTOMER_PROJECTION
    )
    if not doc:
        return None, ""
    return cid, customer_label(doc)


def resolve_unit(shop_db, shop: dict, raw_id, customer_id) -> tuple[ObjectId | None, str]:
    """(unit_id, живой лейбл) активного юнита магазина; с клиентом — только
    его юнит. Иначе (None, '')."""
    uid = oid(_text(raw_id))
    if not uid:
        return None, ""
    query = {"_id": uid, "shop_id": shop["_id"], "is_active": True}
    if customer_id:
        query["customer_id"] = customer_id
    doc = shop_db.units.find_one(query, UNIT_PROJECTION)
    if not doc:
        return None, ""
    return uid, unit_label(doc)


def _norm_id(value) -> ObjectId | None:
    """ObjectId из ObjectId / hex-строки (старые записи хранили строки), иначе None."""
    return oid(_text(value))


def identity_from_snapshot(snapshot: dict) -> dict:
    """
    Клиент/юнит записи по снимку WO — одно правило для хуков
    (link_event_to_work_order, refresh_linked_appointments_identity) и для
    POST/PUT привязанной записи: без фильтра is_active (клиента или юнит WO
    могли деактивировать — запись всё равно его). Лейбл не затираем пустым,
    если id есть, а документа клиента/юнита уже нет.
    """
    fields = {"customer_id": snapshot.get("customer_id"), "unit_id": snapshot.get("unit_id")}
    customer_text = _text(snapshot.get("customer_label"))
    unit_text = _text(snapshot.get("unit_label"))
    if customer_text or not fields["customer_id"]:
        fields["customer_label"] = customer_text
    if unit_text or not fields["unit_id"]:
        fields["unit_label"] = unit_text
    return fields


def _linked_error(snapshot: dict) -> CalendarError:
    return CalendarError(
        f"This appointment is linked to WO #{snapshot.get('wo_number')} — "
        "change the customer or unit on the work order or unlink it first"
    )


def new_event_identity(shop_db, shop: dict, customer_raw, unit_raw, snapshot: dict | None) -> dict:
    """
    Поля клиента/юнита нового документа. Без WO — активный клиент магазина и
    его активный юнит (иначе None / ''). С WO: пустой или равный WO id берётся
    с WO (identity_from_snapshot — ровно как пишут хуки); иной присланный
    клиент важнее WO и резолвится как обычно; юнит WO — только вместе с
    клиентом WO (или без клиента).
    """
    out = {"customer_id": None, "customer_label": "", "unit_id": None, "unit_label": ""}
    wo_fields = identity_from_snapshot(snapshot) if snapshot else {}
    wo_customer = _norm_id(snapshot.get("customer_id")) if snapshot else None
    cid = _norm_id(customer_raw)
    if snapshot and (not cid or cid == wo_customer):
        out["customer_id"], out["customer_label"] = snapshot.get("customer_id"), wo_fields.get("customer_label", "")
    else:
        out["customer_id"], out["customer_label"] = resolve_customer(shop_db, shop, customer_raw)
    uid = _norm_id(unit_raw)
    if snapshot and (not uid or uid == _norm_id(snapshot.get("unit_id"))) and _norm_id(out["customer_id"]) in (None, wo_customer):
        out["unit_id"], out["unit_label"] = snapshot.get("unit_id"), wo_fields.get("unit_label", "")
    else:
        out["unit_id"], out["unit_label"] = resolve_unit(shop_db, shop, unit_raw, out["customer_id"])
    return out


def linked_identity_updates(existing: dict, snapshot: dict, data: dict, *, relinked: bool) -> dict:
    """
    $set клиента/юнита записи, привязанной к WO (после этого запроса): они
    следуют за WO. Присланный id, равный id на WO, → поля с WO
    (identity_from_snapshot, без фильтра is_active); равный сохранённому →
    без изменений (клиент/юнит записи могли разойтись с WO — отменённую
    запись refresh пропускает — и это не должно блокировать правку
    заголовка, статуса или времени); иной → 400. Не присланные поля при
    (пере)привязке заполняются с WO, если пусты; юнит WO — только вместе с
    клиентом WO (или без клиента).
    """
    wo_fields = identity_from_snapshot(snapshot)
    wo_customer = _norm_id(snapshot.get("customer_id"))

    def from_wo(field: str) -> bool:
        """True — поле берём с WO; False — оставляем как есть; иначе 400."""
        wo_value = _norm_id(snapshot.get(field))
        if field in data:
            requested = _norm_id(data.get(field))
            if requested == wo_value:
                return True
            if not relinked and requested == _norm_id(existing.get(field)):
                return False
            raise _linked_error(snapshot)
        return relinked and not existing.get(field) and wo_value is not None

    updates: dict = {}
    customer_from_wo = from_wo("customer_id")
    if customer_from_wo:
        updates.update({k: v for k, v in wo_fields.items() if k.startswith("customer_")})
    unit_from_wo = from_wo("unit_id") or (
        customer_from_wo and "unit_id" not in data
        and _norm_id(existing.get("unit_id")) != _norm_id(snapshot.get("unit_id"))
    )
    if unit_from_wo:
        customer_now = _norm_id(updates["customer_id"] if "customer_id" in updates else existing.get("customer_id"))
        if customer_now in (None, wo_customer):
            updates.update({k: v for k, v in wo_fields.items() if k.startswith("unit_")})
        else:
            # Юнит WO принадлежит клиенту WO — с другим клиентом записи он не живёт.
            updates["unit_id"], updates["unit_label"] = None, ""
    return updates


def clean_status(shop_db, raw) -> str:
    key = _text(raw)
    return key if key and key in status_keys(shop_db) else "scheduled"


def clean_title(raw) -> str:
    return _text(raw)[:TITLE_MAX_LEN]


def _load_snapshot(shop_db, shop: dict, raw_wo_id) -> dict:
    # Ленивый импорт: services.work_orders импортирует лейблы отсюда.
    from app.blueprints.calendar.services.work_orders import work_order_snapshot_for_event

    snapshot = work_order_snapshot_for_event(shop_db, shop, raw_wo_id)
    if not snapshot:
        raise CalendarError("Work order not found", 404)
    return snapshot


# ── сборка документа из запроса ──────────────────────────────

def prepare_new_event(shop_db, shop: dict, data: dict, *, user_id, tenant_id) -> dict:
    """
    Проверенный документ calendar_events из тела POST (ещё не вставлен).
    Привязка к WO (work_order_id): клиент/юнит/бригада/заголовок берутся
    с WO только там, где запрос их не заполнил. Бросает CalendarError.
    """
    data = data if isinstance(data, dict) else {}
    start, end = parse_times(data.get("start_time"), data.get("end_time"))
    mechanics = parse_mechanics(data, shop) or []
    title = clean_title(data.get("title"))

    work_order_id = wo_number = snapshot = None
    if _text(data.get("work_order_id")):
        snapshot = _load_snapshot(shop_db, shop, data.get("work_order_id"))
        work_order_id, wo_number = snapshot["work_order_id"], snapshot.get("wo_number")
        if not mechanics and snapshot.get("mechanics"):
            mechanics = build_mechanics(snapshot["mechanics"], shop)
        if not title:
            title = clean_title(snapshot.get("title"))

    now = utcnow()
    return {
        "title": title,
        "start_time": start,
        "end_time": end,
        "status": clean_status(shop_db, data.get("status")),
        **new_event_identity(shop_db, shop, data.get("customer_id"), data.get("unit_id"), snapshot),
        **mechanic_fields(mechanics),
        "presets": parse_presets(data.get("presets")),
        "work_order_id": work_order_id,
        "wo_number": wo_number,
        "shop_id": shop["_id"],
        "tenant_id": oid(tenant_id) or shop.get("tenant_id"),
        "created_at": now,
        "updated_at": now,
        "created_by": oid(user_id),
        "updated_by": oid(user_id),
    }


def prepare_event_updates(shop_db, shop: dict, existing: dict, data: dict, *, user_id) -> dict:
    """
    $set для PUT: меняются только ключи, присланные в теле (customer_label /
    unit_label клиента игнорируются — лейблы всегда из базы). Привязка к
    новому WO заполняет пустые поля с него; "" в work_order_id — отвязка.
    У привязанной записи клиент/юнит следуют за WO: присланные id, равные
    id на WO или уже сохранённым, — не изменение; иные → 400
    (linked_identity_updates). Бросает CalendarError.
    """
    data = data if isinstance(data, dict) else {}
    updates: dict = {}

    start, end = existing.get("start_time"), existing.get("end_time")
    if "start_time" in data:
        start = updates["start_time"] = parse_required_datetime(data.get("start_time"))
    if "end_time" in data:
        end = updates["end_time"] = parse_required_datetime(data.get("end_time"))
    if "start_time" in data or "end_time" in data:
        check_span(start, end)

    linked_wo_id = existing.get("work_order_id")
    snapshot = None
    relinked = False
    if "work_order_id" in data:
        raw_wo = _text(data.get("work_order_id"))
        if not raw_wo:
            updates["work_order_id"] = None
            updates["wo_number"] = None
            linked_wo_id = None
        else:
            snapshot = _load_snapshot(shop_db, shop, raw_wo)
            linked_wo_id = snapshot["work_order_id"]
            relinked = linked_wo_id != existing.get("work_order_id")
            updates["work_order_id"] = linked_wo_id
            updates["wo_number"] = snapshot.get("wo_number")

    body_customer = "customer_id" in data
    body_unit = "unit_id" in data
    identity_touched = body_customer or body_unit or relinked
    if linked_wo_id and identity_touched and (not snapshot or snapshot["work_order_id"] != linked_wo_id):
        from app.blueprints.calendar.services.work_orders import work_order_snapshot_for_event

        snapshot = work_order_snapshot_for_event(shop_db, shop, linked_wo_id)
    if linked_wo_id and snapshot and identity_touched:
        # Привязанная (после этого запроса) запись: клиент/юнит — по WO.
        updates.update(linked_identity_updates(existing, snapshot, data, relinked=relinked))
    elif body_customer or body_unit:
        # Свободная запись (или мёртвая ссылка на удалённый WO): активный клиент магазина и его юнит.
        customer_id, unit_id = existing.get("customer_id"), existing.get("unit_id")
        unit_raw = _text(data.get("unit_id")) if body_unit else (str(unit_id) if unit_id else "")
        if body_customer:
            customer_id, label = resolve_customer(shop_db, shop, data.get("customer_id"))
            updates["customer_id"], updates["customer_label"] = customer_id, label
            # Клиент сменился — прежний юнит должен ему принадлежать, иначе уходит.
            body_unit = body_unit or bool(unit_id)
        if body_unit:
            unit_id, label = resolve_unit(shop_db, shop, unit_raw, customer_id)
            updates["unit_id"], updates["unit_label"] = unit_id, label

    mechanics = parse_mechanics(data, shop)
    if mechanics is None and relinked and not event_mechanics(existing) and snapshot.get("mechanics"):
        mechanics = build_mechanics(snapshot["mechanics"], shop)
    if mechanics is not None:
        updates.update(mechanic_fields(mechanics))

    if "presets" in data:
        updates["presets"] = parse_presets(data.get("presets"))
    if "status" in data:
        updates["status"] = clean_status(shop_db, data.get("status"))
    if "title" in data:
        updates["title"] = clean_title(data.get("title"))
    elif relinked and not _text(existing.get("title")) and clean_title(snapshot.get("title")):
        updates["title"] = clean_title(snapshot.get("title"))

    updates["updated_at"] = utcnow()
    updates["updated_by"] = oid(user_id)
    return updates


# ── чтение / отдача ──────────────────────────────────────────

def filter_by_mechanic(rows: list[dict], raw) -> list[dict]:
    """mechanic_id=<id> — записи с этим механиком в бригаде; unassigned — без
    бригады; пусто — все."""
    wanted = _text(raw)
    if not wanted:
        return rows
    if wanted == "unassigned":
        return [r for r in rows if not event_mechanics(r)]
    return [r for r in rows if wanted in {str(m["id"]) for m in event_mechanics(r)}]


def load_events(shop_db, shop: dict, start_raw, end_raw, mechanic_raw=None) -> list[dict]:
    """Записи магазина с start_time в [start, end) по возрастанию start_time."""
    query: dict = {"shop_id": shop["_id"]}
    start, end = parse_range_param(start_raw), parse_range_param(end_raw)
    if start or end:
        query["start_time"] = {}
        if start:
            query["start_time"]["$gte"] = start
        if end:
            query["start_time"]["$lt"] = end
    rows = list(shop_db.calendar_events.find(query).sort("start_time", 1))
    return filter_by_mechanic(rows, mechanic_raw)


def refresh_labels(shop_db, shop: dict, rows: list[dict]) -> None:
    """Лейблы клиента/юнита — из живых документов (переименованный клиент
    виден новым именем); сохранённый лейбл — запас. Два $in. Мутирует rows."""
    customer_ids = {r["customer_id"] for r in rows if isinstance(r.get("customer_id"), ObjectId)}
    unit_ids = {r["unit_id"] for r in rows if isinstance(r.get("unit_id"), ObjectId)}
    customers = {
        c["_id"]: customer_label(c)
        for c in shop_db.customers.find({"_id": {"$in": list(customer_ids)}, "shop_id": shop["_id"]}, CUSTOMER_PROJECTION)
    } if customer_ids else {}
    units = {
        u["_id"]: unit_label(u)
        for u in shop_db.units.find({"_id": {"$in": list(unit_ids)}, "shop_id": shop["_id"]}, UNIT_PROJECTION)
    } if unit_ids else {}
    for r in rows:
        label = customers.get(r.get("customer_id"))
        if label:
            r["customer_label"] = label
        label = units.get(r.get("unit_id"))
        if label:
            r["unit_label"] = label


def serialize_event(doc: dict) -> dict:
    """Запись → payload API (данные WO добавляет enrich_linked_appointments)."""
    mechanics = [{"id": str(m["id"]), "name": m["name"]} for m in event_mechanics(doc)]
    return {
        "id": str(doc["_id"]),
        "kind": "appointment",
        "is_virtual": False,
        "editable": True,
        "title": str(doc.get("title") or ""),
        "start_time": naive_iso(doc.get("start_time")),
        "end_time": naive_iso(doc.get("end_time")),
        "status": str(doc.get("status") or "scheduled"),
        "customer_id": str(doc["customer_id"]) if doc.get("customer_id") else "",
        "customer_label": display_customer_label(doc),
        "unit_id": str(doc["unit_id"]) if doc.get("unit_id") else "",
        "unit_label": _text(doc.get("unit_label")),
        "mechanics": mechanics,
        "mechanic_ids": [m["id"] for m in mechanics],
        "mechanic_id": mechanics[0]["id"] if mechanics else "",
        "mechanic_name": ", ".join(m["name"] for m in mechanics),
        "presets": [
            {"id": str(p.get("id") or ""), "name": str(p.get("name") or "")}
            for p in (doc.get("presets") or [])
            if isinstance(p, dict) and p.get("id")
        ],
        "note": appointment_note(doc),
        "work_order_id": str(doc["work_order_id"]) if doc.get("work_order_id") else "",
        "wo_number": doc.get("wo_number"),
        "wo_state": "",
        "labor_summary": [],
    }


def event_label(doc: dict) -> str:
    """Подпись записи для журнала: заголовок, иначе клиент, иначе «Appointment»."""
    return _text(doc.get("title")) or display_customer_label(doc) or "Appointment"
