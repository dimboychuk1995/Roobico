"""Категории запчастей: slug и проверка «такая уже есть».

На ``parts_categories`` стоит уникальный индекс ``(shop_id, slug)``
(``uniq_parts_categories_shop_slug``, его создаёт сидинг магазина). Поэтому
КАЖДАЯ категория обязана нести slug: документ без поля индексируется как
``slug: null``, и вторая такая категория в магазине падает с E11000 — так
Settings → Parts & Pricing отдавал 500 на любую категорию после первой.

Slug строится из имени одним правилом для сидинга и для формы настроек
(``slugify_category``), поэтому он же и ключ дубля: «Filters», «filters» и
« FILTERS » — одна категория.
"""
from __future__ import annotations


def slugify_category(name) -> str:
    """'Brake Parts / Air' -> 'brake-parts-air'; пустое/из одних знаков -> 'category'."""
    s = str(name or "").strip().lower()
    out: list[str] = []
    last_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "category"


def find_duplicate_category(shop_db, shop_id, name, exclude_id=None):
    """Категория магазина с тем же slug, что получится из ``name``, или None.

    Сравниваем slug имени, а не сохранённое поле: у старых документов поля
    slug может не быть вовсе. Категорий в магазине десятки — читаем все.
    """
    slug = slugify_category(name)
    for doc in shop_db.parts_categories.find({"shop_id": shop_id}, {"name": 1, "slug": 1}):
        if exclude_id is not None and doc["_id"] == exclude_id:
            continue
        if doc.get("slug") == slug or slugify_category(doc.get("name")) == slug:
            return doc
    return None


def category_exists_message(existing, name) -> str:
    """Текст отказа; если совпал slug, а написание другое — называем найденную."""
    shown = str((existing or {}).get("name") or name or "").strip()
    return f'Category "{shown}" already exists.'
