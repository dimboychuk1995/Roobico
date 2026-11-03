"""
Предзаполнение WO из записи календаря (appointment → work order).

Механик нажимает «Start work» в записи (мобильное приложение, веб-страница
механика) — WO создаётся сразу с тем, что менеджер уже внёс в запись:

  * пресеты записи → строки работ через ту же ценовую логику, что и у
    пресета, применённого механиком руками (build_mechanic_labors_payload:
    часы/ставка пресета, закреплённые цены партов, misc-чарджи каталога);
  * бригада записи → assigned_mechanics каждой строки без назначений,
    доли поровну в сумме 100 (первый получает остаток от деления);
  * заметка записи (title, если это не просто имя клиента) → описание
    проблемы первой строки; без пресетов заметка становится самой строкой
    работы — как на вебе, где note из календаря уходит в описание работы.

Пресеты/бригада/заметка применяются только к тому юниту, на который
сделана запись (или если юнит в записи не указан) — иначе запись лишь
привязывается к WO (link_event_to_work_order в роуте).

Предзаполнение — best effort: любая ошибка логируется, функции возвращают
безопасное значение, и WO создаётся как без записи. Создание WO из-за
проблем предзаполнения не падает никогда.
"""
from __future__ import annotations

import math

from flask import current_app

from app.blueprints.work_orders.services.common import oid, round2
from app.blueprints.work_orders.services.lookups import (
    get_assignable_mechanics,
    normalize_assigned_mechanics,
)
from app.blueprints.work_orders.services.mechanic_editor import build_mechanic_labors_payload

# Лимиты текста из записи: описание проблемы и строка работы.
NOTE_MAX_LEN = 4000
JOB_LINE_MAX_LEN = 200


def _event_mechanics(doc: dict) -> list[dict]:
    """
    Механики записи: новый список mechanics[{id,name}], иначе legacy
    mechanic_id/mechanic_name. Тот же приоритет, что у
    calendar.routes._event_mechanics (локальная копия, чтобы сервис не
    зависел от модуля роутов календаря). id — строкой.
    """
    raw = doc.get("mechanics")
    if isinstance(raw, list) and raw:
        return [
            {"id": str(m["id"]), "name": str(m.get("name") or "").strip() or "Mechanic"}
            for m in raw
            if isinstance(m, dict) and m.get("id")
        ]
    if doc.get("mechanic_id"):
        return [{
            "id": str(doc["mechanic_id"]),
            "name": str(doc.get("mechanic_name") or "").strip() or "Mechanic",
        }]
    return []


def appointment_note(doc: dict) -> str:
    """
    Заметка записи. title записи дублирует customer_label, когда менеджер
    ничего не написал (так создаёт запись календарь), поэтому заметкой
    считается только непустой title, отличный от лейбла клиента.
    """
    title = str(doc.get("title") or "").strip()
    if not title or title == str(doc.get("customer_label") or "").strip():
        return ""
    return title


def load_appointment(shop_db, shop: dict, event_id) -> dict | None:
    """
    Запись календаря этого магазина для предзаполнения WO:
      {event: документ, presets: [{id: str, name}], mechanics: [{id: str, name}],
       note: заметка или ""}.
    None — записи нет (чужая / удалена / кривой id) или она не прочиталась.
    """
    eid = oid(event_id)
    if shop_db is None or not shop or not eid:
        return None
    try:
        doc = shop_db.calendar_events.find_one({"_id": eid, "shop_id": shop["_id"]})
    except Exception:
        current_app.logger.exception("Appointment prefill: failed to load calendar event %s", eid)
        return None
    if not doc:
        return None

    presets = [
        {"id": str(p.get("id")).strip(), "name": str(p.get("name") or "").strip()}
        for p in (doc.get("presets") or [])
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    ]
    return {
        "event": doc,
        "presets": presets,
        "mechanics": _event_mechanics(doc),
        "note": appointment_note(doc),
    }


def appointment_prefill_applies(appointment: dict | None, unit_id) -> bool:
    """
    Пресеты/бригада/заметка записи переносятся в WO, только если запись
    сделана на этот юнит или без юнита. Запись на другой юнит к WO всё
    равно привязывается — но её содержимое не трогаем.
    """
    if not appointment:
        return False
    event = appointment.get("event") or {}
    event_unit = oid(event.get("unit_id"))
    if not event_unit:
        return True
    if oid(unit_id) == event_unit:
        return True
    current_app.logger.info(
        "Appointment prefill: event %s is for unit %s, work order is for unit %s — linking only",
        event.get("_id"), event_unit, unit_id,
    )
    return False


def _preset_part_qty(value) -> int:
    """
    qty парта пресета → целое ≥ 1. В пресете qty хранится как float (1.0):
    i32 его не парсит, а строка с qty 0 выпадает из тоталов и склада.
    """
    try:
        qty = float(value)
    except (TypeError, ValueError):
        return 1
    if math.isnan(qty) or math.isinf(qty):
        return 1
    return max(1, int(round(qty)))


def _preset_part_raw(part: dict) -> dict:
    """Строка парта пресета в формате, который механик шлёт с клиента:
    парт + qty, деньги проставит сервер (_autofill_part)."""
    return {
        "part_id": str(part.get("part_id") or "").strip(),
        "part_number": str(part.get("part_number") or "").strip(),
        "description": str(part.get("description") or "").strip(),
        "qty": _preset_part_qty(part.get("qty")),
    }


def appointment_preset_labors(shop_db, shop: dict, customer_id, presets) -> list[dict]:
    """
    Строки работ из пресетов записи — payload для compute_labors_and_totals.

    Каждый пресет — одна работа: description пресета (иначе name),
    preset_id и парты пресета; часы/ставку/закреплённые цены/misc каталога
    проставляет build_mechanic_labors_payload — та же реализация, что и для
    пресета, применённого механиком в приложении. Отсутствующие и
    неактивные пресеты пропускаются. Ошибка → [] (WO создаётся без них).
    """
    try:
        raw_labors: list[dict] = []
        for item in presets or []:
            pid = oid(item.get("id") if isinstance(item, dict) else item)
            if not pid:
                continue
            doc = shop_db.wo_presets.find_one(
                {"_id": pid, "shop_id": shop["_id"], "is_active": True},
                {"name": 1, "description": 1, "parts": 1},
            )
            if not doc:
                current_app.logger.info(
                    "Appointment prefill: preset %s is missing or inactive, skipped", pid
                )
                continue
            description = (
                str(doc.get("description") or "").strip()
                or str(doc.get("name") or "").strip()
            )
            raw_labors.append({
                "description": description,
                "preset_id": str(doc["_id"]),
                "issue_description": "",
                "parts": [
                    _preset_part_raw(p) for p in (doc.get("parts") or []) if isinstance(p, dict)
                ],
            })
        if not raw_labors:
            return []
        return build_mechanic_labors_payload(shop_db, shop, customer_id, raw_labors)
    except Exception:
        current_app.logger.exception("Appointment prefill: failed to build labors from presets")
        return []


def crew_shares(count: int) -> list[float]:
    """
    Доли бригады поровну, в сумме ровно 100: база = floor(10000/n)/100,
    первый получает остаток (3 механика → 33.34 / 33.33 / 33.33).
    """
    if count <= 0:
        return []
    if count == 1:
        return [100.0]
    base = math.floor(10000 / count) / 100.0
    first = round2(100.0 - base * (count - 1))
    return [first] + [base] * (count - 1)


def crew_assignments(shop: dict, mechanics) -> list[dict]:
    """
    Бригада записи → assigned_mechanics строки WO (нормализованные:
    ObjectId user_id, name/role из списка назначаемых механиков магазина,
    percent). Неизвестные и неактивные механики отбрасываются, доли
    делятся между оставшимися. Ошибка → [] (строки остаются без назначений).
    """
    try:
        ids: list[str] = []
        for m in mechanics or []:
            mid = str((m.get("id") if isinstance(m, dict) else m) or "").strip()
            if mid and mid not in ids:
                ids.append(mid)
        if not ids:
            return []

        mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
        known = [mid for mid in ids if mid in mechanics_by_id]
        dropped = [mid for mid in ids if mid not in mechanics_by_id]
        if dropped:
            current_app.logger.info(
                "Appointment prefill: crew members %s are not assignable mechanics of shop %s, skipped",
                dropped, shop.get("_id"),
            )
        if not known:
            return []

        raw = [
            {"user_id": mid, "percent": share}
            for mid, share in zip(known, crew_shares(len(known)))
        ]
        return normalize_assigned_mechanics(raw, mechanics_by_id)
    except Exception:
        current_app.logger.exception("Appointment prefill: failed to build crew assignments")
        return []


def _has_issue_description(labors) -> bool:
    return any(
        str(labor.get("issue_description") or "").strip()
        for labor in labors
        if isinstance(labor, dict)
    )


def apply_appointment_defaults(
    labors_payload,
    assignments,
    note,
    set_note: bool = True,
    *,
    shop_db=None,
    shop: dict | None = None,
    customer_id=None,
) -> list[dict]:
    """
    Бригада и заметка записи поверх payload работ (для compute_labors_and_totals):

      * каждая строка без assigned_mechanics получает копию assignments;
      * set_note и есть заметка, и ни у одной строки нет описания проблемы —
        заметка становится issue_description первой строки (если клиент уже
        не сделал её названием этой строки — тогда не дублируем);
      * строк нет вообще, но есть заметка — заметка становится самой строкой
        работы (нужны shop_db/shop/customer_id для автозаполнения через
        build_mechanic_labors_payload); в описание проблемы она тогда не
        дублируется — кроме заметки длиннее JOB_LINE_MAX_LEN: строка
        получает её начало, а полный текст (до NOTE_MAX_LEN) ложится в
        issue_description этой строки, чтобы ничего не потерялось.

    Мутирует словари строк на месте; возвращает список для расчёта.
    Ошибка → исходный список как есть.
    """
    labors = list(labors_payload) if isinstance(labors_payload, list) else []
    note = str(note or "").strip()
    try:
        if not any(isinstance(labor, dict) for labor in labors):
            if not note or shop_db is None or not shop:
                return labors
            # Заметка длиннее строки работы: строка получает начало, полный
            # текст остаётся в описании проблемы — ничего не теряется.
            # Короткая заметка в описание проблемы не дублируется.
            labors = build_mechanic_labors_payload(
                shop_db, shop, customer_id,
                [{
                    "description": note[:JOB_LINE_MAX_LEN],
                    "issue_description": note[:NOTE_MAX_LEN] if len(note) > JOB_LINE_MAX_LEN else "",
                    "parts": [],
                }],
            )
            set_note = False

        if assignments:
            for labor in labors:
                if isinstance(labor, dict) and not labor.get("assigned_mechanics"):
                    labor["assigned_mechanics"] = [dict(a) for a in assignments]

        if set_note and note and not _has_issue_description(labors):
            first = next(labor for labor in labors if isinstance(labor, dict))
            # Офисная форма без пресетов делает заметку строкой работы —
            # второй раз в описание проблемы её не кладём.
            if str(first.get("description") or "").strip().casefold() != note.casefold():
                first["issue_description"] = note[:NOTE_MAX_LEN]
        return labors
    except Exception:
        current_app.logger.exception("Appointment prefill: failed to apply crew/note defaults")
        return labors
