"""Матчинг uAttend-сотрудников с внутренними юзерами через кэш по составу.

Ключ кэша (``employee_matcher.cache_key``) — sha1-отпечаток обоих списков
(id, имена, email), поэтому любое добавление/изменение сотрудника с любой
стороны меняет ключ: чтение по текущему ключу промахивается и матчинг
пересчитывается автоматически. Отдельная инвалидация при редактировании
юзеров не нужна. Документы устаревших составов остаются в
``uattend_match_cache`` и просто не читаются.

Пересчёт зовёт OpenAI не чаще одного раза на состав (результат кэшируется,
включая детерминированный фолбэк при недоступном AI).
"""
from __future__ import annotations

import logging

from app.utils.employee_matcher import cache_key, match_employees

logger = logging.getLogger(__name__)


def build_internal_for_match(master_db, tenant_values) -> list[dict]:
    """Активные юзеры тенанта без владельца — в формате матчера."""
    out = []
    for u in master_db.users.find(
        {
            "tenant_id": {"$in": list(tenant_values or [])},
            "role": {"$ne": "owner"},
            "is_active": True,
        },
        {"first_name": 1, "last_name": 1, "name": 1, "email": 1},
    ):
        name = (u.get("name") or "").strip() or (
            f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
        )
        out.append({
            "internal_id": str(u["_id"]),
            "name": name,
            "email": u.get("email") or "",
        })
    return out


def build_uattend_for_match(shop_db, shop_id) -> list[dict]:
    """Выбранные активные uAttend-сотрудники — в формате матчера."""
    out = []
    for e in shop_db.uattend_employees.find(
        {"shop_id": shop_id, "is_active": True, "selected": True},
        {"uattend_user_id": 1, "first_name": 1, "last_name": 1, "email": 1},
    ):
        name = f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip()
        out.append({
            "uattend_user_id": e.get("uattend_user_id"),
            "name": name or (e.get("email") or ""),
            "email": e.get("email") or "",
        })
    return out


def get_match_map(shop_db, shop_id, internal_for_ai, uattend_for_ai) -> dict[int, dict]:
    """Матчи для данного состава: из кэша по его ключу либо пересчёт.

    Возвращает {uattend_user_id:int -> {"internal_id", "internal_name",
    "internal_email", "confidence"}}.
    """
    if not internal_for_ai or not uattend_for_ai:
        return {}

    ck = cache_key(internal_for_ai, uattend_for_ai)
    cached = shop_db.uattend_match_cache.find_one({"shop_id": shop_id, "key": ck})
    if cached and isinstance(cached.get("matches"), dict):
        out = {}
        for k, v in cached["matches"].items():
            if not isinstance(v, dict):
                continue
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
        return out

    matches = match_employees(internal_for_ai, uattend_for_ai)
    try:
        shop_db.uattend_match_cache.update_one(
            {"shop_id": shop_id, "key": ck},
            {
                "$set": {
                    "shop_id": shop_id,
                    "key": ck,
                    "matches": {str(k): v for k, v in matches.items()},
                },
                "$currentDate": {"updated_at": True},
            },
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("uAttend: failed to store match cache")
    return matches


def current_match_map(shop_db, shop_id, master_db, tenant_values) -> dict[int, dict]:
    """Матчи для ТЕКУЩЕГО состава юзеров и uAttend-сотрудников магазина."""
    return get_match_map(
        shop_db,
        shop_id,
        build_internal_for_match(master_db, tenant_values),
        build_uattend_for_match(shop_db, shop_id),
    )
