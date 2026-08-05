# -*- coding: utf-8 -*-
"""Слияние дублей запчастей (одинаковый part_number в одном магазине).

Запуск (из корня проекта, с активным .env):
    python -m app.scripts.merge_duplicate_parts                # dry-run (отчёт)
    python -m app.scripts.merge_duplicate_parts --apply        # боевой прогон
    python -m app.scripts.merge_duplicate_parts --db <db_name> # один магазин

Выживший в группе — самый старый АКТИВНЫЙ парт; группы без активных
пропускаются (только отчёт). Дубли не удаляются: помечаются
``is_active=False, merged_into=<survivor_id>`` — все исторические экраны
продолжают резолвить их снапшоты.

Что переносится на выжившего:
- ``in_stock`` дубля прибавляется к выжившему (дубль обнуляется);
- строки ``part_location_stock`` мержатся ``$inc``-ом по локациям (у
  коллекции уникальный индекс shop+part+location, поэтому не update_many);
- ``inventory_movements`` перевешиваются на выжившего — объединённая
  история по сумме дельт сходится с объединённым остатком, поэтому
  синтетическое движение НЕ пишется;
- ссылки в work_orders (labors[].parts[].part_id — ObjectId И строки;
  inventory_deductions[].part_id — строки, с суммированием qty_used при
  коллизии), parts_orders (items, returned_stock_chunks,
  received_item_locations-ключи), wo_presets (строки), cores (с мержем
  количества), core_returns, attachments;
- interchange_group наследуется, если у выжившего его нет.

Строки ``stocktake_items`` НЕ трогаются (исторические документы со
снапшотами); незавершённые стоктейки со ссылками на дубли попадают в
отчёт как WARNING — их надо закрыть/поправить руками.

Скрипт идемпотентен: повторный запуск не находит уже слитые группы
(merged-дубли исключаются из группировки).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

# .env репозитория; если скрипт запущен из /tmp на сервере — .env из CWD
try:
    _env = Path(__file__).resolve().parents[2] / ".env"
except IndexError:
    _env = None
load_dotenv(_env if _env and _env.exists() else Path.cwd() / ".env")


def utcnow():
    return datetime.now(timezone.utc)


def norm_pn(value) -> str:
    return str(value or "").strip().lower()


def _fmt(doc):
    return (f"_id={doc['_id']} pn={doc.get('part_number')!r} "
            f"desc={str(doc.get('description') or '')[:36]!r} "
            f"in_stock={doc.get('in_stock')!r} "
            f"track={not doc.get('do_not_track_inventory')} "
            f"active={doc.get('is_active')} created={doc.get('created_at')}")


def collect_groups(sdb, shop_id):
    """Группы дублей по нормализованному part_number (без уже слитых)."""
    by_pn: dict[str, list[dict]] = {}
    for p in sdb.parts.find({"shop_id": shop_id, "merged_into": {"$exists": False}}):
        key = norm_pn(p.get("part_number"))
        if key:
            by_pn.setdefault(key, []).append(p)
    return {k: v for k, v in by_pn.items() if len(v) > 1}


def pick_survivor(docs):
    active = [d for d in docs if d.get("is_active") is not False]
    pool = active or []
    if not pool:
        return None
    return sorted(pool, key=lambda d: (d.get("created_at") or utcnow(), d["_id"]))[0]


def count_refs(sdb, shop_id, part):
    pid, pid_s = part["_id"], str(part["_id"])
    return {
        "wo_labor_lines": sdb.work_orders.count_documents(
            {"shop_id": shop_id, "labors.parts.part_id": {"$in": [pid, pid_s]}}),
        "wo_deductions": sdb.work_orders.count_documents(
            {"shop_id": shop_id, "inventory_deductions.part_id": pid_s}),
        "parts_orders": sdb.parts_orders.count_documents(
            {"shop_id": shop_id, "$or": [
                {"items.part_id": pid},
                {"returned_stock_chunks.part_id": pid},
                {f"received_item_locations.{pid_s}": {"$exists": True}},
            ]}),
        "presets": sdb.wo_presets.count_documents(
            {"shop_id": shop_id, "parts.part_id": pid_s}),
        "cores": sdb.cores.count_documents({"shop_id": shop_id, "part_id": pid}),
        "core_returns": sdb.core_returns.count_documents(
            {"shop_id": shop_id, "part_id": pid}),
        "movements": sdb.inventory_movements.count_documents(
            {"shop_id": shop_id, "part_id": pid}),
        "location_rows": sdb.part_location_stock.count_documents(
            {"shop_id": shop_id, "part_id": pid}),
        "attachments": sdb.attachments.count_documents(
            {"shop_id": shop_id, "entity_type": "part", "entity_id": pid}),
        "stocktake_items": sdb.stocktake_items.count_documents({"part_id": pid}),
    }


def open_stocktakes_for(sdb, shop_id, part_ids):
    """Незавершённые стоктейки, где посчитаны дубли, — только предупреждаем."""
    st_ids = sdb.stocktake_items.distinct("stocktake_id", {"part_id": {"$in": part_ids}})
    if not st_ids:
        return []
    return list(sdb.stocktakes.find(
        {"_id": {"$in": st_ids}, "status": {"$nin": ["completed", "cancelled"]}},
        {"status": 1, "created_at": 1},
    ))


def merge_dupe_into(sdb, shop_id, survivor, dupe, user_id=None):
    """Перенести все ссылки/остатки дубля на выжившего. Возвращает лог-строки."""
    log = []
    now = utcnow()
    s_id, d_id = survivor["_id"], dupe["_id"]
    d_str, s_str = str(d_id), str(s_id)

    # 1. WO: строки партов в labors (ObjectId и строковые ссылки → ObjectId)
    res = sdb.work_orders.update_many(
        {"shop_id": shop_id, "labors.parts.part_id": {"$in": [d_id, d_str]}},
        {"$set": {"labors.$[].parts.$[p].part_id": s_id}},
        array_filters=[{"p.part_id": {"$in": [d_id, d_str]}}],
    )
    log.append(f"wo labor lines: {res.modified_count} WO")

    # 2. WO: inventory_deductions (строки) + суммирование при коллизии
    res = sdb.work_orders.update_many(
        {"shop_id": shop_id, "inventory_deductions.part_id": d_str},
        {"$set": {"inventory_deductions.$[d].part_id": s_str}},
        array_filters=[{"d.part_id": d_str}],
    )
    log.append(f"wo deductions: {res.modified_count} WO")
    for wo in sdb.work_orders.find(
        {"shop_id": shop_id, "inventory_deductions.part_id": s_str},
        {"inventory_deductions": 1},
    ):
        rows = wo.get("inventory_deductions") or []
        merged: dict[str, dict] = {}
        order: list[str] = []
        for r in rows:
            key = str(r.get("part_id") or "")
            if key in merged:
                merged[key]["qty_used"] = int(merged[key].get("qty_used") or 0) + int(r.get("qty_used") or 0)
            else:
                merged[key] = dict(r)
                order.append(key)
        if len(order) != len(rows):
            sdb.work_orders.update_one(
                {"_id": wo["_id"]},
                {"$set": {"inventory_deductions": [merged[k] for k in order]}},
            )
            log.append(f"wo {wo['_id']}: deduction rows collapsed")

    # 3. parts_orders: items, возвраты, ключи received_item_locations
    res = sdb.parts_orders.update_many(
        {"shop_id": shop_id, "items.part_id": d_id},
        {"$set": {"items.$[i].part_id": s_id}},
        array_filters=[{"i.part_id": d_id}],
    )
    log.append(f"parts_orders items: {res.modified_count}")
    res = sdb.parts_orders.update_many(
        {"shop_id": shop_id, "returned_stock_chunks.part_id": d_id},
        {"$set": {"returned_stock_chunks.$[c].part_id": s_id}},
        array_filters=[{"c.part_id": d_id}],
    )
    if res.modified_count:
        log.append(f"parts_orders return chunks: {res.modified_count}")
    for po in sdb.parts_orders.find(
        {"shop_id": shop_id, f"received_item_locations.{d_str}": {"$exists": True}},
        {"received_item_locations": 1},
    ):
        ril = po.get("received_item_locations") or {}
        value = ril.pop(d_str)
        ril.setdefault(s_str, value)  # при коллизии оставляем выбор выжившего
        sdb.parts_orders.update_one(
            {"_id": po["_id"]}, {"$set": {"received_item_locations": ril}})
        log.append(f"parts_order {po['_id']}: received_item_locations re-keyed")

    # 4. Пресеты (строковые part_id)
    res = sdb.wo_presets.update_many(
        {"shop_id": shop_id, "parts.part_id": d_str},
        {"$set": {"parts.$[p].part_id": s_str}},
        array_filters=[{"p.part_id": d_str}],
    )
    if res.modified_count:
        log.append(f"presets: {res.modified_count}")

    # 5. Cores: перенос или мерж количества (upsert-ключ shop+part+is_active)
    for core in sdb.cores.find({"shop_id": shop_id, "part_id": d_id}):
        target = sdb.cores.find_one(
            {"shop_id": shop_id, "part_id": s_id, "is_active": core.get("is_active")})
        if target:
            sdb.cores.update_one(
                {"_id": target["_id"]},
                {"$inc": {"quantity": int(core.get("quantity") or 0)},
                 "$set": {"updated_at": now}},
            )
            sdb.cores.update_one(
                {"_id": core["_id"]},
                {"$set": {"is_active": False, "quantity": 0,
                          "merged_into": target["_id"], "updated_at": now}},
            )
            log.append(f"core {core['_id']}: quantity merged into {target['_id']}")
        else:
            sdb.cores.update_one(
                {"_id": core["_id"]},
                {"$set": {"part_id": s_id,
                          "part_number": survivor.get("part_number"),
                          "updated_at": now}},
            )
            log.append(f"core {core['_id']}: re-pointed")
    res = sdb.core_returns.update_many(
        {"shop_id": shop_id, "part_id": d_id},
        {"$set": {"part_id": s_id, "part_number": survivor.get("part_number")}},
    )
    if res.modified_count:
        log.append(f"core_returns: {res.modified_count}")

    # 6. Вложения
    res = sdb.attachments.update_many(
        {"shop_id": shop_id, "entity_type": "part", "entity_id": d_id},
        {"$set": {"entity_id": s_id}},
    )
    if res.modified_count:
        log.append(f"attachments: {res.modified_count}")

    # 7. История движений — просто перевешивается: сумма дельт объединённой
    #    истории сходится с объединённым in_stock, доп. движение не нужно.
    res = sdb.inventory_movements.update_many(
        {"shop_id": shop_id, "part_id": d_id}, {"$set": {"part_id": s_id}})
    log.append(f"movements: {res.modified_count}")

    # 8. Остатки по локациям: $inc-merge (уникальный индекс!) + удаление строк
    for row in sdb.part_location_stock.find({"shop_id": shop_id, "part_id": d_id}):
        sdb.part_location_stock.update_one(
            {"shop_id": shop_id, "part_id": s_id, "location_id": row.get("location_id")},
            {"$inc": {"qty": int(row.get("qty") or 0)},
             "$set": {"part_number": str(survivor.get("part_number") or ""),
                      "updated_at": now, "updated_by": user_id},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        sdb.part_location_stock.delete_one({"_id": row["_id"]})
        log.append(f"location {row.get('location_id')}: +{int(row.get('qty') or 0)}")

    # 9. Общий остаток: атомарно забираем у дубля, отдаём выжившему
    old = sdb.parts.find_one_and_update(
        {"_id": d_id, "in_stock": {"$exists": True}},
        {"$set": {"in_stock": 0}},
    )
    taken = int((old or {}).get("in_stock") or 0)
    if taken and not survivor.get("do_not_track_inventory"):
        sdb.parts.update_one({"_id": s_id}, {"$inc": {"in_stock": taken}})
        log.append(f"in_stock: +{taken}")
    elif taken:
        log.append(f"WARNING: dupe had in_stock={taken}, survivor does not track inventory")

    # 10. Interchange-группа наследуется, у дубля снимается
    d_group = dupe.get("interchange_group")
    s_group = survivor.get("interchange_group")
    if d_group and not s_group:
        sdb.parts.update_one({"_id": s_id}, {"$set": {"interchange_group": d_group}})
        log.append("interchange_group inherited")
    elif d_group and s_group and d_group != s_group:
        log.append(f"WARNING: differing interchange groups ({s_group} vs {d_group}) — survivor's kept")
    if d_group:
        sdb.parts.update_one({"_id": d_id}, {"$unset": {"interchange_group": ""}})

    # 11. Финализация дубля
    sdb.parts.update_one(
        {"_id": d_id},
        {"$set": {"is_active": False, "merged_into": s_id,
                  "merged_at": now, "updated_at": now,
                  "deactivated_at": now, "deactivated_by": user_id}},
    )
    return log


def process_shop(client, shop, apply_changes: bool) -> int:
    sdb = client[shop["db_name"]]
    shop_id = shop["_id"]
    groups = collect_groups(sdb, shop_id)
    if not groups:
        return 0

    print("=" * 72)
    print(f"SHOP: {shop.get('name')} | db: {shop['db_name']} | групп дублей: {len(groups)}")
    merged = 0
    for key, docs in sorted(groups.items()):
        survivor = pick_survivor(docs)
        print(f"\n--- {key!r} x{len(docs)}")
        if survivor is None:
            print("    SKIP: в группе нет активных партов")
            continue
        dupes = [d for d in docs if d["_id"] != survivor["_id"]]
        print(f"    SURVIVOR: {_fmt(survivor)}")
        for d in dupes:
            refs = count_refs(sdb, shop_id, d)
            refs_note = ", ".join(f"{k}={v}" for k, v in refs.items() if v)
            print(f"    DUPE:     {_fmt(d)}")
            print(f"              refs: {refs_note or 'нет'}")
        warn = open_stocktakes_for(sdb, shop_id, [d["_id"] for d in dupes])
        for st in warn:
            print(f"    WARNING: незавершённый stocktake {st['_id']} "
                  f"(status={st.get('status')}) содержит дубли — поправить руками")
        if not apply_changes:
            continue
        for d in dupes:
            for line in merge_dupe_into(sdb, shop_id, survivor, d):
                print(f"      [{d['_id']}] {line}")
            merged += 1
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="боевой прогон (иначе dry-run)")
    ap.add_argument("--db", help="ограничиться одной shop-базой (db_name)")
    ap.add_argument("--uri", help="Mongo URI (иначе MONGO_URI из .env)")
    args = ap.parse_args()

    uri = args.uri or os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    master_name = os.environ.get("MASTER_DB_NAME") or os.environ.get("MONGO_DB") or "master_db"
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    master = client[master_name]

    print(f"MODE: {'APPLY' if args.apply else 'DRY-RUN'}")
    total = 0
    for shop in master.shops.find({}, {"name": 1, "db_name": 1}):
        if not shop.get("db_name"):
            continue
        if args.db and shop["db_name"] != args.db:
            continue
        total += process_shop(client, shop, args.apply)
    print(f"\nDONE. Слито дублей: {total}" if args.apply else "\nDONE (dry-run, ничего не изменено)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
