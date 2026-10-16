"""
Синхронизация ролей существующих тенантов с актуальным каталогом прав.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.sync_default_roles            # применить
    python -m app.scripts.sync_default_roles --dry-run  # только показать изменения

Только ДОБАВЛЯЕТ права ($addToSet), никогда не убирает — идемпотентен и
безопасен для повторного запуска. Прогонять после деплоя фич, добавляющих
новые permissions или уточняющих, какой ключ проверяет роут (иначе
существующие тенанты права не получат — при создании тенанта роли сеются
один раз).

Три источника добавляемых прав:

  * системные роли (is_system) — догоняются до актуальных дефолтов
    build_default_roles (например, manager получает все parts_orders.*);
  * кастомные роли — IMPLIED_GRANTS: роуты раньше проверяли широкий ключ
    (parts.edit, work_orders.create, settings.manage_org, …), а теперь —
    точные ключи из каталога (parts_orders.receive, work_orders.manage_payments,
    settings.manage_locations, …). Чтобы кастомная роль, собранная при старых
    проверках, не потеряла доступ, ей выдаются точные ключи, которые
    подразумевал широкий. После синка владелец может убрать лишнее в
    Settings → Roles — проверки теперь честные;
  * GRANT_ALL_ROLES — права, включённые по умолчанию у всех ролей
    (владелец отключает их точечно).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app import create_app  # noqa: E402
from app.constants.permissions import build_default_roles  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402

# Точные ключи, которые раньше «прятались» за широким (только кастомные роли).
IMPLIED_GRANTS: dict[str, list[str]] = {
    "parts.view": ["parts_orders.view"],
    "parts.edit": [
        "parts.create",
        "parts.delete",
        "parts.adjust_stock",
        "parts_orders.view",
        "parts_orders.create",
        "parts_orders.edit",
        "parts_orders.delete",
        "parts_orders.receive",
        "parts_orders.manage_payments",
        "settings.manage_parts_settings",
    ],
    "customers.edit": ["customers.create", "customers.manage_units", "customers.manage_portal"],
    "vendors.edit": ["vendors.create"],
    "work_orders.view": ["work_orders.export_pdf"],
    "work_orders.create": [
        "work_orders.edit",
        "work_orders.change_status",
        "work_orders.manage_payments",
        "work_orders.send_to_customer",
    ],
    "dashboard.view": ["dashboard.edit_goals"],
    "reports.view": ["reports.export"],
    "settings.manage_org": [
        "settings.manage_locations",
        "settings.manage_wo_presets",
        "settings.manage_wo_settings",
        "settings.manage_workflows",
        "settings.manage_notifications",
        "settings.manage_pdf_design",
    ],
}

# Права, включённые по умолчанию у ВСЕХ ролей (в т.ч. кастомных) —
# владелец отключает их точечно в Settings → Roles.
GRANT_ALL_ROLES: list[str] = [
    "work_orders.manage_email_contacts",
]


def _default_role_permissions() -> dict[str, set[str]]:
    return {r["key"]: set(r.get("permissions") or []) for r in build_default_roles()}


def implied_permissions(current: set[str]) -> set[str]:
    """Точные ключи, подразумеваемые широкими из current (без уже имеющихся)."""
    out: set[str] = set()
    for broad, implied in IMPLIED_GRANTS.items():
        if broad in current:
            out |= set(implied)
    return out - current


def sync_tenant_roles(tdb, dry_run: bool = False) -> list[tuple[str, list[str]]]:
    """Добавить недостающие права ролям одного тенанта (additive)."""
    defaults = _default_role_permissions()
    changes: list[tuple[str, list[str]]] = []
    for role in tdb.roles.find({}):
        role_key = str(role.get("key") or "")
        current = set(role.get("permissions") or [])
        grants = set(GRANT_ALL_ROLES)
        if role.get("is_system"):
            grants |= defaults.get(role_key, set())
        else:
            grants |= implied_permissions(current)
        missing = sorted(grants - current)
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
