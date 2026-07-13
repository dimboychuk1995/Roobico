"""Проверка рекаллов NHTSA по всем юнитам всех магазинов + email-дайджесты.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.check_recalls --dry-run          # только посчитать, ничего не писать
    python -m app.scripts.check_recalls                    # боевой прогон
    python -m app.scripts.check_recalls --shop-db <name>   # только одна shop-база
    python -m app.scripts.check_recalls --email-override you@example.com
                                                           # все письма на этот адрес (проверка)

Первый прогон по юниту — baseline: текущие рекаллы помечаются как уже
известные, письма не отправляются. Дальше запускать раз в сутки (ночью):
новые кампании уходят клиентам одним дайджестом на клиента, отправки
журналируются в shop-коллекции recall_notifications.

Идемпотентен, безопасен для повторного запуска.
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
from app.blueprints.work_orders.services.recall_check import check_shop_recalls  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402


def _shop_db_name(shop: dict) -> str:
    return str(
        shop.get("db_name")
        or shop.get("database")
        or shop.get("db")
        or shop.get("mongo_db")
        or shop.get("shop_db")
        or ""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Ничего не писать и не отправлять, только посчитать")
    parser.add_argument("--shop-db", default="",
                        help="Обработать только магазин с этим db_name")
    parser.add_argument("--email-override", default="",
                        help="Слать все письма на этот адрес (проверочный прогон)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        cache: dict = {}  # NHTSA-ответы: общий кэш комбинаций make/model/year на весь прогон
        totals: dict = {}

        for shop in master.shops.find({"is_active": {"$ne": False}}).sort("name", 1):
            db_name = _shop_db_name(shop)
            if not db_name:
                continue
            if args.shop_db and db_name != args.shop_db:
                continue

            stats = check_shop_recalls(
                client[db_name], shop, cache,
                dry_run=args.dry_run,
                email_override=args.email_override,
            )
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + value
            print(f"{shop.get('name')} ({db_name}): {stats}")

        print(f"{'DRY RUN — nothing written or sent. ' if args.dry_run else ''}Totals: {totals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
