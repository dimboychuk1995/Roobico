"""Разбор ответов uAttend /reports/punch в часы + кэшированная загрузка периода.

Парсеры терпимы к нескольким формам ответа (см. docstring'и функций) —
uAttend не документирует стабильную схему, поэтому всё best-effort.
`load_uattend_period_hours` — единая точка для дашборда: проверяет интеграцию,
берёт выбранных сотрудников, ходит в API через короткий кэш в shop-коллекции
``uattend_punch_cache`` (TTL-индекс, см. extensions), возвращает часы по дням
и по сотрудникам.
"""
from __future__ import annotations

from datetime import datetime, timezone


_AGGREGATE_HOUR_KEYS = (
    "TotalRegularHours", "RegularHours", "RegularTime",
    "TotalHours", "Hours",
)

_DATE_KEYS = ("Date", "PunchDate", "WorkDate", "DateWorked", "InDate", "PayDate")


def _hhmm_to_hours(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    if ":" in s:
        try:
            parts = s.split(":")
            return int(parts[0]) + (int(parts[1]) if len(parts) > 1 else 0) / 60.0
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _parse_day(value) -> str:
    """Дата из произвольного поля uAttend -> 'YYYY-MM-DD' или ''."""
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    dt = _parse_dt(s)
    if dt is not None:
        return dt.date().isoformat()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except (TypeError, ValueError):
            continue
    return ""


def _node_day(node: dict) -> str:
    for key in _DATE_KEYS:
        if node.get(key):
            day = _parse_day(node.get(key))
            if day:
                return day
    pi = _parse_dt(node.get("PunchIn"))
    if pi is not None:
        return pi.date().isoformat()
    return ""


def _hours_from_node(node: dict) -> float:
    # Prefer explicit aggregate fields, then time-range delta.
    for key in _AGGREGATE_HOUR_KEYS:
        if key in node and node.get(key) not in (None, ""):
            h = _hhmm_to_hours(node.get(key))
            if h > 0:
                return h
    pi = _parse_dt(node.get("PunchIn"))
    po = _parse_dt(node.get("PunchOut"))
    if pi and po and po > pi:
        return (po - pi).total_seconds() / 3600.0
    return 0.0


def _add_uid(totals: dict, uid_raw, hours: float) -> None:
    try:
        uid = int(uid_raw)
    except (TypeError, ValueError):
        return
    if hours and hours > 0:
        totals[uid] = totals.get(uid, 0.0) + hours


def aggregate_hours_by_user(report_response) -> dict[int, float]:
    """Сумма часов по UserId из ответа /reports/punch.

    Формы: Body.PunchReportLineItems[] с Tot; Users[] с тоталами/Workdays;
    плоский Punches[]; иначе — защитный рекурсивный обход.
    """
    totals: dict[int, float] = {}

    if isinstance(report_response, dict):
        body = report_response.get("Body")
        if isinstance(body, dict):
            items = body.get("PunchReportLineItems")
            if isinstance(items, list) and items:
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    h = _hhmm_to_hours(it.get("Tot"))
                    if h <= 0:
                        h = _hours_from_node(it)
                    _add_uid(totals, it.get("UserId"), h)
                if totals:
                    return totals

        users = report_response.get("Users")
        if isinstance(users, list) and users and all(isinstance(u, dict) for u in users):
            captured = False
            for u in users:
                uid = u.get("UserId") or u.get("Id")
                if uid is None:
                    continue
                h = _hours_from_node(u)
                if h <= 0:
                    for wd in u.get("Workdays") or []:
                        for pp in (wd.get("PunchPairs") if isinstance(wd, dict) else None) or []:
                            h += _hours_from_node(pp) if isinstance(pp, dict) else 0.0
                if h > 0:
                    _add_uid(totals, uid, h)
                    captured = True
            if captured:
                return totals

        punches = report_response.get("Punches")
        if isinstance(punches, list) and punches:
            for p in punches:
                if isinstance(p, dict):
                    _add_uid(totals, p.get("UserId"), _hours_from_node(p))
            if totals:
                return totals

    seen: set[int] = set()

    def walk(node, current_uid=None):
        if isinstance(node, dict):
            uid = node.get("UserId") or node.get("Id")
            if uid is not None:
                current_uid = uid
            h = _hours_from_node(node)
            if h > 0 and id(node) not in seen:
                seen.add(id(node))
                _add_uid(totals, current_uid, h)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v, current_uid)
        elif isinstance(node, list):
            for item in node:
                walk(item, current_uid)

    walk(report_response)
    return totals


def aggregate_hours_by_day(report_response) -> dict[str, float]:
    """Сумма часов по дням ('YYYY-MM-DD', все сотрудники вместе).

    Строка без распознаваемой даты пропускается — тоталы по сотрудникам всё
    равно считает :func:`aggregate_hours_by_user`, а график без даты точку
    поставить не может.
    """
    by_day: dict[str, float] = {}

    def add(day: str, hours: float) -> None:
        if day and hours and hours > 0:
            by_day[day] = by_day.get(day, 0.0) + hours

    if isinstance(report_response, dict):
        body = report_response.get("Body")
        if isinstance(body, dict):
            items = body.get("PunchReportLineItems")
            if isinstance(items, list) and items:
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    h = _hhmm_to_hours(it.get("Tot"))
                    if h <= 0:
                        h = _hours_from_node(it)
                    add(_node_day(it), h)
                if by_day:
                    return by_day

        users = report_response.get("Users")
        if isinstance(users, list) and users:
            for u in users:
                if not isinstance(u, dict):
                    continue
                for wd in u.get("Workdays") or []:
                    if not isinstance(wd, dict):
                        continue
                    day = _node_day(wd)
                    h = _hours_from_node(wd)
                    if h <= 0:
                        for pp in wd.get("PunchPairs") or []:
                            if isinstance(pp, dict):
                                add(day or _node_day(pp), _hours_from_node(pp))
                        continue
                    add(day, h)
            if by_day:
                return by_day

        punches = report_response.get("Punches")
        if isinstance(punches, list) and punches:
            for p in punches:
                if isinstance(p, dict):
                    add(_node_day(p), _hours_from_node(p))
            if by_day:
                return by_day

    seen: set[int] = set()

    def walk(node, current_day=""):
        if isinstance(node, dict):
            day = _node_day(node) or current_day
            h = _hours_from_node(node)
            if h > 0 and id(node) not in seen:
                seen.add(id(node))
                add(day, h)
            for v in node.values():
                if isinstance(v, (list, dict)):
                    walk(v, day)
        elif isinstance(node, list):
            for item in node:
                walk(item, current_day)

    walk(report_response)
    return by_day


def load_uattend_period_hours(shop_db, shop_id, date_from: str, date_to: str, exclude_uids=None) -> dict:
    """Часы uAttend за период для дашборда.

    ``exclude_uids`` — uAttend user id, которых не запрашиваем вовсе
    (например, сотрудники, заматченные на менеджеров: дашборд считает
    только механиков).

    Возвращает::

        {
            "connected": bool,      # интеграция настроена и включена
            "by_day":  {iso: h},    # часы по дням (все выбранные сотрудники)
            "by_uid":  {uid: h},    # часы по сотрудникам uAttend
            "error":   str | None,  # текст проблемы (ключ, сеть, ...)
        }

    Ответ API кэшируется в ``shop_db.uattend_punch_cache`` (чистится
    TTL-индексом): дашборд перезагружают часто, а uAttend режет частые запросы.
    """
    from flask import current_app

    from app.utils.integrations.storage import get_integration, get_decrypted_api_key
    from app.utils.integrations.uattend_client import UAttendClient, UAttendError

    out = {"connected": False, "by_day": {}, "by_uid": {}, "error": None}

    doc = get_integration(shop_db, shop_id, "uattend")
    if not doc or not doc.get("enabled"):
        return out
    out["connected"] = True

    if not date_from or not date_to:
        return out

    emp_rows = list(shop_db.uattend_employees.find(
        {"shop_id": shop_id, "is_active": True, "selected": True},
        {"uattend_user_id": 1},
    ))
    excluded = {int(u) for u in (exclude_uids or ())}
    uids = sorted(
        uid for uid in (
            int(r["uattend_user_id"]) for r in emp_rows
            if str(r.get("uattend_user_id") or "").strip().lstrip("-").isdigit()
        )
        if uid not in excluded
    )
    if not uids:
        return out

    cache_key = f"{date_from}|{date_to}|{','.join(map(str, uids))}"
    cached = shop_db.uattend_punch_cache.find_one({"shop_id": shop_id, "key": cache_key})
    if cached and isinstance(cached.get("by_uid"), dict):
        out["by_day"] = {str(k): float(v) for k, v in (cached.get("by_day") or {}).items()}
        out["by_uid"] = {int(k): float(v) for k, v in cached["by_uid"].items()}
        return out

    try:
        api_key = get_decrypted_api_key(shop_db, shop_id, "uattend")
    except Exception:
        current_app.logger.exception("uAttend: failed to decrypt API key for dashboard hours")
        out["error"] = "Could not decrypt the uAttend API key."
        return out
    if not api_key:
        out["error"] = "uAttend API key is not configured."
        return out

    try:
        report = UAttendClient(api_key).get_punches(date_from, date_to, user_ids=uids)
    except UAttendError as exc:
        current_app.logger.warning("uAttend punch report failed for dashboard hours: %s", exc)
        out["error"] = str(exc)
        return out

    out["by_day"] = {k: round(v, 2) for k, v in aggregate_hours_by_day(report).items()}
    out["by_uid"] = {k: round(v, 2) for k, v in aggregate_hours_by_user(report).items()}

    try:
        shop_db.uattend_punch_cache.update_one(
            {"shop_id": shop_id, "key": cache_key},
            {"$set": {
                "shop_id": shop_id,
                "key": cache_key,
                # Mongo-ключи не могут быть int — по дням ключи уже строки,
                # by_uid сериализуем строками и восстанавливаем при чтении.
                "by_day": out["by_day"],
                "by_uid": {str(k): v for k, v in out["by_uid"].items()},
                "fetched_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception:
        current_app.logger.exception("uAttend: failed to store punch cache")

    return out
