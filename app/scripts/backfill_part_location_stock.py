"""
Бэкфилл раскладки остатков по локациям (part_location_stock) во всех shop-базах.

Для каждого учитываемого парта достраивает строки part_location_stock так,
чтобы sum(rows.qty) == parts.in_stock: «хвост» (легаси-остаток без строк)
кладётся в primary-локацию парта (parts.location_id) с движением "backfill".

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_part_location_stock

Идемпотентен: при согласованных данных ничего не пишет. Выполнить один раз
после деплоя фичи multi-location stock; до этого «хвост» отображается
виртуально и материализуется лениво при первом переносе/поправке.
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
from app.blueprints.parts.services.stock import materialize_location_rows  # noqa: E402


def _shop_db_names(master_db) -> list[str]:
    names = set()
    cursor = master_db.shops.find(
        {}, {"db_name": 1, "database": 1, "db": 1, "mongo_db": 1, "shop_db": 1}
    )
    for shop in cursor:
        name = (
            shop.get("db_name")
            or shop.get("database")
            or shop.get("db")
            or shop.get("mongo_db")
            or shop.get("shop_db")
        )
        if name:
            names.add(str(name))
    return sorted(names)


def main() -> int:
    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        for db_name in _shop_db_names(master):
            shop_db = client[db_name]
            scanned = materialized = 0
            for part in shop_db.parts.find(
                {"is_active": True, "do_not_track_inventory": {"$ne": True}},
                {"in_stock": 1, "part_number": 1, "location_id": 1,
                 "do_not_track_inventory": 1, "shop_id": 1},
            ):
                scanned += 1
                shop_id = part.get("shop_id")
                if shop_id is None:
                    continue
                if materialize_location_rows(shop_db, shop_id, part) != 0:
                    materialized += 1
            print(f"{db_name}: parts {materialized}/{scanned} materialized")

    print("Backfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
