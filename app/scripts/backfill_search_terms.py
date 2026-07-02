"""
Бэкфилл search_terms для customers и units во всех shop-базах.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_search_terms            # только записи без search_terms
    python -m app.scripts.backfill_search_terms --rebuild  # пересчитать все записи

Идемпотентен, безопасен для повторного запуска. На проде выполнить один раз
после деплоя фичи search_terms — до этого поиск работает через медленный
regex-fallback (см. app/utils/entity_search.py).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

# Явный путь: find_dotenv() при запуске через `python -m` не всегда находит
# корневой .env, и скрипт молча подключается к дефолтному 127.0.0.1.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402
from app.utils.entity_search import (  # noqa: E402
    build_customer_search_terms,
    build_unit_search_terms,
)


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


def _backfill_collection(col, build_fn, rebuild: bool) -> tuple[int, int]:
    query = {} if rebuild else {"search_terms": {"$exists": False}}
    scanned = updated = 0
    for doc in col.find(query):
        scanned += 1
        terms = build_fn(doc)
        if rebuild and doc.get("search_terms") == terms:
            continue
        col.update_one({"_id": doc["_id"]}, {"$set": {"search_terms": terms}})
        updated += 1
    return scanned, updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="Пересчитать все записи, а не только отсутствующие")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        for db_name in _shop_db_names(master):
            shop_db = client[db_name]
            c_scanned, c_updated = _backfill_collection(
                shop_db.customers, build_customer_search_terms, args.rebuild
            )
            u_scanned, u_updated = _backfill_collection(
                shop_db.units, build_unit_search_terms, args.rebuild
            )
            print(
                f"{db_name}: customers {c_updated}/{c_scanned} updated, "
                f"units {u_updated}/{u_scanned} updated"
            )

    print("Backfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
