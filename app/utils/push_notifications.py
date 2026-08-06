"""Мобильные push-уведомления через Expo Push API.

Токены устройств лежат в master_db.push_tokens (один документ на токен;
пользователь может иметь несколько устройств). Приложение регистрирует токен
после логина через POST /api/mobile/push-token и снимает его при logout.

Отправка — best-effort: HTTP-запрос к Expo уходит в фоновом потоке, ошибки
логируются и никогда не роняют исходный запрос. Токены, на которые Expo
ответил DeviceNotRegistered (приложение удалено / пуши отключены),
подчищаются автоматически.

В тестах: app.config["PUSH_SYNC"]=True делает доставку синхронной, а сам
HTTP-вызов (_post_expo) монкипатчится.
"""
from __future__ import annotations

import threading

from flask import current_app

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_EXPO_BATCH_SIZE = 100

# Роли механиков (см. CLAUDE.md «Механик-режим»): они триггерят события,
# но офисные уведомления им не шлём.
MECHANIC_ROLES = ("mechanic", "senior_mechanic")


# ── токены устройств ─────────────────────────────────────────────────

def register_push_token(master_db, user_id, token: str, platform: str = "") -> bool:
    """Привязать Expo-токен устройства к пользователю (upsert по токену).

    Один физический девайс = один токен: если на нём перелогинился другой
    пользователь, токен переезжает к нему.
    """
    token = str(token or "").strip()
    if not token or user_id is None:
        return False
    from app.blueprints.work_orders.services.common import utcnow

    now = utcnow()
    master_db.push_tokens.update_one(
        {"token": token},
        {
            "$set": {
                "user_id": user_id,
                "platform": str(platform or "").strip().lower(),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return True


def remove_push_token(master_db, token: str) -> None:
    token = str(token or "").strip()
    if token:
        master_db.push_tokens.delete_many({"token": token})


# ── получатели ───────────────────────────────────────────────────────

def office_user_ids_for_shop(master_db, shop: dict, exclude_user_id=None) -> list:
    """Активные НЕ-механики тенанта с доступом к магазину (shop_ids/shop_id
    исторически хранят и ObjectId, и строки — как в get_assignable_mechanics)."""
    from app.blueprints.work_orders.services.lookups import _tenant_variants_from_shop

    shop_id = shop.get("_id")
    tenant_variants = _tenant_variants_from_shop(shop)
    if not shop_id or not tenant_variants:
        return []

    shop_variants = [shop_id, str(shop_id)]
    query = {
        "tenant_id": {"$in": tenant_variants},
        "is_active": True,
        "role": {"$nin": list(MECHANIC_ROLES)},
        "$or": [
            {"shop_ids": {"$in": shop_variants}},
            {"shop_id": {"$in": shop_variants}},
        ],
    }
    if exclude_user_id is not None:
        query["_id"] = {"$ne": exclude_user_id}
    return [u["_id"] for u in master_db.users.find(query, {"_id": 1})]


# ── отправка ─────────────────────────────────────────────────────────

def _post_expo(messages: list[dict]) -> list[dict]:
    """POST одной пачки в Expo; возвращает tickets (по одному на сообщение)."""
    import requests

    resp = requests.post(
        EXPO_PUSH_URL,
        json=messages,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    return data.get("data") or []


def _deliver(master_db, tokens: list[str], title: str, body: str, data: dict, logger) -> None:
    dead_tokens: list[str] = []
    for i in range(0, len(tokens), _EXPO_BATCH_SIZE):
        batch = tokens[i:i + _EXPO_BATCH_SIZE]
        messages = [
            {"to": t, "title": title, "body": body, "sound": "default", "data": data or {}}
            for t in batch
        ]
        try:
            tickets = _post_expo(messages)
        except Exception:
            logger.exception("Expo push send failed (batch of %d)", len(batch))
            continue
        for token, ticket in zip(batch, tickets):
            if not isinstance(ticket, dict) or ticket.get("status") == "ok":
                continue
            details = ticket.get("details") or {}
            if details.get("error") == "DeviceNotRegistered":
                dead_tokens.append(token)
            else:
                logger.warning(
                    "Expo push ticket error for token %s…: %s",
                    token[:24], ticket.get("message") or details.get("error") or "unknown",
                )
    if dead_tokens:
        try:
            master_db.push_tokens.delete_many({"token": {"$in": dead_tokens}})
        except Exception:
            logger.exception("Failed to clean up dead push tokens")


def send_push_to_users(master_db, user_ids: list, title: str, body: str, data: dict | None = None) -> int:
    """Отправить пуш на все устройства перечисленных пользователей.

    Возвращает число токенов, на которые ушла отправка. Сама доставка —
    в фоновом потоке (или синхронно при app.config["PUSH_SYNC"]).
    """
    if not user_ids:
        return 0
    tokens = [
        t["token"]
        for t in master_db.push_tokens.find({"user_id": {"$in": list(user_ids)}}, {"token": 1})
        if t.get("token")
    ]
    if not tokens:
        return 0

    logger = current_app.logger
    if current_app.config.get("PUSH_SYNC"):
        _deliver(master_db, tokens, title, body, data or {}, logger)
    else:
        threading.Thread(
            target=_deliver,
            args=(master_db, tokens, title, body, data or {}, logger),
            daemon=True,
        ).start()
    return len(tokens)
