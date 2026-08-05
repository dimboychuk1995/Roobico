"""Проверки «такая сущность уже существует» перед созданием/переименованием.

Единые правила для web-форм, AJAX, мобильного API и импорта:
- сравнение без учёта регистра и лишних пробелов (по краям и внутри);
- если найден и активный, и неактивный дубль — сообщаем про активный;
- у неактивного дубля отдельный текст: предлагаем реактивировать, а не
  создавать копию.

Слитые скриптом merge_duplicate_parts парты (``merged_into``) из проверки
исключаются — это исторические «оболочки», реактивировать их нельзя.
"""
from __future__ import annotations

import re


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
