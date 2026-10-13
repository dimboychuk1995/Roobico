"""
Бэкфилл ``last_price`` и ``vendor_id`` у запчастей во всех shop-базах.

last_price = цена парта из последнего по дате парт-ордера (активного, не
возврата, подтверждённого), vendor_id = вендор того же заказа — см.
app/blueprints/parts/services/costing.py. Парты без заказов получают
last_price = None, их вендор не меняется.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_part_last_price

Идемпотентен: можно гонять повторно. Выполнить один раз после деплоя фичи
«Last price» на каждой базе (dev и prod).
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

# Явный путь: find_dotenv() при запуске через `python -m` не всегда находит
# корневой .env, и скрипт молча подключается к дефолтному 127.0.0.1.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402
from app.blueprints.parts.services.costing import refresh_last_price  # noqa: E402


def _shop_rows(master_db) -> list[dict]:
    rows = []
    for shop in master_db.shops.find({}, {"db_name": 1, "database": 1, "db": 1, "name": 1}):
        name = shop.get("db_name") or shop.get("database") or shop.get("db")
        if name:
            rows.append({"_id": shop["_id"], "db_name": str(name), "name": shop.get("name") or ""})
    return rows


def main() -> int:
    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()
        total_parts = 0
        with_price = 0
        for shop in _shop_rows(master):
            shop_db = client[shop["db_name"]]
            part_ids = [p["_id"] for p in shop_db.parts.find({"shop_id": shop["_id"]}, {"_id": 1})]
            if not part_ids:
                continue
            for start in range(0, len(part_ids), 500):
                refresh_last_price(shop_db, shop["_id"], part_ids[start:start + 500])
            priced = shop_db.parts.count_documents({"shop_id": shop["_id"], "last_price": {"$ne": None}})
            total_parts += len(part_ids)
            with_price += priced
            print(f"{shop['name']} ({shop['db_name']}): parts={len(part_ids)} with_last_price={priced}")
        print(f"Totals: parts={total_parts} with_last_price={with_price}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
