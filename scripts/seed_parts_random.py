from __future__ import annotations

import argparse
import os
import random
import string
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.utils.parts_search import build_parts_search_terms


def utcnow() -> datetime:
    return datetime.now(UTC)


def pick_shop_db_name(shop_doc: dict) -> str | None:
    return (
        shop_doc.get("db_name")
        or shop_doc.get("database")
        or shop_doc.get("db")
        or shop_doc.get("mongo_db")
        or shop_doc.get("shop_db")
    )


def resolve_context(master_db):
    tenant = master_db.tenants.find_one({"status": "active"}, sort=[("created_at", 1)])
    if not tenant:
        raise RuntimeError("No active tenant found")

    shop = master_db.shops.find_one({"tenant_id": tenant["_id"]}, sort=[("created_at", 1)])
    if not shop:
        raise RuntimeError("No shop found for active tenant")

    user = master_db.users.find_one(
        {
            "tenant_id": tenant["_id"],
            "is_active": True,
            "$or": [
                {"shop_ids": shop["_id"]},
                {"shop_id": shop["_id"]},
                {"shop_ids": {"$exists": False}},
            ],
        },
        sort=[("created_at", 1)],
    )
    if not user:
        user = master_db.users.find_one(
            {"tenant_id": tenant["_id"], "is_active": True},
            sort=[("created_at", 1)],
        )
    if not user:
        raise RuntimeError("No active user found for tenant")

    shop_db_name = pick_shop_db_name(shop)
    if not shop_db_name:
        raise RuntimeError("Shop has no db_name/database field")

    return tenant, shop, user, shop_db_name


def random_part_number(index: int) -> str:
    return f"SP-26-{index + 1:06d}"


def random_description() -> str:
    left = random.choice([
        "Seal",
        "Bearing",
        "Bushing",
        "Filter",
        "Rotor",
        "Pad",
        "Sensor",
        "Pump",
        "Gasket",
        "Hub",
        "Valve",
        "Module",
    ])
    right = random.choice([
        "Kit",
        "Assembly",
        "Premium",
        "Heavy Duty",
        "Performance",
        "Standard",
        "Service",
        "Replacement",
    ])
    return f"{left} {right}"


def random_reference() -> str | None:
    if random.random() < 0.4:
        return None
    token = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
    return f"REF-{token}"


def build_misc_charges() -> list[dict]:
    charge_names = [
        "Disposal",
        "Handling",
        "Packaging",
        "Environmental fee",
        "Special order fee",
        "Warehouse fee",
    ]
    count = random.randint(1, 3)
    out: list[dict] = []
    for _ in range(count):
        out.append(
            {
                "description": random.choice(charge_names),
                "price": float(round(random.uniform(1.0, 45.0), 2)),
            }
        )
    return out


def build_part_doc(
    idx: int,
    tenant_id,
    shop_id,
    user_id,
    vendor_ids: list,
    category_ids: list,
    location_ids: list,
):
    part_number = random_part_number(idx)
    description = random_description()
    reference = random_reference()

    do_not_track_inventory = random.random() < 0.18

    # Rule: do_not_track parts cannot have core charges.
    core_has_charge = False if do_not_track_inventory else (random.random() < 0.35)
    core_cost = float(round(random.uniform(2.0, 35.0), 2)) if core_has_charge else None

    misc_has_charge = random.random() < 0.30
    misc_charges = build_misc_charges() if misc_has_charge else []

    average_cost = float(round(random.uniform(5.0, 450.0), 2))

    doc = {
        "part_number": part_number,
        "description": description,
        "reference": reference,
        "search_terms": build_parts_search_terms(part_number, description, reference),
        "vendor_id": random.choice(vendor_ids) if vendor_ids else None,
        "category_id": random.choice(category_ids) if category_ids else None,
        "location_id": random.choice(location_ids) if location_ids and random.random() < 0.65 else None,
        "do_not_track_inventory": bool(do_not_track_inventory),
        "average_cost": average_cost,
        "core_has_charge": bool(core_has_charge),
        "core_cost": core_cost,
        "misc_has_charge": bool(misc_has_charge),
        "misc_charges": misc_charges,
        "is_active": True,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "created_by": user_id,
        "updated_by": user_id,
        "deactivated_at": None,
        "deactivated_by": None,
        "shop_id": shop_id,
        "tenant_id": tenant_id,
    }

    if not do_not_track_inventory:
        doc["in_stock"] = random.randint(0, 120)

    return doc


def main():
    parser = argparse.ArgumentParser(description="Seed random parts with core/misc/do-not-track rules.")
    parser.add_argument("--count", type=int, default=3000, help="How many parts to insert (default: 3000)")
    parser.add_argument("--seed", type=int, default=20260311, help="Random seed")
    args = parser.parse_args()

    if args.count <= 0:
        raise SystemExit("--count must be > 0")

    random.seed(args.seed)
    load_dotenv()

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_db_name = os.getenv("MASTER_DB_NAME") or os.getenv("MONGO_DB") or "master_db"

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    master_db = client[master_db_name]
    tenant, shop, user, shop_db_name = resolve_context(master_db)
    shop_db = client[str(shop_db_name)]

    vendor_ids = list(shop_db.vendors.distinct("_id", {"is_active": {"$ne": False}}))
    category_ids = list(shop_db.parts_categories.distinct("_id", {"is_active": {"$ne": False}}))
    location_ids = list(shop_db.parts_locations.distinct("_id", {"is_active": {"$ne": False}}))

    if not vendor_ids:
        raise RuntimeError("No active vendors found. Seed vendors first.")
    if not category_ids:
        raise RuntimeError("No active parts categories found.")

    docs = [
        build_part_doc(
            idx=i,
            tenant_id=tenant["_id"],
            shop_id=shop["_id"],
            user_id=user["_id"],
            vendor_ids=vendor_ids,
            category_ids=category_ids,
            location_ids=location_ids,
        )
        for i in range(args.count)
    ]

    result = shop_db.parts.insert_many(docs, ordered=False)

    print(f"inserted_parts: {len(result.inserted_ids)}")
    print(f"tenant_id: {tenant['_id']}")
    print(f"shop_id: {shop['_id']}")
    print(f"user_id(created_by/updated_by): {user['_id']}")
    print(f"shop_db: {shop_db_name}")


if __name__ == "__main__":
    main()
