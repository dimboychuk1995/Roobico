from __future__ import annotations

import argparse
import os
import random
import string
from datetime import UTC, datetime

from dotenv import load_dotenv
from pymongo import MongoClient


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


def random_phone() -> str:
    return f"{random.randint(200, 999)}-{random.randint(200, 999)}-{random.randint(1000, 9999)}"


def random_address() -> str:
    return f"{random.randint(100, 9999)} {random.choice(['Gerry Rd', 'Main St', 'Lake St', 'Cedar Rd', 'Oak Ave'])}"


def random_vendor_name() -> str:
    left = random.choice([
        "Hulk",
        "Titan",
        "Atlas",
        "Summit",
        "Prime",
        "Apex",
        "Vector",
        "Forge",
        "Iron",
        "Rapid",
    ])
    right = random.choice([
        "Depot",
        "Supply",
        "Parts",
        "Distribution",
        "Trucking",
        "Components",
        "Wholesale",
        "Source",
    ])
    token = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
    return f"{left} {right} {token}"


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def random_contact_first_name() -> str:
    return random.choice([
        "Taras",
        "Oleh",
        "Andrii",
        "Ivan",
        "Maksym",
        "Anna",
        "Olena",
        "Iryna",
        "Natalia",
    ])


def random_contact_last_name() -> str:
    return random.choice([
        "Sales",
        "Manager",
        "Support",
        "Koval",
        "Marchak",
        "Melnyk",
        "Tkachenko",
    ])


def random_notes() -> str:
    return random.choice([
        "Trailer Parts",
        "Engine components",
        "Fleet discounts",
        "Same-day delivery",
        "OEM supplier",
        "Aftermarket focus",
    ])


def build_vendor_doc(index: int, tenant_id, shop_id, user_id):
    name = random_vendor_name()
    slug = slugify(name)
    now = utcnow()

    return {
        "name": name,
        "phone": random_phone(),
        "email": f"info+{slug}-{index + 1:03d}@example.com",
        "website": f"{slug}.com",
        "address": random_address(),
        "primary_contact_first_name": random_contact_first_name(),
        "primary_contact_last_name": random_contact_last_name(),
        "notes": random_notes(),
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


def main():
    parser = argparse.ArgumentParser(description="Seed random vendors with real ObjectId links")
    parser.add_argument("--count", type=int, default=40, help="How many vendors to add (default: 40)")
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

    docs = [
        build_vendor_doc(
            index=i,
            tenant_id=tenant["_id"],
            shop_id=shop["_id"],
            user_id=user["_id"],
        )
        for i in range(args.count)
    ]

    result = shop_db.vendors.insert_many(docs, ordered=False)

    print(f"inserted_vendors: {len(result.inserted_ids)}")
    print(f"tenant_id: {tenant['_id']}")
    print(f"shop_id: {shop['_id']}")
    print(f"user_id(created_by/updated_by): {user['_id']}")
    print(f"shop_db: {shop_db_name}")


if __name__ == "__main__":
    main()
