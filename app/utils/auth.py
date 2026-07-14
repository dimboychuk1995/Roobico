from __future__ import annotations

from functools import wraps
from flask import session, redirect, url_for, request

SESSION_USER_ID = "user_id"
SESSION_TENANT_ID = "tenant_id"
SESSION_TENANT_DB = "tenant_db_name"

# ✅ shop context
SESSION_SHOP_ID = "shop_id"          # active shop
SESSION_SHOP_IDS = "shop_ids"        # list of allowed shops (strings)


def login_user(
    user_id: str,
    tenant_id: str,
    tenant_db_name: str,
    shop_ids: list[str] | None = None,
    shop_id: str | None = None,
) -> None:
    """
    Stores the auth session + shop context.

    Rules:
      - shop_ids: list of allowed shop ObjectId strings (primary is shop_ids[0])
      - shop_id: active shop; if missing/invalid -> defaults to shop_ids[0]
    """
    session[SESSION_USER_ID] = str(user_id)
    session[SESSION_TENANT_ID] = str(tenant_id)
    session[SESSION_TENANT_DB] = tenant_db_name

    # Normalize shop ids
    if shop_ids is not None:
        # keep unique, keep order, cast to str
        seen = set()
        norm = []
        for x in shop_ids:
            sx = str(x)
            if sx in seen:
                continue
            seen.add(sx)
            norm.append(sx)
        session[SESSION_SHOP_IDS] = norm
    else:
        # if not provided, don't overwrite existing session value
        session.setdefault(SESSION_SHOP_IDS, [])

    allowed = session.get(SESSION_SHOP_IDS) or []

    # Decide active shop
    if shop_id is not None:
        sid = str(shop_id)
        if not allowed or sid in allowed:
            session[SESSION_SHOP_ID] = sid
        else:
            # invalid requested shop -> fallback to primary
            session[SESSION_SHOP_ID] = allowed[0] if allowed else None
    else:
        # no explicit shop_id -> keep existing if valid, else fallback
        current = session.get(SESSION_SHOP_ID)
        if current and (not allowed or str(current) in allowed):
            session[SESSION_SHOP_ID] = str(current)
        else:
            session[SESSION_SHOP_ID] = allowed[0] if allowed else None

    session.modified = True


def logout_user() -> None:
    # Полная очистка: выборочные pop'ы оставляли в сессии хвосты
    # (например, user_permissions), которым нечего делать после логаута.
    session.clear()
    session.modified = True


def is_logged_in() -> bool:
    return bool(session.get(SESSION_USER_ID)) and bool(session.get(SESSION_TENANT_ID))


def get_active_shop_id() -> str | None:
    """
    Returns active shop_id from session.
    Ensures it matches shop_ids if shop_ids is present.
    """
    allowed = session.get(SESSION_SHOP_IDS) or []
    sid = session.get(SESSION_SHOP_ID)
    if sid and (not allowed or str(sid) in allowed):
        return str(sid)
    if allowed:
        session[SESSION_SHOP_ID] = str(allowed[0])
        session.modified = True
        return str(allowed[0])
    return None


def get_allowed_shop_ids() -> list[str]:
    """
    Returns list of allowed shop ids (strings) from session.
    """
    allowed = session.get(SESSION_SHOP_IDS) or []
    return [str(x) for x in allowed]


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("main.index", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper
