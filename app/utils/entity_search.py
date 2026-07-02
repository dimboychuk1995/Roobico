"""
Нормализованный поиск для customers и units — тот же приём, что и в parts:
на записи хранится массив `search_terms` (триграммы компактного lowercase-
текста всех искомых полей), запрос ищет `$all` по индексу и пост-фильтрует
кандидатов точным contains-сравнением в Python.

Зачем: case-insensitive `$regex` по десятку полей в `$or` не может
использовать индексы и на каждый ввод в поиске сканирует коллекцию целиком.

Поддержание актуальности: build_*_search_terms() вызывается во всех местах
create/update (customers, units, import), бэкфилл существующих записей —
app/scripts/backfill_search_terms.py.
"""
from __future__ import annotations

import re

from app.utils.parts_search import (
    compact_search_text,
    _trigram_tokens,
    build_query_tokens,
)

# Поля, по которым ищем. Должны совпадать в build-функциях, пост-фильтре
# и regex-fallback'е — поэтому объявлены один раз здесь.
CUSTOMER_FIELDS = ("company_name", "first_name", "last_name", "phone", "email", "address")
CUSTOMER_CONTACT_FIELDS = ("first_name", "last_name", "phone", "email")
UNIT_FIELDS = ("unit_number", "vin", "make", "model", "type", "year")


def _customer_values(doc: dict) -> list:
    values = [doc.get(f) for f in CUSTOMER_FIELDS]
    for contact in (doc.get("contacts") or []):
        if isinstance(contact, dict):
            values.extend(contact.get(f) for f in CUSTOMER_CONTACT_FIELDS)
    return values


def _unit_values(doc: dict) -> list:
    return [doc.get(f) for f in UNIT_FIELDS]


def _build_terms(values) -> list[str]:
    tokens = set()
    for raw in values:
        compact = compact_search_text(raw)
        if compact:
            tokens.update(_trigram_tokens(compact))
    return sorted(tokens)


def build_customer_search_terms(doc: dict) -> list[str]:
    return _build_terms(_customer_values(doc))


def build_unit_search_terms(doc: dict) -> list[str]:
    return _build_terms(_unit_values(doc))


def _regex_or_clauses(q: str, fields, contact_fields=()) -> list[dict]:
    # re.escape обязателен: q приходит от пользователя, и без экранирования
    # это regex-инъекция (`.*` или катастрофический backtracking).
    regex = {"$regex": re.escape(q.strip()), "$options": "i"}
    clauses = [{f: regex} for f in fields]
    clauses.extend({f"contacts.{f}": regex} for f in contact_fields)
    return clauses


def _search_ids(collection, q, values_fn, fields, contact_fields=(), limit=1000) -> list:
    """
    _id документов, где q встречается (contains, без учёта регистра и
    пунктуации) хотя бы в одном из полей.

    Быстрый путь: $all по индексу search_terms + точный пост-фильтр.
    Fallback (запрос короче 3 символов или записи ещё без бэкфилла):
    старый regex-$or по исходным полям.
    """
    normalized, tokens = build_query_tokens(q)
    if not normalized:
        return []

    if len(normalized) >= 3:
        projection = {f: 1 for f in fields}
        if contact_fields:
            projection["contacts"] = 1
        ids = []
        cursor = collection.find({"search_terms": {"$all": tokens}}, projection).limit(limit)
        for doc in cursor:
            if any(normalized in compact_search_text(v) for v in values_fn(doc)):
                ids.append(doc["_id"])
        if ids:
            return ids

    clauses = _regex_or_clauses(q, fields, contact_fields)
    return [
        d["_id"]
        for d in collection.find({"$or": clauses}, {"_id": 1}).limit(limit)
    ]


def search_customer_ids(customers_collection, q, limit=1000) -> list:
    return _search_ids(
        customers_collection, q, _customer_values,
        fields=CUSTOMER_FIELDS, contact_fields=CUSTOMER_CONTACT_FIELDS, limit=limit,
    )


def search_unit_ids(units_collection, q, limit=1000) -> list:
    return _search_ids(
        units_collection, q, _unit_values,
        fields=UNIT_FIELDS, limit=limit,
    )
