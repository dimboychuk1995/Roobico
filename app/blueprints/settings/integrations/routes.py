"""Settings → Integrations (3rd-party API connections, per shop)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from bson import ObjectId
from flask import jsonify, redirect, render_template, request, session, url_for, flash, current_app

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_USER_ID,
    SESSION_TENANT_ID,
    SESSION_SHOP_ID,
)
from app.utils.permissions import permission_required, filter_nav_items
from app.utils.layout import build_app_layout_context
from app.blueprints.main.routes import NAV_ITEMS

from app.utils.integrations.crypto import IntegrationsCryptoError
from app.utils.integrations.storage import (
    ensure_indexes,
    get_integration_public,
    get_decrypted_api_key,
    save_integration,
    delete_integration,
    update_test_result,
)
from app.utils.integrations.uattend_client import (
    UAttendClient,
    UAttendError,
)


# Providers supported in the UI. Adding a new one = extend this dict.
SUPPORTED_PROVIDERS = {
    "uattend": {
        "label": "uAttend",
        "tagline": "Time & attendance — pull employees, timecards, and punches.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _resolve_shop_db(master):
    """Return (shop_db, shop_oid) for the currently active shop, or (None, None)."""
    client = get_mongo_client()

    shop_id = _maybe_object_id(session.get(SESSION_SHOP_ID))
    if not shop_id:
        return None, None

    shop = master.shops.find_one({"_id": shop_id})
    if not shop:
        return None, None

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        return None, None

    return client[str(db_name)], shop_id


def _render_integrations_page(template_name: str, **ctx):
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")
    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))
    layout.update(ctx)
    return render_template(template_name, **layout)


def _json_error(message: str, status: int = 400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


# ─────────────────────────────────────────────────────────────────────────────
# Page: index
# ─────────────────────────────────────────────────────────────────────────────


@settings_bp.get("/integrations")
@login_required
@permission_required("settings.manage_integrations")
def integrations_index():
    master = get_master_db()
    shop_db, shop_oid = _resolve_shop_db(master)

    providers = []
    if shop_db is not None and shop_oid is not None:
        ensure_indexes(shop_db)
        for key, meta in SUPPORTED_PROVIDERS.items():
            state = get_integration_public(shop_db, shop_oid, key)
            providers.append({"key": key, **meta, "state": state})
    else:
        empty_state = {
            "configured": False, "enabled": False, "label": "",
            "api_key_masked": "",
            "last_sync_at": None, "last_sync_status": None, "last_sync_error": None,
            "last_tested_at": None, "last_test_status": None, "last_test_error": None,
        }
        for key, meta in SUPPORTED_PROVIDERS.items():
            providers.append({"key": key, **meta, "state": {"provider": key, **empty_state}})

    return _render_integrations_page(
        "public/settings/integrations.html",
        providers=providers,
        no_active_shop=(shop_db is None),
    )


# ─────────────────────────────────────────────────────────────────────────────
# JSON API: per-provider CRUD
# ─────────────────────────────────────────────────────────────────────────────


def _require_shop():
    master = get_master_db()
    shop_db, shop_oid = _resolve_shop_db(master)
    if shop_db is None or shop_oid is None:
        return None, None, _json_error("No active shop in session.", 400)
    ensure_indexes(shop_db)
    return shop_db, shop_oid, None


@settings_bp.get("/integrations/<provider>")
@login_required
@permission_required("settings.manage_integrations")
def integrations_get(provider: str):
    if provider not in SUPPORTED_PROVIDERS:
        return _json_error("Unknown integration provider.", 404)

    shop_db, shop_oid, err = _require_shop()
    if err is not None:
        return err

    state = get_integration_public(shop_db, shop_oid, provider)
    return jsonify({"ok": True, "provider": provider, "state": state})


@settings_bp.post("/integrations/<provider>")
@login_required
@permission_required("settings.manage_integrations")
def integrations_save(provider: str):
    if provider not in SUPPORTED_PROVIDERS:
        return _json_error("Unknown integration provider.", 404)

    shop_db, shop_oid, err = _require_shop()
    if err is not None:
        return err

    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip()
    label = data.get("label")
    enabled = data.get("enabled")

    actor = _maybe_object_id(session.get(SESSION_USER_ID))

    try:
        save_integration(
            shop_db,
            shop_oid,
            provider,
            api_key=api_key or None,
            label=label if label is not None else None,
            enabled=bool(enabled) if enabled is not None else None,
            actor_user_id=actor,
        )
    except IntegrationsCryptoError as exc:
        return _json_error(str(exc), 500)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    state = get_integration_public(shop_db, shop_oid, provider)
    return jsonify({"ok": True, "provider": provider, "state": state})


@settings_bp.delete("/integrations/<provider>")
@login_required
@permission_required("settings.manage_integrations")
def integrations_delete(provider: str):
    if provider not in SUPPORTED_PROVIDERS:
        return _json_error("Unknown integration provider.", 404)

    shop_db, shop_oid, err = _require_shop()
    if err is not None:
        return err

    delete_integration(shop_db, shop_oid, provider)
    state = get_integration_public(shop_db, shop_oid, provider)
    return jsonify({"ok": True, "provider": provider, "state": state})


@settings_bp.post("/integrations/<provider>/test")
@login_required
@permission_required("settings.manage_integrations")
def integrations_test(provider: str):
    if provider not in SUPPORTED_PROVIDERS:
        return _json_error("Unknown integration provider.", 404)

    shop_db, shop_oid, err = _require_shop()
    if err is not None:
        return err

    # Allow testing a key that has been typed in the form but not saved yet.
    data = request.get_json(silent=True) or {}
    candidate_key = (data.get("api_key") or "").strip()

    try:
        api_key = candidate_key or get_decrypted_api_key(shop_db, shop_oid, provider)
    except IntegrationsCryptoError as exc:
        return _json_error(str(exc), 500)

    if not api_key:
        return _json_error("No API key configured for this integration.", 400)

    if provider == "uattend":
        client = UAttendClient(api_key)
        try:
            client.ping()
        except UAttendError as exc:
            # Persist failure only if the key on file was tested (not an ad-hoc one).
            if not candidate_key:
                update_test_result(
                    shop_db, shop_oid, provider, ok=False, error=str(exc)
                )
            return jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "status": getattr(exc, "status", None),
                }
            ), 200

        if not candidate_key:
            update_test_result(shop_db, shop_oid, provider, ok=True)
        state = get_integration_public(shop_db, shop_oid, provider)
        return jsonify({"ok": True, "provider": provider, "state": state})

    return _json_error("Test not implemented for this provider.", 501)


# ─────────────────────────────────────────────────────────────────────────────
# uAttend: employees list (live fetch + upsert cache)
# ─────────────────────────────────────────────────────────────────────────────


# Possible keys uAttend uses for an employee's pay rate. The public docs don't
# guarantee any of these in the /user response, but the raw payload often
# includes one — we capture whichever is present.
_HOURLY_RATE_KEYS = (
    "HourlyRate", "WageRate", "PayRate", "RegularRate", "RegularPayRate",
    "hourly_rate", "wage_rate", "pay_rate", "regular_rate", "regular_pay_rate",
)


def _extract_hourly_rate(raw_user: dict) -> float | None:
    for key in _HOURLY_RATE_KEYS:
        if key in raw_user and raw_user[key] is not None:
            try:
                val = float(raw_user[key])
            except (TypeError, ValueError):
                continue
            if val >= 0:
                return val
    return None


# Cache freshness for the computed hourly rate (from /timecards).
_RATE_CACHE_TTL = timedelta(hours=24)


def _hhmm_to_hours(value) -> float:
    """Convert uAttend's HH:mm duration string to a float of hours."""
    if value is None:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0
    if ":" in s:
        try:
            h, m = s.split(":", 1)
            return int(h) + (int(m) / 60.0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _money_to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _compute_rate_from_timecard(tc_response: dict) -> float | None:
    """
    Compute effective hourly rate from a /timecards response.
        rate = RegularGrossPay / RegularTime (hours)
    Returns None if data isn't usable for this pay period.
    """
    if not isinstance(tc_response, dict):
        return None
    tc = tc_response.get("Timecard") or tc_response.get("timecard") or {}

    gross = _money_to_float(tc.get("RegularGrossPay"))
    hours = _hhmm_to_hours(tc.get("RegularTime"))
    if hours > 0 and gross > 0:
        return round(gross / hours, 4)

    # Fallback: scan individual PunchPairs across all workdays.
    for wd in tc.get("Workdays") or []:
        for pp in wd.get("PunchPairs") or []:
            pg = _money_to_float(pp.get("RegularGrossPay"))
            ph = _hhmm_to_hours(pp.get("RegularHours") or pp.get("RegularTime"))
            if ph > 0 and pg > 0:
                return round(pg / ph, 4)
    return None


def _sum_pay_and_hours(node) -> tuple[float, float]:
    """Walk a JSON structure and sum every (RegularGrossPay, RegularHours/RegularTime)
    pair found on the same dict. Used to aggregate /reports/punch responses.
    """
    total_gross = 0.0
    total_hours = 0.0
    if isinstance(node, dict):
        if "RegularGrossPay" in node and (
            "RegularHours" in node or "RegularTime" in node
        ):
            g = _money_to_float(node.get("RegularGrossPay"))
            h = _hhmm_to_hours(node.get("RegularHours") or node.get("RegularTime"))
            if h > 0 and g > 0:
                total_gross += g
                total_hours += h
        for v in node.values():
            if isinstance(v, (list, dict)):
                g, h = _sum_pay_and_hours(v)
                total_gross += g
                total_hours += h
    elif isinstance(node, list):
        for item in node:
            g, h = _sum_pay_and_hours(item)
            total_gross += g
            total_hours += h
    return total_gross, total_hours


def _compute_rate_for_user(client: "UAttendClient", uid: int) -> float | None:
    """Try several lookbacks until we find a pay period with usable data."""
    # 1) Current pay period.
    try:
        rate = _compute_rate_from_timecard(client.get_timecard(uid))
        if rate:
            return rate
    except UAttendError:
        pass

    # 2) Historical pay periods (uAttend picks the period containing Date).
    today = datetime.now(timezone.utc).date()
    for days_back in (14, 30, 60, 90):
        d = (today - timedelta(days=days_back)).isoformat()
        try:
            rate = _compute_rate_from_timecard(client.get_timecard(uid, date=d))
        except UAttendError:
            continue
        if rate:
            return rate

    # 3) Wide /reports/punch sweep — aggregate gross/hours across 90 days.
    end = today.isoformat()
    start = (today - timedelta(days=90)).isoformat()
    try:
        report = client.get_punches(start, end, user_ids=[uid])
    except UAttendError:
        return None
    gross, hours = _sum_pay_and_hours(report)
    if hours > 0 and gross > 0:
        return round(gross / hours, 4)
    return None


def _refresh_hourly_rates(
    shop_db,
    shop_oid: ObjectId,
    api_key: str,
    user_ids: list[int],
) -> dict[int, float | None]:
    """Fetch /timecards for any user without a fresh cached rate. Returns map uid → rate."""
    if not user_ids:
        return {}

    col = shop_db.uattend_employees
    cutoff = datetime.now(timezone.utc) - _RATE_CACHE_TTL
    cached_rows = list(
        col.find(
            {"shop_id": shop_oid, "uattend_user_id": {"$in": user_ids}},
            {"uattend_user_id": 1, "hourly_rate": 1, "hourly_rate_refreshed_at": 1},
        )
    )
    cached: dict[int, float | None] = {}
    needs_fetch: list[int] = []
    for r in cached_rows:
        uid = r["uattend_user_id"]
        rate = r.get("hourly_rate")
        refreshed = r.get("hourly_rate_refreshed_at")
        if rate is not None and refreshed and refreshed >= cutoff:
            cached[uid] = float(rate)
        else:
            needs_fetch.append(uid)
    # User ids missing from DB entirely (shouldn't happen — we just upserted).
    for uid in user_ids:
        if uid not in cached and uid not in needs_fetch:
            needs_fetch.append(uid)

    if not needs_fetch:
        return cached

    client = UAttendClient(api_key)

    def fetch_one(uid: int) -> tuple[int, float | None]:
        try:
            return uid, _compute_rate_for_user(client, uid)
        except Exception as exc:  # noqa: BLE001 — never break the whole batch
            current_app.logger.warning(
                "uAttend: rate fetch failed for user %s: %s", uid, exc
            )
            return uid, None

    now = datetime.now(timezone.utc)
    results: dict[int, float | None] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for uid, rate in pool.map(fetch_one, needs_fetch):
            results[uid] = rate
            update_doc = {"hourly_rate_refreshed_at": now}
            if rate is not None:
                update_doc["hourly_rate"] = rate
            col.update_one(
                {"shop_id": shop_oid, "uattend_user_id": uid},
                {"$set": update_doc},
            )

    return {**cached, **results}


def _upsert_uattend_employees(shop_db, shop_oid: ObjectId, users: list[dict]) -> None:
    """Upsert into shop_db.uattend_employees keyed by (shop_id, uattend_user_id).

    Preserves the per-shop ``selected`` flag and ``mapped_user_id``.
    """
    col = shop_db.uattend_employees
    try:
        col.create_index(
            [("shop_id", 1), ("uattend_user_id", 1)],
            unique=True,
            name="uniq_uattend_employees_shop_user",
        )
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    seen_ids: list[int] = []
    for u in users or []:
        uid = u.get("UserId")
        if uid is None:
            continue
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        seen_ids.append(uid)

        set_doc = {
            "first_name": (u.get("FirstName") or "").strip(),
            "last_name": (u.get("LastName") or "").strip(),
            "email": (u.get("Email") or "").strip(),
            "role": u.get("Role"),
            "department_id": u.get("DepartmentId"),
            "is_active": bool(u.get("IsActive", True)),
            "payroll_number": (u.get("PayrollNumber") or "").strip(),
            "phone_number": (u.get("PhoneNumber") or "").strip(),
            "employee_reference_id": (u.get("EmployeeReferenceID") or "").strip(),
            "synced_at": now,
        }
        rate = _extract_hourly_rate(u)
        if rate is not None:
            set_doc["hourly_rate"] = rate

        col.update_one(
            {"shop_id": shop_oid, "uattend_user_id": uid},
            {
                "$set": set_doc,
                "$setOnInsert": {
                    "shop_id": shop_oid,
                    "uattend_user_id": uid,
                    "mapped_user_id": None,
                    "selected": False,
                    "created_at": now,
                },
            },
            upsert=True,
        )

    # Mark missing employees as inactive (soft delete, keeps mappings/selection).
    if seen_ids:
        col.update_many(
            {
                "shop_id": shop_oid,
                "uattend_user_id": {"$nin": seen_ids},
                "is_active": True,
            },
            {"$set": {"is_active": False, "synced_at": now}},
        )


@settings_bp.get("/integrations/uattend/employees")
@login_required
@permission_required("settings.manage_integrations")
def integrations_uattend_employees():
    shop_db, shop_oid, err = _require_shop()
    if err is not None:
        return err

    try:
        api_key = get_decrypted_api_key(shop_db, shop_oid, "uattend")
    except IntegrationsCryptoError as exc:
        return _json_error(str(exc), 500)

    if not api_key:
        return _json_error("uAttend integration is not configured for this shop.", 400)

    client = UAttendClient(api_key)
    try:
        resp = client.get_users()
    except UAttendError as exc:
        return jsonify(
            {"ok": False, "error": str(exc), "status": getattr(exc, "status", None)}
        ), 200

    users = resp.get("Users") if isinstance(resp, dict) else None
    if not isinstance(users, list):
        users = []

    # Only active employees are pulled — inactive ones in uAttend are ignored.
    users = [u for u in users if bool(u.get("IsActive", True))]

    _upsert_uattend_employees(shop_db, shop_oid, users)

    user_ids = [int(u["UserId"]) for u in users if u.get("UserId") is not None]

    # Merge live data with persisted per-shop flags (selected / mapping / manual rate).
    persisted_by_uid = {
        d["uattend_user_id"]: d
        for d in shop_db.uattend_employees.find(
            {"shop_id": shop_oid, "uattend_user_id": {"$in": user_ids}},
            {"uattend_user_id": 1, "selected": 1, "mapped_user_id": 1, "hourly_rate": 1},
        )
    }

    out = []
    for u in users:
        try:
            uid = int(u.get("UserId"))
        except (TypeError, ValueError):
            continue
        persisted = persisted_by_uid.get(uid, {})
        out.append(
            {
                "user_id": uid,
                "first_name": (u.get("FirstName") or "").strip(),
                "last_name": (u.get("LastName") or "").strip(),
                "email": (u.get("Email") or "").strip(),
                "role": u.get("Role") or "",
                "department_id": u.get("DepartmentId"),
                "is_active": bool(u.get("IsActive", True)),
                "payroll_number": (u.get("PayrollNumber") or "").strip(),
                "hourly_rate": persisted.get("hourly_rate"),
                "selected": bool(persisted.get("selected", False)),
            }
        )

    # Sort: by last/first name (all rows are active).
    out.sort(key=lambda r: (r["last_name"].lower(), r["first_name"].lower()))

    return jsonify(
        {
            "ok": True,
            "count": len(out),
            "selected_count": sum(1 for r in out if r["selected"]),
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "employees": out,
        }
    )


@settings_bp.post("/integrations/uattend/employees/selection")
@login_required
@permission_required("settings.manage_integrations")
def integrations_uattend_employees_selection():
    """Persist which employees we want to pull time for. Payload: {user_ids: [...]}.

    The list is the *full* set of selected user ids — anything not in it is
    cleared. This makes the UI idempotent and simple (single save click).
    """
    shop_db, shop_oid, err = _require_shop()
    if err is not None:
        return err

    data = request.get_json(silent=True) or {}
    raw_ids = data.get("user_ids") or []
    if not isinstance(raw_ids, list):
        return _json_error("user_ids must be a list.", 400)

    selected_ids: list[int] = []
    for x in raw_ids:
        try:
            selected_ids.append(int(x))
        except (TypeError, ValueError):
            continue

    # Optional: per-employee hourly rate map { user_id: rate | null }.
    raw_rates = data.get("hourly_rates") or {}
    rates: dict[int, float | None] = {}
    if isinstance(raw_rates, dict):
        for k, v in raw_rates.items():
            try:
                uid = int(k)
            except (TypeError, ValueError):
                continue
            if v is None or v == "":
                rates[uid] = None
                continue
            try:
                rate = float(v)
            except (TypeError, ValueError):
                continue
            if rate < 0:
                continue
            rates[uid] = round(rate, 4)

    col = shop_db.uattend_employees
    now = datetime.now(timezone.utc)

    if selected_ids:
        col.update_many(
            {"shop_id": shop_oid, "uattend_user_id": {"$in": selected_ids}},
            {"$set": {"selected": True, "selection_updated_at": now}},
        )
    col.update_many(
        {"shop_id": shop_oid, "uattend_user_id": {"$nin": selected_ids}},
        {"$set": {"selected": False, "selection_updated_at": now}},
    )

    # Persist per-employee rates (one-by-one — small N).
    for uid, rate in rates.items():
        if rate is None:
            col.update_one(
                {"shop_id": shop_oid, "uattend_user_id": uid},
                {"$unset": {"hourly_rate": ""}, "$set": {"hourly_rate_updated_at": now}},
            )
        else:
            col.update_one(
                {"shop_id": shop_oid, "uattend_user_id": uid},
                {"$set": {"hourly_rate": rate, "hourly_rate_updated_at": now}},
            )

    return jsonify({"ok": True, "selected_count": len(selected_ids)})
