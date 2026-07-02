"""
Общие tenant/shop-хелперы для blueprint'ов.

Исторически каждый blueprint держал собственные копии `_oid`,
`_tenant_id_variants`, `_get_active_shop`, `_get_shop_db` — идентичные
друг другу. Здесь их каноническая версия; blueprint'ы импортируют отсюда
(при желании под старыми локальными именами через `as`).
"""
from __future__ import annotations

from typing import Optional

from bson import ObjectId
from flask import session

from app.extensions import get_mongo_client
from app.utils.auth import SESSION_TENANT_ID


def oid(value) -> Optional[ObjectId]:
    """Безопасно привести значение к ObjectId (None, если не приводится)."""
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def tenant_id_variants() -> list:
    """
    Все представления tenant_id текущей сессии (строка и ObjectId).
    Нужна из-за того, что tenant_id исторически мог быть то строкой,
    то ObjectId — до миграции данных фильтры обязаны учитывать оба вида.
    """
    raw = session.get(SESSION_TENANT_ID)
    out = set()
    if raw is None:
        return []
    out.add(raw)
    out.add(str(raw))
    as_oid = oid(raw)
    if as_oid:
        out.add(as_oid)
    return list(out)


def get_active_shop(master):
    """Документ активного магазина сессии, проверенный на принадлежность tenant'у."""
    shop_oid = oid(session.get("shop_id"))
    if not shop_oid:
        return None

    variants = tenant_id_variants()
    if not variants:
        return None

    return master.shops.find_one({"_id": shop_oid, "tenant_id": {"$in": variants}})


def shop_db_name(shop: dict) -> Optional[str]:
    """Имя shop-базы из документа магазина (легаси-поля учитываются)."""
    if not shop:
        return None
    name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    return str(name) if name else None


def get_shop_db(master):
    """
    (shop_db, shop) для активного магазина сессии.
    (None, None) — нет магазина/тенанта; (None, shop) — у магазина нет db_name.
    """
    shop = get_active_shop(master)
    if not shop:
        return None, None

    db_name = shop_db_name(shop)
    if not db_name:
        return None, shop

    client = get_mongo_client()
    return client[db_name], shop
