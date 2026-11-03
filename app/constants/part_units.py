"""
Единицы измерения запчастей (чем считаем: штуки, галлоны, кварты, …).

Хранится ключ (`unit_of_measure` в документе parts); отсутствие поля =
"each". Список фиксированный — нам важно, чтобы одно и то же читалось
одинаково в вебе, приложении, импорте/экспорте и PDF.
"""
from __future__ import annotations

# (ключ, название, короткое обозначение)
PART_UNITS: list[tuple[str, str, str]] = [
    ("each", "Each", "ea"),
    ("gallon", "Gallon", "gal"),
    ("quart", "Quart", "qt"),
    ("pint", "Pint", "pt"),
    ("fl_oz", "Fluid ounce", "fl oz"),
    ("liter", "Liter", "L"),
    ("milliliter", "Milliliter", "mL"),
    ("pound", "Pound", "lb"),
    ("ounce", "Ounce", "oz"),
    ("kilogram", "Kilogram", "kg"),
    ("gram", "Gram", "g"),
    ("foot", "Foot", "ft"),
    ("inch", "Inch", "in"),
    ("meter", "Meter", "m"),
    ("box", "Box", "box"),
    ("case", "Case", "case"),
    ("pack", "Pack", "pk"),
    ("pair", "Pair", "pr"),
    ("set", "Set", "set"),
    ("roll", "Roll", "roll"),
    ("can", "Can", "can"),
    ("bottle", "Bottle", "btl"),
    ("tube", "Tube", "tube"),
    ("drum", "Drum", "drum"),
    ("pail", "Pail", "pail"),
    ("kit", "Kit", "kit"),
]

DEFAULT_PART_UNIT = "each"
PART_UNIT_KEYS: tuple[str, ...] = tuple(key for key, _label, _short in PART_UNITS)

_BY_KEY = {key: (label, short) for key, label, short in PART_UNITS}
# Любое написание → ключ: "gal", "Gallon", "GALLONS", "fl oz", "fl_oz" …
_ALIASES: dict[str, str] = {}
for _key, _label, _short in PART_UNITS:
    for _alias in (_key, _label, _short, _label + "s", _short + "s"):
        _ALIASES[_alias.strip().lower().replace("_", " ")] = _key
_ALIASES.update({
    "pcs": "each", "pc": "each", "piece": "each", "pieces": "each", "unit": "each", "units": "each",
    "gl": "gallon", "qrt": "quart", "ltr": "liter", "litre": "liter", "litres": "liter",
    "ml": "milliliter", "lbs": "pound", "kgs": "kilogram", "ft.": "foot", "feet": "foot",
    "inches": "inch", "in.": "inch", "boxes": "box", "cases": "case", "packs": "pack",
    "pairs": "pair", "sets": "set", "rolls": "roll", "cans": "can", "bottles": "bottle",
    "tubes": "tube", "drums": "drum", "pails": "pail", "kits": "kit",
})


def unit_label(key: str | None) -> str:
    """Полное название единицы по ключу ("Gallon"); неизвестный/пустой → Each."""
    return _BY_KEY.get(str(key or "").strip().lower(), _BY_KEY[DEFAULT_PART_UNIT])[0]


def unit_short(key: str | None) -> str:
    """Короткое обозначение по ключу ("gal"); неизвестный/пустой → ea."""
    return _BY_KEY.get(str(key or "").strip().lower(), _BY_KEY[DEFAULT_PART_UNIT])[1]


def normalize_unit(value) -> str | None:
    """
    Пользовательский ввод (ключ, название, сокращение, множественное число,
    любой регистр) → ключ каталога; пустое значение → DEFAULT_PART_UNIT;
    неизвестное → None (вызывающий решает: 400 или дефолт).
    """
    text = str(value or "").strip().lower().replace("_", " ")
    if not text:
        return DEFAULT_PART_UNIT
    return _ALIASES.get(text)


def part_unit_options() -> list[dict]:
    """Для селектов (веб, приложение): [{key, label, short}] в порядке каталога."""
    return [{"key": key, "label": label, "short": short} for key, label, short in PART_UNITS]
