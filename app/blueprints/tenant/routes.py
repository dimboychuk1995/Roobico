from __future__ import annotations

import re
import hashlib
from datetime import datetime, timezone

from bson import ObjectId
from flask import request, jsonify
from werkzeug.security import generate_password_hash
from pymongo.errors import DuplicateKeyError

from app.extensions import get_master_db, get_mongo_client
from app.utils.sales_tax import extract_us_zip, refresh_zip_tax_rate
from . import tenant_bp


def utcnow():
    return datetime.now(timezone.utc)


def slugify_company_name(name: str) -> str:
    """
    Convert company name to safe slug: a-z0-9-
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) < 3:
        s = (s + "-tenant").strip("-")
    return s[:32]


def make_tenant_db_name(company_name: str) -> str:
    slug = slugify_company_name(company_name)
    return f"tenant_{slug}"


def slugify_shop_name(name: str) -> str:
    """
    Shop slug (same rules).
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) < 2:
        s = (s + "-shop").strip("-")
    return s[:32]


def make_shop_db_name(tenant_slug: str, shop_slug: str) -> str:
    """
    Atlas limit: max 38 bytes for db name.
    Format: shop_<tenant10>_<shop10>_<hash6>
    """
    t10 = (tenant_slug or "")[:10]
    s10 = (shop_slug or "")[:10]

    raw = f"{tenant_slug}:{shop_slug}"
    h6 = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]

    db = f"shop_{t10}_{s10}_{h6}"
    return db[:38]


def ensure_tenant_collections_indexes(tenant_db):
    """
    Create indexes for tenant DB collections.
    Safe to call multiple times.
    """
    tenant_db.settings.create_index("key", unique=True, name="uniq_settings_key")
    tenant_db.roles.create_index("key", unique=True, name="uniq_roles_key")
    tenant_db.roles.create_index("name", name="idx_roles_name")


def init_tenant_database(db_name: str, tenant_doc: dict):
    """
    Creates tenant DB and seeds defaults:
    - settings
    - roles (RBAC)
    """
    client = get_mongo_client()
    tdb = client[db_name]

    tdb.settings.insert_one({
        "key": "tenant",
        "tenant_name": tenant_doc["name"],
        "tenant_slug": tenant_doc["slug"],
        "timezone": tenant_doc.get("timezone", "UTC"),
        "created_at": utcnow(),
    })

    ensure_tenant_collections_indexes(tdb)

    from app.constants.permissions import build_default_roles

    if tdb.roles.count_documents({}) == 0:
        now = utcnow()
        roles = build_default_roles()
        for r in roles:
            r["created_at"] = now
            r["updated_at"] = now
        tdb.roles.insert_many(roles)


# -----------------------------
# NEW: default parts categories seeding
# -----------------------------

DEFAULT_PARTS_CATEGORIES = [
    "Filters",
    "Electrical",
    "Exhaust",
    "Body",
    "Interior",
    "DEF",
    "Cores",
]


def _slugify_simple(name: str) -> str:
    s = (name or "").strip().lower()
    out = []
    last_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        else:
            if not last_dash:
                out.append("-")
                last_dash = True
    slug = "".join(out).strip("-")
    return slug or "category"


def seed_parts_categories(shop_db, shop_id: ObjectId):
    """
    Ensure default parts categories exist in shop DB (idempotent).
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.parts_categories

    # Prevent duplicates per shop
    try:
        col.create_index([("shop_id", 1), ("slug", 1)], unique=True, name="uniq_parts_categories_shop_slug")
    except Exception:
        pass

    now = utcnow()

    for name in DEFAULT_PARTS_CATEGORIES:
        slug = _slugify_simple(name)
        col.update_one(
            {"shop_id": shop_id, "slug": slug},
            {
                "$setOnInsert": {
                    "name": name,
                    "slug": slug,
                    "shop_id": shop_id,
                    "is_active": True,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                },
            },
            upsert=True,
        )


# -----------------------------
# NEW: labor rates seeding (shop DB)
# -----------------------------

DEFAULT_LABOR_RATES = [
    {"code": "standard", "name": "Standard", "hourly_rate": 100.0},
    {"code": "after_hours", "name": "After Hours", "hourly_rate": 150.0},
]


def seed_labor_rates(shop_db, shop_id: ObjectId):
    """
    Ensure default labor rates exist in shop DB (idempotent, per shop).
    Collection: labor_rates
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.labor_rates

    # Prevent duplicates per shop
    try:
        col.create_index([("shop_id", 1), ("code", 1)], unique=True, name="uniq_labor_rates_shop_code")
    except Exception:
        pass

    now = utcnow()

    for item in DEFAULT_LABOR_RATES:
        code = item["code"]
        col.update_one(
            {"shop_id": shop_id, "code": code},
            {
                "$setOnInsert": {
                    "shop_id": shop_id,
                    "code": code,
                    "name": item.get("name") or code,
                    "hourly_rate": float(item.get("hourly_rate") or 0),
                    "is_active": True,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                }
            },
            upsert=True,
        )


# -----------------------------
# NEW: shop supply amount rules seeding (shop DB)
# -----------------------------

def seed_shop_supply_amount_rules(shop_db, shop_id: ObjectId):
    """
    Ensure default shop supply amount rules exist in shop DB (idempotent).
    Collection: shop_supply_amount_rules
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.shop_supply_amount_rules

    try:
        col.create_index([("shop_id", 1)], unique=True, name="uniq_shop_supply_amount_rules_shop")
    except Exception:
        pass

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


def seed_core_charge_rules(shop_db, shop_id: ObjectId, created_by=None, updated_by=None):
    """
    Ensure default core charge rules exist in shop DB (idempotent).
    Collection: core_charge_rules
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.core_charge_rules

    try:
        col.create_index([("shop_id", 1)], unique=True, name="uniq_core_charge_rules_shop")
    except Exception:
        pass

    now = utcnow()

    col.update_one(
        {"shop_id": shop_id},
        {
            "$setOnInsert": {
                "shop_id": shop_id,
                "charge_for_cores_default": False,
                "created_at": now,
                "created_by": created_by,
            },
            "$set": {
                "updated_at": now,
                "updated_by": updated_by,
            },
        },
        upsert=True,
    )


def ensure_shop_collections_indexes(shop_db):
    """
    Create indexes for shop-scoped collections used by UI/API queries.
    Safe to call multiple times.
    """
    if shop_db is None:
        return

    # parts
    parts = shop_db.parts
    parts.create_index([("shop_id", 1), ("is_active", 1), ("part_number", 1)], name="idx_parts_shop_active_part_number")
    parts.create_index([("shop_id", 1), ("is_active", 1), ("in_stock", 1)], name="idx_parts_shop_active_in_stock")
    parts.create_index([("shop_id", 1), ("is_active", 1), ("search_terms", 1)], name="idx_parts_shop_active_search_terms")

    # customers
    customers = shop_db.customers
    customers.create_index([("shop_id", 1), ("is_active", -1), ("company_name", 1), ("last_name", 1), ("first_name", 1), ("created_at", -1)], name="idx_customers_shop_list")

    # vendors
    vendors = shop_db.vendors
    vendors.create_index([("shop_id", 1), ("is_active", -1), ("name", 1), ("created_at", -1)], name="idx_vendors_shop_list")

    # units
    units = shop_db.units
    units.create_index([("customer_id", 1), ("is_active", 1), ("created_at", -1)], name="idx_units_customer_active_created")
    units.create_index([("shop_id", 1), ("is_active", 1), ("created_at", -1)], name="idx_units_shop_active_created")

    # work orders
    work_orders = shop_db.work_orders
    work_orders.create_index([("shop_id", 1), ("is_active", 1), ("created_at", -1)], name="idx_work_orders_shop_active_created")
    work_orders.create_index([("shop_id", 1), ("status", 1), ("created_at", -1)], name="idx_work_orders_shop_status_created")

    # payments
    payments = shop_db.work_order_payments
    payments.create_index([("work_order_id", 1), ("is_active", 1), ("created_at", -1)], name="idx_payments_work_order_active_created")
    payments.create_index([("shop_id", 1), ("is_active", 1), ("created_at", -1)], name="idx_payments_shop_active_created")

    # reference collections used in settings/work order forms
    shop_db.parts_categories.create_index([("shop_id", 1), ("is_active", 1), ("name", 1)], name="idx_parts_categories_shop_active_name")
    shop_db.parts_locations.create_index([("shop_id", 1), ("is_active", 1), ("name", 1)], name="idx_parts_locations_shop_active_name")
    shop_db.labor_rates.create_index([("shop_id", 1), ("is_active", 1), ("name", 1)], name="idx_labor_rates_shop_active_name")


def init_shop_database(shop_db_name: str, tenant_doc: dict, shop_doc: dict, actor_user_id=None):
    """
    Creates shop DB and seeds minimal defaults:
    - settings (idempotent upsert)
    - default parts categories (idempotent)
    - default parts pricing rules (margin/markup ranges) (idempotent)
    - default labor rates (idempotent)   <-- NEW
    - default shop supply amount rules (idempotent)
    """
    client = get_mongo_client()
    sdb = client[shop_db_name]

    now = utcnow()

    # -----------------------------
    # settings (idempotent)
    # -----------------------------
    sdb.settings.update_one(
        {"key": "shop"},
        {"$setOnInsert": {
            "key": "shop",
            "shop_name": shop_doc.get("name"),
            "shop_slug": shop_doc.get("slug"),
            "created_at": now,
        }},
        upsert=True
    )

    sdb.settings.update_one(
        {"key": "tenant_ref"},
        {"$setOnInsert": {
            "key": "tenant_ref",
            "tenant_name": tenant_doc.get("name"),
            "tenant_slug": tenant_doc.get("slug"),
            "timezone": tenant_doc.get("timezone", "UTC"),
            "created_at": now,
        }},
        upsert=True
    )

    try:
        sdb.settings.create_index("key", unique=True, name="uniq_settings_key")
    except Exception:
        pass

    # -----------------------------
    # resolve shop_id (ObjectId)
    # -----------------------------
    shop_id = shop_doc.get("_id")
    shop_oid = None

    if isinstance(shop_id, ObjectId):
        shop_oid = shop_id
    elif shop_id is not None:
        try:
            shop_oid = ObjectId(str(shop_id))
        except Exception:
            shop_oid = None

    if not shop_oid:
        # without shop_id we cannot seed shop-scoped collections
        return

    # -----------------------------
    # create indexes for all shop collections
    # -----------------------------
    try:
        ensure_shop_collections_indexes(sdb)
    except Exception:
        pass

    # -----------------------------
    # seed default categories
    # -----------------------------
    try:
        seed_parts_categories(sdb, shop_oid)
    except Exception:
        pass

    # -----------------------------
    # seed default parts pricing rules
    # -----------------------------
    col = sdb.parts_pricing_rules

    # one rules doc per shop
    try:
        col.create_index([("shop_id", 1)], unique=True, name="uniq_parts_pricing_rules_shop")
    except Exception:
        pass

    default_rules = [
        {"from": 0, "to": 20, "value_percent": 100},
        {"from": 20, "to": 100, "value_percent": 60},
        {"from": 100, "to": None, "value_percent": 50},  # None = infinity
    ]

    # idempotent upsert
    col.update_one(
        {"shop_id": shop_oid},
        {
            "$setOnInsert": {
                "shop_id": shop_oid,
                "mode": "margin",          # "margin" or "markup"
                "rules": default_rules,
                "is_active": True,
                "created_at": now,
            },
            "$set": {
                "updated_at": now,
            }
        },
        upsert=True
    )

    # -----------------------------
    # seed default labor rates (NEW)
    # -----------------------------
    try:
        seed_labor_rates(sdb, shop_oid)
    except Exception:
        pass

    # -----------------------------
    # seed default shop supply amount rules (NEW)
    # -----------------------------
    try:
        seed_shop_supply_amount_rules(sdb, shop_oid)
    except Exception:
        pass

    # -----------------------------
    # seed default core charge rules (NEW)
    # -----------------------------
    try:
        seed_core_charge_rules(sdb, shop_oid, created_by=actor_user_id, updated_by=actor_user_id)
    except Exception:
        pass


@tenant_bp.post("/register")
def register_tenant():
    master = get_master_db()

    company_name = (request.form.get("company_name") or "").strip()
    company_address = (request.form.get("company_address") or "").strip()
    company_phone = (request.form.get("company_phone") or "").strip()

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    errors = []
    if len(company_name) < 2:
        errors.append("Company name is required.")
    if len(company_address) < 5:
        errors.append("Company address is required.")
    if len(company_phone) < 7:
        errors.append("Company phone is required.")
    if len(first_name) < 1:
        errors.append("First name is required.")
    if len(last_name) < 1:
        errors.append("Last name is required.")
    if "@" not in email:
        errors.append("Valid email is required.")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters.")

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Email must be globally unique
    if master.users.find_one({"email": email}):
        return jsonify({"ok": False, "errors": ["Email already exists. Use another email."]}), 409

    tenant_slug = slugify_company_name(company_name)
    tenant_db_name = make_tenant_db_name(company_name)

    # First shop name = organization name
    first_shop_name = company_name
    first_shop_slug = slugify_shop_name(first_shop_name)
    shop_db_name = make_shop_db_name(tenant_slug, first_shop_slug)

    created_at = utcnow()
    tenant_id = None
    created_tenant_db = False
    created_shop_db = False

    try:
        tenant_doc = {
            "name": company_name,
            "slug": tenant_slug,
            "db_name": tenant_db_name,
            "address": company_address,
            "zip": extract_us_zip(company_address) or None,
            "phone": company_phone,
            "email": email,
            "contact_name": f"{first_name} {last_name}".strip(),
            "contact_email": email,
            "contact_phone": company_phone,
            "billing_email": email,
            "billing_phone": company_phone,
            "billing_address": company_address,
            "timezone": "America/Chicago",
            "status": "active",
            "created_at": created_at,
            "updated_at": created_at,
        }
        tenant_res = master.tenants.insert_one(tenant_doc)
        tenant_id = tenant_res.inserted_id

        shop_doc = {
            "tenant_id": tenant_id,
            "name": first_shop_name,
            "slug": first_shop_slug,
            "db_name": shop_db_name,
            "address": company_address,
            "zip": extract_us_zip(company_address) or None,
            "phone": company_phone,
            "email": email,
            "billing_address": company_address,
            "status": "active",
            "is_active": True,
            "is_primary": True,
            "created_at": created_at,
            "updated_at": created_at,
        }
        shop_res = master.shops.insert_one(shop_doc)
        shop_id = shop_res.inserted_id

        # Refresh sales-tax cache for the new shop's ZIP (best-effort).
        try:
            refresh_zip_tax_rate(master, company_address)
        except Exception:
            pass

        # ✅ IMPORTANT: нужен shop_id для seed parts_categories / labor_rates
        shop_doc["_id"] = shop_id

        # ✅ user has ONLY shop_ids; NO shop_id field
        user_doc = {
            "tenant_id": tenant_id,
            "shop_ids": [shop_id],
            "first_name": first_name,
            "last_name": last_name,
            "name": f"{first_name} {last_name}".strip(),
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": "owner",
            "is_active": True,
            "created_at": created_at,
            "updated_at": created_at,
        }
        user_res = master.users.insert_one(user_doc)
        owner_user_id = user_res.inserted_id

        # Create tenant DB
        init_tenant_database(tenant_db_name, tenant_doc)
        created_tenant_db = True

        # Create shop DB (will seed parts_categories + pricing rules + labor rates)
        init_shop_database(shop_db_name, tenant_doc, shop_doc, actor_user_id=owner_user_id)
        created_shop_db = True

        return jsonify({
            "ok": True,
            "tenant": {
                "tenant_id": str(tenant_id),
                "name": company_name,
                "slug": tenant_slug,
                "db_name": tenant_db_name,
            },
            "shop": {
                "shop_id": str(shop_id),
                "name": first_shop_name,
                "slug": first_shop_slug,
                "db_name": shop_db_name,
            }
        }), 201

    except DuplicateKeyError:
        if tenant_id:
            master.users.delete_many({"tenant_id": tenant_id})
            master.shops.delete_many({"tenant_id": tenant_id})
            master.tenants.delete_one({"_id": tenant_id})

        client = get_mongo_client()
        if created_shop_db:
            client.drop_database(shop_db_name)
        if created_tenant_db:
            client.drop_database(tenant_db_name)

        return jsonify({
            "ok": False,
            "errors": ["Company already exists (slug/db conflict). Try a different company name."]
        }), 409

    except Exception as e:
        if tenant_id:
            master.users.delete_many({"tenant_id": tenant_id})
            master.shops.delete_many({"tenant_id": tenant_id})
            master.tenants.delete_one({"_id": tenant_id})

        client = get_mongo_client()
        if created_shop_db:
            client.drop_database(shop_db_name)
        if created_tenant_db:
            client.drop_database(tenant_db_name)

        return jsonify({"ok": False, "errors": [str(e)]}), 500


