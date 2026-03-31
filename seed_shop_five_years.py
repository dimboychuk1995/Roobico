from __future__ import annotations

import argparse
import os
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def round2(value: float) -> float:
    return round(float(value or 0.0) + 1e-12, 2)


def random_dt(start: datetime, end: datetime) -> datetime:
    if end <= start:
        return start
    seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, seconds))


def as_utc(dt: datetime | None, fallback: datetime) -> datetime:
    if not isinstance(dt, datetime):
        return fallback
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def slugify(value: str) -> str:
    out = [c.lower() if c.isalnum() else "-" for c in value]
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "shop"


def make_phone() -> str:
    return f"+1 ({random.randint(200, 989)}) {random.randint(100, 999)}-{random.randint(1000, 9999)}"


def make_email(first: str, last: str, domain: str = "example.com") -> str:
    base = f"{first}.{last}".lower().replace("'", "")
    suffix = random.randint(1, 999)
    return f"{base}{suffix}@{domain}"


def make_contact(first: str, last: str, is_main: bool) -> dict:
    return {
        "first_name": first,
        "last_name": last,
        "phone": make_phone(),
        "email": make_email(first, last),
        "is_main": bool(is_main),
    }


FIRST_NAMES = [
    "Liam", "Olivia", "Noah", "Emma", "Amelia", "James", "Sophia", "Benjamin", "Mia", "Lucas",
    "Ethan", "Ava", "Mason", "Harper", "Elijah", "Evelyn", "Logan", "Abigail", "Henry", "Ella",
    "Jack", "Chloe", "Sebastian", "Aria", "Michael", "Scarlett", "Daniel", "Nora", "Mateo", "Luna",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
    "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Walker",
]

VENDOR_PREFIXES = [
    "Prime", "Metro", "North", "South", "Rapid", "Global", "Central", "Apex", "Blue", "Summit",
]

VENDOR_SUFFIXES = [
    "Parts", "Supply", "Distributors", "Auto", "Components", "Wholesale", "Depot", "Solutions", "Logistics", "Group",
]

PART_WORDS = [
    "Filter", "Bearing", "Belt", "Sensor", "Pump", "Rotor", "Pad", "Seal", "Valve", "Harness",
    "Compressor", "Module", "Switch", "Relay", "Starter", "Alternator", "Coupling", "Axle", "Hub", "Gasket",
]

UNIT_MAKES_MODELS = [
    ("Ford", ["F-150", "Transit", "Escape", "Ranger"]),
    ("Chevrolet", ["Silverado", "Express", "Colorado", "Tahoe"]),
    ("Toyota", ["Tacoma", "Tundra", "RAV4", "Corolla"]),
    ("Honda", ["Civic", "CR-V", "Pilot", "Ridgeline"]),
    ("Nissan", ["Frontier", "Rogue", "Altima", "Titan"]),
]

UNIT_TYPES = ["Truck", "Van", "SUV", "Sedan", "Crossover"]


@dataclass
class SeedContext:
    client: MongoClient
    master_db_name: str
    shop: dict
    tenant_id: ObjectId | None
    user_id: ObjectId | None
    shop_db_name: str
    start_date: datetime
    end_date: datetime
    seed_tag: str


def pick_shop(client: MongoClient, master_db_name: str, shop_id: str | None) -> dict:
    master = client[master_db_name]
    query = {}
    if shop_id:
        query["_id"] = ObjectId(shop_id)

    shop = master.shops.find_one(query, sort=[("created_at", 1)])
    if not shop:
        raise RuntimeError("No shop found in master.shops")
    return shop


def resolve_shop_db_name(shop: dict) -> str:
    db_name = (
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
    )
    if not db_name:
        raise RuntimeError("Selected shop has no db_name/database/db field")
    return str(db_name)


def ensure_reference_docs(ctx: SeedContext) -> tuple[list[dict], list[dict], list[dict]]:
    db = ctx.client[ctx.shop_db_name]
    now = utcnow()

    categories = ["Engine", "Brakes", "Electrical", "HVAC", "Suspension", "Transmission", "Fluids"]
    locations = ["A1", "A2", "B1", "B2", "C1", "Rear Rack", "Overflow"]
    labor_rates = [
        {"code": "standard", "name": "Standard", "hourly_rate": 115.0},
        {"code": "diagnostic", "name": "Diagnostic", "hourly_rate": 140.0},
        {"code": "heavy", "name": "Heavy Duty", "hourly_rate": 165.0},
    ]

    for name in categories:
        slug = slugify(name)
        db.parts_categories.update_one(
            {"shop_id": ctx.shop["_id"], "slug": slug},
            {
                "$setOnInsert": {
                    "shop_id": ctx.shop["_id"],
                    "tenant_id": ctx.tenant_id,
                    "name": name,
                    "slug": slug,
                    "is_active": True,
                    "created_at": now,
                    "created_by": ctx.user_id,
                },
                "$set": {"updated_at": now, "updated_by": ctx.user_id},
            },
            upsert=True,
        )

    for name in locations:
        slug = slugify(name)
        db.parts_locations.update_one(
            {"shop_id": ctx.shop["_id"], "slug": slug},
            {
                "$setOnInsert": {
                    "shop_id": ctx.shop["_id"],
                    "tenant_id": ctx.tenant_id,
                    "name": name,
                    "slug": slug,
                    "is_active": True,
                    "created_at": now,
                    "created_by": ctx.user_id,
                },
                "$set": {"updated_at": now, "updated_by": ctx.user_id},
            },
            upsert=True,
        )

    for rate in labor_rates:
        db.labor_rates.update_one(
            {"shop_id": ctx.shop["_id"], "code": rate["code"]},
            {
                "$setOnInsert": {
                    "shop_id": ctx.shop["_id"],
                    "tenant_id": ctx.tenant_id,
                    "is_active": True,
                    "created_at": now,
                    "created_by": ctx.user_id,
                },
                "$set": {
                    "name": rate["name"],
                    "code": rate["code"],
                    "hourly_rate": rate["hourly_rate"],
                    "updated_at": now,
                    "updated_by": ctx.user_id,
                },
            },
            upsert=True,
        )

    cats = list(db.parts_categories.find({"shop_id": ctx.shop["_id"], "is_active": True}))
    locs = list(db.parts_locations.find({"shop_id": ctx.shop["_id"], "is_active": True}))
    rates = list(db.labor_rates.find({"shop_id": ctx.shop["_id"], "is_active": True}))
    return cats, locs, rates


def generate_vendors(ctx: SeedContext, count: int) -> list[dict]:
    db = ctx.client[ctx.shop_db_name]
    now = utcnow()
    docs = []
    for idx in range(count):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        second_first = random.choice(FIRST_NAMES)
        second_last = random.choice(LAST_NAMES)

        name = f"{random.choice(VENDOR_PREFIXES)} {random.choice(VENDOR_SUFFIXES)} {idx + 1:03d}"
        contacts = [
            make_contact(first, last, True),
            make_contact(second_first, second_last, False) if random.random() < 0.35 else None,
        ]
        contacts = [c for c in contacts if c]
        created_at = random_dt(ctx.start_date, ctx.end_date)

        main = contacts[0]
        docs.append(
            {
                "name": name,
                "website": f"https://{slugify(name)}.com",
                "address": f"{random.randint(100, 9999)} Supply Ave",
                "notes": "Seeded vendor",
                "contacts": contacts,
                "phone": main["phone"],
                "email": main["email"],
                "primary_contact_first_name": main["first_name"],
                "primary_contact_last_name": main["last_name"],
                "is_active": True,
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "deactivated_at": None,
                "deactivated_by": None,
                "shop_id": ctx.shop["_id"],
                "tenant_id": ctx.tenant_id,
                "seed_tag": ctx.seed_tag,
            }
        )

    if docs:
        db.vendors.insert_many(docs)
    return docs


def generate_customers(ctx: SeedContext, count: int, labor_rates: list[dict]) -> list[dict]:
    db = ctx.client[ctx.shop_db_name]
    docs = []

    rate_ids = [r.get("_id") for r in labor_rates if r.get("_id")]
    for idx in range(count):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        second_first = random.choice(FIRST_NAMES)
        second_last = random.choice(LAST_NAMES)

        company_name = None
        if random.random() < 0.42:
            company_name = f"{last} Fleet {idx + 1:04d}"

        contacts = [make_contact(first, last, True)]
        if random.random() < 0.28:
            contacts.append(make_contact(second_first, second_last, False))

        main = contacts[0]
        created_at = random_dt(ctx.start_date, ctx.end_date)
        docs.append(
            {
                "company_name": company_name,
                "contacts": contacts,
                "first_name": main["first_name"],
                "last_name": main["last_name"],
                "phone": main["phone"],
                "email": main["email"],
                "address": f"{random.randint(100, 9999)} Main St",
                "taxable": random.random() < 0.87,
                "default_labor_rate": random.choice(rate_ids) if rate_ids else None,
                "is_active": True,
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "deactivated_at": None,
                "deactivated_by": None,
                "shop_id": ctx.shop["_id"],
                "tenant_id": ctx.tenant_id,
                "seed_tag": ctx.seed_tag,
            }
        )

    if docs:
        db.customers.insert_many(docs)
    return docs


def _make_vin() -> str:
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(random.choice(chars) for _ in range(17))


def generate_units(ctx: SeedContext, customers: list[dict]) -> list[dict]:
    db = ctx.client[ctx.shop_db_name]
    docs = []

    for customer in customers:
        customer_id = customer.get("_id")
        if not customer_id:
            continue
        unit_count = 1 if random.random() < 0.74 else (2 if random.random() < 0.85 else 3)
        for i in range(unit_count):
            make, models = random.choice(UNIT_MAKES_MODELS)
            model = random.choice(models)
            year = random.randint(2005, utcnow().year)
            created_at = random_dt(
                max(ctx.start_date, as_utc(customer.get("created_at"), ctx.start_date)),
                ctx.end_date,
            )
            docs.append(
                {
                    "customer_id": customer_id,
                    "vin": _make_vin(),
                    "unit_number": f"U-{str(customer_id)[-5:]}-{i + 1}",
                    "make": make,
                    "model": model,
                    "year": year,
                    "type": random.choice(UNIT_TYPES),
                    "mileage": random.randint(5_000, 285_000),
                    "shop_id": ctx.shop["_id"],
                    "tenant_id": ctx.tenant_id,
                    "is_active": True,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "created_by": ctx.user_id,
                    "updated_by": ctx.user_id,
                    "seed_tag": ctx.seed_tag,
                }
            )

    if docs:
        db.units.insert_many(docs)
    return docs


def build_search_terms(part_number: str, description: str, reference: str) -> list[str]:
    raw = f"{part_number} {description} {reference}".lower()
    for ch in "/_-.":
        raw = raw.replace(ch, " ")
    terms = [t.strip() for t in raw.split() if t.strip()]
    return sorted(set(terms))


def generate_parts(ctx: SeedContext, vendors: list[dict], categories: list[dict], locations: list[dict], count: int) -> list[dict]:
    db = ctx.client[ctx.shop_db_name]
    docs = []

    vendor_ids = [v.get("_id") for v in vendors if v.get("_id")]
    category_ids = [c.get("_id") for c in categories if c.get("_id")]
    location_ids = [l.get("_id") for l in locations if l.get("_id")]

    for i in range(count):
        part_number = f"P-{i + 1:06d}"
        desc = f"{random.choice(PART_WORDS)} {random.choice(PART_WORDS)}"
        ref = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        avg_cost = round2(random.uniform(8, 420))
        has_selling_price = random.random() < 0.86
        selling_price = round2(avg_cost * random.uniform(1.25, 1.8)) if has_selling_price else None
        do_not_track = random.random() < 0.12
        core_has_charge = (not do_not_track) and random.random() < 0.18
        core_cost = round2(random.uniform(5, 55)) if core_has_charge else None

        created_at = random_dt(ctx.start_date, ctx.end_date)
        docs.append(
            {
                "part_number": part_number,
                "description": desc,
                "reference": ref,
                "search_terms": build_search_terms(part_number, desc, ref),
                "vendor_id": random.choice(vendor_ids) if vendor_ids else None,
                "category_id": random.choice(category_ids) if category_ids else None,
                "location_id": random.choice(location_ids) if location_ids else None,
                "do_not_track_inventory": bool(do_not_track),
                "average_cost": avg_cost,
                "has_selling_price": bool(has_selling_price),
                "selling_price": selling_price,
                "core_has_charge": bool(core_has_charge),
                "core_cost": core_cost,
                "misc_has_charge": False,
                "misc_charges": [],
                "in_stock": random.randint(8, 140) if not do_not_track else None,
                "is_active": True,
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "deactivated_at": None,
                "deactivated_by": None,
                "shop_id": ctx.shop["_id"],
                "tenant_id": ctx.tenant_id,
                "seed_tag": ctx.seed_tag,
            }
        )

    if docs:
        db.parts.insert_many(docs)
    return docs


def _next_counter(db, shop_id: ObjectId, counter_name: str, default_start: int) -> int:
    key = f"{counter_name}:{shop_id}"
    row = db.counters.find_one_and_update(
        {"_id": key},
        {
            "$setOnInsert": {
                "shop_id": shop_id,
                "created_at": utcnow(),
            },
            "$inc": {"seq": 1},
        },
        upsert=True,
        return_document=True,
    )
    seq = int((row or {}).get("seq") or 1)
    if seq < default_start:
        # keep legacy style numbering if collection was empty and caller expects larger base.
        db.counters.update_one({"_id": key}, {"$set": {"seq": default_start}})
        return default_start
    return seq


def _parts_total(items: list[dict], non_inventory_amounts: list[dict]) -> float:
    items_total = sum(round2((x.get("price") or 0) * (x.get("quantity") or 0)) for x in items)
    misc_total = sum(round2(x.get("amount") or 0) for x in non_inventory_amounts)
    return round2(items_total + misc_total)


def generate_parts_orders(
    ctx: SeedContext,
    vendors: list[dict],
    parts: list[dict],
    count: int,
) -> tuple[list[dict], list[dict], list[UpdateOne]]:
    db = ctx.client[ctx.shop_db_name]
    orders = []
    payments = []
    stock_updates: dict[ObjectId, int] = {}

    vendor_ids = [v.get("_id") for v in vendors if v.get("_id")]
    parts_by_vendor: dict[ObjectId, list[dict]] = {}
    for part in parts:
        vid = part.get("vendor_id")
        if vid:
            parts_by_vendor.setdefault(vid, []).append(part)

    for _ in range(count):
        vendor_id = random.choice(vendor_ids) if vendor_ids else None
        vendor_parts = parts_by_vendor.get(vendor_id) or parts
        if not vendor_parts:
            continue

        created_at = random_dt(ctx.start_date, ctx.end_date)
        order_no = _next_counter(db, ctx.shop["_id"], "parts_order_number", 999)

        item_count = random.randint(1, 5)
        items = []
        selected_parts = random.sample(vendor_parts, k=min(item_count, len(vendor_parts)))
        for part in selected_parts:
            qty = random.randint(1, 18)
            price = round2(max(1.0, (part.get("average_cost") or 0) * random.uniform(0.95, 1.25)))
            items.append(
                {
                    "part_id": part.get("_id"),
                    "part_number": part.get("part_number"),
                    "description": part.get("description"),
                    "price": price,
                    "quantity": qty,
                }
            )

        non_inventory_amounts = []
        if random.random() < 0.22:
            non_inventory_amounts.append(
                {
                    "type": "shipping",
                    "description": "Freight",
                    "amount": round2(random.uniform(25, 180)),
                }
            )

        total_amount = _parts_total(items, non_inventory_amounts)
        status = "received" if random.random() < 0.82 else "ordered"

        if status == "received":
            paid_ratio = random.random()
            if paid_ratio < 0.62:
                paid_amount = total_amount
            elif paid_ratio < 0.88:
                paid_amount = round2(total_amount * random.uniform(0.25, 0.95))
            else:
                paid_amount = 0.0
        else:
            paid_amount = round2(total_amount * random.uniform(0.0, 0.35)) if random.random() < 0.35 else 0.0

        paid_amount = min(total_amount, paid_amount)
        remaining = round2(total_amount - paid_amount)
        payment_status = "paid" if remaining <= 0.01 else ("partial" if paid_amount > 0 else "unpaid")

        order_id = ObjectId()
        order_doc = {
            "_id": order_id,
            "vendor_id": vendor_id,
            "order_number": order_no,
            "vendor_bill": f"VB-{order_no}",
            "items": items,
            "non_inventory_amounts": non_inventory_amounts,
            "status": status,
            "order_date": created_at,
            "payment_status": payment_status,
            "paid_amount": paid_amount,
            "remaining_balance": remaining,
            "is_active": True,
            "created_at": created_at,
            "updated_at": created_at,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
            "shop_id": ctx.shop["_id"],
            "tenant_id": ctx.tenant_id,
            "seed_tag": ctx.seed_tag,
        }
        orders.append(order_doc)

        if status == "received":
            for item in items:
                part_id = item.get("part_id")
                qty = int(item.get("quantity") or 0)
                if part_id and qty > 0:
                    stock_updates[part_id] = stock_updates.get(part_id, 0) + qty

        if paid_amount > 0:
            # 1-3 payments that exactly match paid_amount
            payment_chunks = random.randint(1, 3)
            remaining_chunk = paid_amount
            for chunk_idx in range(payment_chunks):
                if chunk_idx == payment_chunks - 1:
                    amount = round2(remaining_chunk)
                else:
                    max_amount = max(0.01, remaining_chunk - (payment_chunks - chunk_idx - 1) * 0.01)
                    amount = round2(random.uniform(0.01, max_amount))
                    remaining_chunk = round2(remaining_chunk - amount)

                if amount <= 0:
                    continue

                pay_date = created_at + timedelta(days=random.randint(0, 35))
                payments.append(
                    {
                        "parts_order_id": order_id,
                        "shop_id": ctx.shop["_id"],
                        "tenant_id": ctx.tenant_id,
                        "amount": amount,
                        "payment_method": random.choice(["cash", "ach", "credit_card", "check"]),
                        "notes": "Seeded payment",
                        "payment_date": pay_date,
                        "is_active": True,
                        "created_at": pay_date,
                        "created_by": ctx.user_id,
                        "seed_tag": ctx.seed_tag,
                    }
                )

    if orders:
        db.parts_orders.insert_many(orders)
    if payments:
        db.parts_order_payments.insert_many(payments)

    bulk_updates = [
        UpdateOne(
            {"_id": part_id, "shop_id": ctx.shop["_id"], "is_active": True, "do_not_track_inventory": {"$ne": True}},
            {"$inc": {"in_stock": delta}},
        )
        for part_id, delta in stock_updates.items()
        if delta
    ]
    if bulk_updates:
        db.parts.bulk_write(bulk_updates, ordered=False)

    return orders, payments, bulk_updates


def _pick_rate(rates: list[dict]) -> dict:
    if not rates:
        return {"code": "standard", "hourly_rate": 120.0}
    row = random.choice(rates)
    return {
        "code": row.get("code") or "standard",
        "hourly_rate": float(row.get("hourly_rate") or 120.0),
    }


def generate_work_orders(
    ctx: SeedContext,
    customers: list[dict],
    units: list[dict],
    parts: list[dict],
    labor_rates: list[dict],
    count: int,
) -> tuple[list[dict], list[dict], list[UpdateOne]]:
    db = ctx.client[ctx.shop_db_name]
    orders = []
    payments = []
    stock_deltas: dict[ObjectId, int] = {}

    units_by_customer: dict[ObjectId, list[dict]] = {}
    for unit in units:
        cid = unit.get("customer_id")
        if cid:
            units_by_customer.setdefault(cid, []).append(unit)

    tracked_parts = [p for p in parts if p.get("_id") and not p.get("do_not_track_inventory")]
    all_parts = [p for p in parts if p.get("_id")]

    # mutable inventory snapshot for safe deductions
    inventory = {p.get("_id"): int(p.get("in_stock") or 0) for p in tracked_parts}

    for _ in range(count):
        customer = random.choice(customers) if customers else None
        if not customer:
            continue

        customer_id = customer.get("_id")
        candidate_units = units_by_customer.get(customer_id) or []
        if not candidate_units:
            continue

        unit = random.choice(candidate_units)
        work_date = random_dt(
            max(ctx.start_date, as_utc(customer.get("created_at"), ctx.start_date)),
            ctx.end_date,
        )

        wo_number = _next_counter(db, ctx.shop["_id"], "work_order_number", 9999)
        block_count = random.randint(1, 3)
        labors = []

        labor_total_sum = 0.0
        parts_base_sum = 0.0
        core_sum = 0.0
        misc_sum = 0.0

        for block_index in range(block_count):
            rate = _pick_rate(labor_rates)
            hours = round2(random.uniform(0.5, 6.5))
            labor_total = round2(hours * rate["hourly_rate"])

            block_parts = []
            part_lines = random.randint(0, 4)
            for _line in range(part_lines):
                part = random.choice(all_parts) if all_parts else None
                if not part:
                    continue
                pid = part.get("_id")
                if not pid:
                    continue

                qty = random.randint(1, 3)
                available = inventory.get(pid)
                if available is not None:
                    if available <= 0:
                        continue
                    qty = min(qty, available)
                    if qty <= 0:
                        continue
                    inventory[pid] = max(0, available - qty)
                    stock_deltas[pid] = stock_deltas.get(pid, 0) - qty

                avg_cost = float(part.get("average_cost") or 0.0)
                if part.get("has_selling_price") and part.get("selling_price") is not None:
                    price = round2(float(part.get("selling_price") or 0.0))
                else:
                    price = round2(max(1.0, avg_cost * random.uniform(1.2, 1.8)))

                core_charge = round2(float(part.get("core_cost") or 0.0)) if part.get("core_has_charge") else 0.0
                block_parts.append(
                    {
                        "part_id": str(pid),
                        "part_number": part.get("part_number"),
                        "description": part.get("description"),
                        "qty": qty,
                        "cost": avg_cost,
                        "price": price,
                        "core_charge": core_charge if core_charge > 0 else None,
                        "misc_charge": None,
                        "misc_charge_description": "",
                        "one_time_part": False,
                    }
                )

            block_parts_base = sum(round2((p.get("price") or 0) * (p.get("qty") or 0)) for p in block_parts)
            block_core = sum(round2((p.get("core_charge") or 0) * (p.get("qty") or 0)) for p in block_parts)
            block_misc = 0.0
            block_total = round2(labor_total + block_parts_base + block_core + block_misc)

            labors.append(
                {
                    "labor": {
                        "description": f"Labor block {block_index + 1}",
                        "hours": str(hours),
                        "rate_code": rate["code"],
                        "labor_full_total": labor_total,
                        "assigned_mechanics": [],
                    },
                    "parts": block_parts,
                }
            )

            labor_total_sum = round2(labor_total_sum + labor_total)
            parts_base_sum = round2(parts_base_sum + block_parts_base)
            core_sum = round2(core_sum + block_core)
            misc_sum = round2(misc_sum + block_misc)

        parts_total_sum = round2(parts_base_sum + core_sum + misc_sum)
        taxable = bool(customer.get("taxable", False))
        sales_tax_rate = 0.0725
        sales_tax_total = round2(parts_base_sum * sales_tax_rate) if taxable else 0.0
        grand_total = round2(labor_total_sum + parts_total_sum + sales_tax_total)

        totals = {
            "labor": labor_total_sum,
            "labor_total": labor_total_sum,
            "parts": parts_base_sum,
            "parts_total": parts_total_sum,
            "core_total": core_sum,
            "misc_total": misc_sum,
            "misc_taxable_total": 0.0,
            "shop_supply_total": 0.0,
            "cost_total": parts_base_sum,
            "parts_taxable_total": parts_base_sum,
            "sales_tax_rate": sales_tax_rate if taxable else 0.0,
            "sales_tax_total": sales_tax_total,
            "is_taxable": taxable,
            "grand_total": grand_total,
            "labors": [
                {
                    "labor": round2((b.get("labor") or {}).get("labor_full_total") or 0),
                    "labor_total": round2((b.get("labor") or {}).get("labor_full_total") or 0),
                    "parts": round2(sum(round2((p.get("price") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))),
                    "parts_total": round2(
                        sum(round2((p.get("price") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))
                        + sum(round2((p.get("core_charge") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))
                    ),
                    "core_total": round2(sum(round2((p.get("core_charge") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))),
                    "misc_total": 0.0,
                    "shop_supply_total": 0.0,
                    "cost_total": round2(sum(round2((p.get("price") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))),
                    "labor_full_total": round2(
                        round2((b.get("labor") or {}).get("labor_full_total") or 0)
                        + sum(round2((p.get("price") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))
                        + sum(round2((p.get("core_charge") or 0) * (p.get("qty") or 0)) for p in (b.get("parts") or []))
                    ),
                }
                for b in labors
            ],
        }

        # payments
        if random.random() < 0.58:
            paid_amount = grand_total
        elif random.random() < 0.78:
            paid_amount = round2(grand_total * random.uniform(0.2, 0.9))
        else:
            paid_amount = 0.0
        paid_amount = min(grand_total, paid_amount)
        status = "paid" if grand_total > 0 and abs(grand_total - paid_amount) <= 0.01 else "open"

        order_id = ObjectId()
        order_doc = {
            "_id": order_id,
            "shop_id": ctx.shop["_id"],
            "tenant_id": ctx.tenant_id,
            "wo_number": wo_number,
            "customer_id": customer_id,
            "unit_id": unit.get("_id"),
            "status": status,
            "labors": labors,
            "work_order_date": work_date,
            "totals": totals,
            "inventory_deducted": any((x.get("parts") or []) for x in labors),
            "inventory_deductions": [],
            "is_active": True,
            "created_at": work_date,
            "updated_at": work_date,
            "created_by": ctx.user_id,
            "updated_by": ctx.user_id,
            "seed_tag": ctx.seed_tag,
        }
        orders.append(order_doc)

        if paid_amount > 0:
            chunk_count = random.randint(1, 3)
            left = paid_amount
            for chunk_idx in range(chunk_count):
                if chunk_idx == chunk_count - 1:
                    amount = round2(left)
                else:
                    max_amount = max(0.01, left - (chunk_count - chunk_idx - 1) * 0.01)
                    amount = round2(random.uniform(0.01, max_amount))
                    left = round2(left - amount)

                if amount <= 0:
                    continue
                pay_date = work_date + timedelta(days=random.randint(0, 20))
                payments.append(
                    {
                        "work_order_id": order_id,
                        "shop_id": ctx.shop["_id"],
                        "tenant_id": ctx.tenant_id,
                        "amount": amount,
                        "payment_method": random.choice(["cash", "check", "credit_card", "ach", "other"]),
                        "notes": "Seeded payment",
                        "payment_date": pay_date,
                        "is_active": True,
                        "created_at": pay_date,
                        "created_by": ctx.user_id,
                        "seed_tag": ctx.seed_tag,
                    }
                )

    if orders:
        db.work_orders.insert_many(orders)
    if payments:
        db.work_order_payments.insert_many(payments)

    stock_bulk = [
        UpdateOne(
            {"_id": pid, "shop_id": ctx.shop["_id"], "is_active": True, "do_not_track_inventory": {"$ne": True}},
            {"$inc": {"in_stock": delta}},
        )
        for pid, delta in stock_deltas.items()
        if delta
    ]
    if stock_bulk:
        db.parts.bulk_write(stock_bulk, ordered=False)

    return orders, payments, stock_bulk


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed 5 years of realistic shop data.")
    parser.add_argument("--shop-id", dest="shop_id", default=None, help="master.shops _id (optional)")
    parser.add_argument("--seed", dest="seed", type=int, default=20260330, help="Random seed")
    parser.add_argument("--vendors", dest="vendors", type=int, default=120)
    parser.add_argument("--customers", dest="customers", type=int, default=1800)
    parser.add_argument("--parts", dest="parts", type=int, default=2200)
    parser.add_argument("--parts-orders", dest="parts_orders", type=int, default=1400)
    parser.add_argument("--work-orders", dest="work_orders", type=int, default=9500)
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    random.seed(args.seed)

    mongo_uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_db_name = os.environ.get("MASTER_DB_NAME") or os.environ.get("MONGO_DB") or "master_db"

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)
    client.admin.command("ping")

    shop = pick_shop(client, master_db_name, args.shop_id)
    shop_db_name = resolve_shop_db_name(shop)

    tenant_id = shop.get("tenant_id") if isinstance(shop.get("tenant_id"), ObjectId) else None
    master = client[master_db_name]
    user = None
    if tenant_id:
        user = master.users.find_one({"tenant_id": tenant_id, "is_active": True}, sort=[("created_at", 1)])
    user_id = user.get("_id") if user else None

    end_date = utcnow()
    start_date = end_date - timedelta(days=365 * 5)
    seed_tag = f"seed-5y-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    ctx = SeedContext(
        client=client,
        master_db_name=master_db_name,
        shop=shop,
        tenant_id=tenant_id,
        user_id=user_id,
        shop_db_name=shop_db_name,
        start_date=start_date,
        end_date=end_date,
        seed_tag=seed_tag,
    )

    print(f"Shop: {shop.get('name') or shop.get('_id')} ({shop.get('_id')})")
    print(f"Shop DB: {shop_db_name}")
    print(f"Period: {start_date.date()} -> {end_date.date()}")
    print(f"Seed tag: {seed_tag}")

    categories, locations, labor_rates = ensure_reference_docs(ctx)

    vendors = generate_vendors(ctx, args.vendors)
    # refresh with _id from DB insert if insert_many doesn't mutate (depends on driver behavior)
    vendors = list(client[shop_db_name].vendors.find({"seed_tag": seed_tag}))

    customers = generate_customers(ctx, args.customers, labor_rates)
    customers = list(client[shop_db_name].customers.find({"seed_tag": seed_tag}))

    units = generate_units(ctx, customers)
    units = list(client[shop_db_name].units.find({"seed_tag": seed_tag}))

    parts = generate_parts(ctx, vendors, categories, locations, args.parts)
    parts = list(client[shop_db_name].parts.find({"seed_tag": seed_tag}))

    parts_orders, parts_order_payments, _parts_stock_bulk = generate_parts_orders(
        ctx,
        vendors,
        parts,
        args.parts_orders,
    )

    work_orders, work_order_payments, _wo_stock_bulk = generate_work_orders(
        ctx,
        customers,
        units,
        parts,
        labor_rates,
        args.work_orders,
    )

    print("\nSeeding completed:")
    print(f"  vendors: {len(vendors)}")
    print(f"  customers: {len(customers)}")
    print(f"  units: {len(units)}")
    print(f"  parts: {len(parts)}")
    print(f"  parts_orders: {len(parts_orders)}")
    print(f"  parts_order_payments: {len(parts_order_payments)}")
    print(f"  work_orders: {len(work_orders)}")
    print(f"  work_order_payments: {len(work_order_payments)}")


if __name__ == "__main__":
    main()
