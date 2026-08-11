"""
Perks — быстрые инструменты вне основного потока WO.

Пока один блок: Annual Inspection для юнитов, которых не хотим заводить
в систему (VIN + тип + кастомер руками в общей модалке). Здесь же —
реестр всех сохранённых инспекций магазина с PDF и удалением.
"""
from __future__ import annotations

from app.blueprints.main.routes import NAV_ITEMS
from app.blueprints.perks import perks_bp
from app.blueprints.work_orders.services.inspections import (
    ANNUAL_INSPECTION_VEHICLE_TYPES,
    VEHICLE_TYPE_COMPONENT_DEFAULTS,
    annual_inspection_checklist,
    inspection_expiry,
    inspection_expiry_status,
)
from app.extensions import get_master_db
from app.utils.auth import login_required
from app.utils.display_datetime import format_date_mmddyyyy, get_active_shop_today_iso
from app.utils.layout import render_internal_page
from app.utils.permissions import filter_nav_items, has_permission, permission_required
from app.utils.tenant import get_shop_db


@perks_bp.get("")
@login_required
@permission_required("work_orders.view")
def perks_index():
    layout_nav = filter_nav_items(NAV_ITEMS)
    return render_internal_page("public/perks.html", layout_nav, "perks")


@perks_bp.get("/annual-inspection")
@login_required
@permission_required("work_orders.view")
def perks_annual_inspection_page():
    layout_nav = filter_nav_items(NAV_ITEMS)

    shop_db, shop = get_shop_db(get_master_db())
    if shop_db is None:
        return render_internal_page(
            "public/perks/annual_inspection.html",
            layout_nav,
            "perks",
            rows=[],
            error="Shop database not configured.",
        )

    inspections = list(
        shop_db.annual_inspections.find({"shop_id": shop["_id"]})
        .sort([("created_at", -1)])
        .limit(200)
    )

    unit_ids = [i.get("unit_id") for i in inspections if i.get("unit_id")]
    units_map = {}
    if unit_ids:
        for unit in shop_db.units.find(
            {"_id": {"$in": unit_ids}},
            {"unit_number": 1, "customer_id": 1},
        ):
            units_map[unit["_id"]] = unit

    rows = []
    for insp in inspections:
        expires_at = inspection_expiry(insp)
        unit = units_map.get(insp.get("unit_id"))
        type_label, _ = ANNUAL_INSPECTION_VEHICLE_TYPES.get(
            str(insp.get("vehicle_type") or "").strip().lower(), ("", "")
        )
        rows.append({
            "id": str(insp.get("_id")),
            "date": format_date_mmddyyyy(insp.get("inspection_date") or insp.get("created_at")),
            "report_number": str(insp.get("report_number") or "").strip() or str(insp.get("_id") or "")[-6:].upper(),
            "vin": insp.get("vin") or "-",
            "carrier": insp.get("motor_carrier_operator") or "-",
            "vehicle_type": type_label or (insp.get("vehicle_type") or "-"),
            "inspector_name": insp.get("inspector_name") or "-",
            "unit_number": (unit or {}).get("unit_number") or "",
            "unit_id": str(insp.get("unit_id") or ""),
            "unit_customer_id": str((unit or {}).get("customer_id") or ""),
            "expires": format_date_mmddyyyy(expires_at) if expires_at else "-",
            "expiry_status": inspection_expiry_status(expires_at),
        })

    return render_internal_page(
        "public/perks/annual_inspection.html",
        layout_nav,
        "perks",
        rows=rows,
        error=None,
        can_create_inspection=has_permission("work_orders.create"),
        can_delete_inspection=has_permission("work_orders.delete"),
        avi_checklist=annual_inspection_checklist(),
        avi_type_defaults=VEHICLE_TYPE_COMPONENT_DEFAULTS,
        today_date_input_value=get_active_shop_today_iso(),
    )
