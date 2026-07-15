"""
Бэкфилл стабильных labor_id на labor-блоках существующих work orders.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_wo_labor_ids

К labor_id привязываются логи времени механиков (wo_time_logs), поэтому
каждому блоку без id проставляется новый ObjectId-строкой. Идемпотентен:
блоки с уже заданным labor_id не трогаются. Есть и ленивая генерация при
открытии WO механиком, так что скрипт лишь ускоряет полное покрытие.
"""
from __future__ import annotations

import sys
from pathlib import Path

from bson import ObjectId
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402


def _shop_db_names(master_db) -> list[str]:
    names = set()
    for shop in master_db.shops.find({}, {"db_name": 1}):
        name = shop.get("db_name")
        if name:
            names.add(str(name))
    return sorted(names)


def backfill_shop(shop_db) -> tuple[int, int]:
    scanned = updated = 0
    query = {
        "$or": [
            {"labors": {"$elemMatch": {"labor_id": {"$exists": False}}}},
            {"labors": {"$elemMatch": {"labor_id": ""}}},
        ]
    }
    for wo in shop_db.work_orders.find(query, {"labors": 1}):
        scanned += 1
        labors = wo.get("labors") or []
        changed = False
        for block in labors:
            if not isinstance(block, dict):
                continue
            if not str(block.get("labor_id") or "").strip():
                block["labor_id"] = str(ObjectId())
                changed = True
        if changed:
            shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"labors": labors}})
            updated += 1
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
