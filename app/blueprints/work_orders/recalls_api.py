"""NHTSA safety recalls for a unit.

Рекаллы запрашиваются с api.nhtsa.gov по make/model/year юнита (если каких-то
полей нет, но есть 17-значный VIN — недостающее добираем через vPIC-декод).
Имя модели у vPIC и в каталоге рекаллов часто различается (vPIC: «F-450»,
рекаллы: «F-450 SD»), поэтому модель сопоставляется с каталогом NHTSA и
запрашиваются все подходящие варианты. На юните хранится снапшот просмотренных
кампаний (`recalls_seen`): рекаллы, которых не было в снапшоте на момент
прошлого просмотра, помечаются как новые.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import current_app, jsonify

from app.blueprints.work_orders import work_orders_bp
from app.blueprints.work_orders.services.lookups import unit_label
from app.blueprints.work_orders.vin_api import _extract_value, _fetch_vpic
from app.extensions import get_master_db
from app.utils.auth import login_required
from app.utils.display_datetime import format_date_mmddyyyy
from app.utils.permissions import permission_required_any
from app.utils.tenant import get_shop_db, oid


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # api.nhtsa.gov отвечает 400 с валидным JSON-телом ({"Count": 0, ...})
        # на неизвестные make/model — это «нет данных», а не отказ сервиса.
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise exc
    return json.loads(raw)


def _fetch_recalls(make: str, model: str, year: str) -> dict:
    url = (
        "https://api.nhtsa.gov/recalls/recallsByVehicle?"
        + urllib.parse.urlencode({"make": make, "model": model, "modelYear": year})
    )
    return _http_get_json(url)


def _fetch_model_catalog(make: str, year: str) -> list[str]:
    """Каноничные имена моделей данной марки/года в каталоге рекаллов NHTSA."""
    url = (
        "https://api.nhtsa.gov/products/vehicle/models?"
        + urllib.parse.urlencode({"modelYear": year, "make": make, "issueType": "r"})
    )
    payload = _http_get_json(url)
    rows = payload.get("results") if isinstance(payload, dict) else None
    rows = rows if isinstance(rows, list) else []
    return [str(r.get("model") or "").strip() for r in rows if isinstance(r, dict) and r.get("model")]


def _norm_model(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


_MAX_MODEL_VARIANTS = 10


def _matching_nhtsa_models(make: str, model: str, year: str) -> list[str]:
    """Варианты имени модели в каталоге NHTSA, соответствующие модели юнита.

    Совпадением считаем равенство нормализованных имён либо продолжение по
    границе слова в любую сторону: «F-450» ↔ «F-450 SD», «F-150» ↔
    «F-150 (SUPER CREW) GAS». Если каталог недоступен или ничего не нашлось —
    пробуем исходное имя как есть.
    """
    try:
        catalog = _fetch_model_catalog(make, year)
    except Exception:
        current_app.logger.exception("Recalls: NHTSA model catalog failed for %s %s", year, make)
        return [model]

    target = _norm_model(model)
    if not target:
        return [model]

    out: list[str] = []
    seen: set[str] = set()
    for name in catalog:
        n = _norm_model(name)
        if not n or n in seen:
            continue
        if n == target or n.startswith(target + " ") or target.startswith(n + " "):
            seen.add(n)
            out.append(name)
    return out[:_MAX_MODEL_VARIANTS] or [model]


def _format_report_date(raw: str) -> str:
    """NHTSA отдаёт ReportReceivedDate как DD/MM/YYYY — приводим к MM/DD/YYYY."""
    s = str(raw or "").strip()
    try:
        return datetime.strptime(s, "%d/%m/%Y").strftime("%m/%d/%Y")
    except ValueError:
        return s


def _vehicle_identity(unit: dict) -> tuple[str, str, str]:
    """make/model/year юнита; недостающее пробуем добрать vPIC-декодом VIN."""
    make = str(unit.get("make") or "").strip()
    model = str(unit.get("model") or "").strip()
    year = str(unit.get("year") or "").strip()
    if make and model and year:
        return make, model, year

    vin = str(unit.get("vin") or "").strip().upper()
    if len(vin) != 17:
        return make, model, year

    try:
        payload = _fetch_vpic(vin)
    except Exception:
        current_app.logger.exception("Recalls: vPIC lookup failed for VIN %s", vin)
        return make, model, year

    results = payload.get("Results") if isinstance(payload, dict) else None
    row = results[0] if isinstance(results, list) and results else {}
    make = make or _extract_value(row, ["Make"])
    model = model or _extract_value(row, ["Model"])
    year = year or _extract_value(row, ["ModelYear", "Model Year", "Year"])
    return make, model, year


@work_orders_bp.get("/work_orders/api/units/<unit_id>/recalls")
@login_required
@permission_required_any("work_orders.create", "customers.view")
def api_unit_recalls(unit_id):
    shop_db, shop = get_shop_db(get_master_db())
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    uid = oid(unit_id)
    if not uid:
        return jsonify({"ok": False, "error": "unit_invalid"}), 200

    unit = shop_db.units.find_one({"_id": uid, "shop_id": shop["_id"]})
    if not unit:
        return jsonify({"ok": False, "error": "unit_not_found"}), 200

    make, model, year = _vehicle_identity(unit)
    if not (make and model and year):
        return jsonify({
            "ok": False,
            "error": "unit_missing_info",
            "message": "Recalls lookup needs Year, Make and Model (or a valid 17-character VIN) on the unit.",
        }), 200

    nhtsa_models = _matching_nhtsa_models(make, model, year)

    rows = []
    fetched_any = False
    for nhtsa_model in nhtsa_models:
        try:
            payload = _fetch_recalls(make, nhtsa_model, year)
        except Exception:
            current_app.logger.exception(
                "Recalls lookup failed for unit %s (%s %s %s)", uid, year, make, nhtsa_model
            )
            continue
        fetched_any = True
        chunk = payload.get("results") if isinstance(payload, dict) else None
        rows.extend(chunk if isinstance(chunk, list) else [])

    if not fetched_any:
        return jsonify({
            "ok": False,
            "error": "recalls_lookup_failed",
            "message": "Failed to reach the NHTSA recalls service. Please try again later.",
        }), 200

    prev = unit.get("recalls_seen") if isinstance(unit.get("recalls_seen"), dict) else None
    prev_campaigns = set(prev.get("campaigns") or []) if prev else None

    recalls = []
    campaigns = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        campaign = str(row.get("NHTSACampaignNumber") or "").strip()
        if campaign:
            # Одна кампания покрывает несколько вариантов модели —
            # после объединения выдач дубли отбрасываем.
            if campaign in campaigns:
                continue
            campaigns.append(campaign)
        recalls.append({
            "campaign_number": campaign,
            "report_date": _format_report_date(row.get("ReportReceivedDate")),
            "component": str(row.get("Component") or "").strip(),
            "summary": str(row.get("Summary") or "").strip(),
            "consequence": str(row.get("Consequence") or "").strip(),
            "remedy": str(row.get("Remedy") or "").strip(),
            "park_it": bool(row.get("parkIt")),
            "park_outside": bool(row.get("parkOutSide")),
            # Новым считаем рекалл, которого не было в снапшоте прошлого
            # просмотра; при самом первом просмотре ничего не подсвечиваем.
            "is_new": bool(prev_campaigns is not None and campaign and campaign not in prev_campaigns),
        })

    shop_db.units.update_one(
        {"_id": uid, "shop_id": shop["_id"]},
        {"$set": {"recalls_seen": {
            "campaigns": sorted(set(campaigns)),
            "checked_at": datetime.now(timezone.utc),
        }}},
    )

    return jsonify({
        "ok": True,
        "unit_label": unit_label(unit),
        "vehicle": {"make": make, "model": model, "year": year},
        "nhtsa_models": nhtsa_models,
        "count": len(recalls),
        "new_count": sum(1 for r in recalls if r["is_new"]),
        "prev_checked": format_date_mmddyyyy(prev.get("checked_at"), default="") if prev else "",
        "recalls": recalls,
    }), 200
