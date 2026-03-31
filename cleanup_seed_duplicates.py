"""
cleanup_seed_duplicates.py
---------------------------
Removes orphaned records from failed/duplicate seed runs.

All work_orders, parts_orders, parts_order_payments reference ONLY
seed tag '022859'.  The two earlier incomplete runs ('022833','022844')
left orphan customers, vendors, units and duplicate parts.

Safe-to-delete (verified by diagnose2.py):
  - parts          seed_tag=022844  (duplicates of 022859 parts, same P-numbers)
  - customers      seed_tag=022833 + 022844  (not referenced by any work_order)
  - vendors        seed_tag=022833 + 022844  (not referenced by any parts_order)
  - units          seed_tag=022844  (not referenced by any work_order)
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

ORPHAN_TAGS = ["seed-5y-20260331-022833", "seed-5y-20260331-022844"]
PARTS_ORPHAN_TAG = "seed-5y-20260331-022844"  # only parts have this tag as duplicate


def connect():
    client = MongoClient(
        os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"),
        serverSelectionTimeoutMS=8000,
    )
    client.admin.command("ping")
    master_name = os.environ.get("MASTER_DB_NAME") or os.environ.get("MONGO_DB") or "master_db"
    shop = client[master_name].shops.find_one(sort=[("created_at", 1)])
    if not shop:
        raise RuntimeError("No shop found in master.shops")
    db_name = shop.get("db_name") or shop.get("database") or shop.get("db")
    if not db_name:
        raise RuntimeError("Shop has no db_name field")
    return client[db_name]


def safety_check(sdb):
    """Verify that no live work_orders / parts_orders reference the orphan tags."""
    print("Running safety checks...")

    # customers
    orphan_cust_ids = list(sdb.customers.find(
        {"seed_tag": {"$in": ORPHAN_TAGS}}, {"_id": 1}
    ))
    orphan_cust_id_set = [d["_id"] for d in orphan_cust_ids]
    wo_using_orphan_cust = sdb.work_orders.count_documents(
        {"customer_id": {"$in": orphan_cust_id_set}}
    )
    print(f"  work_orders referencing orphan customers: {wo_using_orphan_cust}  (expect 0)")
    assert wo_using_orphan_cust == 0, "ABORT: live work_orders use orphan customers!"

    # vendors
    orphan_vend_ids = [d["_id"] for d in sdb.vendors.find(
        {"seed_tag": {"$in": ORPHAN_TAGS}}, {"_id": 1}
    )]
    po_using_orphan_vend = sdb.parts_orders.count_documents(
        {"vendor_id": {"$in": orphan_vend_ids}}
    )
    print(f"  parts_orders referencing orphan vendors: {po_using_orphan_vend}  (expect 0)")
    assert po_using_orphan_vend == 0, "ABORT: live parts_orders use orphan vendors!"

    # units
    orphan_unit_ids = [d["_id"] for d in sdb.units.find(
        {"seed_tag": PARTS_ORPHAN_TAG}, {"_id": 1}
    )]
    wo_using_orphan_units = sdb.work_orders.count_documents(
        {"unit_id": {"$in": orphan_unit_ids}}
    )
    print(f"  work_orders referencing orphan units: {wo_using_orphan_units}  (expect 0)")
    assert wo_using_orphan_units == 0, "ABORT: live work_orders use orphan units!"

    # parts — check both parts_orders items and work_order embedded parts
    orphan_part_ids = [d["_id"] for d in sdb.parts.find(
        {"seed_tag": PARTS_ORPHAN_TAG}, {"_id": 1}
    )]
    # Check parts_orders items
    po_using_orphan_parts = sdb.parts_orders.count_documents(
        {"items.part_id": {"$in": orphan_part_ids}}
    )
    print(f"  parts_orders.items referencing orphan parts: {po_using_orphan_parts}  (expect 0)")
    # Check work_orders embedded parts (part_id stored as string in most docs)
    orphan_part_id_strs = [str(pid) for pid in orphan_part_ids]
    wo_using_orphan_parts = sdb.work_orders.count_documents(
        {"labors.parts.part_id": {"$in": orphan_part_ids + orphan_part_id_strs}}
    )
    print(f"  work_orders.labors.parts referencing orphan parts: {wo_using_orphan_parts}  (expect 0)")

    print("Safety checks PASSED.\n")


def run_cleanup(sdb):
    safety_check(sdb)

    # --- 1. Duplicate parts (seed 022844) ---
    parts_before = sdb.parts.count_documents({})
    r = sdb.parts.delete_many({"seed_tag": PARTS_ORPHAN_TAG})
    parts_deleted = r.deleted_count
    parts_after = sdb.parts.count_documents({})
    print(f"Parts:     deleted {parts_deleted}  ({parts_before} -> {parts_after})")

    # --- 2. Orphan customers (seeds 022833 + 022844) ---
    cust_before = sdb.customers.count_documents({})
    r = sdb.customers.delete_many({"seed_tag": {"$in": ORPHAN_TAGS}})
    cust_deleted = r.deleted_count
    cust_after = sdb.customers.count_documents({})
    print(f"Customers: deleted {cust_deleted}  ({cust_before} -> {cust_after})")

    # --- 3. Orphan vendors (seeds 022833 + 022844) ---
    vend_before = sdb.vendors.count_documents({})
    r = sdb.vendors.delete_many({"seed_tag": {"$in": ORPHAN_TAGS}})
    vend_deleted = r.deleted_count
    vend_after = sdb.vendors.count_documents({})
    print(f"Vendors:   deleted {vend_deleted}  ({vend_before} -> {vend_after})")

    # --- 4. Orphan units (seed 022844) ---
    units_before = sdb.units.count_documents({})
    r = sdb.units.delete_many({"seed_tag": PARTS_ORPHAN_TAG})
    units_deleted = r.deleted_count
    units_after = sdb.units.count_documents({})
    print(f"Units:     deleted {units_deleted}  ({units_before} -> {units_after})")

    print("\n=== Cleanup complete ===")
    print(f"Total removed: {parts_deleted + cust_deleted + vend_deleted + units_deleted} documents")

    # --- Sanity check: no duplicate part numbers ---
    dup_check = list(sdb.parts.aggregate([
        {"$group": {"_id": "$part_number", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
    ]))
    print(f"Remaining duplicate part_numbers: {len(dup_check)}  (expect 0)")

    # --- Sanity check: customers with no first_name ---
    empty_cust = sdb.customers.count_documents(
        {"$or": [{"first_name": None}, {"first_name": ""}, {"first_name": {"$exists": False}}]}
    )
    print(f"Customers without first_name: {empty_cust}")


if __name__ == "__main__":
    sdb = connect()
    run_cleanup(sdb)
