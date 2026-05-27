"""CLI: backfill labor.hourly_rate snapshot and recompute missing labor hours
on existing work orders, so historical reports stay correct even if labor rates
change or get deleted later.

For every shop database it:
  1) loads current labor_rates -> { code: hourly_rate }
  2) iterates work_orders and for each labors[i] block:
       - if labor.hourly_rate is missing/0 and a rate_code is present,
         writes the current rate as a per-block snapshot
       - if labor.hours is missing/0, totals.labors[i].labor > 0 and a rate is
         known (snapshot or current), sets labor.hours = amount / rate

Usage (run from project root with the venv active):

    python -m app.scripts.backfill_labor_hourly_rate              # dry-run
    python -m app.scripts.backfill_labor_hourly_rate --apply      # write changes
    python -m app.scripts.backfill_labor_hourly_rate --apply --shop-db roobico_acme

Safe to re-run: only fills in missing values.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import get_master_db, get_mongo_client


def _round2(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


def _load_rates_map(shop_db) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        for r in shop_db.labor_rates.find({}, {"code": 1, "hourly_rate": 1}):
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            try:
                out[code] = float(r.get("hourly_rate") or 0)
            except (ValueError, TypeError):
                out[code] = 0.0
    except Exception as exc:
        print(f"    ! failed to read labor_rates: {exc}", file=sys.stderr)
    return out


def _process_shop_db(shop_db, *, apply: bool) -> dict:
    stats = {
        "wo_seen": 0,
        "wo_updated": 0,
        "blocks_rate_filled": 0,
        "blocks_hours_filled": 0,
    }
    rates_map = _load_rates_map(shop_db)

    for wo in shop_db.work_orders.find(
        {"labors": {"$exists": True, "$ne": []}},
        {"labors": 1, "totals": 1},
    ):
        stats["wo_seen"] += 1
        labors = wo.get("labors") or []
        totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
        totals_blocks = totals.get("labors") if isinstance(totals.get("labors"), list) else []

        changed = False
        for i, block in enumerate(labors):
            if not isinstance(block, dict):
                continue
            labor = block.get("labor")
            if not isinstance(labor, dict):
                continue

            rate_code = str(labor.get("rate_code") or "").strip()
            try:
                snap_rate = float(labor.get("hourly_rate") or 0)
            except (ValueError, TypeError):
                snap_rate = 0.0

            # 1) snapshot the current rate if missing
            if snap_rate <= 0 and rate_code:
                current = float(rates_map.get(rate_code) or 0.0)
                if current > 0:
                    labor["hourly_rate"] = _round2(current)
                    snap_rate = current
                    stats["blocks_rate_filled"] += 1
                    changed = True

            # 2) recompute hours if missing and we have rate + amount
            try:
                hours = float(labor.get("hours") or 0)
            except (ValueError, TypeError):
                hours = 0.0
            if hours <= 0 and snap_rate > 0:
                amt = 0.0
                if i < len(totals_blocks) and isinstance(totals_blocks[i], dict):
                    try:
                        amt = float(totals_blocks[i].get("labor") or 0)
                    except (ValueError, TypeError):
                        amt = 0.0
                if amt > 0:
                    labor["hours"] = _round2(amt / snap_rate)
                    stats["blocks_hours_filled"] += 1
                    changed = True

        if changed:
            stats["wo_updated"] += 1
            if apply:
                shop_db.work_orders.update_one(
                    {"_id": wo["_id"]},
                    {"$set": {"labors": labors}},
                )

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill labor.hourly_rate snapshot and missing labor.hours on work orders."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--shop-db",
        dest="shop_db",
        help="Limit to a single shop database name. Default: process all shops in master.shops.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        if args.shop_db:
            db_names = [args.shop_db]
        else:
            db_names = sorted({
                str(s.get("db_name")).strip()
                for s in master.shops.find({}, {"db_name": 1})
                if s.get("db_name")
            })

        if not db_names:
            print("No shop databases found.")
            return 0

        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] processing {len(db_names)} shop db(s)")
        grand = {"wo_seen": 0, "wo_updated": 0, "blocks_rate_filled": 0, "blocks_hours_filled": 0}

        for db_name in db_names:
            print(f"  -> {db_name}")
            shop_db = client[db_name]
            s = _process_shop_db(shop_db, apply=args.apply)
            print(
                f"     WO scanned: {s['wo_seen']}, WO to update: {s['wo_updated']}, "
                f"rates filled: {s['blocks_rate_filled']}, hours filled: {s['blocks_hours_filled']}"
            )
            for k, v in s.items():
                grand[k] += v

        print(
            f"\n[{mode}] DONE. WO scanned: {grand['wo_seen']}, "
            f"WO updated: {grand['wo_updated']}, "
            f"rates filled: {grand['blocks_rate_filled']}, "
            f"hours filled: {grand['blocks_hours_filled']}"
        )
        if not args.apply:
            print("Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
