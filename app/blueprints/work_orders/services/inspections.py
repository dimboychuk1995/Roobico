"""
Годовая инспекция (AVIR, 49 CFR 396 Appendix G): чеклист компонентов со
стабильными ключами пунктов, санитайзер отметок и срок действия.

Ключ пункта = номер секции + буква пункта ("1a", "7c"); у секций с одним
безбуквенным пунктом — просто номер ("5", "12"). Ключи стабильны, пока
стабильны константы формы, и используются и в UI, и в хранении
(annual_inspections.components), и в PDF.
"""
from __future__ import annotations

from datetime import datetime


# AVIR component checklist (49 CFR 396, Appendix G) grouped into 3 print columns.
ANNUAL_INSPECTION_COMPONENT_COLUMNS = [
    [
        {"title": "1. BRAKE SYSTEM", "items": [
            "a. Service Brakes",
            "b. Parking Brake System",
            "c. Brake Drums or Rotors",
            "d. Brake Hose",
            "e. Brake Tubing",
            "f. Low Pressure Warning Device",
            "g. Tractor Protection Valve",
            "h. Air Compressor",
            "i. Electric Brakes",
            "j. Hydraulic Brakes",
            "k. Vacuum Systems",
        ]},
        {"title": "2. COUPLING DEVICES", "items": [
            "a. Fifth Wheels",
            "b. Pintle Hooks",
            "c. Drawbar/Towbar Eye",
            "d. Drawbar/Towbar Tongue",
            "e. Safety Devices",
            "f. Saddle-Mounts",
        ]},
        {"title": "3. EXHAUST SYSTEM", "items": [
            "a. Any exhaust system determined to be leaking at a point forward of or directly below the driver/sleeper compartment.",
            "b. A bus exhaust system leaking or discharging to the atmosphere in violation of standards (1), (2) or (3).",
            "c. No part of the exhaust system of any motor vehicle shall be so located as would be likely to result in burning, charring, or damaging the electrical wiring, the fuel supply, or any combustible part of the motor vehicle.",
        ]},
    ],
    [
        {"title": "4. FUEL SYSTEM", "items": [
            "a. Visible leak",
            "b. Fuel tank filler cap missing",
            "c. Fuel tank securely attached",
        ]},
        {"title": "5. LIGHTING DEVICES", "items": [
            "All lighting devices and reflectors required by Section 393 shall be operable.",
        ]},
        {"title": "6. SAFE LOADING", "items": [
            "a. Part(s) of vehicle or condition of loading such that the spare tire or any part of the load or dunnage can fall onto the roadway.",
            "b. Protection against shifting cargo",
        ]},
        {"title": "7. STEERING MECHANISM", "items": [
            "a. Steering Wheel Free Play",
            "b. Steering Column",
            "c. Front Axle Beam and All Steering Components Other Than Steering Column",
            "d. Steering Gear Box",
            "e. Pitman Arm",
            "f. Power Steering",
            "g. Ball and Socket Joints",
            "h. Tie Rods and Drag Links",
            "i. Nuts",
            "j. Steering System",
        ]},
        {"title": "8. SUSPENSION", "items": [
            "a. Any U-bolt(s), spring hanger(s), or other axle positioning part(s) cracked, broken, loose or missing resulting in shifting of an axle from its normal position.",
            "b. Spring Assembly",
            "c. Torque, Radius or Tracking Components.",
        ]},
    ],
    [
        {"title": "9. FRAME", "items": [
            "a. Frame Members",
            "b. Tire and Wheel Clearance",
            "c. Adjustable Axle Assemblies (Sliding Subframes)",
        ]},
        {"title": "10. TIRES", "items": [
            "a. Tires on any steering axle of a power unit.",
            "b. All other tires.",
        ]},
        {"title": "11. WHEELS AND RIMS", "items": [
            "a. Lock or Side Ring",
            "b. Wheels and Rims",
            "c. Fasteners",
            "d. Welds",
        ]},
        {"title": "12. WINDSHIELD GLAZING", "items": [
            "Requirements and exceptions as stated pertaining to any crack, discoloration or vision reducing matter (reference 393.60 for exceptions)",
        ]},
        {"title": "13. WINDSHIELD WIPERS", "items": [
            "Any power unit that has an inoperative wiper, or missing or damaged parts that render it ineffective.",
        ]},
    ],
]


# value -> (display label, which checkbox to mark on the printed form)
ANNUAL_INSPECTION_VEHICLE_TYPES = {
    "semi_trailer": ("Semi Trailer", "trailer"),
    "semi_truck": ("Semi Truck", "tractor"),
    "hot_shot_electric": ("Hot Shot Trailer with Electric Brakes", "trailer"),
    "hot_shot_hydraulic": ("Hot Shot Trailer with Hydraulic Brakes", "trailer"),
    "pickup_truck": ("Pick Up Truck", "truck"),
}

COMPONENT_STATUSES = ("ok", "repair", "na")


# Пресеты чеклиста по типу техники: какие пункты по умолчанию OK, какие NA.
# Раскладки даны владельцем (2026-08-11); порядок секций: 1 Brake system,
# 2 Coupling, 3 Exhaust, 4 Fuel, 5 Lighting, 6 Safe loading, 7 Steering,
# 8 Suspension, 9 Frame, 10 Tires, 11 Wheels, 12 Glazing, 13 Wipers.
VEHICLE_TYPE_COMPONENT_DEFAULTS = {
    "semi_trailer": {
        "1a": "ok", "1b": "ok", "1c": "ok", "1d": "ok", "1e": "ok", "1f": "ok",
        "1g": "na", "1h": "na", "1i": "na", "1j": "ok", "1k": "na",
        "2a": "na", "2b": "na", "2c": "na", "2d": "na", "2e": "na", "2f": "na",
        "3a": "na", "3b": "na", "3c": "na",
        "4a": "na", "4b": "na", "4c": "na",
        "5": "ok",
        "6a": "ok", "6b": "ok",
        "7a": "na", "7b": "na", "7c": "na", "7d": "na", "7e": "na",
        "7f": "na", "7g": "na", "7h": "na", "7i": "na", "7j": "na",
        "8a": "ok", "8b": "ok", "8c": "ok",
        "9a": "ok", "9b": "ok", "9c": "ok",
        "10a": "na", "10b": "ok",
        "11a": "ok", "11b": "ok", "11c": "ok", "11d": "ok",
        "12": "na",
        "13": "na",
    },
    "semi_truck": {
        "1a": "ok", "1b": "ok", "1c": "ok", "1d": "ok", "1e": "ok", "1f": "ok",
        "1g": "ok", "1h": "ok", "1i": "na", "1j": "ok", "1k": "na",
        "2a": "ok", "2b": "ok", "2c": "ok", "2d": "ok", "2e": "ok", "2f": "ok",
        "3a": "ok", "3b": "na", "3c": "ok",
        "4a": "ok", "4b": "ok", "4c": "ok",
        "5": "ok",
        "6a": "na", "6b": "na",
        "7a": "ok", "7b": "ok", "7c": "ok", "7d": "ok", "7e": "ok",
        "7f": "ok", "7g": "ok", "7h": "ok", "7i": "ok", "7j": "ok",
        "8a": "ok", "8b": "ok", "8c": "ok",
        "9a": "ok", "9b": "ok", "9c": "ok",
        "10a": "ok", "10b": "ok",
        "11a": "ok", "11b": "ok", "11c": "ok", "11d": "ok",
        "12": "ok",
        "13": "ok",
    },
    "hot_shot_electric": {
        "1a": "ok", "1b": "na", "1c": "ok", "1d": "na", "1e": "na", "1f": "na",
        "1g": "na", "1h": "na", "1i": "ok", "1j": "na", "1k": "na",
        "2a": "na", "2b": "na", "2c": "na", "2d": "na", "2e": "na", "2f": "na",
        "3a": "na", "3b": "na", "3c": "na",
        "4a": "na", "4b": "na", "4c": "na",
        "5": "ok",
        "6a": "ok", "6b": "ok",
        "7a": "na", "7b": "na", "7c": "na", "7d": "na", "7e": "na",
        "7f": "na", "7g": "na", "7h": "na", "7i": "na", "7j": "na",
        "8a": "ok", "8b": "ok", "8c": "ok",
        "9a": "ok", "9b": "ok", "9c": "na",
        "10a": "na", "10b": "ok",
        "11a": "ok", "11b": "ok", "11c": "ok", "11d": "ok",
        "12": "na",
        "13": "na",
    },
    # Как hot_shot_electric, но гидравлические тормоза (1j) — OK.
    "hot_shot_hydraulic": {
        "1a": "ok", "1b": "na", "1c": "ok", "1d": "na", "1e": "na", "1f": "na",
        "1g": "na", "1h": "na", "1i": "ok", "1j": "ok", "1k": "na",
        "2a": "na", "2b": "na", "2c": "na", "2d": "na", "2e": "na", "2f": "na",
        "3a": "na", "3b": "na", "3c": "na",
        "4a": "na", "4b": "na", "4c": "na",
        "5": "ok",
        "6a": "ok", "6b": "ok",
        "7a": "na", "7b": "na", "7c": "na", "7d": "na", "7e": "na",
        "7f": "na", "7g": "na", "7h": "na", "7i": "na", "7j": "na",
        "8a": "ok", "8b": "ok", "8c": "ok",
        "9a": "ok", "9b": "ok", "9c": "na",
        "10a": "na", "10b": "ok",
        "11a": "ok", "11b": "ok", "11c": "ok", "11d": "ok",
        "12": "na",
        "13": "na",
    },
    "pickup_truck": {
        "1a": "ok", "1b": "ok", "1c": "ok", "1d": "ok", "1e": "ok", "1f": "ok",
        "1g": "na", "1h": "na", "1i": "ok", "1j": "ok", "1k": "ok",
        "2a": "ok", "2b": "ok", "2c": "ok", "2d": "ok", "2e": "ok", "2f": "ok",
        "3a": "ok", "3b": "na", "3c": "ok",
        "4a": "ok", "4b": "ok", "4c": "ok",
        "5": "ok",
        "6a": "na", "6b": "na",
        "7a": "ok", "7b": "ok", "7c": "ok", "7d": "ok", "7e": "ok",
        "7f": "ok", "7g": "ok", "7h": "ok", "7i": "ok", "7j": "ok",
        "8a": "ok", "8b": "ok", "8c": "ok",
        "9a": "ok", "9b": "ok", "9c": "na",
        "10a": "ok", "10b": "ok",
        "11a": "ok", "11b": "ok", "11c": "ok", "11d": "ok",
        "12": "ok",
        "13": "ok",
    },
}


def default_components_for_type(vehicle_type: str) -> dict:
    """{key: {"status": ...}} для типа техники или {} если пресета нет."""
    defaults = VEHICLE_TYPE_COMPONENT_DEFAULTS.get(str(vehicle_type or "").strip().lower())
    if not defaults:
        return {}
    return {key: {"status": status} for key, status in defaults.items()}


def _component_item_key(section_title: str, item_text: str) -> str:
    sec_num = section_title.split(".", 1)[0].strip()
    letter = ""
    if len(item_text) > 1 and item_text[1] == "." and item_text[0].isalpha():
        letter = item_text[0].lower()
    return f"{sec_num}{letter}"


def annual_inspection_checklist() -> list:
    """Колонки формы, где каждый пункт — {"key", "text"}."""
    columns = []
    for column in ANNUAL_INSPECTION_COMPONENT_COLUMNS:
        out_col = []
        for section in column:
            out_col.append({
                "title": section["title"],
                "items": [
                    {"key": _component_item_key(section["title"], item), "text": item}
                    for item in section["items"]
                ],
            })
        columns.append(out_col)
    return columns


def annual_inspection_component_keys() -> set:
    return {
        item["key"]
        for column in annual_inspection_checklist()
        for section in column
        for item in section["items"]
    }


def sanitize_components(raw) -> dict:
    """Отметки чеклиста из клиента -> {key: {"status", ["repaired_date"]}}.

    Неизвестные ключи и статусы отбрасываются; repaired_date хранится только
    у пунктов со статусом "repair" (строка YYYY-MM-DD как прислал клиент).
    """
    if not isinstance(raw, dict):
        return {}
    valid_keys = annual_inspection_component_keys()
    out = {}
    for key, val in raw.items():
        if key not in valid_keys or not isinstance(val, dict):
            continue
        status = str(val.get("status") or "").strip().lower()
        if status not in COMPONENT_STATUSES:
            continue
        entry = {"status": status}
        if status == "repair":
            repaired = str(val.get("repaired_date") or "").strip()[:10]
            if repaired:
                entry["repaired_date"] = repaired
        out[key] = entry
    return out


def inspection_expiry(inspection: dict):
    """Дата истечения (инспекция действует 12 месяцев) или None."""
    base = inspection.get("inspection_date") or inspection.get("created_at")
    if not isinstance(base, datetime):
        return None
    try:
        return base.replace(year=base.year + 1)
    except ValueError:  # 29 февраля
        return base.replace(year=base.year + 1, day=28)


def inspection_expiry_status(expires_at, now=None) -> str:
    """"valid" | "expiring" (≤30 дней) | "expired" | "" (нет даты)."""
    if not isinstance(expires_at, datetime):
        return ""
    now = now or datetime.utcnow()
    if expires_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=expires_at.tzinfo)
    elif expires_at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    delta_days = (expires_at - now).days
    if delta_days < 0:
        return "expired"
    if delta_days <= 30:
        return "expiring"
    return "valid"
