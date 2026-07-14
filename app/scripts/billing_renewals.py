"""Ночное автопродление подписок Roobico (Stripe).

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.billing_renewals --dry-run   # только посчитать, кто due
    python -m app.scripts.billing_renewals             # боевой прогон
    python -m app.scripts.billing_renewals --tenant <slug>   # один тенант

Логика в app/blueprints/billing/services/renewals.py: тенантам, у которых
подписка кончается в ближайшие 3 дня, создаётся Stripe-инвойс — списание
с сохранённой карты либо письмо с hosted-страницей. Продление периода
делает webhook invoice.paid. Идемпотентен: пока по тенанту висит открытый
инвойс, второй не создаётся; повторный запуск за ночь безопасен.

Exit code 1, если по каким-то тенантам были Stripe-ошибки (для алертов cron).
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
from app.blueprints.billing.services.renewals import run_renewals  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Ничего не создавать в Stripe, только посчитать")
    parser.add_argument("--tenant", default="",
                        help="Обработать только тенанта с этим slug")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        stats = run_renewals(dry_run=args.dry_run, tenant_slug=args.tenant)

    prefix = "DRY RUN — nothing billed. " if args.dry_run else ""
    print(f"{prefix}Totals: {stats}")
    return 1 if stats.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
