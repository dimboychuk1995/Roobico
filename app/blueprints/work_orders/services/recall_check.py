"""Периодическая проверка рекаллов NHTSA по всем юнитам магазина.

Логика ночного джоба (запускается через app/scripts/check_recalls.py):

- юниты группируются по (make, model, year) — один запрос NHTSA на комбинацию,
  результат кэшируется на весь прогон (включая другие магазины);
- снапшот уведомлённых кампаний хранится на юните в `recalls_notified`
  (отдельно от `recalls_seen`, которым управляет модалка в UI, — ночной джоб
  не должен гасить бэйдж NEW для сотрудников);
- первый прогон по юниту — baseline: снапшот записывается, письма не шлются;
- новые кампании собираются в один дайджест на клиента и отправляются на
  email его контактов; факт отправки фиксируется в shop-коллекции
  `recall_notifications` (идемпотентность при повторных прогонах);
- если письмо не ушло — снапшот юнита не обновляется, кампания уйдёт при
  следующем прогоне.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from flask import current_app, render_template

from app.blueprints.work_orders import recalls_api
from app.blueprints.work_orders.services.lookups import customer_label, unit_label
from app.utils.contacts import get_contacts, get_main_contact_name
from app.utils.email_sender import send_email

# Пауза между HTTP-запросами к NHTSA, чтобы ночной обход не выглядел как флуд.
THROTTLE_SECONDS = 0.2

# Маркер «запрос к NHTSA не удался» в кэше комбинаций (отличаем от пустого
# списка рекаллов): юниты такой комбинации пропускаются без изменения снапшота.
_FETCH_FAILED = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _throttle() -> None:
    if THROTTLE_SECONDS:
        time.sleep(THROTTLE_SECONDS)


def _fetch_group_recalls(make: str, model: str, year: str, cache: dict):
    """Список рекаллов для комбинации make/model/year (с кэшем на прогон).

    Возвращает list[dict] или None, если NHTSA недоступна.
    """
    key = (
        recalls_api._norm_model(make),
        recalls_api._norm_model(model),
        str(year).strip(),
    )
    if key in cache:
        return cache[key]

    nhtsa_models = recalls_api._matching_nhtsa_models(make, model, year)
    _throttle()

    rows = []
    fetched_any = False
    for nhtsa_model in nhtsa_models:
        try:
            payload = recalls_api._fetch_recalls(make, nhtsa_model, year)
        except Exception:
            current_app.logger.exception(
                "Recall check: NHTSA fetch failed for %s %s %s", year, make, nhtsa_model
            )
            continue
        finally:
            _throttle()
        fetched_any = True
        chunk = payload.get("results") if isinstance(payload, dict) else None
        rows.extend(chunk if isinstance(chunk, list) else [])

    if not fetched_any:
        cache[key] = _FETCH_FAILED
        return _FETCH_FAILED

    recalls = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        campaign = str(row.get("NHTSACampaignNumber") or "").strip()
        if not campaign or campaign in seen:
            continue
        seen.add(campaign)
        recalls.append({
            "campaign_number": campaign,
            "report_date": recalls_api._format_report_date(row.get("ReportReceivedDate")),
            "component": str(row.get("Component") or "").strip(),
            "summary": str(row.get("Summary") or "").strip(),
            "remedy": str(row.get("Remedy") or "").strip(),
        })
    cache[key] = recalls
    return recalls


def _customer_emails(customer: dict) -> list[str]:
    out = []
    for contact in get_contacts(customer, entity_type="customer"):
        email = str(contact.get("email") or "").strip().lower()
        if email and "@" in email and email not in out:
            out.append(email)
    return out


def _snapshot(campaigns: list[str]) -> dict:
    return {"campaigns": sorted(set(campaigns)), "checked_at": _utcnow()}


def _journal_rows(shop, customer_id, items, status: str, to_emails: list[str]) -> list[dict]:
    now = _utcnow()
    rows = []
    for item in items:
        for recall in item["new_recalls"]:
            rows.append({
                "shop_id": shop["_id"],
                "customer_id": customer_id,
                "unit_id": item["unit"]["_id"],
                "campaign_number": recall["campaign_number"],
                "channel": "email",
                "status": status,
                "to": to_emails,
                "created_at": now,
            })
    return rows


def check_shop_recalls(shop_db, shop, cache: dict, *, dry_run: bool = False,
                       email_override: str = "") -> dict:
    """Обойти юниты магазина, разослать дайджесты о новых рекаллах.

    dry_run — ничего не писать и не отправлять, только посчитать.
    email_override — слать все письма на этот адрес (проверка на dev-базе).
    """
    stats = {
        "units_total": 0,
        "units_skipped_no_info": 0,
        "units_fetch_failed": 0,
        "units_baselined": 0,
        "new_campaigns": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "customers_notified": 0,
        "customers_no_email": 0,
    }

    # customer_id -> список {unit, new_recalls, campaigns_current}
    pending: dict = {}

    for unit in shop_db.units.find({"shop_id": shop["_id"], "is_active": True}):
        stats["units_total"] += 1

        make = str(unit.get("make") or "").strip()
        model = str(unit.get("model") or "").strip()
        year = str(unit.get("year") or "").strip()
        if not (make and model and year and unit.get("customer_id")):
            stats["units_skipped_no_info"] += 1
            continue

        recalls = _fetch_group_recalls(make, model, year, cache)
        if recalls is _FETCH_FAILED:
            stats["units_fetch_failed"] += 1
            continue

        campaigns = [r["campaign_number"] for r in recalls]
        prev = unit.get("recalls_notified") if isinstance(unit.get("recalls_notified"), dict) else None

        if prev is None:
            stats["units_baselined"] += 1
            if not dry_run:
                shop_db.units.update_one(
                    {"_id": unit["_id"]},
                    {"$set": {"recalls_notified": _snapshot(campaigns)}},
                )
            continue

        prev_set = set(prev.get("campaigns") or [])
        fresh = [r for r in recalls if r["campaign_number"] not in prev_set]

        if fresh:
            # Идемпотентность: не слать кампании, по которым уведомление уже
            # зафиксировано в журнале (например, снапшот юнита откатили).
            already = {
                doc.get("campaign_number")
                for doc in shop_db.recall_notifications.find(
                    {
                        "unit_id": unit["_id"],
                        "campaign_number": {"$in": [r["campaign_number"] for r in fresh]},
                    },
                    {"campaign_number": 1},
                )
            }
            fresh = [r for r in fresh if r["campaign_number"] not in already]

        if not fresh:
            if not dry_run:
                shop_db.units.update_one(
                    {"_id": unit["_id"]},
                    {"$set": {"recalls_notified": _snapshot(campaigns)}},
                )
            continue

        stats["new_campaigns"] += len(fresh)
        pending.setdefault(unit["customer_id"], []).append({
            "unit": unit,
            "new_recalls": fresh,
            "campaigns_current": campaigns,
        })

    shop_name = str(shop.get("name") or "").strip()
    shop_email = str(shop.get("email") or "").strip()
    shop_phone = str(shop.get("phone") or "").strip()

    for customer_id, items in pending.items():
        customer = shop_db.customers.find_one({"_id": customer_id, "shop_id": shop["_id"]})
        to_emails = [email_override] if email_override else _customer_emails(customer or {})

        if dry_run:
            current_app.logger.info(
                "Recall check (dry run): would email %s about %d unit(s) with new recalls",
                to_emails or "<no email>", len(items),
            )
            stats["customers_notified"] += 1
            continue

        def _commit(status: str) -> None:
            rows = _journal_rows(shop, customer_id, items, status, to_emails)
            if rows:
                shop_db.recall_notifications.insert_many(rows)
            for item in items:
                shop_db.units.update_one(
                    {"_id": item["unit"]["_id"]},
                    {"$set": {"recalls_notified": _snapshot(item["campaigns_current"])}},
                )

        if not to_emails:
            # Уведомить некуда: фиксируем в журнале и двигаем снапшот, иначе
            # каждый прогон будет спотыкаться об одни и те же кампании.
            stats["customers_no_email"] += 1
            _commit("skipped_no_email")
            continue

        html_body = render_template(
            "emails/unit_recalls_alert.html",
            cust_name=(get_main_contact_name(customer, entity_type="customer")
                       or customer_label(customer or {})),
            shop_name=shop_name,
            shop_phone=shop_phone,
            shop_email=shop_email,
            items=[{
                "unit_label": unit_label(item["unit"]),
                "recalls": item["new_recalls"],
            } for item in items],
        )
        total_new = sum(len(item["new_recalls"]) for item in items)
        subject = (
            f"Safety recall notice ({total_new} new) — {shop_name}"
            if shop_name else f"Safety recall notice ({total_new} new)"
        )

        try:
            send_email(
                to_emails,
                subject,
                html_body,
                reply_to=shop_email or None,
                from_name=shop_name or None,
            )
        except Exception:
            # Снапшот намеренно не обновляем — уйдёт при следующем прогоне.
            current_app.logger.exception(
                "Recall check: failed to email customer %s (shop %s)", customer_id, shop.get("_id")
            )
            stats["emails_failed"] += 1
            continue

        stats["emails_sent"] += 1
        stats["customers_notified"] += 1
        _commit("sent")

    return stats
