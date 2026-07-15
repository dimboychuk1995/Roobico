"""
Синхронизация системных ролей существующих тенантов с обновлёнными дефолтами.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.sync_default_roles            # применить
    python -m app.scripts.sync_default_roles --dry-run  # только показать изменения

Только ДОБАВЛЯЕТ права ($addToSet) системным ролям (is_system: true) —
никогда не убирает и не трогает кастомные роли, поэтому идемпотентен и
безопасен для повторного запуска. Прогнать один раз после деплоя
механик-режима (иначе у существующих тенантов роль mechanic не сможет
сохранять WO, а parts_manager/viewer потеряют цены в WO).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402

# Какие права добавить каким системным ролям.
ROLE_GRANTS: dict[str, list[str]] = {
    "mechanic": ["work_orders.create", "attachments.upload"],
    "parts_manager": ["work_orders.view_costs"],
    "viewer": ["work_orders.view_costs"],
}


def sync_tenant_roles(tdb, dry_run: bool = False) -> list[tuple[str, list[str]]]:
    """Добавить недостающие права системным ролям одного тенанта (additive)."""
    changes: list[tuple[str, list[str]]] = []
    for role_key, grants in ROLE_GRANTS.items():
        role = tdb.roles.find_one({"key": role_key, "is_system": True})
        if not role:
            continue
        missing = sorted(set(grants) - set(role.get("permissions") or []))
        if not missing:
            continue
        if not dry_run:
            tdb.roles.update_one(
                {"_id": role["_id"]},
                {"$addToSet": {"permissions": {"$each": missing}}},
            )
        changes.append((role_key, missing))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Показать изменения без записи")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        for tenant in master.tenants.find({}, {"db_name": 1, "name": 1}):
            db_name = (tenant.get("db_name") or "").strip()
            if not db_name:
                continue
            for role_key, missing in sync_tenant_roles(client[db_name], dry_run=args.dry_run):
                suffix = " (dry-run)" if args.dry_run else ""
                print(f"{db_name}: {role_key} +{missing}{suffix}")

    print("Role sync complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
