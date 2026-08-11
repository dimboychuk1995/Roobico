"""
Контексты для PDF work order и годовой инспекции (AVIR): собирают данные
для рендера HTML-шаблонов, из которых генерируется PDF.
"""
from __future__ import annotations

import base64

from bson import ObjectId

from app.extensions import get_master_db
from app.utils.contacts import get_main_contact_email, get_main_contact_phone
from app.utils.display_datetime import format_date_mmddyyyy
from app.blueprints.work_orders.services.common import (
    f64,
    format_dt_label,
    i32,
    oid,
    round2,
)
from app.blueprints.work_orders.services.lookups import customer_label, unit_label
from app.blueprints.work_orders.services.totals import _parse_misc_items, normalize_parts_payload
from app.blueprints.work_orders.services.payments import (
    _build_work_order_payment_summary,
    _sum_active_work_order_payments,
)
from app.blueprints.work_orders.services.totals import (
    _work_order_grand_total,
    _work_order_tax_total,
)


from app.blueprints.work_orders.services.inspections import (
    ANNUAL_INSPECTION_VEHICLE_TYPES,
    annual_inspection_checklist,
)


def _annual_inspection_marked_columns(components) -> list:
    """Колонки чеклиста с отметками для печати: X в OK / NEEDS REPAIR,
    NA в колонке OK, дата ремонта. Пустые отметки — незаполненный пункт
    (старые записи печатаются с пустой формой, как раньше)."""
    from datetime import datetime

    def _short_date(raw: str) -> str:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").strftime("%m/%d/%y")
        except (TypeError, ValueError):
            return raw or ""

    components = components if isinstance(components, dict) else {}
    columns = []
    for column in annual_inspection_checklist():
        out_col = []
        for section in column:
            items = []
            for item in section["items"]:
                state = components.get(item["key"]) or {}
                status = str(state.get("status") or "").lower()
                # Раскладка печати: OK — галочка в 1-й колонке; needs repair —
                # X во 2-й + дата в 3-й; N/A — отметка в 3-й колонке.
                third = ""
                if status == "repair":
                    third = _short_date(str(state.get("repaired_date") or ""))
                elif status == "na":
                    third = "N/A"
                items.append({
                    "text": item["text"],
                    "ok_mark": "✔" if status == "ok" else "",
                    "repair_mark": "X" if status == "repair" else "",
                    "repaired_date": third,
                })
            out_col.append({"title": section["title"], "items": items})
        columns.append(out_col)
    return columns


def _build_annual_inspection_pdf_context(shop_db, inspection):
    unit = shop_db.units.find_one({"_id": inspection.get("unit_id")}) if inspection.get("unit_id") else None
    unit = unit or {}

    vehicle_type = str(inspection.get("vehicle_type") or "").strip().lower()
    type_label, type_checkbox = ANNUAL_INSPECTION_VEHICLE_TYPES.get(vehicle_type, ("", ""))
    if not type_checkbox and vehicle_type in {"tractor", "trailer", "truck"}:
        type_checkbox = vehicle_type

    return {
        # Вписанный вручную номер приоритетнее; без него — хвост _id (как раньше).
        "report_number": str(inspection.get("report_number") or "").strip() or str(inspection.get("_id") or "")[-6:].upper(),
        "fleet_unit_number": str(unit.get("unit_number") or inspection.get("fleet_unit_number") or "").strip(),
        "inspection_date_label": format_date_mmddyyyy(inspection.get("inspection_date") or inspection.get("created_at")),
        "motor_carrier_operator": inspection.get("motor_carrier_operator") or "",
        "address": inspection.get("address") or "",
        "city_state_zip": inspection.get("city_state_zip") or "",
        "inspector_name": inspection.get("inspector_name") or "",
        "inspector_qualified": bool(inspection.get("inspector_qualified", True)),
        "vin": inspection.get("vin") or "",
        "inspection_agency": inspection.get("inspection_agency") or "",
        "vehicle_type": type_checkbox,
        "vehicle_type_other": bool(vehicle_type and not type_checkbox),
        "vehicle_type_label": type_label,
        "component_columns": _annual_inspection_marked_columns(inspection.get("components")),
    }


def _build_wo_pdf_context(shop_db, shop, wo):
    """Build the template context dict used by work_order_pdf.html."""
    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}) or {}
    cust_name = customer_label(customer)
    customer_email_val = get_main_contact_email(customer, entity_type="customer")
    customer_phone = get_main_contact_phone(customer, entity_type="customer")
    customer_address = str(customer.get("address") or "").strip()

    unit = shop_db.units.find_one({"_id": wo.get("unit_id")}) or {}
    unit_lbl = unit_label(unit)
    unit_vin = str(unit.get("vin") or "").strip()
    unit_mileage = str(unit.get("mileage") or "").strip()
    unit_number = str(unit.get("unit_number") or "").strip()
    unit_year = str(unit.get("year") or "").strip()
    unit_make = str(unit.get("make") or "").strip()
    unit_model = str(unit.get("model") or "").strip()

    shop_name = str(shop.get("name") or "").strip()
    addr_full = str(shop.get("address") or "").strip()
    addr_parts = [shop.get("address_line"), shop.get("city"), shop.get("state"), shop.get("zip")]
    shop_address = addr_full or ", ".join(str(p).strip() for p in addr_parts if p and str(p).strip())
    contact_parts = [str(shop.get("phone") or "").strip(), str(shop.get("email") or "").strip()]
    shop_contact = " · ".join(p for p in contact_parts if p)

    # Remit-to block: explicit billing address if set, else fall back to the
    # shop's physical address.
    shop_billing_address = str(shop.get("billing_address") or "").strip() or shop_address

    wo_date = wo.get("work_order_date") or wo.get("created_at")
    wo_date_label = format_date_mmddyyyy(wo_date) if wo_date else ""
    wo_number = str(wo.get("wo_number") or str(wo["_id"]))

    raw_labors = wo.get("labors") or []
    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    totals_labors = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []

    labors_built = []
    for i, block in enumerate(raw_labors):
        if not isinstance(block, dict):
            continue
        labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        desc = str(labor_src.get("description") or block.get("labor_description") or "").strip()
        hours = str(labor_src.get("hours") or block.get("labor_hours") or "").strip()
        rate_code = str(labor_src.get("rate_code") or block.get("labor_rate_code") or "").strip()
        issue_description = str(labor_src.get("issue_description") or block.get("issue_description") or "").strip()

        block_totals = totals_labors[i] if i < len(totals_labors) and isinstance(totals_labors[i], dict) else {}
        labor_base = round2(block_totals.get("labor") or 0)
        shop_supply = round2(block_totals.get("shop_supply_total") or 0)
        labor_total_val = round2(block_totals.get("labor_total") or block_totals.get("labor") or 0)
        parts_subtotal = round2(block_totals.get("parts_total") or 0)
        core_t = round2(block_totals.get("core_total") or 0)
        misc_t = round2(block_totals.get("misc_total") or 0)
        block_total = round2(block_totals.get("labor_full_total") or (labor_total_val + parts_subtotal))

        normalized_parts = normalize_parts_payload(block.get("parts") or [])

        # Misc charges are stored as a JSON list on the FIRST part row's
        # misc_charge_description. Parse it once per labor block so the PDF
        # can render each item as a separate line.
        misc_items_built = []
        first_row_misc = _parse_misc_items(
            (normalized_parts[0] or {}).get("misc_charge_description")
        ) if normalized_parts else []
        for item in first_row_misc:
            price = round2(item.get("price") or 0)
            iqty = f64(item.get("quantity") if item.get("quantity") is not None else 0) or 0
            if price <= 0 or iqty <= 0:
                continue
            misc_items_built.append({
                "description": str(item.get("description") or "").strip() or "Misc Charge",
                "qty": iqty,
                "price": price,
                "total": round2(price * iqty),
            })
        has_json_misc = bool(first_row_misc)

        parts_detail = []
        for p in normalized_parts:
            qty = i32(p.get("qty")) or 0
            if qty <= 0:
                continue
            part_number = str(p.get("part_number") or "").strip()
            description = str(p.get("description") or "").strip()
            price = round2(p.get("price") or 0)
            core_per = round2(p.get("core_charge") or 0)
            misc_per = round2(p.get("misc_charge") or 0)
            # If the misc list is stored as JSON on the first row, don't show
            # the JSON blob as a per-part description (legacy fallback only).
            raw_misc_desc = str(p.get("misc_charge_description") or "").strip()
            legacy_misc_desc = "" if has_json_misc else raw_misc_desc
            parts_detail.append({
                "part_number": part_number,
                "description": description,
                "label": part_number or description,
                "qty": qty,
                "price": price,
                "price_fmt": f"${price:.2f}",
                "total": round2(price * qty),
                "core": round2(core_per * qty),
                "misc": 0 if has_json_misc else round2(misc_per * qty),
                "misc_desc": legacy_misc_desc,
            })

        labors_built.append({
            "labor_desc": desc or f"Labor {i + 1}",
            "hours": hours,
            "labor_hours": hours,
            "rate_code": rate_code,
            "issue_description": issue_description,
            "labor_base": labor_base,
            "shop_supply": shop_supply,
            "labor_total": labor_total_val,
            "labor_total_fmt": f"${labor_total_val:.2f}",
            "parts_subtotal": parts_subtotal,
            "core_total": core_t,
            "misc_total": misc_t,
            "block_total": block_total,
            "parts": parts_detail,
            "misc_items": misc_items_built,
        })

    paid_total = _sum_active_work_order_payments(shop_db, wo.get("_id"))
    pay_summary = _build_work_order_payment_summary(wo, paid_total)
    t = {
        "labor_total": round2(totals_doc.get("labor_total") or totals_doc.get("labor") or 0),
        "parts_total": round2(totals_doc.get("parts_total") or totals_doc.get("parts") or 0),
        "core_total": round2(totals_doc.get("core_total") or 0),
        "shop_supply_total": round2(totals_doc.get("shop_supply_total") or 0),
        "misc_total": round2(totals_doc.get("misc_total") or 0),
        "sales_tax_total": round2(totals_doc.get("sales_tax_total") or 0),
        "grand_total": pay_summary["grand_total"],
        "paid_total": pay_summary["paid_amount"],
        "remaining_balance": pay_summary["remaining_balance"],
        "is_fully_paid": pay_summary["is_fully_paid"],
    }

    pdf_cfg = shop_db.pdf_design.find_one({"shop_id": shop["_id"]}) or {}

    # Build logo as base64 data URI (xhtml2pdf can't fetch localhost URLs)
    shop_logo_url = ""
    if pdf_cfg.get("show_logo", True) and shop.get("logo_data"):
        ct = shop.get("logo_content_type") or "image/png"
        b64 = base64.b64encode(bytes(shop["logo_data"])).decode("ascii")
        shop_logo_url = f"data:{ct};base64,{b64}"

    return dict(
        shop_name=shop_name,
        shop_address=shop_address,
        shop_contact=shop_contact,
        shop_billing_address=shop_billing_address,
        wo_number=wo_number,
        wo_date_label=wo_date_label,
        cust_name=cust_name,
        customer_email=customer_email_val,
        customer_phone=customer_phone,
        customer_address=customer_address,
        unit_label=unit_lbl,
        unit_vin=unit_vin,
        unit_mileage=unit_mileage,
        unit_number=unit_number,
        unit_year=unit_year,
        unit_make=unit_make,
        unit_model=unit_model,
        labors=labors_built,
        t=t,
        pdf_cfg=pdf_cfg,
        shop_logo_url=shop_logo_url,
    )
