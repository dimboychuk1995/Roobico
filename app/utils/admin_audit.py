"""Admin audit log for sensitive admin-panel actions.

Every state-changing admin action (deactivate tenant/shop/user, plan change,
etc.) MUST be recorded here. Read paths do not log.

Stored in `master_db.admin_audit` so it lives alongside `admin_users` and is
never tied to any single tenant DB.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from flask import request

from app.extensions import get_master_db


def log_admin_action(
    admin: dict,
    action: str,
    target_type: str,
    target_id: Optional[ObjectId],
    *,
    before: Any = None,
    after: Any = None,
    extra: Optional[dict] = None,
) -> None:
    master = get_master_db()
    doc = {
        "admin_id": admin.get("_id") if admin else None,
        "admin_email": (admin or {}).get("email"),
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "before": before,
        "after": after,
        "ip": (request.headers.get("CF-Connecting-IP")
               or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
               or request.remote_addr),
        "user_agent": request.headers.get("User-Agent"),
        "created_at": datetime.utcnow(),
    }
    if extra:
        doc["extra"] = extra
    master.admin_audit.insert_one(doc)
