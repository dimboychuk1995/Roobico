"""
Простой fixed-window rate limiter на master-базе Mongo.

Хранится в коллекции master.rate_limits: по документу на ключ
(scope + идентификатор, например IP). Окно фиксированное: первая попытка
задаёт window_start, счётчик инкрементится атомарно; по истечении окна
документ переиспользуется заново. TTL-индекс на updated_at подчищает
устаревшие ключи (создаётся в app.extensions).

Mongo выбран вместо in-memory словаря сознательно: gunicorn запускает
несколько воркеров, и лимит должен быть общим для всех процессов.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.extensions import get_master_db


def hit_rate_limit(scope: str, identifier: str, max_attempts: int, window_seconds: int) -> bool:
    """
    Зарегистрировать попытку и вернуть True, если лимит превышен.

    scope        — логическая область ("login", "forgot_password", ...)
    identifier   — кто ограничивается (обычно IP-адрес)
    max_attempts — сколько попыток разрешено в окне
    window_seconds — длина окна в секундах
    """
    if not identifier:
        return False

    key = f"{scope}:{identifier}"
    now = time.time()

    try:
        col = get_master_db().rate_limits

        # Попытка в рамках живого окна: атомарный инкремент.
        doc = col.find_one_and_update(
            {"_id": key, "window_start": {"$gt": now - window_seconds}},
            {
                "$inc": {"count": 1},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc is not None:
            return doc.get("count", 0) > max_attempts

        # Окно истекло или ключа ещё нет — начинаем новое окно.
        col.update_one(
            {"_id": key},
            {
                "$set": {
                    "window_start": now,
                    "count": 1,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        return False
    except Exception:
        # Fail-open: недоступность Mongo не должна блокировать вход в систему.
        return False
