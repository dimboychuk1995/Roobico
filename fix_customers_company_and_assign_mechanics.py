from __future__ import annotations

import os
import random
from datetime import datetime, timezone

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from werkzeug.security import generate_password_hash


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def round2(value) -> float:
    return round(float(value or 0.0) + 1e-12, 2)


def contact_name_from_customer(customer: dict) -> str:
    contacts = customer.get("contacts") if isinstance(customer.get("contacts"), list) else []
    if contacts:
        main = next((x for x in contacts if isinstance(x, dict) and x.get("is_main")), None)
        if not main:
            main = next((x for x in contacts if isinstance(x, dict)), None)
        if isinstance(main, dict):
            first = str(main.get("first_name") or "").strip()
            last = str(main.get("last_name") or "").strip()
            full = f"{first} {last}".strip()
            if full:
                return full

    first = str(customer.get("first_name") or "").strip()
    last = str(customer.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full

    legacy_email = str(customer.get("email") or "").strip()
    if legacy_email and "@" in legacy_email:
        return legacy_email.split("@", 1)[0].replace(".", " ").title()

    return ""


def build_company_name(customer: dict) -> str:
    base = contact_name_from_customer(customer)
    if base:
        return f"{base} Service"
    return f"Customer {str(customer.get('_id'))[-6:]} Service"


def pick_shop_and_db(master):
    shop = master.shops.find_one(sort=[("created_at", 1)])
    if not shop:
        raise RuntimeError("No shop found in master.shops")

    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        raise RuntimeError("Shop has no db_name/database/db field")

    return shop, str(db_name)


def ensure_mechanics(master, tenant_id: ObjectId, shop_id: ObjectId) -> list[dict]:
    now = utcnow()
    base = [
        ("Alex", "Miller", "senior_mechanic"),
        ("Brian", "Lopez", "mechanic"),
        ("Chris", "Johnson", "mechanic"),
        ("Dylan", "Garcia", "mechanic"),
        ("Evan", "Walker", "mechanic"),
    ]

    users = []
    for idx, (first, last, role) in enumerate(base, start=1):
        email = f"seed.mechanic{idx}.shop{str(shop_id)[-6:]}@smallshop.local"
        insert_doc = {
            "tenant_id": tenant_id,
            "email": email,
            "password_hash": generate_password_hash("SeedMechanic123!"),
            "first_name": first,
            "last_name": last,
            "name": f"{first} {last}",
            "phone": f"+1 (555) 200-{1000 + idx}",
            "must_reset_password": False,
            "allow_permissions": [],
            "deny_permissions": [],
            "created_at": now,
            "seed_tag": "seed-mechanics-v1",
        }

        set_doc = {
            "shop_ids": [shop_id],
            "shop_id": shop_id,
            "is_active": True,
            "role": role,
            "updated_at": now,
        }

        res = master.users.find_one_and_update(
            {"email": email},
            {
                "$setOnInsert": insert_doc,
                "$set": set_doc,
            },
            upsert=True,
            return_document=True,
        )
        users.append(res)

    out = []
    for u in users:
        out.append(
            {
                "id": u.get("_id"),
                "name": str(u.get("name") or "").strip() or f"{u.get('first_name','')} {u.get('last_name','')}",
                "role": str(u.get("role") or "mechanic").strip(),
            }
        )
    return out


def build_assignment(mechanics: list[dict]) -> list[dict]:
    # 1-3 mechanics per labor block, % sum = 100
    count = random.choices([1, 2, 3], weights=[0.45, 0.4, 0.15], k=1)[0]
    selected = random.sample(mechanics, k=min(count, len(mechanics)))

    if len(selected) == 1:
        m = selected[0]
        return [{"user_id": m["id"], "name": m["name"], "role": m["role"], "percent": 100.0}]

    # split 100 into n positive parts
    cuts = sorted(random.sample(range(1, 100), len(selected) - 1))
    parts = []
    prev = 0
    for c in cuts + [100]:
        parts.append(c - prev)
        prev = c

    assignments = []
    for mechanic, pct in zip(selected, parts):
        assignments.append(
            {
                "user_id": mechanic["id"],
                "name": mechanic["name"],
                "role": mechanic["role"],
                "percent": round2(pct),
            }
        )
    return assignments


def update_customers_company(shop_db, shop_id: ObjectId):
    now = utcnow()
    query = {
        "shop_id": shop_id,
        "$or": [
            {"company_name": None},
            {"company_name": ""},
            {"company_name": {"$exists": False}},
        ],
    }

    updates = []
    count = 0
    for c in shop_db.customers.find(query, {"_id": 1, "company_name": 1, "contacts": 1, "first_name": 1, "last_name": 1, "email": 1}):
        name = build_company_name(c)
        updates.append(
            UpdateOne(
                {"_id": c["_id"]},
                {"$set": {"company_name": name, "updated_at": now}},
            )
        )
        count += 1

    if updates:
        shop_db.customers.bulk_write(updates, ordered=False)
    return count


def assign_mechanics_to_all_work_orders(shop_db, shop_id: ObjectId, mechanics: list[dict]):
    now = utcnow()
    updated_orders = 0
    updated_blocks = 0

    cursor = shop_db.work_orders.find(
        {"shop_id": shop_id, "is_active": True},
        {"_id": 1, "labors": 1},
    )

    bulk = []
    for wo in cursor:
        labors = wo.get("labors") if isinstance(wo.get("labors"), list) else []
        if not labors:
            continue

        changed = False
        new_labors = []
        for block in labors:
            if not isinstance(block, dict):
                new_labors.append(block)
                continue

            block_copy = dict(block)
            labor = block_copy.get("labor") if isinstance(block_copy.get("labor"), dict) else {}
            labor_copy = dict(labor)
            labor_copy["assigned_mechanics"] = build_assignment(mechanics)
            block_copy["labor"] = labor_copy
            new_labors.append(block_copy)
            changed = True
            updated_blocks += 1

        if changed:
            bulk.append(
                UpdateOne(
                    {"_id": wo["_id"]},
                    {"$set": {"labors": new_labors, "updated_at": now}},
                )
            )
            updated_orders += 1

            if len(bulk) >= 500:
                shop_db.work_orders.bulk_write(bulk, ordered=False)
                bulk.clear()

    if bulk:
        shop_db.work_orders.bulk_write(bulk, ordered=False)

    return updated_orders, updated_blocks


def validate(shop_db, shop_id: ObjectId):
    no_company = shop_db.customers.count_documents(
        {
            "shop_id": shop_id,
            "$or": [
                {"company_name": None},
                {"company_name": ""},
                {"company_name": {"$exists": False}},
            ],
        }
    )

    missing_assignments = 0
    total_blocks = 0
    for wo in shop_db.work_orders.find({"shop_id": shop_id, "is_active": True}, {"labors": 1}):
        for block in (wo.get("labors") or []):
            if not isinstance(block, dict):
                continue
            total_blocks += 1
            labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
            assigned = labor.get("assigned_mechanics") if isinstance(labor.get("assigned_mechanics"), list) else []
            if not assigned:
                missing_assignments += 1

    return no_company, total_blocks, missing_assignments


def main():
    random.seed(20260331)
    load_dotenv()

    mongo_uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_db_name = os.environ.get("MASTER_DB_NAME") or os.environ.get("MONGO_DB") or "master_db"

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)
    client.admin.command("ping")

    master = client[master_db_name]
    shop, shop_db_name = pick_shop_and_db(master)
    shop_db = client[shop_db_name]

    shop_id = shop.get("_id")
    tenant_id = shop.get("tenant_id")
    if not isinstance(shop_id, ObjectId) or not isinstance(tenant_id, ObjectId):
        raise RuntimeError("Shop _id or tenant_id is invalid")

    fixed_customers = update_customers_company(shop_db, shop_id)
    mechanics = ensure_mechanics(master, tenant_id, shop_id)
    updated_orders, updated_blocks = assign_mechanics_to_all_work_orders(shop_db, shop_id, mechanics)

    no_company, total_blocks, missing_assignments = validate(shop_db, shop_id)

    print("shop_id", shop_id)
    print("shop_db", shop_db_name)
    print("fixed_customers", fixed_customers)
    print("created_or_updated_mechanics", len(mechanics))
    print("updated_work_orders", updated_orders)
    print("updated_labor_blocks", updated_blocks)
    print("validate_no_company", no_company)
    print("validate_total_labor_blocks", total_blocks)
    print("validate_missing_assignments", missing_assignments)


if __name__ == "__main__":
    main()
