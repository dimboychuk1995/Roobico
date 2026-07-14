"""Бэкфилл подписки для legacy-тенантов без subscription-полей.

Тенанты, созданные до включения биллинга, не имеют subscription_until и
проходят в приложение бесплатно (enforcement их пропускает), а ночной
renewal-скрипт их пропускает с warning'ом. Этот скрипт даёт каждому такому
тенанту trial на N дней — дальше их подхватывает обычный биллинг-цикл.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_subscriptions --dry-run
    python -m app.scripts.backfill_subscriptions              # trial 30 дней
    python -m app.scripts.backfill_subscriptions --days 14

Идемпотентен: тенанты, у которых subscription_until уже стоит, не трогаются.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.extensions import get_master_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать, кого затронет")
    parser.add_argument("--days", type=int, default=30,
                        help="Длительность trial в днях (default: 30)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        now = datetime.utcnow()
        trial_until = now + timedelta(days=args.days)

        query = {"subscription_until": {"$not": {"$type": "date"}}}
        updated = 0
        for tenant in master.tenants.find(query).sort("slug", 1):
            slug = tenant.get("slug") or str(tenant["_id"])
            if args.dry_run:
                print(f"[dry-run] would set trial until {trial_until:%Y-%m-%d} "
                      f"for {slug} ({tenant.get('name')})")
                continue
            master.tenants.update_one(
                {"_id": tenant["_id"],
                 "subscription_until": {"$not": {"$type": "date"}}},
                {"$set": {
                    "subscription_status": "trial",
                    "subscription_until": trial_until,
                    "trial_started_at": now,
                    "updated_at": now,
                }},
            )
            updated += 1
            print(f"set trial until {trial_until:%Y-%m-%d} for {slug} "
                  f"({tenant.get('name')})")

        print(f"{'DRY RUN — nothing written. ' if args.dry_run else ''}"
              f"Updated: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
