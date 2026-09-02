"""
Сканирование VIN механиком в мобильном приложении.

Флоу: приложение сканирует VIN-баркод камерой (наведение без кнопок),
затем зовёт /vin/resolve. Если активный юнит с таким VIN уже есть —
возвращаем его вместе с клиентом (несколько компаний → механик выбирает).
Если нет — юнит создаётся автоматически под системным клиентом
"NEW Customer" (is_system: true, защищён от деактивации), чтобы механик
мог начать работу до того, как офис разберётся с принадлежностью юнита.
"""
from __future__ import annotations

import re

from flask import current_app, jsonify, request, session
from pymongo import ReturnDocument

from app.blueprints.mobile_api import mobile_api_bp
from app.blueprints.mobile_api.routes import api_login_required, get_shop_db
from app.utils.permissions import permission_required
from app.utils.tenant import oid

SYSTEM_CUSTOMER_NAME = "NEW Customer"

_VIN_FORBIDDEN = ("I", "O", "Q")


def normalize_scanned_vin(raw) -> str | None:
    """Сырая строка со сканера → валидный 17-символьный VIN или None."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()
    # VIN-баркоды (Code 39) часто несут ведущий служебный символ "I"/"O"/"Q":
    # сам VIN этих букв не содержит, поэтому такой префикс безопасно срезать.
    if len(s) == 18 and s[0] in _VIN_FORBIDDEN:
        s = s[1:]
    if len(s) != 17 or any(c in s for c in _VIN_FORBIDDEN):
        return None
    return s


def get_or_create_system_customer(shop_db, shop, user_id):
    """Системный клиент "NEW Customer" — технический владелец юнитов,
    отсканированных до выбора настоящей компании. Создаётся лениво,
    upsert-ом (без гонок), и всегда возвращается активным."""
    from app.blueprints.work_orders.services.common import utcnow
    from app.utils.contacts import build_customer_legacy_contact_fields
    from app.utils.entity_search import build_customer_search_terms

    now = utcnow()
    doc = shop_db.customers.find_one_and_update(
        {"shop_id": shop["_id"], "is_system": True},
        {
            # Деактивировать системного клиента нельзя, но если старые данные
            # успели — воскрешаем при первом же обращении.
            "$set": {"is_active": True, "updated_at": now},
            "$unset": {"deactivated_at": "", "deactivated_by": ""},
            "$setOnInsert": {
                "company_name": SYSTEM_CUSTOMER_NAME,
                "is_system": True,
                "contacts": [],
                "taxable": False,
                "created_at": now,
                "created_by": user_id,
                "shop_id": shop["_id"],
                "tenant_id": shop.get("tenant_id"),
                **build_customer_legacy_contact_fields([]),
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not doc.get("search_terms"):
        terms = build_customer_search_terms(doc)
        shop_db.customers.update_one({"_id": doc["_id"]}, {"$set": {"search_terms": terms}})
        doc["search_terms"] = terms
    return doc


def _decode_vin_best_effort(vin: str) -> dict:
    """vPIC-расшифровка make/model/year/type; сбой сети — просто пустые поля."""
    from app.blueprints.work_orders.vin_api import _extract_value, _fetch_vpic

    try:
        payload = _fetch_vpic(vin)
        results = payload.get("Results") if isinstance(payload, dict) else None
        row = results[0] if isinstance(results, list) and results else {}
    except Exception as exc:
        current_app.logger.warning("VIN scan: vPIC decode failed for %s: %s", vin, exc)
        return {}
    return {
        "make": _extract_value(row, ["Make"]),
        "model": _extract_value(row, ["Model"]),
        "year": _extract_value(row, ["ModelYear", "Model Year", "Year"]),
        "type": _extract_value(row, ["VehicleType", "Vehicle Type"]),
    }


def _match_payload(unit_doc, customer_doc) -> dict:
    from app.blueprints.work_orders.services.lookups import customer_label, unit_label

    return {
        "unit_id": str(unit_doc["_id"]),
        "unit_label": unit_label(unit_doc),
        "customer_id": str(customer_doc["_id"]),
        "customer_label": customer_label(customer_doc),
        "is_system_customer": bool(customer_doc.get("is_system")),
    }


@mobile_api_bp.post("/api/mobile/vin/resolve")
@api_login_required
@permission_required("work_orders.create")
def mobile_vin_resolve():
    """VIN → юнит + клиент. Нет юнита — создаём под "NEW Customer"."""
    from app.blueprints.work_orders.services.common import i32, utcnow
    from app.utils.entity_search import build_unit_search_terms

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    vin = normalize_scanned_vin(data.get("vin"))
    if not vin:
        return jsonify({
            "ok": False, "error": "vin_invalid",
            "message": "Scanned code is not a valid 17-character VIN.",
        }), 200

    vin_filter = {"$regex": f"^{re.escape(vin)}$", "$options": "i"}
    units = list(shop_db.units.find({
        "shop_id": shop["_id"], "is_active": True, "vin": vin_filter,
    }))
    customer_ids = [u.get("customer_id") for u in units if u.get("customer_id")]
    customers = {
        c["_id"]: c for c in shop_db.customers.find({"_id": {"$in": customer_ids}})
    } if customer_ids else {}

    # Только активные юниты активных клиентов: механик работает с живыми
    # компаниями; один матч приложение подставляет сразу, несколько — выбор.
    matches = []
    for u in units:
        c = customers.get(u.get("customer_id"))
        if not c or c.get("is_active") is False:
            continue
        matches.append(_match_payload(u, c))
    if matches:
        return jsonify({"ok": True, "vin": vin, "created": False, "matches": matches}), 200

    user_id = oid(session.get("user_id"))
    system_customer = get_or_create_system_customer(shop_db, shop, user_id)

    # Юнит с этим VIN уже был у системного клиента, но деактивирован —
    # воскрешаем вместо создания дубля.
    dormant = shop_db.units.find_one({
        "shop_id": shop["_id"], "customer_id": system_customer["_id"], "vin": vin_filter,
    })
    now = utcnow()
    if dormant:
        shop_db.units.update_one(
            {"_id": dormant["_id"]},
            {"$set": {"is_active": True, "updated_at": now, "updated_by": user_id}},
        )
        dormant["is_active"] = True
        return jsonify({
            "ok": True, "vin": vin, "created": True,
            "matches": [_match_payload(dormant, system_customer)],
        }), 200

    decoded = _decode_vin_best_effort(vin)
    unit_doc = {
        "customer_id": system_customer["_id"],
        "vin": vin,
        "unit_number": None,
        "make": decoded.get("make") or None,
        "model": decoded.get("model") or None,
        "year": i32(decoded.get("year")),
        "type": decoded.get("type") or None,
        "mileage": None,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    unit_doc["search_terms"] = build_unit_search_terms(unit_doc)
    res = shop_db.units.insert_one(unit_doc)
    unit_doc["_id"] = res.inserted_id

    return jsonify({
        "ok": True, "vin": vin, "created": True,
        "matches": [_match_payload(unit_doc, system_customer)],
    }), 200
