"""Проверки «такая сущность уже существует» перед созданием/переименованием.

Единые правила для web-форм, AJAX, мобильного API и импорта:
- сравнение без учёта регистра и лишних пробелов (по краям и внутри);
- если найден и активный, и неактивный дубль — сообщаем про активный;
- у неактивного дубля отдельный текст: предлагаем реактивировать, а не
  создавать копию.

Слитые скриптом merge_duplicate_parts парты (``merged_into``) из проверки
исключаются — это исторические «оболочки», реактивировать их нельзя.

Проверка «найти дубль → вставить» сама по себе не атомарна: два одновременных
запроса (двойной клик по Save) оба не находят дубля и оба вставляют — так в
одну секунду появлялись два одинаковых парта. Создание поэтому оборачивается
в :func:`creation_claim` — право создать сущность с данным ключом в один
момент принадлежит одному запросу.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError, PyMongoError

# Сколько живёт заявка, если запрос умер, не сняв её (TTL-индекс). В норме
# заявка снимается сразу после создания, в том же запросе.
CREATION_CLAIM_TTL_SECONDS = 120

_claim_indexes_ready: set[str] = set()


def _ci_exact(value):
    """Anchored case-insensitive матч с терпимостью к лишним пробелам."""
    tokens = [re.escape(t) for t in str(value or "").strip().split()]
    if not tokens:
        return None
    return {"$regex": r"^\s*" + r"\s+".join(tokens) + r"\s*$", "$options": "i"}


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _prefer_active(docs):
    if not docs:
        return None
    for d in docs:
        if d.get("is_active") is not False:
            return d
    return docs[0]


def same_text(a, b) -> bool:
    """Одно и то же значение по правилам проверки дублей (регистр и лишние
    пробелы не считаются)."""
    return _norm(a) == _norm(b)


def ensure_creation_claim_indexes(shop_db) -> None:
    """Индексы коллекции заявок: уникальный ключ (на нём держится вся
    страховка) и TTL для заявок умерших запросов. Идемпотентно."""
    coll = shop_db.creation_claims
    coll.create_index(
        [("shop_id", 1), ("kind", 1), ("key", 1)],
        unique=True, name="uniq_creation_claims_shop_kind_key",
    )
    coll.create_index(
        [("created_at", 1)],
        expireAfterSeconds=CREATION_CLAIM_TTL_SECONDS, name="ttl_creation_claims_created",
    )


@contextmanager
def creation_claim(shop_db, shop_id, kind: str, value):
    """Право создать сущность ``kind`` с ключом ``value`` на время запроса.

    Использование — ВОКРУГ проверки дубля и вставки::

        with creation_claim(db, shop_id, "part", part_number) as owned:
            if not owned:      # тот же номер прямо сейчас создаёт другой запрос
                ...отказ...
            ...find_duplicate_part → insert...

    Заявка — документ в ``creation_claims`` с уникальным индексом по
    (shop_id, kind, нормализованный ключ): второй одновременный запрос
    получает DuplicateKeyError и ``owned == False``. Порядок важен: сначала
    заявка, потом проверка дубля — запрос, пришедший после снятия заявки,
    уже видит созданную сущность обычной проверкой.

    Страховка не должна мешать работе: при ошибке базы (кроме дубля ключа)
    отдаём ``True`` — остаётся прежняя проверка дублей.
    """
    key = _norm(value)
    claim_id = None
    owned = True
    if shop_db is not None and key:
        try:
            if shop_db.name not in _claim_indexes_ready:
                ensure_creation_claim_indexes(shop_db)
                _claim_indexes_ready.add(shop_db.name)
            claim_id = shop_db.creation_claims.insert_one({
                "shop_id": shop_id, "kind": kind, "key": key,
                "created_at": datetime.now(timezone.utc),
            }).inserted_id
        except DuplicateKeyError:
            owned = False
        except PyMongoError:
            from flask import current_app

            current_app.logger.exception("creation_claim: could not take a claim for %s %r", kind, key)
    try:
        yield owned
    finally:
        if claim_id is not None:
            try:
                shop_db.creation_claims.delete_one({"_id": claim_id})
            except PyMongoError:
                from flask import current_app

                current_app.logger.exception("creation_claim: could not release the claim for %s %r", kind, key)


def being_created_message(kind: str, display_name) -> str:
    """Текст для второго из двух одновременных запросов на создание."""
    name = str(display_name or "").strip()
    return (f'{kind} "{name}" is being saved right now — the second click was ignored. '
            f"Check the list; if it is not there in a moment, try again.")


def find_duplicate_part(shop_db, shop_id, part_number, exclude_id=None):
    pattern = _ci_exact(part_number)
    if not pattern:
        return None
    query = {
        "shop_id": shop_id,
        "part_number": pattern,
        "merged_into": {"$exists": False},
    }
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return _prefer_active(list(
        shop_db.parts.find(query, {"part_number": 1, "is_active": 1})
    ))


def find_duplicate_vendor(shop_db, shop_id, name, exclude_id=None):
    pattern = _ci_exact(name)
    if not pattern:
        return None
    query = {"shop_id": shop_id, "name": pattern}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return _prefer_active(list(
        shop_db.vendors.find(query, {"name": 1, "is_active": 1})
    ))


def find_duplicate_unit(shop_db, shop_id, customer_id, vin, exclude_id=None):
    """Дубль VIN в рамках ОДНОГО клиента (у разных клиентов VIN не блокируем)."""
    pattern = _ci_exact(vin)
    if not pattern or not customer_id:
        return None
    query = {"shop_id": shop_id, "customer_id": customer_id, "vin": pattern}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return _prefer_active(list(
        shop_db.units.find(query, {"vin": 1, "unit_number": 1, "is_active": 1})
    ))


def customer_display_name(doc) -> str:
    """Название клиента: company_name, иначе имя главного контакта."""
    company = str(doc.get("company_name") or "").strip()
    if company:
        return company
    contacts = [c for c in doc.get("contacts") or [] if isinstance(c, dict)]
    for pool in ([c for c in contacts if c.get("is_main")], contacts):
        for c in pool:
            name = f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip()
            if name:
                return name
    return f"{doc.get('first_name') or ''} {doc.get('last_name') or ''}".strip()


def find_duplicate_customer(shop_db, shop_id, company_name, contacts, exclude_id=None):
    """Дубль по отображаемому названию (company_name либо имя контакта)."""
    target = _norm(customer_display_name(
        {"company_name": company_name, "contacts": contacts}
    ))
    if not target:
        return None
    matches = []
    for doc in shop_db.customers.find(
        {"shop_id": shop_id},
        {"company_name": 1, "contacts": 1, "first_name": 1, "last_name": 1, "is_active": 1},
    ):
        if exclude_id is not None and doc["_id"] == exclude_id:
            continue
        if _norm(customer_display_name(doc)) == target:
            matches.append(doc)
    return _prefer_active(matches)


def duplicate_message(kind: str, display_name, existing) -> str:
    """Текст ошибки; для неактивного дубля — подсказка реактивировать."""
    name = str(display_name or "").strip()
    if existing.get("is_active") is False:
        if kind == "Part":
            # Партам нет кнопки Restore: активируются только через парт-ордер.
            return (f'{kind} "{name}" already exists but is inactive. '
                    f"Add it to a parts order — it becomes active again automatically.")
        return (f'{kind} "{name}" already exists but is deactivated. '
                f"Reactivate it instead of creating a new one.")
    return f'{kind} "{name}" already exists.'


def unit_duplicate_message(existing) -> str:
    vin = str(existing.get("vin") or "").strip()
    unit_no = str(existing.get("unit_number") or "").strip()
    suffix = f" (unit {unit_no})" if unit_no else ""
    if existing.get("is_active") is False:
        return (f'A unit with VIN "{vin}"{suffix} already exists for this customer '
                f"but is deactivated. Reactivate it instead of creating a new one.")
    return f'A unit with VIN "{vin}"{suffix} already exists for this customer.'
