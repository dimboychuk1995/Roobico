from __future__ import annotations

import json
import urllib.parse
import urllib.request

from flask import current_app, request, jsonify

from app.blueprints.work_orders import work_orders_bp
from app.utils.auth import login_required
from app.utils.permissions import permission_required


def _fetch_vpic(vin: str) -> dict:
    url = (
        "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/"
        f"{urllib.parse.quote(vin)}?format=json"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _extract_value(row: dict, keys: list[str]) -> str:
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


@work_orders_bp.get("/work_orders/api/vin")
@login_required
@permission_required("work_orders.create")
def api_decode_vin():
    vin = (request.args.get("vin") or "").strip().upper()
    if not vin:
        return jsonify({"ok": False, "error": "vin_required"}), 200

    if len(vin) != 17:
        return jsonify({"ok": False, "error": "vin_length", "message": "VIN must be exactly 17 characters"}), 200

    # Basic VIN validation - no I, O, or Q characters allowed
    if any(c in vin for c in ['I', 'O', 'Q']):
        return jsonify({"ok": False, "error": "vin_invalid_chars", "message": "VIN cannot contain I, O, or Q"}), 200

    try:
        payload = _fetch_vpic(vin)
    except Exception:
        current_app.logger.exception("VIN lookup: failed to reach vPIC for %s", vin)
        return jsonify({"ok": False, "error": "vin_lookup_failed", "message": "Failed to connect to VIN lookup service"}), 200

    results = payload.get("Results") if isinstance(payload, dict) else None
    if not results or not isinstance(results, list):
        return jsonify({"ok": False, "error": "vin_no_results", "message": "No results found for this VIN"}), 200

    row = results[0] if results else {}

    make = _extract_value(row, ["Make"])
    model = _extract_value(row, ["Model"])
    year = _extract_value(row, ["ModelYear", "Model Year", "Year"])
    vehicle_type = _extract_value(row, ["VehicleType", "Vehicle Type"])

    error_code = str(row.get("ErrorCode") or "").strip()
    error_text = _extract_value(row, ["ErrorText"]) or "Invalid VIN number"

    # vPIC ставит ненулевой ErrorCode и для предупреждений (напр. 3 — «VIN
    # исправлен в одной позиции», 14 — «нет данных по части символов»), при
    # которых расшифровка всё равно возвращается. Отказ — только когда vPIC
    # не дал вообще никаких данных.
    if not (make or model or year or vehicle_type):
        current_app.logger.info("VIN decode failed for %s: %s", vin, error_text)
        return jsonify({"ok": False, "error": "vin_invalid", "message": error_text}), 200

    warning = None
    if error_code and error_code != "0":
        warning = error_text
        current_app.logger.info("VIN decoded with warnings for %s: %s", vin, error_text)
    else:
        current_app.logger.info("VIN decoded for %s: %s %s %s", vin, year, make, model)

    return jsonify(
        {
            "ok": True,
            "vin": vin,
            "make": make,
            "model": model,
            "year": year,
            "type": vehicle_type,
            "warning": warning,
            "suggested_vin": _extract_value(row, ["SuggestedVIN"]) or None,
        }
    ), 200
