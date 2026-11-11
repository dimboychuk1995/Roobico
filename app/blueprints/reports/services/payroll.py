"""Payroll периода — единый расчёт для Timecard / Salary и General Revenue.

Раньше отчёты считали зарплату двумя независимыми функциями, и они
разошлись: General Revenue пропускал КАЖДОГО uAttend-сотрудника с матчем на
внутреннего пользователя, даже почасового, — их часы × ставка не попадали
никуда, и отчёт показывал «Hourly (uAttend) $0». Теперь оба отчёта читают
результат :func:`compute_payroll`, поэтому суммы совпадают по построению.

Правила:
  * внутренний пользователь (не owner, активный) с pay_type=salary — строка
    «salary»: недельная ставка × недель в периоде;
  * выбранный (selected) активный uAttend-сотрудник — строка «hourly»:
    часы из /reports/punch × hourly_rate из Settings → Integrations;
  * матчинг uAttend ↔ внутренние пользователи (uattend_match) убирает
    двойной счёт: uAttend-строка скрывается ТОЛЬКО когда сотрудник заматчен
    на salary-пользователя с недельной ставкой > 0 (его часы показаны в
    salary-строке, платит salary). Матч на почасового пользователя
    (механика) или на salary-пользователя без ставки строку не скрывает —
    иначе зарплата человека не считается нигде.

Зарплата не читает тайм-логи работ (job timers) — только salary_amount и
punches uAttend.
"""
from __future__ import annotations

from datetime import date

from flask import current_app

from app.blueprints.work_orders.services.common import round2
from app.utils.user_names import user_display_name

_USER_PROJECTION = {
    "first_name": 1, "last_name": 1, "name": 1, "email": 1,
    "role": 1, "pay_type": 1, "salary_amount": 1,
}

_EMPLOYEE_PROJECTION = {
    "uattend_user_id": 1, "email": 1,
    "first_name": 1, "last_name": 1,
    "hourly_rate": 1, "selected": 1,
}


def weeks_in_period(date_from, date_to) -> float:
    """Недель в периоде: дни (включительно) ÷ 7. 0.0 — период не задан или
    задан некорректно (нет дат, не ISO, конец раньше начала)."""
    if not date_from or not date_to:
        return 0.0
    try:
        start = date.fromisoformat(str(date_from))
        end = date.fromisoformat(str(date_to))
    except (TypeError, ValueError):
        return 0.0
    if end < start:
        return 0.0
    return round(((end - start).days + 1) / 7.0, 4)


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_uid(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _internal_name(user: dict) -> str:
    return (user.get("name") or "").strip() or (
        f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
    )


def _uattend_name(emp: dict) -> str:
    return (
        f"{emp.get('first_name') or ''} {emp.get('last_name') or ''}".strip()
        or (emp.get("email") or "")
    )


def _is_salary(user: dict) -> bool:
    """pay_type не задан — считаем salary (так форма пользователя и создаёт)."""
    return (user.get("pay_type") or "salary").lower() == "salary"


def _weekly_salary(user: dict) -> float:
    return _to_float(user.get("salary_amount")) or 0.0


def _load_internal_users(master_db, tenant_values) -> list[dict]:
    values = list(tenant_values or [])
    if not values:
        return []
    return list(
        master_db.users.find(
            {
                "tenant_id": {"$in": values},
                "role": {"$ne": "owner"},
                "is_active": True,
            },
            _USER_PROJECTION,
        ).sort([("last_name", 1), ("first_name", 1)])
    )


def _match_map(shop_db, shop_id, internal_users, emp_rows) -> dict[int, dict]:
    """{uattend_user_id -> {internal_id, internal_name, internal_email,
    confidence}} для текущего состава (кэш по отпечатку состава)."""
    from app.utils.integrations import uattend_match

    internal_for_match = [
        {
            "internal_id": str(u.get("_id")),
            "name": _internal_name(u),
            "email": u.get("email") or "",
        }
        for u in internal_users
    ]
    uattend_for_match = [
        {
            "uattend_user_id": e.get("uattend_user_id"),
            "name": _uattend_name(e),
            "email": e.get("email") or "",
        }
        for e in emp_rows
    ]
    try:
        raw = uattend_match.get_match_map(
            shop_db, shop_id, internal_for_match, uattend_for_match
        )
    except Exception:  # noqa: BLE001 — отчёт важнее матчинга: считаем без него
        current_app.logger.exception("Payroll: uAttend employee match failed")
        return {}

    out: dict[int, dict] = {}
    for key, match in (raw or {}).items():
        uid = _to_uid(key)
        if uid is not None and isinstance(match, dict):
            out[uid] = match
    return out


def compute_payroll(
    master_db,
    shop_db,
    shop_id,
    tenant_values,
    date_from,
    date_to,
    *,
    refresh_hours: bool = False,
    debug: bool = False,
) -> dict:
    """Зарплата сотрудников магазина за период.

    ``refresh_hours`` — не читать кэш часов uAttend, сходить в API (страница
    Timecard всегда свежая и заодно прогревает кэш для General Revenue).
    ``debug`` — вернуть сырой запрос/ответ uAttend в ``debug_payload``.

    Возвращает::

        {
            "rows": [...],            # строки сотрудников (формат Timecard)
            "totals": {"salary", "hourly", "hours", "grand"},
            "weeks_in_period": float, # множитель salary; 1.0, если периода нет
            "has_period": bool,       # даты заданы и корректны
            "integration_status": "none" | "disabled" | "configured",
            "integration_error": str | None,  # часы uAttend не получены
            "debug_payload": {"request", "response"},
        }

    Строка: name, email, role, pay_type ("salary" | "hourly"), salary_amount,
    salary_weeks, hourly_rate, hours, total (None — нечего считать), note,
    ignored_hourly_rate (salary-строка: ставка uAttend-двойника, которая не
    оплачивается), matched_uattend_name / matched_internal_name /
    matched_internal_email / matched_confidence.
    """
    from app.utils.integrations.storage import get_integration
    from app.utils.integrations import uattend_hours

    weeks = weeks_in_period(date_from, date_to)
    has_period = weeks > 0
    # Без периода salary всё равно показываем — за одну неделю.
    salary_weeks = weeks if has_period else 1.0

    internal_users = _load_internal_users(master_db, tenant_values)

    integration_status = "none"
    integration_error = None
    debug_payload = {"request": None, "response": None}
    emp_rows: list[dict] = []
    hours_by_uid: dict[int, float] = {}
    match_map: dict[int, dict] = {}

    # shop_db None — активный магазин не выбран: считаем только salary.
    integration = get_integration(shop_db, shop_id, "uattend") if shop_db is not None else None
    if integration and not integration.get("enabled"):
        integration_status = "disabled"
    elif integration:
        integration_status = "configured"
        # Только сотрудники, отмеченные галочкой в Settings → Integrations.
        emp_rows = list(
            shop_db.uattend_employees.find(
                {"shop_id": shop_id, "is_active": True, "selected": True},
                _EMPLOYEE_PROJECTION,
            )
        )
        match_map = _match_map(shop_db, shop_id, internal_users, emp_rows)

        if has_period:
            loaded = uattend_hours.load_uattend_period_hours(
                shop_db, shop_id, str(date_from), str(date_to),
                refresh=refresh_hours or debug, with_raw=debug,
            )
            # Зарплата считается от точных часов; округлённые by_uid — запас
            # для ответа без by_uid_exact.
            exact = loaded.get("by_uid_exact")
            source = exact if isinstance(exact, dict) and exact else (loaded.get("by_uid") or {})
            for key, value in source.items():
                uid = _to_uid(key)
                hours = _to_float(value)
                if uid is not None and hours is not None:
                    hours_by_uid[uid] = hours
            integration_error = loaded.get("error") or None
            if debug and isinstance(loaded.get("raw"), dict):
                debug_payload = loaded["raw"]

    emp_by_uid: dict[int, dict] = {}
    for emp in emp_rows:
        uid = _to_uid(emp.get("uattend_user_id"))
        if uid is not None:
            emp_by_uid.setdefault(uid, emp)

    internal_by_id = {str(u.get("_id")): u for u in internal_users}
    # Salary «поглощает» uAttend-двойника, только когда salary реально
    # платится: пользователь без недельной ставки (pay_type по умолчанию —
    # salary) не должен прятать почасовую оплату двойника.
    absorbing_ids = {
        str(u.get("_id"))
        for u in internal_users
        if _is_salary(u) and _weekly_salary(u) > 0
    }

    # internal_id -> uAttend-двойник (для salary-строки: бейдж, часы, ставка).
    # Один двойник на пользователя: второй uAttend-сотрудник с матчем на
    # того же человека остаётся отдельной почасовой строкой.
    twin_by_internal_id: dict[str, dict] = {}
    for uid, match in match_map.items():
        internal_id = str((match or {}).get("internal_id") or "")
        emp = emp_by_uid.get(uid)
        if internal_id not in absorbing_ids or emp is None:
            continue
        twin_by_internal_id.setdefault(internal_id, {
            "uattend_user_id": uid,
            "uattend_name": _uattend_name(emp),
            "hours": hours_by_uid.get(uid),
            "rate": _to_float(emp.get("hourly_rate")),
        })
    absorbed_uids = {twin["uattend_user_id"] for twin in twin_by_internal_id.values()}

    rows: list[dict] = []
    totals = {"salary": 0.0, "hourly": 0.0, "hours": 0.0, "grand": 0.0}

    # ── Salary: внутренние пользователи с фиксированной недельной ставкой ──
    for user in internal_users:
        # Почасовых внутренних пользователей здесь нет: их платит uAttend,
        # строка — ниже, среди сотрудников uAttend.
        if not _is_salary(user):
            continue
        weekly = _weekly_salary(user)
        period_total = round2(weekly * salary_weeks)
        twin = twin_by_internal_id.get(str(user.get("_id"))) or {}
        ignored_rate = twin.get("rate") if (twin.get("rate") or 0) > 0 else None
        note = f"${weekly:,.2f}/wk × {salary_weeks:g} weeks" if weekly else ""
        if ignored_rate:
            note += (
                f" · uAttend rate ${ignored_rate:,.2f}/hr is not used — Pay type is Salary"
                " (set it to Hourly in Settings → Users to pay by punches)"
            )
        rows.append({
            "name": _internal_name(user) or "—",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
            "pay_type": "salary",
            "salary_amount": weekly,
            "salary_weeks": salary_weeks,
            "hourly_rate": twin.get("rate"),
            "hours": twin.get("hours"),
            "total": period_total,
            "note": note,
            "ignored_hourly_rate": ignored_rate,
            "matched_uattend_name": twin.get("uattend_name") or "",
        })
        totals["salary"] += period_total
        totals["grand"] += period_total
        if twin.get("hours"):
            totals["hours"] += twin["hours"]

    # ── Hourly: сотрудники uAttend ────────────────────────────────────────
    for emp in emp_rows:
        uid = _to_uid(emp.get("uattend_user_id"))
        # Двойник salary-пользователя уже учтён его salary-строкой.
        if uid is not None and uid in absorbed_uids:
            continue
        match = match_map.get(uid) if uid is not None else None
        matched_internal_id = str((match or {}).get("internal_id") or "")
        internal_user = internal_by_id.get(matched_internal_id) if matched_internal_id else None
        rate = _to_float(emp.get("hourly_rate"))
        hours = hours_by_uid.get(uid) if uid is not None else None
        row = {
            "name": _uattend_name(emp) or "—",
            "email": (emp.get("email") or "") or (internal_user or {}).get("email") or "",
            "role": (internal_user or {}).get("role") or "uAttend employee",
            "pay_type": "hourly",
            "salary_amount": None,
            "hourly_rate": rate,
            "hours": hours,
            "total": None,
            "note": "",
            # Бейдж «↔ Internal: …» — живое имя по internal_id; имя из кэша
            # матчинга — запас (кэш мог пережить переименование).
            "matched_internal_name": (
                (user_display_name(internal_user) if internal_user else "")
                or (match or {}).get("internal_name") or ""
            ),
            "matched_internal_email": (
                (internal_user or {}).get("email")
                or (match or {}).get("internal_email") or ""
            ),
            "matched_confidence": (match or {}).get("confidence") or 0,
        }
        if rate is None and hours is None:
            row["note"] = "Hourly rate not set · no punches in this period."
        elif rate is None:
            row["note"] = "Hourly rate not set in Integrations."
        elif hours is None:
            row["note"] = "No punches in this period."
        else:
            row["total"] = round2(rate * hours)
            totals["hourly"] += row["total"]
            totals["hours"] += hours
            totals["grand"] += row["total"]
        rows.append(row)

    return {
        "rows": rows,
        "totals": {
            "salary": round2(totals["salary"]),
            "hourly": round2(totals["hourly"]),
            "hours": round2(totals["hours"]),
            "grand": round2(totals["grand"]),
        },
        "weeks_in_period": salary_weeks,
        "has_period": has_period,
        "integration_status": integration_status,
        "integration_error": integration_error,
        "debug_payload": debug_payload,
    }
