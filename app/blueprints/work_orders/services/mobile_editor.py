"""
Серверный расчёт лейборов/тоталов для мобильного редактора WO.

В вебе тоталы считает JS (recalcAll в work_order_details.js); мобильное
приложение шлёт только исходные данные, а математика выполняется здесь —
теми же формулами: labor = hours × hourly_rate (или ручной total),
shop supply = процент от labor, parts = Σ qty × price, core/misc — из
полей запчастей. Дальше результат прогоняется через те же
normalize/align/_apply_sales_tax, что и веб-сохранение.
"""
from __future__ import annotations

from app.blueprints.work_orders.services.common import catalog_part_unit, ensure_labor_id, f64, i32, oid, round2
from app.blueprints.work_orders.services.lookups import (
    get_labor_rates,
    get_shop_supply_percentage,
)
from app.blueprints.work_orders.services.pdf_contexts import _parse_misc_items
from app.blueprints.work_orders.services.totals import normalize_parts_payload


def suggest_part_price(shop_db, shop_id, customer_id, part_doc, pinned_price=None) -> float:
    """
    Цена продажи для строки WO — та же логика, что в веб-JS автозаполнении:
    если у запчасти задан selling price и клиент его не переопределяет —
    берём его; иначе считаем от cost по прайсинг-матрице (margin/markup).

    pinned_price — цена, закреплённая вручную в сервис-темплейте
    (price_overridden): как и на вебе, она сильнее selling price и
    матрицы, но кастомер с «override part selling price» всё равно
    получает цену по своей шкале.
    """
    from app.blueprints.work_orders.services.lookups import get_pricing_rules_json

    cost = float(part_doc.get("average_cost") or 0)
    pricing = get_pricing_rules_json(shop_db, shop_id, customer_id=customer_id)
    override = bool((pricing or {}).get("override_part_selling_price"))

    if not override and pinned_price is not None:
        return round2(float(pinned_price))

    if not override and part_doc.get("has_selling_price") and float(part_doc.get("selling_price") or 0) > 0:
        return round2(float(part_doc.get("selling_price") or 0))

    if not pricing or cost <= 0:
        return round2(cost)

    rule = None
    for r in pricing.get("rules") or []:
        frm = r.get("from")
        to = r.get("to")
        if cost >= frm and (to is None or cost <= to):
            rule = r
            break
    if not rule:
        return round2(cost)

    pct = float(rule.get("value_percent") or 0)
    if (pricing.get("mode") or "margin") == "markup":
        return round2(cost * (1 + pct / 100.0))
    # margin: price = cost / (1 - pct/100)
    if pct >= 100:
        return round2(cost)
    return round2(cost / (1 - pct / 100.0))


def compute_labors_and_totals(shop_db, shop, labors_payload):
    """
    labors_payload (от мобильного клиента):
      [{description, hours, rate_code, labor_total (опц. ручной),
        issue_description, parts: [{part_id, part_number, description, qty,
        cost, price, core_charge, misc_charge, misc_charge_description,
        one_time_part}], client_key (опц.), minted (опц.)}]

    Возвращает (labors_nested, totals_raw): канонические labors для
    документа WO и сырые totals для normalize_totals_payload/align.
    """
    rates = {r["code"]: r["hourly_rate"] for r in get_labor_rates(shop_db, shop["_id"])}
    supply_pct = float(get_shop_supply_percentage(shop_db, shop["_id"]) or 0)

    labors_nested = []
    blocks = []

    g_labor = g_supply = g_parts = g_core = g_misc = g_misc_taxable = g_cost = 0.0

    for raw in (labors_payload or []):
        if not isinstance(raw, dict):
            continue

        description = str(raw.get("description") or "").strip()
        hours_raw = str(raw.get("hours") or "").strip()
        rate_code = str(raw.get("rate_code") or "").strip()
        issue_description = str(raw.get("issue_description") or "").strip()

        parts_clean = normalize_parts_payload(raw.get("parts") or [])

        manual_total = f64(raw.get("labor_total"))
        hours = f64(hours_raw) or 0.0
        if manual_total is not None and manual_total > 0:
            labor_base = round2(manual_total)
        else:
            labor_base = round2(hours * float(rates.get(rate_code) or 0))

        shop_supply = round2(labor_base * supply_pct / 100.0)

        parts_sum = 0.0
        core_sum = 0.0
        cost_sum = 0.0
        legacy_misc_sum = 0.0
        for p in parts_clean:
            qty = i32(p.get("qty")) or 0
            if qty <= 0:
                continue
            parts_sum = round2(parts_sum + round2((f64(p.get("price")) or 0) * qty))
            core_sum = round2(core_sum + round2((f64(p.get("core_charge")) or 0) * qty))
            cost_sum = round2(cost_sum + round2((f64(p.get("cost")) or 0) * qty))
            legacy_misc_sum = round2(legacy_misc_sum + round2((f64(p.get("misc_charge")) or 0) * qty))

        # Misc-позиции хранятся JSON-списком на первой строке запчастей
        # (см. pdf_contexts). Если JSON есть — legacy per-part misc не считаем.
        misc_items = _parse_misc_items(
            (parts_clean[0] or {}).get("misc_charge_description")
        ) if parts_clean else []
        misc_sum = 0.0
        misc_taxable_sum = 0.0
        if misc_items:
            for item in misc_items:
                price = round2(item.get("price") or 0)
                qty = f64(item.get("quantity") if item.get("quantity") is not None else 0) or 0
                if price <= 0 or qty <= 0:
                    continue
                line = round2(price * qty)
                misc_sum = round2(misc_sum + line)
                if item.get("taxable"):
                    misc_taxable_sum = round2(misc_taxable_sum + line)
        else:
            misc_sum = legacy_misc_sum

        block_full = round2(labor_base + shop_supply + parts_sum + core_sum + misc_sum)

        assigned = raw.get("assigned_mechanics")
        stored_block = {
            "labor_id": ensure_labor_id(raw.get("labor_id")),
            "labor": {
                "description": description,
                "hours": hours_raw,
                "rate_code": rate_code,
                "labor_full_total": block_full,
                "assigned_mechanics": assigned if isinstance(assigned, list) else [],
                "issue_description": issue_description,
                # Откуда часы: "preset" (фиксированы пресетом) / "tracked"
                # (из таймеров) / "" (вручную). Управляет автопересчётом.
                "hours_source": str(raw.get("hours_source") or "").strip(),
            },
            "parts": parts_clean,
        }
        # Метки merge (labor_merge.py) живут на уровне блока рядом с labor_id:
        # client_key — ключ новой строки от клиента (ретрай не дублирует её),
        # minted — «создана таким-то запросом такого-то устройства» (привязка
        # id-less строк старых клиентов). Каждая перезапись блока их переносит.
        client_key = str(raw.get("client_key") or "").strip()
        if client_key:
            stored_block["client_key"] = client_key
        if isinstance(raw.get("minted"), dict):
            stored_block["minted"] = dict(raw["minted"])
        labors_nested.append(stored_block)

        blocks.append({
            "labor": labor_base,
            "parts": parts_sum,
            "core_total": core_sum,
            "misc_total": misc_sum,
            "shop_supply_total": shop_supply,
            "cost_total": cost_sum,
            "labor_full_total": block_full,
        })

        g_labor = round2(g_labor + labor_base)
        g_supply = round2(g_supply + shop_supply)
        g_parts = round2(g_parts + parts_sum)
        g_core = round2(g_core + core_sum)
        g_misc = round2(g_misc + misc_sum)
        g_misc_taxable = round2(g_misc_taxable + misc_taxable_sum)
        g_cost = round2(g_cost + cost_sum)

    totals_raw = {
        "labors": blocks,
        "labor": g_labor,
        "parts": g_parts,
        "core_total": g_core,
        "misc_total": g_misc,
        "misc_taxable_total": g_misc_taxable,
        "shop_supply_total": g_supply,
        "cost_total": g_cost,
    }
    return labors_nested, totals_raw


def merge_mobile_manager_edit(shop_db, shop, existing_wo: dict, payload_labors, *, base=None, **ctx):
    """
    Правка WO менеджером в мобильном редакторе: трёхсторонний merge
    base × current × mine по MOBILE_MANAGER_SPEC (labor_merge.merge_labors).
    Возвращает MergeResult; .rows — payload для compute_labors_and_totals.

    Форма менеджера шлёт тексты, денежную группу (hours, rate_code,
    labor_total) и деньги запчастей, но не шлёт назначения механиков,
    hours_source и единицу измерения — они берутся из текущего WO (раньше
    каждый Save с телефона стирал назначения и метку tracked). Новая
    запчасть с part_id без unit получает единицу каталога. Затреканные часы
    (hours_source="tracked") остаются, пока менеджер их явно не поменял;
    при смене одной ставки база пересчитывается из этих часов.

    base — BaseChoice.rows или None; ctx — поля MergeContext.
    """
    from app.blueprints.work_orders.services import edit_views as ev
    from app.blueprints.work_orders.services.labor_merge import MOBILE_MANAGER_SPEC, MergeContext, merge_labors

    shop_id = shop["_id"]
    unit_cache: dict = {}

    def _with_catalog_unit(part):
        if not isinstance(part, dict):
            return part
        part = dict(part)
        if part.get("unit") is None and part.get("unit_of_measure") is None:
            pid = oid(part.get("part_id"))
            if pid:
                if pid not in unit_cache:
                    doc = shop_db.parts.find_one({"_id": pid, "shop_id": shop_id}, {"unit_of_measure": 1})
                    unit_cache[pid] = catalog_part_unit(doc) if doc else None
                if unit_cache[pid]:
                    part["unit"] = unit_cache[pid]
        return part

    def new_block(raw: dict) -> dict:
        row = dict(raw)
        row["parts"] = [_with_catalog_unit(p) for p in (raw.get("parts") or []) if isinstance(p, dict)]
        return row

    rates = {r["code"]: r["hourly_rate"] for r in get_labor_rates(shop_db, shop_id)}
    merge_ctx = MergeContext(**{"rates": rates, "new_block": new_block, "new_part": _with_catalog_unit, **ctx})
    current = ev.work_rows_from_wo(existing_wo)
    return merge_labors(base, current, list(payload_labors or []), MOBILE_MANAGER_SPEC, ctx=merge_ctx)
