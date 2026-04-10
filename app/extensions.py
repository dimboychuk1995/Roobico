from __future__ import annotations

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import OperationFailure
from flask import current_app

def get_mongo_client() -> MongoClient:
    client = current_app.extensions.get("mongo_client")
    if client is None:
        raise RuntimeError("Mongo client is not initialized. Call init_mongo(app) inside create_app().")
    return client

def get_master_db():
    client = get_mongo_client()
    return client[current_app.config["MASTER_DB_NAME"]]


def _safe_create_index(collection, keys, **kwargs):
    try:
        return collection.create_index(keys, **kwargs)
    except OperationFailure as exc:
        msg = str(exc)
        code = getattr(exc, "code", None)
        # Existing equivalent index may have another name in legacy datasets.
        if code == 85 and "already exists with a different name" in msg:
            return None
        raise


def ensure_master_collections_indexes(master_db):
    """
    Create indexes for master DB collections.
    Safe to call multiple times.
    """
    _safe_create_index(master_db.tenants, [("slug", ASCENDING)], unique=True, name="uniq_tenant_slug")
    _safe_create_index(master_db.tenants, [("db_name", ASCENDING)], unique=True, name="uniq_tenant_db_name")

    # global unique email (so login can be email+password only)
    _safe_create_index(master_db.users, [("email", ASCENDING)], unique=True, name="uniq_user_email_global")
    _safe_create_index(master_db.users, [("tenant_id", ASCENDING), ("is_active", ASCENDING)], name="idx_users_tenant_active")

    _safe_create_index(master_db.shops, [("tenant_id", ASCENDING)], name="idx_shop_tenant_id")
    _safe_create_index(master_db.shops, [("tenant_id", ASCENDING), ("created_at", ASCENDING)], name="idx_shop_tenant_created")

    # Centralized ZIP -> sales tax lookup data for all shops.
    _safe_create_index(master_db.zip_sales_tax_rates, [("zip_code", ASCENDING)], unique=True, name="uniq_zip_sales_tax_rates_zip")
    _safe_create_index(master_db.zip_sales_tax_rates, [("updated_at", DESCENDING)], name="idx_zip_sales_tax_rates_updated_desc")

    # Audit journal for create/edit/delete operations across all routes.
    _safe_create_index(master_db.audit_journal, [("created_at", DESCENDING)], name="idx_audit_journal_created_desc")
    _safe_create_index(master_db.audit_journal, [("tenant_id", ASCENDING), ("created_at", DESCENDING)], name="idx_audit_journal_tenant_created")
    _safe_create_index(master_db.audit_journal, [("shop_id", ASCENDING), ("created_at", DESCENDING)], name="idx_audit_journal_shop_created")
    _safe_create_index(master_db.audit_journal, [("endpoint", ASCENDING), ("created_at", DESCENDING)], name="idx_audit_journal_endpoint_created")
    _safe_create_index(master_db.audit_journal, [("method", ASCENDING), ("created_at", DESCENDING)], name="idx_audit_journal_method_created")


def ensure_shop_collections_indexes(shop_db):
    """
    Create indexes for shop-scoped DB collections.
    Safe to call multiple times.
    """
    # Vendors
    _safe_create_index(shop_db.vendors, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_vendors_shop_active_name")
    _safe_create_index(shop_db.vendors, [("shop_id", ASCENDING), ("created_at", DESCENDING)], name="idx_vendors_shop_created_desc")

    # Parts
    _safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("part_number", ASCENDING)], name="idx_parts_shop_active_partnum")
    _safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("vendor_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_shop_vendor_active")
    _safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("category_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_shop_category_active")
    _safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("location_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_shop_location_active")
    _safe_create_index(shop_db.parts, [("search_terms", ASCENDING)], name="idx_parts_search_terms")

    # Parts orders
    _safe_create_index(shop_db.parts_orders, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_parts_orders_shop_active_created_desc")
    _safe_create_index(shop_db.parts_orders, [("shop_id", ASCENDING), ("vendor_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_parts_orders_shop_vendor_active_created_desc")
    _safe_create_index(shop_db.parts_orders, [("shop_id", ASCENDING), ("order_number", ASCENDING)], name="idx_parts_orders_shop_order_number")
    _safe_create_index(shop_db.parts_orders, [("payment_status", ASCENDING)], name="idx_parts_orders_payment_status")
    _safe_create_index(shop_db.parts_orders, [("status", ASCENDING)], name="idx_parts_orders_status")

    # Parts order payments
    _safe_create_index(shop_db.parts_order_payments, [("parts_order_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_order_payments_order_active")
    _safe_create_index(shop_db.parts_order_payments, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_parts_order_payments_shop_active_created_desc")

    # Customers and units
    _safe_create_index(shop_db.customers, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_customers_shop_active_created_desc")
    _safe_create_index(shop_db.customers, [("shop_id", ASCENDING), ("name", ASCENDING)], name="idx_customers_shop_name")
    _safe_create_index(shop_db.units, [("shop_id", ASCENDING), ("customer_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_units_shop_customer_active_created_desc")

    # Work orders and payments
    _safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_active_created_desc")
    _safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("customer_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_customer_active_created_desc")
    _safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("unit_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_unit_active_created_desc")
    _safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("status", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_status_active_created_desc")

    _safe_create_index(shop_db.work_order_payments, [("work_order_id", ASCENDING), ("is_active", ASCENDING)], name="idx_work_order_payments_order_active")
    _safe_create_index(shop_db.work_order_payments, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_order_payments_shop_active_created_desc")

    # Settings/reference collections used in lookups and pagination
    _safe_create_index(shop_db.labor_rates, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_labor_rates_shop_active_name")
    _safe_create_index(shop_db.labor_rates, [("shop_id", ASCENDING), ("code", ASCENDING)], name="idx_labor_rates_shop_code")

    _safe_create_index(shop_db.parts_categories, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_parts_categories_shop_active_name")
    _safe_create_index(shop_db.parts_locations, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_parts_locations_shop_active_name")
    _safe_create_index(shop_db.parts_pricing_rules, [("shop_id", ASCENDING)], name="idx_parts_pricing_rules_shop")

    _safe_create_index(shop_db.cores, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("part_id", ASCENDING)], name="idx_cores_shop_active_part")
    _safe_create_index(shop_db.cores, [("shop_id", ASCENDING), ("quantity", DESCENDING)], name="idx_cores_shop_quantity_desc")

    # Generic counters/settings collections used by parts/work-orders settings.
    _safe_create_index(shop_db.counters, [("_id", ASCENDING)], name="idx_counters_id")
    _safe_create_index(shop_db.settings, [("shop_id", ASCENDING)], name="idx_settings_shop")

    # Attachments (photos / PDFs stored as BSON Binary)
    _safe_create_index(shop_db.attachments, [("entity_type", ASCENDING), ("entity_id", ASCENDING), ("uploaded_at", DESCENDING)], name="idx_attachments_entity_type_id")
    _safe_create_index(shop_db.attachments, [("parent_id", ASCENDING)], name="idx_attachments_parent_id", sparse=True)


def ensure_all_shop_databases_indexes(client, master_db):
    shops_cursor = master_db.shops.find(
        {
            "$or": [
                {"db_name": {"$exists": True, "$ne": None}},
                {"database": {"$exists": True, "$ne": None}},
                {"db": {"$exists": True, "$ne": None}},
                {"mongo_db": {"$exists": True, "$ne": None}},
                {"shop_db": {"$exists": True, "$ne": None}},
            ]
        },
        {"db_name": 1, "database": 1, "db": 1, "mongo_db": 1, "shop_db": 1},
    )

    seen = set()
    for shop in shops_cursor:
        db_name = (
            shop.get("db_name")
            or shop.get("database")
            or shop.get("db")
            or shop.get("mongo_db")
            or shop.get("shop_db")
        )
        if not db_name:
            continue
        db_name = str(db_name)
        if db_name in seen:
            continue
        seen.add(db_name)
        ensure_shop_collections_indexes(client[db_name])

def init_mongo(app):
    client = MongoClient(app.config["MONGO_URI"], serverSelectionTimeoutMS=5000)
    app.extensions["mongo_client"] = client

    # fail fast if mongo not reachable
    client.admin.command("ping")

    master_db = client[app.config["MASTER_DB_NAME"]]
    ensure_master_collections_indexes(master_db)
    ensure_all_shop_databases_indexes(client, master_db)
