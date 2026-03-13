from __future__ import annotations

import argparse
import os
import random
from datetime import UTC, datetime

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne


NON_INVENTORY_TYPES = (
    "shop_supply",
    "tools",
    "utilities",
    "payment_to_another_service",
)


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


def parse_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def parse_int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def items_total(order_doc: dict) -> float:
    total = 0.0
    for item in (order_doc.get("items") or []):
        if not isinstance(item, dict):
            continue
        qty = max(0, parse_int(item.get("quantity"), 0))
        price = max(0.0, parse_float(item.get("price"), 0.0))
        total += qty * price
    return float(round(total, 2))


def non_inventory_total(lines) -> float:
    total = 0.0
    for line in (lines or []):
        if not isinstance(line, dict):
            continue
        total += max(0.0, parse_float(line.get("amount"), 0.0))
    return float(round(total, 2))


def payment_status_from_amounts(total_amount: float, paid_amount: float) -> str:
    total = float(parse_float(total_amount, 0.0))
    paid = float(parse_float(paid_amount, 0.0))
    if total <= 0:
        return "paid"
    if paid <= 0:
        return "unpaid"
    if paid + 0.01 >= total:
        return "paid"
    return "partially_paid"


def build_non_inventory_lines() -> list[dict]:
    amount = round(random.uniform(7.0, 120.0), 2)
    line_type = random.choice(NON_INVENTORY_TYPES)
    return [
        {
            "type": line_type,
            "description": f"{line_type.replace('_', ' ').title()} charge",
            "amount": float(amount),
        }
    ]


def batch_delete_orders(shop_db, shop_id, ids_to_delete: list, batch_size: int):
    deleted_orders = 0
    deleted_payments = 0

    for i in range(0, len(ids_to_delete), batch_size):
        chunk = ids_to_delete[i : i + batch_size]
        if not chunk:
            continue

        res_orders = shop_db.parts_orders.delete_many({"shop_id": shop_id, "_id": {"$in": chunk}})
        deleted_orders += int(res_orders.deleted_count or 0)

        # Keep referential cleanliness in payments collection.
        res_pay = shop_db.parts_order_payments.delete_many({"parts_order_id": {"$in": chunk}})
        deleted_payments += int(res_pay.deleted_count or 0)

    return deleted_orders, deleted_payments


def main():
    parser = argparse.ArgumentParser(
        description="Reduce parts_orders to target count and enforce non_inventory ratio."
    )
    parser.add_argument("--target", type=int, default=30000, help="Target total orders (default: 30000)")
    parser.add_argument(
        "--non-inventory-ratio",
        type=float,
        default=0.5,
        help="Desired ratio of orders with non_inventory_amounts (default: 0.5)",
    )
    parser.add_argument("--batch-size", type=int, default=2000, help="Batch size for updates/deletes")
    parser.add_argument("--seed", type=int, default=20260312, help="Random seed")
    args = parser.parse_args()

    if args.target <= 0:
        raise SystemExit("--target must be > 0")
    if not (0.0 <= args.non_inventory_ratio <= 1.0):
        raise SystemExit("--non-inventory-ratio must be in [0, 1]")
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

    shop_filter = {"shop_id": shop["_id"]}
    total_before = shop_db.parts_orders.count_documents(shop_filter)

    if total_before < args.target:
        raise RuntimeError(
            f"Current orders ({total_before}) are less than target ({args.target}). "
            "Cannot reduce to target without creating new orders."
        )

    to_delete = total_before - args.target
    ids_to_delete = []
    if to_delete > 0:
        cursor = (
            shop_db.parts_orders.find(shop_filter, {"_id": 1})
            .sort([("created_at", 1), ("_id", 1)])
            .limit(to_delete)
        )
        ids_to_delete = [row.get("_id") for row in cursor if row.get("_id")]

    deleted_orders = 0
    deleted_payments = 0
    if ids_to_delete:
        deleted_orders, deleted_payments = batch_delete_orders(
            shop_db=shop_db,
            shop_id=shop["_id"],
            ids_to_delete=ids_to_delete,
            batch_size=args.batch_size,
        )

    total_after_delete = shop_db.parts_orders.count_documents(shop_filter)
    if total_after_delete != args.target:
        raise RuntimeError(
            f"Expected {args.target} orders after delete, got {total_after_delete}."
        )

    target_non_inventory = int(round(args.target * args.non_inventory_ratio))
    has_non_inv_filter = {**shop_filter, "non_inventory_amounts.0": {"$exists": True}}
    current_non_inventory = shop_db.parts_orders.count_documents(has_non_inv_filter)

    to_add_non_inventory = max(0, target_non_inventory - current_non_inventory)
    to_remove_non_inventory = max(0, current_non_inventory - target_non_inventory)

    now = utcnow()
    user_id = user["_id"]

    if to_add_non_inventory > 0:
        cursor = (
            shop_db.parts_orders.find(
                {**shop_filter, "$or": [{"non_inventory_amounts": {"$exists": False}}, {"non_inventory_amounts": []}]},
                {"_id": 1, "items": 1, "paid_amount": 1},
            )
            .sort([("created_at", -1), ("_id", -1)])
            .limit(to_add_non_inventory)
        )

        ops = []
        touched = 0
        for order in cursor:
            lines = build_non_inventory_lines()
            total_amount = items_total(order) + non_inventory_total(lines)
            paid_amount = max(0.0, parse_float(order.get("paid_amount"), 0.0))
            status = payment_status_from_amounts(total_amount, paid_amount)
            remaining = max(0.0, round(total_amount - paid_amount, 2))

            ops.append(
                UpdateOne(
                    {"_id": order["_id"]},
                    {
                        "$set": {
                            "non_inventory_amounts": lines,
                            "payment_status": status,
                            "remaining_balance": float(remaining),
                            "updated_at": now,
                            "updated_by": user_id,
                        }
                    },
                )
            )
            touched += 1

            if len(ops) >= args.batch_size:
                shop_db.parts_orders.bulk_write(ops, ordered=False)
                ops = []

        if ops:
            shop_db.parts_orders.bulk_write(ops, ordered=False)

        print(f"added_non_inventory_to: {touched}")

    if to_remove_non_inventory > 0:
        cursor = (
            shop_db.parts_orders.find(
                has_non_inv_filter,
                {"_id": 1, "items": 1, "paid_amount": 1},
            )
            .sort([("created_at", 1), ("_id", 1)])
            .limit(to_remove_non_inventory)
        )

        ops = []
        touched = 0
        for order in cursor:
            total_amount = items_total(order)
            paid_amount = max(0.0, parse_float(order.get("paid_amount"), 0.0))
            status = payment_status_from_amounts(total_amount, paid_amount)
            remaining = max(0.0, round(total_amount - paid_amount, 2))

            ops.append(
                UpdateOne(
                    {"_id": order["_id"]},
                    {
                        "$set": {
                            "non_inventory_amounts": [],
                            "payment_status": status,
                            "remaining_balance": float(remaining),
                            "updated_at": now,
                            "updated_by": user_id,
                        }
                    },
                )
            )
            touched += 1

            if len(ops) >= args.batch_size:
                shop_db.parts_orders.bulk_write(ops, ordered=False)
                ops = []

        if ops:
            shop_db.parts_orders.bulk_write(ops, ordered=False)

        print(f"removed_non_inventory_from: {touched}")

    total_final = shop_db.parts_orders.count_documents(shop_filter)
    non_inventory_final = shop_db.parts_orders.count_documents(has_non_inv_filter)

    print(f"total_before: {total_before}")
    print(f"deleted_orders: {deleted_orders}")
    print(f"deleted_related_parts_order_payments: {deleted_payments}")
    print(f"total_after: {total_final}")
    print(f"target_total: {args.target}")
    print(f"target_non_inventory_orders: {target_non_inventory}")
    print(f"non_inventory_orders_final: {non_inventory_final}")
    print(f"tenant_id: {tenant['_id']}")
    print(f"shop_id: {shop['_id']}")
    print(f"shop_db: {shop_db_name}")

    if total_final != args.target or non_inventory_final != target_non_inventory:
        raise RuntimeError("Final counts do not match requested targets.")


if __name__ == "__main__":
    main()
