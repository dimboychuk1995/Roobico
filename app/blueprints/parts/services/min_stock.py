"""
Минимальный остаток запчасти (reorder point).

Поля документа parts: `min_stock_enabled: bool` («this part requires minimum
stock») и `min_stock: int >= 0`. Состояние считается при чтении — никаких
триггеров на движениях склада:

  * "below" — in_stock < min_stock: ниже минимума, пора заказывать;
  * "near"  — min_stock <= in_stock <= near_limit(min_stock): на минимуме
    или близко к нему (+20 %, но не меньше чем +1 единица);
  * None    — минимум не задан, склад по парту не ведётся
    (do_not_track_inventory) или запаса хватает.

Один и тот же порог используется для бейджей/подсветки в списке и для
фильтра «ниже / близко к минимуму» (stock_level_filter) — чтобы фильтр
показывал ровно те строки, которые подсвечены.
"""
from __future__ import annotations

# «Близко к минимуму» = в пределах 20 % сверх минимума.
NEAR_PERCENT = 20

STATE_BELOW = "below"
STATE_NEAR = "near"

# Значения параметра фильтра списка: below — только ниже минимума;
# near — ниже ИЛИ близко к минимуму (всё, что подсвечено).
STOCK_LEVELS = (STATE_BELOW, STATE_NEAR)


def near_limit(min_stock) -> int:
    """
    Верхняя граница «близко к минимуму»: ceil(min × 1.2), но не меньше
    min + 1 (для маленьких минимумов 20 % — меньше единицы). Целочисленно,
    без плавающей точки: ceil(12·m / 10).
    """
    m = max(0, _to_int(min_stock))
    return max(m + 1, -(-12 * m // 10))


def min_stock_state(part: dict | None) -> str | None:
    """Состояние запаса парта по его документу: "below" / "near" / None."""
    if not part or not part.get("min_stock_enabled") or part.get("do_not_track_inventory"):
        return None
    minimum = max(0, _to_int(part.get("min_stock")))
    stock = _to_int(part.get("in_stock"))
    if stock < minimum:
        return STATE_BELOW
    if stock <= near_limit(minimum):
        return STATE_NEAR
    return None


def stock_level_filter(level) -> dict | None:
    """
    Фрагмент Mongo-фильтра для списка запчастей (объединять через $and с
    фильтром по shop_id/активности): "below" — ниже минимума, "near" — ниже
    или близко. Неизвестное значение → None (фильтр не применяется).
    Порог считается в запросе так же, как в near_limit: max(min + 1,
    ceil(12·min / 10)); отсутствующие in_stock/min_stock считаются нулём.
    """
    level = str(level or "").strip().lower()
    if level not in STOCK_LEVELS:
        return None
    stock = {"$ifNull": ["$in_stock", 0]}
    minimum = {"$ifNull": ["$min_stock", 0]}
    if level == STATE_BELOW:
        expr = {"$lt": [stock, minimum]}
    else:
        limit = {"$max": [
            {"$add": [minimum, 1]},
            {"$ceil": {"$divide": [{"$multiply": [minimum, 12]}, 10]}},
        ]}
        expr = {"$lte": [stock, limit]}
    return {
        "min_stock_enabled": True,
        "do_not_track_inventory": {"$ne": True},
        "$expr": expr,
    }


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
