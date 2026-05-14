"""Admin-panel auth helpers.

Admin users live in `master_db.admin_users` and are completely isolated
from tenant users (different collection, different session cookie — see
`app.utils.sessions.HostAwareSessionInterface`).
"""
from __future__ import annotations

from functools import wraps

from bson import ObjectId
from flask import session, redirect, url_for, request, g

from app.extensions import get_master_db


SESSION_ADMIN_USER_ID = "admin_user_id"
SESSION_ADMIN_EMAIL = "admin_email"


def admin_login(user_doc: dict) -> None:
    session[SESSION_ADMIN_USER_ID] = str(user_doc["_id"])
    session[SESSION_ADMIN_EMAIL] = user_doc.get("email", "")
    session.modified = True


def admin_logout() -> None:
    session.pop(SESSION_ADMIN_USER_ID, None)
    session.pop(SESSION_ADMIN_EMAIL, None)
    session.modified = True


def get_current_admin() -> dict | None:
    """Loads (and caches per-request) the current admin user doc, or None."""
    cached = getattr(g, "_admin_user", None)
    if cached is not None:
        return cached or None  # `False` sentinel = explicitly absent

    raw_id = session.get(SESSION_ADMIN_USER_ID)
    if not raw_id:
        g._admin_user = False
        return None

    try:
        oid = ObjectId(str(raw_id))
    except Exception:
        admin_logout()
        g._admin_user = False
        return None

    master = get_master_db()
    user = master.admin_users.find_one({"_id": oid, "is_active": True})
    if not user:
        admin_logout()
        g._admin_user = False
        return None

    g._admin_user = user
    return user


def admin_required(view_func):
    """Decorator: 302 to /admin/login when there is no admin session."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not get_current_admin():
            return redirect(url_for("admin_panel.login_page", next=request.path))
        return view_func(*args, **kwargs)

    return wrapper
