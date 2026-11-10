"""
Бэкфилл стабильных labor_id на labor-блоках существующих work orders.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_wo_labor_ids

К labor_id привязываются логи времени механиков (wo_time_logs), поэтому
каждому блоку без id проставляется новый ObjectId-строкой. Идемпотентен:
блоки с уже заданным labor_id не трогаются. Есть и ленивая генерация при
открытии WO (детали механика и мобильного менеджера), так что скрипт лишь
ускоряет полное покрытие.

Запись — compare-and-swap по labors_rev (+1): сохранение, успевшее между
чтением и записью, не откатывается — WO перечитывается (до MAX_CAS_ATTEMPTS
раз), иначе пропускается и попадает в счётчик skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

from bson import ObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.blueprints.work_orders.services import edit_views as ev  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402


def _shop_db_names(master_db) -> list[str]:
    names = set()
    for shop in master_db.shops.find({}, {"db_name": 1}):
        name = shop.get("db_name")
        if name:
            names.add(str(name))
    return sorted(names)


def _with_ids(labors) -> tuple[list, bool]:
    changed = False
    for block in labors:
        if not isinstance(block, dict):
            continue
        if not str(block.get("labor_id") or "").strip():
            block["labor_id"] = str(ObjectId())
            changed = True
    return labors, changed


def backfill_shop(shop_db) -> tuple[int, int]:
    """(просмотрено, обновлено); WO, который всё время менялся, — skipped (печать)."""
    scanned = updated = skipped = 0
    query = {
        "$or": [
            {"labors": {"$elemMatch": {"labor_id": {"$exists": False}}}},
            {"labors": {"$elemMatch": {"labor_id": ""}}},
        ]
    }
    projection = ev.with_rev({"labors": 1})
    for wo in shop_db.work_orders.find(query, projection):
        scanned += 1
        for _attempt in range(ev.MAX_CAS_ATTEMPTS):
            labors, changed = _with_ids(wo.get("labors") or [])
            if not changed:
                break
            res = shop_db.work_orders.update_one(
                {"_id": wo["_id"], **ev.rev_filter(ev.labors_rev(wo))},
                ev.bump_rev({"$set": {"labors": labors}}),
            )
            if res.matched_count:
                updated += 1
                break
            wo = shop_db.work_orders.find_one({"_id": wo["_id"]}, projection)
            if not wo:
                break
        else:
            skipped += 1
    if skipped:
        print(f"  ! {skipped} work order(s) kept changing during the backfill — re-run later")
    return scanned, updated


def main() -> int:
    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()
        for db_name in _shop_db_names(master):
            scanned, updated = backfill_shop(client[db_name])
            print(f"{db_name}: work_orders {updated}/{scanned} updated")
    print("Backfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
