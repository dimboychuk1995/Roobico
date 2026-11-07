"""
Настройки календаря магазина: статусы записей и рабочие часы сетки.

Хранятся в shop-коллекции calendar_settings двумя документами:
  {key: "statuses", statuses: [{key, label, color}, ...]}
  {key: "hours", start_hour: int, end_hour: int}
Без документов действуют дефолты (APPOINTMENT_STATUSES, 06:00–20:00).

Системные статусы (SYSTEM_STATUS_KEYS) можно переименовать и перекрасить,
но не удалить: на них держится синк записей с work orders
(scheduled / in_progress / completed) и правило «отменённую не трогаем».
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

APPOINTMENT_STATUSES = [
    {"key": "scheduled", "label": "Scheduled", "color": "#1a73e8"},
    {"key": "confirmed", "label": "Confirmed", "color": "#0d904f"},
    {"key": "in_progress", "label": "In Progress", "color": "#e8710a"},
    {"key": "completed", "label": "Completed", "color": "#5f6368"},
    {"key": "cancelled", "label": "Cancelled", "color": "#d93025"},
]

SYSTEM_STATUS_KEYS = ("scheduled", "in_progress", "completed", "cancelled")

DEFAULT_START_HOUR = 6
DEFAULT_END_HOUR = 20
SLOT_MINUTES = 15
DEFAULT_COLOR = "#888888"

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify_key(raw) -> str:
    """Ключ статуса: нижний регистр, всё кроме [a-z0-9_] → '_'."""
    return re.sub(r"[^a-z0-9_]", "_", str(raw or "").strip().lower())


def get_statuses(shop_db) -> list[dict]:
    """Статусы записей магазина или дефолтные."""
    if shop_db is None:
        return [dict(s) for s in APPOINTMENT_STATUSES]
    doc = shop_db.calendar_settings.find_one({"key": "statuses"}, {"statuses": 1})
    if doc and isinstance(doc.get("statuses"), list) and doc["statuses"]:
        return [
            {
                "key": str(s.get("key") or ""),
                "label": str(s.get("label") or s.get("key") or ""),
                "color": str(s.get("color") or DEFAULT_COLOR),
            }
            for s in doc["statuses"]
            if isinstance(s, dict) and s.get("key")
        ] or [dict(s) for s in APPOINTMENT_STATUSES]
    return [dict(s) for s in APPOINTMENT_STATUSES]


def status_keys(shop_db) -> set[str]:
    return {s["key"] for s in get_statuses(shop_db)}


def get_hours(shop_db) -> dict:
    """{start_hour, end_hour} сетки; документ с кривыми числами → дефолты."""
    start, end = DEFAULT_START_HOUR, DEFAULT_END_HOUR
    if shop_db is not None:
        doc = shop_db.calendar_settings.find_one({"key": "hours"}, {"start_hour": 1, "end_hour": 1})
        if doc:
            parsed, _ = clean_hours(doc.get("start_hour"), doc.get("end_hour"))
            if parsed:
                start, end = parsed
    return {"start_hour": start, "end_hour": end}


def get_settings(shop_db) -> dict:
    """Полный объект настроек календаря (ответ GET/PUT /calendar/api/settings)."""
    return {
        "statuses": get_statuses(shop_db),
        **get_hours(shop_db),
        "slot_minutes": SLOT_MINUTES,
    }


def clean_statuses(items) -> tuple[list[dict] | None, str | None]:
    """
    Проверить и нормализовать список статусов из запроса → (clean, None)
    или (None, текст ошибки для 400).
    """
    if not isinstance(items, list) or not items:
        return None, "At least one status is required"
    clean: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        key = slugify_key(item.get("key") or label)
        if not label:
            return None, "Each status needs a label"
        if not key:
            return None, f"Status '{label}' needs a key"
        if key in seen:
            return None, f"Duplicate status key: {key}"
        seen.add(key)
        color = str(item.get("color") or "").strip()
        if not _COLOR_RE.match(color):
            color = DEFAULT_COLOR
        clean.append({"key": key, "label": label, "color": color.lower()})
    if not clean:
        return None, "At least one status is required"
    missing = [key for key in SYSTEM_STATUS_KEYS if key not in seen]
    if missing:
        return None, "System statuses cannot be removed: " + ", ".join(missing)
    return clean, None


def _as_hour(value):
    """Целый час 0..24 из int/строки; None — не число (bool не считается)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def clean_hours(start, end) -> tuple[tuple[int, int] | None, str | None]:
    """Рабочие часы сетки: целые, 0 <= start < end <= 24."""
    start_h, end_h = _as_hour(start), _as_hour(end)
    if start_h is None or end_h is None or not (0 <= start_h < end_h <= 24):
        return None, "Working hours must be whole hours from 0 to 24 with the start before the end"
    return (start_h, end_h), None


def save_settings(shop_db, payload: dict) -> tuple[dict | None, str | None]:
    """
    Сохранить то, что пришло (statuses и/или start_hour/end_hour): ключи,
    которых нет в payload, не трогаем. Возвращает (полные настройки, None)
    или (None, ошибка для 400). Валидация — до любой записи.
    """
    payload = payload if isinstance(payload, dict) else {}
    clean_list = None
    hours = None

    if "statuses" in payload:
        clean_list, error = clean_statuses(payload.get("statuses"))
        if error:
            return None, error

    if "start_hour" in payload or "end_hour" in payload:
        current = get_hours(shop_db)
        start = payload.get("start_hour", current["start_hour"])
        end = payload.get("end_hour", current["end_hour"])
        hours, error = clean_hours(start, end)
        if error:
            return None, error

    if clean_list is None and hours is None:
        return None, "Nothing to save"

    now = _utcnow()
    if clean_list is not None:
        shop_db.calendar_settings.update_one(
            {"key": "statuses"},
            {"$set": {"statuses": clean_list, "updated_at": now}},
            upsert=True,
        )
    if hours is not None:
        shop_db.calendar_settings.update_one(
            {"key": "hours"},
            {"$set": {"start_hour": hours[0], "end_hour": hours[1], "updated_at": now}},
            upsert=True,
        )
    return get_settings(shop_db), None
