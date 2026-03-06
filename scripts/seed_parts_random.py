from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient

# Ensure project root is importable when script is run as scripts/seed_parts_random.py.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.utils.parts_search import build_parts_search_terms


ADJECTIVES = [
    "Front",
    "Rear",
    "Upper",
    "Lower",
    "Heavy",
    "Quick",
    "Standard",
    "Premium",
    "Utility",
    "Industrial",
]

NOUNS = [
    "Seal",
    "Gasket",
    "Bearing",
    "Filter",
    "Bracket",
    "Valve",
    "Sensor",
    "Bushing",
    "Coupler",
    "Clamp",
]

REFERENCES = [
    "none",
    "std",
    "shop",
    "stock",
    "oem",
    "aftermarket",
    "fleet",
    "bulk",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def discover_target_db(client: MongoClient, explicit_db_name: str | None) -> str:
    if explicit_db_name:
        return explicit_db_name

    candidates: list[str] = []
    for db_name in client.list_database_names():
        db = client[db_name]
        if "parts" not in db.list_collection_names():
            continue
        if db.parts.count_documents({}) <= 0:
            continue
        candidates.append(db_name)

    if not candidates:
        raise RuntimeError("No database with non-empty 'parts' collection found. Use --db-name.")

    if len(candidates) > 1:
        raise RuntimeError(
            "More than one DB has non-empty 'parts' collection: "
            + ", ".join(candidates)
            + ". Use --db-name to select target."
        )

    return candidates[0]


def random_part_number(existing: set[str]) -> str:
    while True:
        left = random.randint(10, 99)
        mid = random.randint(100, 999)
        if random.random() < 0.45:
            right = random.randint(10, 99)
            candidate = f"{left}-{mid}-{right}"
        else:
            candidate = f"{left}-{mid}"
        if candidate not in existing:
            return candidate


def make_random_doc(
    *,
    existing_part_numbers: set[str],
    vendor_ids: list[ObjectId],
    category_ids: list[ObjectId],
    location_ids: list[ObjectId],
    shop_id: ObjectId,
    tenant_id: ObjectId,
    user_id: ObjectId,
) -> dict:
    part_number = random_part_number(existing_part_numbers)
    description = f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}"
    reference = random.choice(REFERENCES)

    location_id = None
    if location_ids and random.random() < 0.4:
        location_id = random.choice(location_ids)

    avg_cost = round(random.uniform(0.8, 180.0), 2)
    in_stock = random.randint(0, 600)

    now = utcnow()

    return {
        "part_number": part_number,
        "description": description,
        "reference": reference,
        "search_terms": build_parts_search_terms(part_number, description, reference),
        "vendor_id": random.choice(vendor_ids),
        "category_id": random.choice(category_ids),
        "location_id": location_id,
        "in_stock": in_stock,
        "average_cost": avg_cost,
        "core_has_charge": False,
        "core_cost": None,
        "misc_has_charge": False,
        "misc_charges": [],
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
        "deactivated_at": None,
        "deactivated_by": None,
        "shop_id": shop_id,
        "tenant_id": tenant_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed random parts into shop parts collection.")
    parser.add_argument("--count", type=int, default=200, help="How many random records to insert.")
    parser.add_argument("--db-name", type=str, default=None, help="Target Mongo database name.")
    parser.add_argument("--dry-run", action="store_true", help="Do not insert, only print plan.")
    args = parser.parse_args()

    if args.count <= 0:
        raise SystemExit("--count must be > 0")

    mongo_uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    db_name = discover_target_db(client, args.db_name)
    db = client[db_name]

    sample = db.parts.find_one({"is_active": True}) or db.parts.find_one({})
    if not sample:
        raise RuntimeError("Target parts collection is empty. Create at least one sample record first.")

    shop_id = sample.get("shop_id")
    tenant_id = sample.get("tenant_id")
    user_id = sample.get("updated_by") or sample.get("created_by")

    if not isinstance(shop_id, ObjectId):
        raise RuntimeError("Sample part has invalid shop_id")
    if not isinstance(tenant_id, ObjectId):
        raise RuntimeError("Sample part has invalid tenant_id")
    if not isinstance(user_id, ObjectId):
        raise RuntimeError("Sample part has invalid created_by/updated_by")

    vendor_ids = [
        x["_id"] for x in db.vendors.find({"is_active": {"$ne": False}}, {"_id": 1}) if x.get("_id")
    ]
    category_ids = [
        x["_id"] for x in db.parts_categories.find({"is_active": {"$ne": False}}, {"_id": 1}) if x.get("_id")
    ]
    location_ids = [
        x["_id"] for x in db.parts_locations.find({"is_active": {"$ne": False}}, {"_id": 1}) if x.get("_id")
    ]

    if not vendor_ids:
        raise RuntimeError("No active vendors found in target DB")
    if not category_ids:
        raise RuntimeError("No active categories found in target DB")

    existing_part_numbers = {
        (x.get("part_number") or "").strip()
        for x in db.parts.find({}, {"part_number": 1})
        if (x.get("part_number") or "").strip()
    }

    docs: list[dict] = []
    for _ in range(args.count):
        doc = make_random_doc(
            existing_part_numbers=existing_part_numbers,
            vendor_ids=vendor_ids,
            category_ids=category_ids,
            location_ids=location_ids,
            shop_id=shop_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        docs.append(doc)
        existing_part_numbers.add(doc["part_number"])

    print(f"Target DB: {db_name}")
    print(f"Will insert: {len(docs)}")
    print(f"Using shop_id={shop_id}, tenant_id={tenant_id}, user_id={user_id}")
    print(f"Active refs: vendors={len(vendor_ids)}, categories={len(category_ids)}, locations={len(location_ids)}")

    if args.dry_run:
        print("Dry run complete. No records inserted.")
        return

    result = db.parts.insert_many(docs, ordered=False)
    print(f"Inserted records: {len(result.inserted_ids)}")


if __name__ == "__main__":
    main()
