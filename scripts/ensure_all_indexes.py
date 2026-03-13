from __future__ import annotations

import os

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure


def safe_create_index(collection, keys, **kwargs):
    try:
        return collection.create_index(keys, **kwargs)
    except OperationFailure as exc:
        msg = str(exc)
        code = getattr(exc, "code", None)
        if code == 85 and "already exists with a different name" in msg:
            return None
        raise


def ensure_master_collections_indexes(master_db):
    safe_create_index(master_db.tenants, [("slug", ASCENDING)], unique=True, name="uniq_tenant_slug")
    safe_create_index(master_db.tenants, [("db_name", ASCENDING)], unique=True, name="uniq_tenant_db_name")

    safe_create_index(master_db.users, [("email", ASCENDING)], unique=True, name="uniq_user_email_global")
    safe_create_index(master_db.users, [("tenant_id", ASCENDING), ("is_active", ASCENDING)], name="idx_users_tenant_active")

    safe_create_index(master_db.shops, [("tenant_id", ASCENDING)], name="idx_shop_tenant_id")
    safe_create_index(master_db.shops, [("tenant_id", ASCENDING), ("created_at", ASCENDING)], name="idx_shop_tenant_created")


def ensure_shop_collections_indexes(shop_db):
    safe_create_index(shop_db.vendors, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_vendors_shop_active_name")
    safe_create_index(shop_db.vendors, [("shop_id", ASCENDING), ("created_at", DESCENDING)], name="idx_vendors_shop_created_desc")

    safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("part_number", ASCENDING)], name="idx_parts_shop_active_partnum")
    safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("vendor_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_shop_vendor_active")
    safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("category_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_shop_category_active")
    safe_create_index(shop_db.parts, [("shop_id", ASCENDING), ("location_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_shop_location_active")
    safe_create_index(shop_db.parts, [("search_terms", ASCENDING)], name="idx_parts_search_terms")

    safe_create_index(shop_db.parts_orders, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_parts_orders_shop_active_created_desc")
    safe_create_index(shop_db.parts_orders, [("shop_id", ASCENDING), ("vendor_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_parts_orders_shop_vendor_active_created_desc")
    safe_create_index(shop_db.parts_orders, [("shop_id", ASCENDING), ("order_number", ASCENDING)], name="idx_parts_orders_shop_order_number")
    safe_create_index(shop_db.parts_orders, [("payment_status", ASCENDING)], name="idx_parts_orders_payment_status")
    safe_create_index(shop_db.parts_orders, [("status", ASCENDING)], name="idx_parts_orders_status")

    safe_create_index(shop_db.parts_order_payments, [("parts_order_id", ASCENDING), ("is_active", ASCENDING)], name="idx_parts_order_payments_order_active")
    safe_create_index(shop_db.parts_order_payments, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_parts_order_payments_shop_active_created_desc")

    safe_create_index(shop_db.customers, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_customers_shop_active_created_desc")
    safe_create_index(shop_db.customers, [("shop_id", ASCENDING), ("name", ASCENDING)], name="idx_customers_shop_name")
    safe_create_index(shop_db.units, [("shop_id", ASCENDING), ("customer_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_units_shop_customer_active_created_desc")

    safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_active_created_desc")
    safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("customer_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_customer_active_created_desc")
    safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("unit_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_unit_active_created_desc")
    safe_create_index(shop_db.work_orders, [("shop_id", ASCENDING), ("status", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_orders_shop_status_active_created_desc")

    safe_create_index(shop_db.work_order_payments, [("work_order_id", ASCENDING), ("is_active", ASCENDING)], name="idx_work_order_payments_order_active")
    safe_create_index(shop_db.work_order_payments, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("created_at", DESCENDING)], name="idx_work_order_payments_shop_active_created_desc")

    safe_create_index(shop_db.labor_rates, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_labor_rates_shop_active_name")
    safe_create_index(shop_db.labor_rates, [("shop_id", ASCENDING), ("code", ASCENDING)], name="idx_labor_rates_shop_code")

    safe_create_index(shop_db.parts_categories, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_parts_categories_shop_active_name")
    safe_create_index(shop_db.parts_locations, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("name", ASCENDING)], name="idx_parts_locations_shop_active_name")
    safe_create_index(shop_db.parts_pricing_rules, [("shop_id", ASCENDING)], name="idx_parts_pricing_rules_shop")

    safe_create_index(shop_db.cores, [("shop_id", ASCENDING), ("is_active", ASCENDING), ("part_id", ASCENDING)], name="idx_cores_shop_active_part")
    safe_create_index(shop_db.cores, [("shop_id", ASCENDING), ("quantity", DESCENDING)], name="idx_cores_shop_quantity_desc")

    safe_create_index(shop_db.counters, [("_id", ASCENDING)], name="idx_counters_id")
    safe_create_index(shop_db.settings, [("shop_id", ASCENDING)], name="idx_settings_shop")


def main():
    load_dotenv()

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_db_name = os.getenv("MASTER_DB_NAME") or os.getenv("MONGO_DB") or "master_db"

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    master_db = client[master_db_name]
    ensure_master_collections_indexes(master_db)

    shops = list(
        master_db.shops.find(
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
    )

    seen_db_names = set()
    for shop in shops:
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
        if db_name in seen_db_names:
            continue
        seen_db_names.add(db_name)
        ensure_shop_collections_indexes(client[db_name])

    print(f"master_db: {master_db_name}")
    print(f"shop_databases_indexed: {len(seen_db_names)}")


if __name__ == "__main__":
    main()
