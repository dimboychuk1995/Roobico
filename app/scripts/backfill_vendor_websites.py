"""
Бэкфилл сайтов вендоров: старые значения → нормализованный URL.

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.backfill_vendor_websites --dry-run             # только посчитать
    python -m app.scripts.backfill_vendor_websites                       # боевой прогон
    python -m app.scripts.backfill_vendor_websites --tenant <id|slug>    # один тенант

Что делает: во всех shop-базах (или только магазинов указанного тенанта)
приводит vendors.website к виду, который теперь сохраняют формы:
«fleetpride.com» → «https://fleetpride.com», «WWW.X.COM/» → «https://www.x.com»
(логика — app/utils/urls.normalize_website). Значения, которые не
нормализуются («N/A», «see notes», javascript:…), НЕ трогаются — скрипт
печатает каждое такое значение, чтобы их поправили руками. updated_at и
Activity Journal не трогаются. Идемпотентен: повторный прогон ничего не
меняет. Экраны и без бэкфилла показывают старые значения рабочими ссылками
(website_href) — скрипт чистит сами данные. На проде — только по явной
команде владельца.

Вывод: строка на каждую shop-базу (scanned / changed / ok / invalid), каждое
изменение (в --dry-run — «would update») и каждое невалидное значение, общий
итог. Exit code 1 — были ошибки записи (подробности в логе), 2 — тенант не
найден.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

# Явный путь: find_dotenv() при запуске через `python -m` не всегда находит
# корневой .env, и скрипт молча подключается к дефолтному 127.0.0.1.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from bson import ObjectId  # noqa: E402
from flask import current_app  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import get_master_db, get_mongo_client  # noqa: E402
from app.utils.tenant import oid, shop_db_name  # noqa: E402
from app.utils.urls import normalize_website  # noqa: E402

COUNTER_KEYS = ("scanned", "changed", "ok", "invalid", "errors")


def _tenant_filter(raw: str) -> dict:
    value = str(raw or "").strip()
    if not value:
        return {}
    parsed = oid(value)
    if parsed:
        return {"$or": [{"_id": parsed}, {"slug": value}]}
    return {"slug": value}


def _tenant_id_values(tenant_id) -> list:
    """tenant_id в shops исторически и ObjectId, и строкой."""
    values = [tenant_id, str(tenant_id)]
    if not isinstance(tenant_id, ObjectId) and oid(tenant_id):
        values.append(oid(tenant_id))
    return values


def backfill_vendors(vendors_coll, *, dry_run: bool, verb: str, log=print) -> dict:
    """Нормализовать website всех вендоров коллекции. Счётчики."""
    counters = {key: 0 for key in COUNTER_KEYS}
    cursor = vendors_coll.find(
        {"website": {"$type": "string", "$ne": ""}}, {"name": 1, "website": 1},
    ).sort("_id", 1)
    for vendor in cursor:
        counters["scanned"] += 1
        stored = vendor.get("website")
        normalized = normalize_website(stored)
        if not normalized:
            counters["invalid"] += 1
            log(f"  INVALID (left as is) {vendor['_id']} {vendor.get('name')!r}: {stored!r}")
            continue
        if normalized == stored:
            counters["ok"] += 1
            continue
        counters["changed"] += 1
        log(f"  {verb} {vendor['_id']} {vendor.get('name')!r}: {stored!r} -> {normalized!r}")
        if dry_run:
            continue
        try:
            vendors_coll.update_one(
                {"_id": vendor["_id"], "website": stored},
                {"$set": {"website": normalized}},
            )
        except PyMongoError:
            counters["errors"] += 1
            current_app.logger.exception("backfill_vendor_websites: update failed for vendor %s", vendor["_id"])
    return counters


def _line(counters: dict) -> str:
    return " ".join(f"{key}={counters.get(key, 0)}" for key in COUNTER_KEYS)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Ничего не менять, только посчитать")
    parser.add_argument("--tenant", default="", help="Только этот тенант (id или slug)")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        master = get_master_db()
        client = get_mongo_client()

        shop_query: dict = {}
        if args.tenant:
            tenants = list(master.tenants.find(_tenant_filter(args.tenant), {"_id": 1}))
            if not tenants:
                print(f"Tenant not found: {args.tenant}")
                return 2
            tenant_values = [v for t in tenants for v in _tenant_id_values(t["_id"])]
            shop_query = {"tenant_id": {"$in": tenant_values}}

        verb = "would update" if args.dry_run else "updated"
        grand = {key: 0 for key in COUNTER_KEYS}
        seen_dbs: set[str] = set()
        for shop in master.shops.find(shop_query).sort("_id", 1):
            db_name = shop_db_name(shop)
            # Несколько документов магазина могут смотреть в одну базу.
            if not db_name or db_name in seen_dbs:
                continue
            seen_dbs.add(db_name)
            print(f"{db_name} ({shop.get('name') or shop['_id']}):")
            counters = backfill_vendors(client[db_name].vendors, dry_run=args.dry_run, verb=verb)
            for key in COUNTER_KEYS:
                grand[key] += counters[key]
            print(f"{db_name}: {_line(counters)}")

        prefix = "DRY RUN — nothing changed. " if args.dry_run else ""
        print(f"{prefix}Totals: shop_dbs={len(seen_dbs)} {_line(grand)}")
    return 1 if grand["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
