from __future__ import annotations

import argparse
import os
import random
from datetime import UTC, datetime

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient


VIN_ALLOWED = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
UNIT_TYPES = [
    "PASSENGER CAR",
    "SUV",
    "PICKUP",
    "VAN",
    "LIGHT TRUCK",
]
MAKES_MODELS = [
    ("NISSAN", "Altima"),
    ("TOYOTA", "Camry"),
    ("HONDA", "Accord"),
    ("FORD", "F-150"),
    ("CHEVROLET", "Silverado"),
    ("HYUNDAI", "Elantra"),
    ("KIA", "Sorento"),
    ("MAZDA", "CX-5"),
    ("SUBARU", "Outback"),
    ("VOLKSWAGEN", "Jetta"),
]


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


def random_vin(existing_vins: set[str]) -> str:
    while True:
        vin = "".join(random.choice(VIN_ALLOWED) for _ in range(17))
        if vin not in existing_vins:
            existing_vins.add(vin)
            return vin


def build_unit_doc(
    customer_id: ObjectId,
    tenant_id: ObjectId,
    shop_id: ObjectId,
    user_id: ObjectId,
    unit_number: str,
    existing_vins: set[str],
):
    make, model = random.choice(MAKES_MODELS)
    year = random.randint(2005, 2026)
    now = utcnow()

    mileage = None
    if random.random() > 0.2:
        mileage = random.randint(10_000, 260_000)

    return {
        "customer_id": customer_id,
        "vin": random_vin(existing_vins),
        "unit_number": unit_number,
        "make": make,
        "model": model,
        "year": year,
        "type": random.choice(UNIT_TYPES),
        "mileage": mileage,
        "shop_id": shop_id,
        "tenant_id": tenant_id,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Seed units so each active customer has target number of active units."
    )
    parser.add_argument(
        "--units-per-customer",
        type=int,
        default=25,
        help="Target active units per active customer (default: 25)",
    )
    parser.add_argument("--seed", type=int, default=20260311, help="Random seed")
    args = parser.parse_args()

    if args.units_per_customer <= 0:
        raise SystemExit("--units-per-customer must be > 0")

    random.seed(args.seed)
    load_dotenv()

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_db_name = os.getenv("MASTER_DB_NAME") or os.getenv("MONGO_DB") or "master_db"

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    master_db = client[master_db_name]
    tenant, shop, user, shop_db_name = resolve_context(master_db)
    shop_db = client[str(shop_db_name)]

    customers = list(
        shop_db.customers.find(
            {
                "shop_id": shop["_id"],
                "tenant_id": tenant["_id"],
                "is_active": True,
            },
            {"_id": 1},
        )
    )
    if not customers:
        raise RuntimeError("No active customers found for this shop/tenant")

    existing_vins = set(
        v for v in shop_db.units.distinct("vin", {"vin": {"$type": "string"}}) if v
    )

    docs_to_insert = []
    touched_customers = 0

    for row in customers:
        customer_id = row["_id"]
        current_count = shop_db.units.count_documents(
            {
                "customer_id": customer_id,
                "shop_id": shop["_id"],
                "tenant_id": tenant["_id"],
                "is_active": True,
            }
        )

        deficit = args.units_per_customer - current_count
        if deficit <= 0:
            continue

        touched_customers += 1
        for i in range(deficit):
            unit_number = f"{str(customer_id)[-4:]}-{current_count + i + 1:02d}"
            docs_to_insert.append(
                build_unit_doc(
                    customer_id=customer_id,
                    tenant_id=tenant["_id"],
                    shop_id=shop["_id"],
                    user_id=user["_id"],
                    unit_number=unit_number,
                    existing_vins=existing_vins,
                )
            )

    inserted = 0
    if docs_to_insert:
        result = shop_db.units.insert_many(docs_to_insert, ordered=False)
        inserted = len(result.inserted_ids)

    print(f"active_customers: {len(customers)}")
    print(f"customers_topped_up: {touched_customers}")
    print(f"inserted_units: {inserted}")
    print(f"target_units_per_customer: {args.units_per_customer}")
    print(f"tenant_id: {tenant['_id']}")
    print(f"shop_id: {shop['_id']}")
    print(f"user_id(created_by/updated_by): {user['_id']}")
    print(f"shop_db: {shop_db_name}")


if __name__ == "__main__":
    main()
