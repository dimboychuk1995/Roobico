"""Ночная уборка системного клиента "NEW Customer" во всех магазинах.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.cleanup_system_customer --dry-run           # только посчитать
    python -m app.scripts.cleanup_system_customer                     # боевой прогон
    python -m app.scripts.cleanup_system_customer --shop-db <name>    # одна shop-база
    python -m app.scripts.cleanup_system_customer --grace-hours 12    # не трогать юниты моложе 12 ч

Что делает: в каждой shop-базе (все тенанты, все магазины с db_name)
находит системного клиента (customers с is_system: true — тот самый
"NEW Customer", под которым мобильное приложение заводит юниты по
неизвестным VIN) и удаляет его юниты, на которые нет ни одной ссылки:
ни work order / сметы, ни записи календаря, ни annual inspection, ни
вложений. Юниты моложе --grace-hours (по умолчанию 6 ч по created_at /
updated_at) пропускаются — механик мог только что отсканировать VIN.
Перед самим удалением ссылки проверяются ещё раз внутри транзакции:
юнит, на который за это время появилась ссылка, остаётся
(kept_late_referenced).
Вместе с юнитом уходит его журнал NHTSA-рекаллов (recall_notifications).
Сам системный клиент, обычные клиенты, их юниты и work orders не
трогаются. Логика — app/blueprints/customers/services/system_customer.py.

Идемпотентен: повторный запуск ничего не удаляет. По каждому магазину,
где что-то удалено, пишется событие "Nightly cleanup" в Activity Journal
(Reports → Activity Journal, source System). В stdout (cron-лог) по каждому
магазину — строка счётчиков и по одной строке на каждый удалённый (в
--dry-run — подлежащий удалению) юнит: id, VIN, unit #, label.

Exit code 1, если по каким-то магазинам были ошибки (для алертов cron).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

# Явный путь: find_dotenv() при запуске через `python -m` не всегда находит
# корневой .env, и скрипт молча подключается к дефолтному 127.0.0.1.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.blueprints.customers.services.system_customer import (  # noqa: E402
    DEFAULT_GRACE_HOURS,
    cleanup_system_customer_units,
)
from app.extensions import get_master_db, get_mongo_client  # noqa: E402
from app.utils.tenant import shop_db_name  # noqa: E402

_TOTAL_KEYS = (
    "system_customers", "candidates", "deleted", "kept_referenced",
    "kept_late_referenced", "skipped_recent", "recall_notifications_deleted",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Ничего не удалять, только посчитать")
    parser.add_argument("--shop-db", default="",
                        help="Обработать только магазин с этим db_name")
    parser.add_argument("--grace-hours", type=float, default=DEFAULT_GRACE_HOURS,
                        help="Не трогать юниты, созданные/изменённые позже чем N часов назад "
                             f"(по умолчанию {DEFAULT_GRACE_HOURS:g})")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        totals: dict = {"shops": 0, "errors": 0, **{key: 0 for key in _TOTAL_KEYS}}

        for shop in master.shops.find({}).sort("name", 1):
            db_name = shop_db_name(shop)
            if not db_name:
                continue
            if args.shop_db and db_name != args.shop_db:
                continue

            totals["shops"] += 1
            try:
                summary = cleanup_system_customer_units(
                    client[db_name], shop,
                    grace_hours=args.grace_hours, dry_run=args.dry_run,
                )
            except Exception:
                totals["errors"] += 1
                app.logger.exception(
                    "cleanup_system_customer: shop %s (%s) failed", shop.get("name"), db_name,
                )
                print(f"{shop.get('name')} ({db_name}): ERROR — see log")
                continue

            for key in _TOTAL_KEYS:
                totals[key] += int(summary.get(key) or 0)
            counts = " ".join(f"{key}={summary.get(key, 0)}" for key in _TOTAL_KEYS)
            print(f"{shop.get('name')} ({db_name}): {counts}")
            # Полный список юнитов — в stdout (cron-лог), по строке на юнит:
            # логгер приложения вне debug INFO не пишет, а в событии журнала
            # список лежит одной строкой.
            verb = "would delete" if args.dry_run else "deleted"
            for unit in summary.get("deleted_units") or []:
                print(
                    f"  {verb} {unit['id']} VIN {unit['vin'] or '-'} "
                    f"unit # {unit['unit_number'] or '-'} {unit['label']}"
                )

        prefix = "DRY RUN — nothing deleted. " if args.dry_run else ""
        print(f"{prefix}Totals: {totals}")
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
