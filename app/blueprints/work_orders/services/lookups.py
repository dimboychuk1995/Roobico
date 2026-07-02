"""
Справочные данные для форм work orders: лейблы клиентов/юнитов, дропдауны,
механики, прайс-правила, настройки shop supply / core charge.
"""
from __future__ import annotations

from bson import ObjectId

from app.extensions import get_master_db
from app.utils.contacts import (
    get_main_contact_email,
    get_main_contact_name,
    get_main_contact_phone,
)
from app.blueprints.work_orders.services.common import f64, oid, round2, utcnow
from app.blueprints.work_orders.services.totals import normalize_parts_payload


def customer_label(c: dict) -> str:
    company = (c.get("company_name") or "").strip()
    if company:
        return company
    name = get_main_contact_name(c, entity_type="customer")
    return name or "(no name)"


def unit_label(u: dict) -> str:
    parts = []
    if u.get("unit_number"):
        parts.append(str(u.get("unit_number")))
    if u.get("year"):
        parts.append(str(u.get("year")))
    if u.get("make"):
        parts.append(str(u.get("make")))
    if u.get("model"):
        parts.append(str(u.get("model")))
    if u.get("vin"):
        parts.append(f"VIN {u.get('vin')}")
    return " • ".join([p for p in parts if p]) or "(unit)"


def get_customers(shop_db):
    # Проекция обязательна: документ клиента тяжёлый (notes, история,
    # адресные поля), а дропдауну нужно лишь несколько полей. Без неё
    # каждая форма создания WO тянула всю коллекцию целиком.
    rows = list(
        shop_db.customers.find(
            {"is_active": True},
            {
                "company_name": 1,
                "first_name": 1,
                "last_name": 1,
                "contacts": 1,
                "default_labor_rate": 1,
                "taxable": 1,
                "address": 1,
            },
        ).sort([("company_name", 1), ("last_name", 1), ("first_name", 1)])
    )

    rate_rows = list(
        shop_db.labor_rates.find({"is_active": True}, {"_id": 1, "code": 1})
    )
    rates_by_id = {r.get("_id"): str(r.get("code") or "").strip() for r in rate_rows if r.get("_id")}

    def resolve_customer_rate_code(value):
        if isinstance(value, ObjectId):
            return rates_by_id.get(value, "")
        legacy = str(value or "").strip().lower()
        if legacy == "standart":
            return "standard"
        return legacy

    return [
        {
            "id": str(x["_id"]),
            "label": customer_label(x),
            "default_labor_rate": resolve_customer_rate_code(x.get("default_labor_rate")),
            "taxable": bool(x.get("taxable", False)),
            "company_name": (x.get("company_name") or "").strip(),
            "contact_name": get_main_contact_name(x, entity_type="customer"),
            "phone": get_main_contact_phone(x, entity_type="customer"),
            "email": get_main_contact_email(x, entity_type="customer"),
            "address": (x.get("address") or "").strip(),
        }
        for x in rows
    ]


def get_units(shop_db, customer_id: ObjectId):
    rows = list(
        shop_db.units.find(
            {"customer_id": customer_id, "is_active": True},
            {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1},
        ).sort([("created_at", -1)])
    )
    return [{"id": str(x["_id"]), "label": unit_label(x)} for x in rows]


def get_labor_rates(shop_db, shop_id: ObjectId):
    rows = list(
        shop_db.labor_rates.find(
            {"shop_id": shop_id, "is_active": True},
            {"code": 1, "name": 1, "hourly_rate": 1},
        ).sort([("name", 1)])
    )
    return [
        {
            "code": r.get("code") or "",
            "name": r.get("name") or (r.get("code") or ""),
            "hourly_rate": float(r.get("hourly_rate") or 0),
        }
        for r in rows
    ]


def _tenant_variants_from_shop(shop: dict):
    raw = shop.get("tenant_id")
    if raw is None:
        return []

    out = {raw, str(raw)}
    parsed = oid(raw)
    if parsed:
        out.add(parsed)
    return list(out)


def get_assignable_mechanics(shop: dict):
    shop_id = shop.get("_id")
    if not shop_id:
        return []

    tenant_variants = _tenant_variants_from_shop(shop)
    if not tenant_variants:
        return []

    shop_variants = [shop_id, str(shop_id)]
    master = get_master_db()
    rows = list(
        master.users.find(
            {
                "tenant_id": {"$in": tenant_variants},
                "is_active": True,
                "role": {"$in": ["senior_mechanic", "mechanic"]},
                "$or": [
                    {"shop_ids": {"$in": shop_variants}},
                    {"shop_id": {"$in": shop_variants}},
                ],
            },
            {
                "first_name": 1,
                "last_name": 1,
                "name": 1,
                "email": 1,
                "role": 1,
            },
        ).sort([("first_name", 1), ("last_name", 1), ("name", 1), ("email", 1)])
    )

    out = []
    for u in rows:
        first_name = str(u.get("first_name") or "").strip()
        last_name = str(u.get("last_name") or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        fallback_name = str(u.get("name") or "").strip()
        email = str(u.get("email") or "").strip()
        display_name = full_name or fallback_name or email
        if not display_name:
            continue

        out.append(
            {
                "id": str(u.get("_id")),
                "name": display_name,
                "role": str(u.get("role") or "").strip(),
            }
        )

    return out


def normalize_assigned_mechanics(raw, mechanics_by_id: dict[str, dict]):
    if not isinstance(raw, list):
        return []

    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue

        user_id = str(item.get("user_id") or item.get("id") or "").strip()
        if not user_id or user_id in seen:
            continue

        mechanic = mechanics_by_id.get(user_id)
        if not mechanic:
            continue

        percent = round2(item.get("percent"))
        if percent < 0:
            percent = 0.0

        user_oid = oid(user_id)
        if not user_oid:
            continue

        out.append(
            {
                "user_id": user_oid,
                "name": mechanic.get("name") or "",
                "role": mechanic.get("role") or "",
                "percent": percent,
            }
        )
        seen.add(user_id)

    if len(out) == 1 and out[0].get("percent", 0) <= 0:
        out[0]["percent"] = 100.0

    return out


def apply_assignments_to_labors(labors, mechanics_by_id: dict[str, dict]):
    if not isinstance(labors, list):
        return []

    out = []
    for block in labors:
        if not isinstance(block, dict):
            continue

        block_copy = dict(block)
        labor_src = block_copy.get("labor") if isinstance(block_copy.get("labor"), dict) else None

        # The frontend (work_order_details.js serializeBlocks) sends a FLAT
        # shape: {labor_description, labor_hours, labor_rate_code, ...}.
        # Lift those into a nested `labor` dict so the stored document always
        # has the canonical shape `{labor: {description, hours, rate_code, ...}}`.
        if labor_src is None and any(
            k in block_copy for k in (
                "labor_description", "labor_hours", "labor_rate_code", "labor_full_total", "issue_description"
            )
        ):
            labor_src = {
                "description": block_copy.get("labor_description"),
                "hours": block_copy.get("labor_hours"),
                "rate_code": block_copy.get("labor_rate_code"),
                "labor_full_total": block_copy.get("labor_full_total"),
                "assigned_mechanics": block_copy.get("assigned_mechanics"),
                "issue_description": block_copy.get("issue_description"),
            }
            # strip the flat fields so we don't store both shapes
            for k in (
                "labor_description", "labor_hours", "labor_rate_code", "labor_full_total"
            ):
                block_copy.pop(k, None)

        if labor_src is not None:
            labor_copy = dict(labor_src)
            labor_copy["description"] = str(labor_copy.get("description") or "").strip()
            labor_copy["hours"] = str(labor_copy.get("hours") or "").strip()
            labor_copy["rate_code"] = str(labor_copy.get("rate_code") or "").strip()
            labor_copy["labor_full_total"] = round2(labor_copy.get("labor_full_total"))
            normalized = normalize_assigned_mechanics(labor_copy.get("assigned_mechanics"), mechanics_by_id)
            labor_copy["assigned_mechanics"] = normalized
            block_copy["labor"] = labor_copy
            block_copy["assigned_mechanics"] = normalized
        else:
            normalized = normalize_assigned_mechanics(block_copy.get("assigned_mechanics"), mechanics_by_id)
            block_copy["assigned_mechanics"] = normalized
            block_copy["labor_full_total"] = round2(block_copy.get("labor_full_total"))

        block_copy["parts"] = normalize_parts_payload(block_copy.get("parts") or [])

        out.append(block_copy)

    return out


def get_pricing_rules_json(shop_db, shop_id: ObjectId, customer_id: ObjectId | None = None):
    customer_doc = None
    if customer_id:
        customer_doc = shop_db.customers.find_one(
            {"_id": customer_id},
            {"pricing_rule_id": 1, "override_part_selling_price": 1},
        )

    doc = None
    customer_rule_id = (customer_doc or {}).get("pricing_rule_id") if customer_doc else None
    if isinstance(customer_rule_id, ObjectId):
        doc = shop_db.parts_pricing_rules.find_one({"_id": customer_rule_id, "shop_id": shop_id})
    if not doc:
        doc = (
            shop_db.parts_pricing_rules.find_one({"shop_id": shop_id, "is_default": True, "is_active": True})
            or shop_db.parts_pricing_rules.find_one({"shop_id": shop_id, "is_default": True})
            or shop_db.parts_pricing_rules.find_one({"shop_id": shop_id, "is_active": True})
            or shop_db.parts_pricing_rules.find_one({"shop_id": shop_id})
        )
    if not doc:
        return None

    mode = (doc.get("mode") or "margin").strip().lower()  # margin | markup
    rules = []
    for r in (doc.get("rules") or []):
        frm = r.get("from")
        to = r.get("to")
        vp = r.get("value_percent")

        try:
            frm_f = float(frm)
        except Exception:
            continue

        if to is None:
            to_f = None
        else:
            try:
                to_f = float(to)
            except Exception:
                to_f = None

        try:
            vp_f = float(vp)
        except Exception:
            continue

        rules.append({"from": frm_f, "to": to_f, "value_percent": vp_f})

    override_part_selling_price = bool((customer_doc or {}).get("override_part_selling_price", False))

    return {
        "mode": mode,
        "rules": rules,
        "override_part_selling_price": override_part_selling_price,
    }


def get_shop_supply_percentage(shop_db, shop_id: ObjectId) -> float:
    col = shop_db.shop_supply_amount_rules
    doc = col.find_one({"shop_id": shop_id})
    if not doc:
        now = utcnow()
        col.update_one(
            {"shop_id": shop_id},
            {
                "$setOnInsert": {
                    "shop_id": shop_id,
                    "shop_supply_procentage": 5,
                    "is_active": True,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                },
            },
            upsert=True,
        )
        doc = col.find_one({"shop_id": shop_id}) or {}
    try:
        raw = doc.get("shop_supply_procentage")
        return float(raw) if raw is not None else 0.0
    except Exception:
        return 0.0


def get_core_charge_default(shop_db, shop_id: ObjectId) -> bool:
    doc = shop_db.core_charge_rules.find_one({"shop_id": shop_id})
    if not doc:
        doc = shop_db.core_charge_rules.find_one({}) or {}
    return bool(doc.get("charge_for_cores_default", True))
