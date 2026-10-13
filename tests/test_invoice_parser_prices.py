"""
AI-парсер инвойсов: сверка unit price с суммой строки. Модель просят вернуть
и цену за единицу, и напечатанную сумму строки; если она положила сумму в
цену (qty 3, amount 150 → price 150), цена восстанавливается как amount/qty.
Чистые функции — без OpenAI и без базы.
"""
from __future__ import annotations

import pytest

from app.utils.invoice_parser import normalize_line_item, reconcile_line_price


@pytest.mark.parametrize(
    "qty, price, line_total, expected_price, expected_note",
    [
        # Нет суммы строки — цена как есть.
        (3, 50.0, 0.0, 50.0, ""),
        # Согласовано: 3 × 50 = 150.
        (3, 50.0, 150.0, 50.0, ""),
        # Копеечное округление — согласовано.
        (3, 33.33, 100.0, 33.33, ""),
        # Классическая ошибка: сумма строки записана в цену.
        (3, 150.0, 150.0, 50.0, "extended_used_as_unit"),
        # Цена не прочитана — берём из суммы.
        (4, 0.0, 200.0, 50.0, "price_from_line_total"),
        # qty>1 и числа не сходятся (в price попал List) — доверяем сумме строки.
        (2, 120.0, 180.0, 90.0, "price_from_line_total"),
        # qty 1, цена больше суммы (List вместо Net) — берём сумму.
        (1, 120.0, 90.0, 90.0, "price_from_line_total"),
        # qty 1, цена меньше суммы (в сумме core/налог) — оставляем цену.
        (1, 90.0, 120.0, 90.0, ""),
        # Нулевое/битое количество считается как 1.
        (0, 150.0, 150.0, 150.0, ""),
    ],
)
def test_reconcile_line_price(qty, price, line_total, expected_price, expected_note):
    assert reconcile_line_price(qty, price, line_total) == (expected_price, expected_note)


def test_normalize_line_item_fixes_extended_in_price():
    item = normalize_line_item({
        "part_number": " DR 8600310 ",
        "description": "Brake drum",
        "quantity": "3",
        "price": "150.00",
        "line_total": "150.00",
    })
    assert item == {
        "part_number": "DR 8600310",
        "description": "Brake drum",
        "quantity": 3,
        "price": 50.0,
        "line_total": 150.0,
        "price_note": "extended_used_as_unit",
    }


def test_normalize_line_item_keeps_consistent_line_and_tolerates_garbage():
    assert normalize_line_item({"part_number": "A", "quantity": 2, "price": 10, "line_total": 20}) == {
        "part_number": "A", "description": "", "quantity": 2, "price": 10.0,
        "line_total": 20.0, "price_note": "",
    }
    # Без line_total (старый формат ответа) поведение прежнее.
    assert normalize_line_item({"part_number": "B", "quantity": 2, "price": 10})["price"] == 10.0
    # Мусор в числах не роняет парсер.
    garbage = normalize_line_item({"part_number": "C", "quantity": "x", "price": "n/a", "line_total": None})
    assert garbage["quantity"] == 1 and garbage["price"] == 0.0 and garbage["line_total"] == 0.0
    assert normalize_line_item("not a dict") is None
