"""CLI: repair stale part_id references inside wo_presets.

Background: presets store a part_id snapshot. When parts get re-imported they
get new _ids, so the preset keeps pointing at a part document that no longer
exists. Inventory deduction at runtime now falls back to part_number, but the
stale part_id stays in the data. This script heals the stored data: for every
preset part whose part_id no longer resolves to an active part, it looks the
part up by part_number and rewrites part_id to the current _id.

For every shop database it:
  1) iterates wo_presets
  2) for each part with a part_id:
       - if the part_id resolves to an active part -> leave as is
       - else look up by part_number among active parts:
           * exactly one match  -> rewrite part_id to the current _id
           * several matches     -> ambiguous, report and skip
           * no match            -> missing, report and skip

Usage (run from project root with the venv active):

    python -m app.scripts.fix_preset_part_ids              # dry-run
    python -m app.scripts.fix_preset_part_ids --apply      # write changes
    python -m app.scripts.fix_preset_part_ids --apply --shop-db shop_acme

Which database is touched is decided by MONGO_URI in the active .env:
  - locally this points at the dev Mongo (27017)
  - on the server it points at the production Mongo (27018)

Safe to re-run: only rewrites part_ids that currently don't resolve.
"""
from __future__ import annotations

import argparse
import sys

from bson import ObjectId
from bson.errors import InvalidId

from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import get_master_db, get_mongo_client


def _to_oid(value):
    if isinstance(value, ObjectId):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return ObjectId(s)
    except (InvalidId, TypeError, ValueError):
        return None


def _process_shop_db(shop_db, *, apply: bool) -> dict:
    stats = {
        "presets_seen": 0,
        "presets_updated": 0,
        "parts_with_id": 0,
        "parts_ok": 0,
        "parts_fixed": 0,
        "parts_renumbered": 0,
        "parts_ambiguous": 0,
        "parts_missing": 0,
        "parts_bad_id": 0,
    }

    for preset in shop_db.wo_presets.find({}, {"name": 1, "parts": 1}):
        stats["presets_seen"] += 1
        name = str(preset.get("name") or "").strip() or "(unnamed)"
        parts = preset.get("parts") or []
        if not isinstance(parts, list):
            continue

        changed = False
        for part in parts:
            if not isinstance(part, dict):
                continue

            raw_id = part.get("part_id")
            if not raw_id:
                # one-time / manual line without inventory link -> leave alone
                continue

            stats["parts_with_id"] += 1
            part_number = str(part.get("part_number") or "").strip()

            oid = _to_oid(raw_id)
            if oid is not None:
                live = shop_db.parts.find_one(
                    {"_id": oid, "is_active": True}, {"_id": 1, "part_number": 1}
                )
                if live:
                    stats["parts_ok"] += 1
                    # part_id is valid; refresh the stored part_number snapshot
                    # if the part was renumbered since the preset was saved.
                    live_pn = str(live.get("part_number") or "").strip()
                    if live_pn and live_pn != part_number:
                        print(
                            f"     # [{name}] renumber: '{part_number}' -> '{live_pn}'"
                        )
                        part["part_number"] = live_pn
                        stats["parts_renumbered"] += 1
                        changed = True
                    continue

            # part_id is stale (or malformed) -> try to heal via part_number
            if oid is None:
                stats["parts_bad_id"] += 1

            if not part_number:
                stats["parts_missing"] += 1
                print(f"     ! [{name}] stale part_id={raw_id!r} with no part_number")
                continue

            matches = list(
                shop_db.parts.find(
                    {"part_number": part_number, "is_active": True}, {"_id": 1}
                )
            )
            if len(matches) == 1:
                new_id = str(matches[0]["_id"])
                print(
                    f"     ~ [{name}] '{part_number}': {raw_id} -> {new_id}"
                )
                part["part_id"] = new_id
                stats["parts_fixed"] += 1
                changed = True
            elif len(matches) > 1:
                ids = ", ".join(str(m["_id"]) for m in matches)
                print(
                    f"     ? [{name}] '{part_number}': AMBIGUOUS, {len(matches)} active parts ({ids}) -- skipped"
                )
                stats["parts_ambiguous"] += 1
            else:
                print(
                    f"     ! [{name}] '{part_number}': NOT FOUND in active parts -- skipped"
                )
                stats["parts_missing"] += 1

        if changed:
            stats["presets_updated"] += 1
            if apply:
                shop_db.wo_presets.update_one(
                    {"_id": preset["_id"]},
                    {"$set": {"parts": parts}},
                )

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair stale part_id references in wo_presets by matching part_number."
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
        grand = {
            "presets_seen": 0,
            "presets_updated": 0,
            "parts_with_id": 0,
            "parts_ok": 0,
            "parts_fixed": 0,
            "parts_renumbered": 0,
            "parts_ambiguous": 0,
            "parts_missing": 0,
            "parts_bad_id": 0,
        }

        for db_name in db_names:
            print(f"  -> {db_name}")
            shop_db = client[db_name]
            s = _process_shop_db(shop_db, apply=args.apply)
            print(
                f"     presets: {s['presets_seen']}, presets to update: {s['presets_updated']}, "
                f"parts w/ id: {s['parts_with_id']}, ok: {s['parts_ok']}, "
                f"fixed: {s['parts_fixed']}, renumbered: {s['parts_renumbered']}, "
                f"ambiguous: {s['parts_ambiguous']}, missing: {s['parts_missing']}"
            )
            for k, v in s.items():
                grand[k] += v

        print(
            f"\n[{mode}] DONE. presets: {grand['presets_seen']}, "
            f"presets updated: {grand['presets_updated']}, "
            f"parts fixed: {grand['parts_fixed']}, "
            f"renumbered: {grand['parts_renumbered']}, "
            f"ambiguous: {grand['parts_ambiguous']}, "
            f"missing: {grand['parts_missing']}, "
            f"malformed ids: {grand['parts_bad_id']}"
        )
        if not args.apply:
            print("Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
