"""
Backfill script: set `taxable` flag on existing misc charge items in work orders.

For every work order that has misc charges stored in `misc_charge_description` JSON,
this script randomly assigns taxable=True or taxable=False to each item that is
missing the `taxable` field.

Usage:
    python scripts/backfill_misc_charge_taxable.py [--uri mongodb://...] [--dry-run] [--seed 42]

Options:
    --uri      MongoDB connection URI (default: mongodb://127.0.0.1:27017)
    --dry-run  Print what would be changed without writing to the database
    --seed     Random seed for reproducible assignment (default: none / truly random)
"""

import argparse
import json
import random
import sys
from pymongo import MongoClient


def parse_args():
    parser = argparse.ArgumentParser(description="Backfill taxable flag on misc charge items.")
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27017", help="MongoDB URI")
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    return parser.parse_args()


def apply_taxable_to_items(items: list, rng: random.Random) -> tuple:
    """Return (updated_items, changed_count). Items without `taxable` get a random value."""
    changed = 0
    result = []
    for item in items:
        if not isinstance(item, dict):
            result.append(item)
            continue
        if "taxable" not in item:
            item = dict(item)
            item["taxable"] = rng.choice([True, False])
            changed += 1
        result.append(item)
    return result, changed


def process_shop_db(db, dry_run: bool, rng: random.Random) -> dict:
    stats = {"inspected": 0, "updated": 0, "items_set": 0}

    cursor = db.work_orders.find(
        {"labors": {"$exists": True}},
        {"_id": 1, "labors": 1},
    )

    for wo in cursor:
        stats["inspected"] += 1
        labors = wo.get("labors") or []
        if not isinstance(labors, list):
            continue

        wo_changed = False
        new_labors = []

        for labor_block in labors:
            if not isinstance(labor_block, dict):
                new_labors.append(labor_block)
                continue

            parts = labor_block.get("parts") or []
            if not isinstance(parts, list) or not parts:
                new_labors.append(labor_block)
                continue

            # Misc items live in the first part row's misc_charge_description
            first_part = parts[0] if parts else None
            if not isinstance(first_part, dict):
                new_labors.append(labor_block)
                continue

            misc_raw = first_part.get("misc_charge_description") or ""
            if not misc_raw:
                new_labors.append(labor_block)
                continue

            try:
                misc_items = json.loads(misc_raw)
            except Exception:
                new_labors.append(labor_block)
                continue

            if not isinstance(misc_items, list):
                new_labors.append(labor_block)
                continue

            updated_items, changed = apply_taxable_to_items(misc_items, rng)
            if changed > 0:
                new_first_part = dict(first_part)
                new_first_part["misc_charge_description"] = json.dumps(updated_items)
                new_parts = [new_first_part] + list(parts[1:])
                labor_block = dict(labor_block)
                labor_block["parts"] = new_parts
                wo_changed = True
                stats["items_set"] += changed

            new_labors.append(labor_block)

        if wo_changed:
            stats["updated"] += 1
            if not dry_run:
                db.work_orders.update_one(
                    {"_id": wo["_id"]},
                    {"$set": {"labors": new_labors}},
                )

    return stats


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    client = MongoClient(args.uri)
    master_db = client["master_db"]

    shops = list(master_db.shops.find({}, {"_id": 0, "db_name": 1, "name": 1}))
    if not shops:
        print("No shops found in master.shops collection.", file=sys.stderr)
        sys.exit(1)

    dry_label = "[DRY RUN] " if args.dry_run else ""
    total_updated = 0
    total_items = 0

    for shop in shops:
        db_name = shop.get("db_name")
        shop_name = shop.get("name") or db_name
        if not db_name:
            continue

        shop_db = client[db_name]
        stats = process_shop_db(shop_db, dry_run=args.dry_run, rng=rng)

        print(
            f"{dry_label}Shop '{shop_name}' ({db_name}): "
            f"inspected={stats['inspected']}, WOs updated={stats['updated']}, "
            f"misc items assigned={stats['items_set']}"
        )
        total_updated += stats["updated"]
        total_items += stats["items_set"]

    print(f"\n{dry_label}Total: {total_updated} work orders updated, {total_items} misc items assigned taxable flag.")
    if args.dry_run:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
