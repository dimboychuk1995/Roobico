"""
Бэкфилл снимков имён пользователей в shop-базах.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_user_name_snapshots --dry-run             # только посчитать
    python -m app.scripts.backfill_user_name_snapshots                       # боевой прогон
    python -m app.scripts.backfill_user_name_snapshots --tenant <id|slug>    # один тенант

Что делает: для каждого пользователя (включая неактивных) приводит снимки
его имени во всех shop-базах его тенанта к ТЕКУЩЕМУ имени из master.users
(«first last», иначе name, иначе email): назначения на строки work orders
(labors[].labor.assigned_mechanics и блочное зеркало), отметки Done
(mechanic_done_marks), тайм-логи (wo_time_logs.user_name), бригады записей
календаря (mechanics[].name, mechanic_name). Логика —
app/utils/user_names.refresh_user_name_snapshots.

Экраны и так показывают живое имя по user_id — скрипт чистит историю для
«сырых» читателей (AI-ассистент, старые сборки мобильного приложения).
updated_at, labors_rev и Activity Journal не трогаются. Пользователи без
имени и email пропускаются. Идемпотентен: повторный прогон ничего не
меняет. На проде — только по явной команде владельца.

Вывод: строка счётчиков на каждого пользователя, у которого что-то
изменилось (в --dry-run — изменилось бы), итог по тенанту и общий итог.
Exit code 1 — были ошибки (подробности в логе), 2 — тенант не найден.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

# Явный путь: find_dotenv() при запуске через `python -m` не всегда находит
# корневой .env, и скрипт молча подключается к дефолтному 127.0.0.1.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.extensions import get_master_db  # noqa: E402
from app.utils.tenant import oid  # noqa: E402
from app.utils.user_names import (  # noqa: E402
    SNAPSHOT_COUNTER_KEYS,
    refresh_user_name_snapshots,
    tenant_id_values,
    user_display_name,
)

_USER_PROJECTION = {"first_name": 1, "last_name": 1, "name": 1, "email": 1}


def _tenant_filter(raw: str) -> dict:
    value = str(raw or "").strip()
    if not value:
        return {}
    parsed = oid(value)
    if parsed:
        return {"$or": [{"_id": parsed}, {"slug": value}]}
    return {"slug": value}


def _counts_line(counters: dict) -> str:
    return " ".join(f"{key}={counters.get(key, 0)}" for key in SNAPSHOT_COUNTER_KEYS)


def backfill_tenant(master_db, tenant: dict, *, dry_run: bool, verb: str) -> dict:
    """Все пользователи тенанта → refresh_user_name_snapshots. Счётчики тенанта."""
    totals = {"users": 0, "users_changed": 0, "users_skipped": 0, "errors": 0,
              **{key: 0 for key in SNAPSHOT_COUNTER_KEYS}}
    users = master_db.users.find(
        {"tenant_id": {"$in": tenant_id_values(tenant["_id"])}}, _USER_PROJECTION,
    ).sort("_id", 1)
    for user in users:
        totals["users"] += 1
        name = user_display_name(user)
        if not name:
            totals["users_skipped"] += 1
            continue
        counters = refresh_user_name_snapshots(master_db, tenant["_id"], user["_id"], name, dry_run=dry_run)
        totals["errors"] += int(counters.get("errors") or 0)
        changed = sum(int(counters.get(key) or 0) for key in SNAPSHOT_COUNTER_KEYS)
        if changed:
            totals["users_changed"] += 1
            for key in SNAPSHOT_COUNTER_KEYS:
                totals[key] += int(counters.get(key) or 0)
            print(f"  {verb} {user['_id']} -> {name!r}: {_counts_line(counters)}")
        if counters.get("errors"):
            print(f"  ERROR {user['_id']} ({name!r}): {counters['errors']} failed update(s) — see log")
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Ничего не менять, только посчитать")
    parser.add_argument("--tenant", default="",
                        help="Только этот тенант (id или slug)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        tenants = list(master.tenants.find(_tenant_filter(args.tenant), {"name": 1, "slug": 1}).sort("_id", 1))
        if args.tenant and not tenants:
            print(f"Tenant not found: {args.tenant}")
            return 2

        verb = "would update" if args.dry_run else "updated"
        grand = {"tenants": 0, "users": 0, "users_changed": 0, "users_skipped": 0, "errors": 0,
                 **{key: 0 for key in SNAPSHOT_COUNTER_KEYS}}
        for tenant in tenants:
            label = f"{tenant.get('name') or tenant.get('slug') or '-'} ({tenant['_id']})"
            print(f"{label}:")
            totals = backfill_tenant(master, tenant, dry_run=args.dry_run, verb=verb)
            grand["tenants"] += 1
            for key, value in totals.items():
                grand[key] += value
            print(
                f"{label}: users={totals['users']} changed={totals['users_changed']} "
                f"skipped={totals['users_skipped']} errors={totals['errors']} {_counts_line(totals)}"
            )

        prefix = "DRY RUN — nothing changed. " if args.dry_run else ""
        print(f"{prefix}Totals: {grand}")
    return 1 if grand["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
