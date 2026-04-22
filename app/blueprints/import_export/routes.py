from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from bson import ObjectId
from flask import request, redirect, url_for, flash, session, jsonify

from app.blueprints.import_export import import_export_bp
from app.blueprints.main.routes import _render_app_page, NAV_ITEMS
from app.extensions import get_master_db, get_mongo_client
from app.utils.parts_search import build_parts_search_terms
from app.utils.auth import (
    login_required,
    SESSION_TENANT_ID,
    SESSION_USER_ID,
    SESSION_SHOP_ID,
)
from app.utils.permissions import permission_required


def _oid(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def utcnow():
    return datetime.now(timezone.utc)


# ── field definitions per entity ────────────────────────────────────

ENTITY_FIELDS = {
    "customers": [
        {"key": "company_name", "label": "Company Name"},
        {"key": "first_name", "label": "First Name"},
        {"key": "last_name", "label": "Last Name"},
        {"key": "phone", "label": "Phone"},
        {"key": "email", "label": "Email"},
        {"key": "address", "label": "Address"},
        {"key": "pricing_rule_name", "label": "Pricing Scale Name"},
    ],
    "units": [
        {"key": "unit_number", "label": "Unit Number"},
        {"key": "vin", "label": "VIN"},
        {"key": "year", "label": "Year"},
        {"key": "make", "label": "Make"},
        {"key": "model", "label": "Model"},
        {"key": "type", "label": "Type"},
        {"key": "mileage", "label": "Mileage"},
    ],
    "vendors": [
        {"key": "name", "label": "Vendor Name"},
        {"key": "first_name", "label": "Contact First Name"},
        {"key": "last_name", "label": "Contact Last Name"},
        {"key": "phone", "label": "Phone"},
        {"key": "email", "label": "Email"},
        {"key": "website", "label": "Website"},
        {"key": "address", "label": "Address"},
        {"key": "notes", "label": "Notes"},
    ],
    "parts": [
        {"key": "part_number", "label": "Part Number"},
        {"key": "description", "label": "Description"},
        {"key": "reference", "label": "Reference"},
        {"key": "in_stock", "label": "In Stock"},
        {"key": "average_cost", "label": "Average Cost"},
        {"key": "selling_price", "label": "Selling Price"},
    ],
}

ENTITY_LABELS = {
    "customers": "Customers",
    "units": "Units",
    "vendors": "Vendors",
    "parts": "Parts",
}

# ── helpers ──────────────────────────────────────────────────────────


def _get_shop_db():
    master = get_master_db()
    tenant_id = _oid(session.get(SESSION_TENANT_ID))
    shop_id = _oid(session.get(SESSION_SHOP_ID))
    if not tenant_id or not shop_id:
        return None, None

    shop = master.shops.find_one({"_id": shop_id, "tenant_id": tenant_id, "is_active": True})
    if not shop:
        return None, None

    db_name = shop.get("db_name")
    if not db_name:
        return None, None

    client = get_mongo_client()
    return client[db_name], shop


def _parse_file_headers(file_storage):
    """Return list of header strings from uploaded CSV or Excel file."""
    filename = (file_storage.filename or "").lower()

    if filename.endswith(".csv"):
        raw = file_storage.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.reader(io.StringIO(text))
        first_row = next(reader, None)
        file_storage.seek(0)
        if not first_row:
            return []
        return [h.strip() for h in first_row if h.strip()]

    if filename.endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(file_storage, read_only=True, data_only=True)
        ws = wb.active
        first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
        wb.close()
        file_storage.seek(0)
        if not first_row:
            return []
        return [str(h).strip() for h in first_row if h is not None and str(h).strip()]

    return []


def _parse_all_rows(file_storage):
    """Return (headers, rows) where rows is list of dicts keyed by header."""
    filename = (file_storage.filename or "").lower()

    if filename.endswith(".csv"):
        raw = file_storage.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        headers = reader.fieldnames or []
        rows = list(reader)
        return headers, rows

    if filename.endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(file_storage, read_only=True, data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not all_rows:
            return [], []
        headers = [str(h).strip() if h else "" for h in all_rows[0]]
        rows = []
        for row_vals in all_rows[1:]:
            row_dict = {}
            for i, h in enumerate(headers):
                if h:
                    val = row_vals[i] if i < len(row_vals) else None
                    row_dict[h] = val
            rows.append(row_dict)
        return headers, rows

    return [], []


def _safe_int(val):
    if val is None:
        return None
    try:
        s = _clean_excel(val)
        s = s.replace('$', '').replace(',', '').strip()
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _safe_float(val):
    if val is None:
        return None
    try:
        s = _clean_excel(val)
        s = s.replace('$', '').replace(',', '').strip()
        return round(float(s), 2)
    except (ValueError, TypeError):
        return None


def _clean_excel(val):
    """Strip Excel ='"..."' and ="..." wrappers from a value."""
    if val is None:
        return None
    s = str(val).strip()
    # ="value" or ='value'
    if (s.startswith('="') and s.endswith('"')) or (s.startswith("='") and s.endswith("'")):
        s = s[2:-1]
    return s


def _safe_str(val):
    if val is None:
        return None
    s = _clean_excel(val)
    s = s.strip() if s else None
    return s if s else None


def _build_customer_doc(mapped_row, shop, now, user_id, default_labor_rate_id=None,
                        pricing_rule_lookup=None, default_pricing_rule_id=None):
    company_name = _safe_str(mapped_row.get("company_name"))
    first_name = _safe_str(mapped_row.get("first_name"))
    last_name = _safe_str(mapped_row.get("last_name"))
    phone = _safe_str(mapped_row.get("phone"))
    email = _safe_str(mapped_row.get("email"))
    address = _safe_str(mapped_row.get("address"))
    pricing_rule_name = _safe_str(mapped_row.get("pricing_rule_name"))

    # Must have company_name or a contact name
    if not company_name and not first_name and not last_name:
        return None

    contacts = []
    if first_name or last_name or phone or email:
        contacts.append({
            "first_name": first_name or "",
            "last_name": last_name or "",
            "phone": phone or "",
            "email": (email or "").lower(),
            "is_main": True,
        })

    doc = {
        "company_name": company_name,
        "contacts": contacts,
        "address": address,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "email": (email or "").lower() if email else None,
        "main_contact_name": " ".join(filter(None, [first_name, last_name])) or None,
        "main_contact_phone": phone,
        "main_contact_email": (email or "").lower() if email else None,
        "taxable": False,
        "current_balance": 0.0,
        "default_labor_rate": default_labor_rate_id,
        "pricing_rule_id": (
            (pricing_rule_lookup or {}).get((pricing_rule_name or "").strip().lower())
            if pricing_rule_name else None
        ) or default_pricing_rule_id,
        "override_part_selling_price": False,
        "is_active": True,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    return doc


def _build_unit_doc(mapped_row, customer_id, shop, now, user_id):
    doc = {
        "customer_id": customer_id,
        "unit_number": _safe_str(mapped_row.get("unit_number")),
        "vin": _safe_str(mapped_row.get("vin")),
        "year": _safe_int(mapped_row.get("year")),
        "make": _safe_str(mapped_row.get("make")),
        "model": _safe_str(mapped_row.get("model")),
        "type": _safe_str(mapped_row.get("type")),
        "mileage": _safe_int(mapped_row.get("mileage")),
        "is_active": True,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    return doc


def _build_vendor_doc(mapped_row, shop, now, user_id):
    name = _safe_str(mapped_row.get("name"))
    if not name:
        return None

    first_name = _safe_str(mapped_row.get("first_name"))
    last_name = _safe_str(mapped_row.get("last_name"))
    phone = _safe_str(mapped_row.get("phone"))
    email = _safe_str(mapped_row.get("email"))

    contacts = []
    if first_name or last_name or phone or email:
        contacts.append({
            "first_name": first_name or "",
            "last_name": last_name or "",
            "phone": phone or "",
            "email": (email or "").lower(),
            "is_main": True,
        })

    doc = {
        "name": name,
        "website": _safe_str(mapped_row.get("website")),
        "address": _safe_str(mapped_row.get("address")),
        "notes": _safe_str(mapped_row.get("notes")),
        "contacts": contacts,
        "primary_contact_first_name": first_name,
        "primary_contact_last_name": last_name,
        "phone": phone,
        "email": (email or "").lower() if email else None,
        "is_active": True,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    return doc


def _build_part_doc(mapped_row, shop, now, user_id):
    part_number = _safe_str(mapped_row.get("part_number"))
    if not part_number:
        return None

    description = _safe_str(mapped_row.get("description"))
    reference = _safe_str(mapped_row.get("reference"))
    in_stock = _safe_int(mapped_row.get("in_stock"))
    average_cost = _safe_float(mapped_row.get("average_cost"))
    selling_price = _safe_float(mapped_row.get("selling_price"))

    doc = {
        "part_number": part_number,
        "description": description,
        "reference": reference,
        "search_terms": build_parts_search_terms(part_number, description, reference),
        "in_stock": in_stock or 0,
        "average_cost": average_cost or 0.0,
        "has_selling_price": selling_price is not None and selling_price > 0,
        "selling_price": selling_price,
        "do_not_track_inventory": False,
        "core_has_charge": False,
        "core_cost": None,
        "misc_has_charge": False,
        "misc_charges": [],
        "vendor_id": None,
        "category_id": None,
        "location_id": None,
        "is_active": True,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    return doc


# ── routes ───────────────────────────────────────────────────────────


@import_export_bp.get("/")
@login_required
@permission_required("settings.manage_org")
def import_export_index():
    tab = (request.args.get("tab") or "customers").strip().lower()
    if tab not in ENTITY_LABELS:
        tab = "customers"

    return _render_app_page(
        "public/import_export.html",
        active_page="import_export",
        active_tab=tab,
        entity_tabs=ENTITY_LABELS,
        entity_fields=ENTITY_FIELDS.get(tab, []),
        entity_fields_json=json.dumps(ENTITY_FIELDS.get(tab, [])),
    )


@import_export_bp.post("/upload-headers")
@login_required
@permission_required("settings.manage_org")
def upload_headers():
    """Parse uploaded file and return headers as JSON."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    fname = f.filename.lower()
    if not fname.endswith((".csv", ".xlsx", ".xls")):
        return jsonify({"ok": False, "error": "Unsupported file format. Use CSV or Excel."}), 400

    headers = _parse_file_headers(f)
    if not headers:
        return jsonify({"ok": False, "error": "No headers found in the file."}), 400

    return jsonify({"ok": True, "headers": headers})


@import_export_bp.post("/import")
@login_required
@permission_required("settings.manage_org")
def run_import():
    """Execute the import with field mapping."""
    shop_db, shop = _get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not configured."}), 400

    entity_type = (request.form.get("entity_type") or "").strip()
    if entity_type not in ENTITY_LABELS:
        return jsonify({"ok": False, "error": "Invalid entity type."}), 400

    mapping_raw = request.form.get("mapping") or "{}"
    try:
        mapping = json.loads(mapping_raw)
    except (json.JSONDecodeError, TypeError):
        return jsonify({"ok": False, "error": "Invalid field mapping."}), 400

    if not mapping:
        return jsonify({"ok": False, "error": "No fields mapped."}), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    headers, rows = _parse_all_rows(f)
    if not rows:
        return jsonify({"ok": False, "error": "No data rows found."}), 400

    now = utcnow()
    user_id = _oid(session.get(SESSION_USER_ID))

    # mapping: {file_header: our_field_key}
    imported = 0
    skipped = 0
    errors = []

    # For units import we need a customer_id lookup
    customer_lookup = {}
    if entity_type == "units":
        # Pre-build lookup by company_name (case-insensitive)
        for c in shop_db.customers.find({"shop_id": shop["_id"], "is_active": True}, {"company_name": 1}):
            cn = (c.get("company_name") or "").strip().lower()
            if cn:
                customer_lookup[cn] = c["_id"]

    # Pre-resolve default labor rate for customer imports so the field is never empty.
    customer_default_rate_id = None
    pricing_rule_lookup = {}
    customer_default_pricing_rule_id = None
    if entity_type == "customers":
        from app.blueprints.customers.routes import (
            _resolve_default_labor_rate_id,
            _resolve_default_pricing_rule_id,
        )
        customer_default_rate_id = _resolve_default_labor_rate_id(shop_db, shop["_id"])
        if not customer_default_rate_id:
            return jsonify({"ok": False, "error": "No labor rates configured for this shop. Please create at least one labor rate first."}), 400
        customer_default_pricing_rule_id = _resolve_default_pricing_rule_id(shop_db, shop["_id"])
        for s in shop_db.parts_pricing_rules.find({"shop_id": shop["_id"]}, {"_id": 1, "name": 1}):
            nm = (s.get("name") or "").strip().lower()
            if nm:
                pricing_rule_lookup[nm] = s["_id"]

    for i, row in enumerate(rows):
        # Map file headers to our field keys
        mapped_row = {}
        for file_header, our_key in mapping.items():
            if our_key and file_header in row:
                mapped_row[our_key] = row[file_header]

        try:
            if entity_type == "customers":
                doc = _build_customer_doc(
                    mapped_row, shop, now, user_id,
                    default_labor_rate_id=customer_default_rate_id,
                    pricing_rule_lookup=pricing_rule_lookup,
                    default_pricing_rule_id=customer_default_pricing_rule_id,
                )
                if doc is None:
                    skipped += 1
                    continue
                shop_db.customers.insert_one(doc)
                imported += 1

            elif entity_type == "units":
                doc = _build_unit_doc(mapped_row, None, shop, now, user_id)
                # Try to assign customer_id if we have company name or customer ref
                doc["customer_id"] = None
                shop_db.units.insert_one(doc)
                imported += 1

            elif entity_type == "vendors":
                doc = _build_vendor_doc(mapped_row, shop, now, user_id)
                if doc is None:
                    skipped += 1
                    continue
                shop_db.vendors.insert_one(doc)
                imported += 1

            elif entity_type == "parts":
                doc = _build_part_doc(mapped_row, shop, now, user_id)
                if doc is None:
                    skipped += 1
                    continue
                shop_db.parts.insert_one(doc)
                imported += 1

        except Exception as exc:
            skipped += 1
            if len(errors) < 10:
                errors.append(f"Row {i + 2}: {str(exc)}")

    result = {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "total": len(rows),
    }
    if errors:
        result["errors"] = errors
    return jsonify(result)
