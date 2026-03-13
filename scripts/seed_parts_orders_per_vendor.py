from __future__ import annotations

import argparse
import os
import random
from datetime import UTC, datetime

from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument


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


def reserve_order_number_block(shop_db, shop_id, count: int) -> tuple[int, int]:
    if count <= 0:
        raise ValueError("count must be > 0")

    result = shop_db.counters.find_one_and_update(
        {"_id": f"order_number_{shop_id}"},
        {
            "$inc": {"seq": count},
            "$setOnInsert": {"initial_value": 1000},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    seq_end = int(result.get("seq", count))
    seq_start = seq_end - count + 1
    initial = int(result.get("initial_value", 1000))
    order_number_start = initial + seq_start - 1
    order_number_end = initial + seq_end - 1
    return order_number_start, order_number_end


def build_part_pool(shop_db):
    vendor_ids = list(shop_db.vendors.distinct("_id", {"is_active": {"$ne": False}}))
    if not vendor_ids:
        raise RuntimeError("No active vendors found. Seed vendors first.")

    # Pull active parts once and group by vendor.
    part_rows = list(
        shop_db.parts.find(
            {"is_active": {"$ne": False}},
            {
                "_id": 1,
                "vendor_id": 1,
                "part_number": 1,
                "description": 1,
                "average_cost": 1,
            },
        )
    )
    if not part_rows:
        raise RuntimeError("No active parts found. Seed parts first.")

    by_vendor: dict = {}
    all_parts: list[dict] = []

    for row in part_rows:
        part_id = row.get("_id")
        if not part_id:
            continue

        avg_cost = float(row.get("average_cost") or 0.0)
        if avg_cost < 0:
            avg_cost = 0.0

        part_doc = {
            "part_id": part_id,
            "part_number": row.get("part_number") or "",
            "description": row.get("description") or "",
            "average_cost": avg_cost,
        }
        all_parts.append(part_doc)

        vendor_id = row.get("vendor_id")
        if vendor_id:
            by_vendor.setdefault(vendor_id, []).append(part_doc)

    if not all_parts:
        raise RuntimeError("No valid active parts found.")

    return vendor_ids, by_vendor, all_parts


def calc_total_amount(items: list[dict]) -> float:
    total = 0.0
    for item in items:
        qty = int(item.get("quantity") or 0)
        price = float(item.get("price") or 0.0)
        if qty > 0 and price >= 0:
            total += qty * price
    return float(round(total, 2))


def build_order_items(part_pool: list[dict]) -> list[dict]:
    item_count = random.randint(1, 5)
    picked = random.sample(part_pool, k=min(item_count, len(part_pool)))

    items: list[dict] = []
    for part in picked:
        base_price = float(part.get("average_cost") or 0.0)
        if base_price <= 0:
            base_price = round(random.uniform(5.0, 120.0), 2)

        # Keep realistic positive price with small variance from part average cost.
        price = round(max(0.01, base_price * random.uniform(0.85, 1.35)), 2)
        qty = random.randint(1, 8)

        items.append(
            {
                "part_id": part["part_id"],
                "part_number": part.get("part_number") or "",
                "description": part.get("description") or "",
                "price": float(price),
                "quantity": int(qty),
            }
        )

    return items


def seed_parts_orders(
    shop_db,
    tenant_id,
    shop_id,
    user_id,
    vendor_ids: list,
    vendor_part_pool: dict,
    all_parts_pool: list,
    per_vendor: int,
    batch_size: int,
):
    if per_vendor <= 0:
        raise ValueError("per_vendor must be > 0")
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    total_orders = len(vendor_ids) * per_vendor
    order_number_start, order_number_end = reserve_order_number_block(shop_db, shop_id, total_orders)
    next_order_number = order_number_start

    inserted_total = 0

    for v_idx, vendor_id in enumerate(vendor_ids, start=1):
        part_pool = vendor_part_pool.get(vendor_id) or all_parts_pool
        if not part_pool:
            raise RuntimeError("No part pool available for order generation.")

        docs_batch: list[dict] = []

        for _ in range(per_vendor):
            now = utcnow()
            items = build_order_items(part_pool)
            total_amount = calc_total_amount(items)

            doc = {
                "vendor_id": vendor_id,
                "order_number": int(next_order_number),
                "items": items,
                "non_inventory_amounts": [],
                "status": "ordered",
                "payment_status": "unpaid",
                "paid_amount": 0.0,
                "remaining_balance": float(total_amount),
                "is_active": True,
                "created_at": now,
                "updated_at": now,
                "created_by": user_id,
                "updated_by": user_id,
                "shop_id": shop_id,
                "tenant_id": tenant_id,
            }
            next_order_number += 1
            docs_batch.append(doc)

            if len(docs_batch) >= batch_size:
                result = shop_db.parts_orders.insert_many(docs_batch, ordered=False)
                inserted_total += len(result.inserted_ids)
                docs_batch = []

        if docs_batch:
            result = shop_db.parts_orders.insert_many(docs_batch, ordered=False)
            inserted_total += len(result.inserted_ids)

        print(
            f"vendor_progress: {v_idx}/{len(vendor_ids)} | "
            f"vendor_id={vendor_id} | inserted_for_vendor={per_vendor}"
        )

    return {
        "inserted_total": inserted_total,
        "vendors_total": len(vendor_ids),
        "per_vendor": per_vendor,
        "order_number_start": order_number_start,
        "order_number_end": order_number_end,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Seed parts orders: N unpaid orders per active vendor with valid links and order numbers."
    )
    parser.add_argument(
        "--per-vendor",
        type=int,
        default=5000,
        help="How many orders to create per vendor (default: 5000)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Mongo insert batch size (default: 1000)",
    )
    parser.add_argument("--seed", type=int, default=20260312, help="Random seed")
    args = parser.parse_args()

    if args.per_vendor <= 0:
        raise SystemExit("--per-vendor must be > 0")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be > 0")

    random.seed(args.seed)
    load_dotenv()

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_db_name = os.getenv("MASTER_DB_NAME") or os.getenv("MONGO_DB") or "master_db"

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    master_db = client[master_db_name]
    tenant, shop, user, shop_db_name = resolve_context(master_db)
    shop_db = client[str(shop_db_name)]

    vendor_ids, by_vendor, all_parts = build_part_pool(shop_db)

    stats = seed_parts_orders(
        shop_db=shop_db,
        tenant_id=tenant["_id"],
        shop_id=shop["_id"],
        user_id=user["_id"],
        vendor_ids=vendor_ids,
        vendor_part_pool=by_vendor,
        all_parts_pool=all_parts,
        per_vendor=args.per_vendor,
        batch_size=args.batch_size,
    )

    print(f"inserted_parts_orders: {stats['inserted_total']}")
    print(f"vendors_total: {stats['vendors_total']}")
    print(f"orders_per_vendor: {stats['per_vendor']}")
    print(f"order_number_range: {stats['order_number_start']}..{stats['order_number_end']}")
    print(f"payment_status_for_all: unpaid")
    print(f"tenant_id: {tenant['_id']}")
    print(f"shop_id: {shop['_id']}")
    print(f"user_id(created_by/updated_by): {user['_id']}")
    print(f"shop_db: {shop_db_name}")


if __name__ == "__main__":
    main()
