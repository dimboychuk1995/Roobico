"""
Ночная уборка системного клиента "NEW Customer".

Механик сканирует VIN в мобильном приложении — неизвестный VIN заводит
юнит под системным клиентом магазина (customers.is_system: true, см.
mobile_api/vin_scan.py). Дальше либо офис переносит WO на настоящую
компанию (Transfer в work_orders/routes.py: исходный юнит деактивируется,
у компании появляется копия), либо юнит так и остаётся «болтаться» —
отсканирован, а WO не создан. За месяцы у "NEW Customer" копятся десятки
таких юнитов, и владелец чистит их руками.

cleanup_system_customer_units(shop_db, shop) удаляет из shop-базы только
юниты системного клиента, на которые нет ни одной ссылки
(REFERENCING_COLLECTIONS) и которые старше grace-периода (механик мог
отсканировать VIN минуту назад и ещё не успеть создать WO). Всё остальное
— сам документ системного клиента, обычные клиенты (в том числе компания,
которую просто назвали "New Customer"), work orders и прочие коллекции —
не трогается. Запуск по cron: app/scripts/cleanup_system_customer.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import current_app

from app.utils import audit_log
from app.utils.mongo_tx import run_atomically

SYSTEM_CUSTOMER_LABEL = "NEW Customer"

# Актор события в Activity Journal (user_name) — запись идёт без сессии.
CLEANUP_ACTOR = {"name": "Nightly cleanup"}

# Grace-период по умолчанию (часы): юнит, созданный или менявшийся позже,
# не трогаем — механик мог отсканировать VIN и ещё не создать WO (смена
# может длиться дольше пары часов, отсюда 6, а не 2).
DEFAULT_GRACE_HOURS = 6.0

# Коллекции shop-базы, ссылка из которых делает юнит «живым»: такой юнит не
# удаляется никогда. Запись: (коллекция, поле со ссылкой на units._id,
# дополнительный фильтр, scoped_by_shop).
#
# scoped_by_shop=True — в запрос добавляется shop_id магазина: каждый
# документ этих коллекций хранит shop_id своего магазина (проверено на
# копии прода), а их индексы начинаются с него
# (idx_work_orders_shop_unit_active_created_desc,
# idx_calendar_events_shop_unit_start, idx_annual_inspections_shop_unit_created
# в app/extensions.py) — без shop_id запрос шёл бы мимо индекса.
#
# Список собран grep'ом по всему app/ (unit_id и entity_type == "unit") —
# при появлении новой коллекции со ссылкой на юнит добавить её сюда.
REFERENCING_COLLECTIONS = (
    # Work orders и сметы (estimate — статус WO в той же коллекции): любой
    # WO, активный или удалённый (is_active: false), — главная причина
    # оставить юнит; после Transfer WO ссылается уже на копию юнита.
    ("work_orders", "unit_id", None, True),
    # Записи календаря: appointment на юнит, в том числе ещё без WO.
    ("calendar_events", "unit_id", None, True),
    # Annual (DOT) inspections: история хранится целиком, DOT требует retention.
    ("annual_inspections", "unit_id", None, True),
    # Файлы/фото, загруженные на страницу юнита (entity_id — ObjectId юнита,
    # см. app/utils/attachments.py). Индекс idx_attachments_entity_type_id —
    # (entity_type, entity_id) без shop_id, поэтому фильтр по shop_id сюда
    # не добавляем: запрос и так идёт по индексу, а unit _id уникален.
    ("attachments", "entity_id", {"entity_type": "unit"}, False),
)
# Проверены и на юнит НЕ ссылаются (поэтому здесь не нужны):
# wo_time_logs, work_order_payments — work_order_id/labor_id;
# parts_orders, parts_order_payments, inbound_emails, inventory_movements,
# cores, core_returns, stocktakes, stocktake_items — парты/вендоры/PO;
# customers, units — нет ссылок на другие юниты (копия при Transfer не
# хранит id исходника); uattend_* — сотрудники; master.audit_journal —
# журнал событий, а не ссылка.

# Производные данные юнита — удаляются вместе с ним и причиной оставить
# юнит не считаются: журнал отправленных NHTSA-рекаллов
# (work_orders/services/recall_check.py) без юнита смысла не имеет.
DERIVED_COLLECTIONS = (
    ("recall_notifications", "unit_id"),
)

_UNIT_FIELDS = {
    "vin": 1, "unit_number": 1, "year": 1, "make": 1, "model": 1,
    "is_active": 1, "created_at": 1, "updated_at": 1,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value) -> datetime | None:
    """Дата из Mongo (наивная = UTC) → aware UTC; не дата → None."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _id_variants(ids: list[ObjectId]) -> list:
    """ObjectId и его строковая форма — матчим ссылку, как бы она ни хранилась."""
    return list(ids) + [str(i) for i in ids]


def _is_recent(unit: dict, since: datetime) -> bool:
    """Юнит создан или менялся после since. Без created_at — считаем старым."""
    for key in ("created_at", "updated_at"):
        stamp = _as_utc(unit.get(key))
        if stamp is not None and stamp >= since:
            return True
    return False


def _unit_summary(unit: dict) -> dict:
    return {
        "id": str(unit["_id"]),
        "vin": str(unit.get("vin") or ""),
        "unit_number": str(unit.get("unit_number") or ""),
        "label": audit_log.unit_label(unit),
        "is_active": unit.get("is_active") is not False,
    }


def _unit_line(unit_summary: dict) -> str:
    """Человекочитаемая строка юнита для журнала: «label — VIN x, unit # y (id z)»."""
    return (
        f"{unit_summary['label']} — VIN {unit_summary['vin'] or '-'}, "
        f"unit # {unit_summary['unit_number'] or '-'} (id {unit_summary['id']})"
    )


def _live_references(shop_db, shop_id, unit_ids: list[ObjectId],
                     session=None) -> dict[ObjectId, list[str]]:
    """
    Реальные запросы к Mongo: {unit_id: [коллекции, где на него ссылаются]}
    по REFERENCING_COLLECTIONS. Shop-scoped коллекции запрашиваются с
    shop_id магазина (по индексу), attachments — по (entity_type, entity_id).
    session — чтобы повторная проверка перед удалением шла внутри той же
    транзакции (distinct принимает session).
    """
    if not unit_ids:
        return {}
    by_key = {str(i): i for i in unit_ids}
    variants = _id_variants(unit_ids)
    refs: dict[ObjectId, list[str]] = {}
    for coll_name, field, extra, scoped_by_shop in REFERENCING_COLLECTIONS:
        query = {"shop_id": shop_id} if scoped_by_shop else {}
        query[field] = {"$in": variants}
        if extra:
            query.update(extra)
        for value in shop_db[coll_name].distinct(field, query, session=session):
            uid = by_key.get(str(value))
            if uid is not None and coll_name not in refs.setdefault(uid, []):
                refs[uid].append(coll_name)
    return refs


def find_unit_references(shop_db, shop_id, unit_ids: list[ObjectId], *,
                         session=None) -> dict[ObjectId, list[str]]:
    """
    {unit_id: [коллекции, где на него ссылаются]} — первый проход cleanup.

    Тесты подменяют именно это имя, чтобы сымитировать устаревший первый
    проход; повторная проверка перед самим удалением идёт через
    _live_references и подменой не отключается.
    """
    return _live_references(shop_db, shop_id, unit_ids, session=session)


def cleanup_system_customer_units(shop_db, shop: dict, *, now=None,
                                  grace_hours: float = DEFAULT_GRACE_HOURS,
                                  dry_run: bool = False) -> dict:
    """
    Удалить «висячие» юниты системного клиента "NEW Customer" одного магазина.

    shop — документ master.shops ({_id, tenant_id, name, db_name}).
    Юнит удаляется, только если на него нет ни одной ссылки из
    REFERENCING_COLLECTIONS и он старше grace_hours (по умолчанию 6 ч — по
    created_at и updated_at). Перед самим удалением, уже внутри транзакции,
    ссылки проверяются ещё раз: юнит, на который за это время появился WO /
    запись календаря / инспекция / вложение, остаётся (kept_late_referenced).
    dry_run — посчитать и ничего не удалять. Не трогает сам документ
    системного клиента, других клиентов и их юниты.

    Возвращает сводку: system_customers (найдено системных клиентов),
    candidates (юнитов у них, активных и нет), deleted (удалено; в dry_run —
    было бы удалено), kept_referenced (+ kept_referenced_by по коллекциям),
    kept_late_referenced (ссылка появилась между проходом и удалением),
    skipped_recent (моложе grace), recall_notifications_deleted,
    deleted_units [{id, vin, unit_number, label}], dry_run.
    """
    now = _as_utc(now) or _utcnow()
    grace_since = now - timedelta(hours=max(float(grace_hours or 0), 0.0))
    shop_id = shop["_id"]
    shop_name = str(shop.get("name") or "")

    summary = {
        "shop_id": str(shop_id),
        "shop_name": shop_name,
        "system_customers": 0,
        "candidates": 0,
        "deleted": 0,
        "kept_referenced": 0,
        "kept_referenced_by": {},
        "kept_late_referenced": 0,
        "skipped_recent": 0,
        "recall_notifications_deleted": 0,
        "deleted_units": [],
        "dry_run": bool(dry_run),
    }

    # Только системный клиент этого магазина. Обычный клиент, которого
    # назвали "New Customer", под фильтр не попадает — у него нет is_system.
    system_customers = list(
        shop_db.customers.find(
            {"shop_id": shop_id, "is_system": True},
            {"_id": 1, "company_name": 1, "created_at": 1},
        ).sort("created_at", 1)
    )
    summary["system_customers"] = len(system_customers)
    if not system_customers:
        current_app.logger.info("cleanup_system_customer: %s — no system customer", shop_name)
        return summary

    customer_ids = [c["_id"] for c in system_customers]
    candidates = list(
        shop_db.units.find(
            {"shop_id": shop_id, "customer_id": {"$in": customer_ids}},
            _UNIT_FIELDS,
        )
    )
    summary["candidates"] = len(candidates)

    refs = find_unit_references(shop_db, shop_id, [u["_id"] for u in candidates])
    to_delete: list[dict] = []
    for unit in candidates:
        referenced_by = refs.get(unit["_id"])
        if referenced_by:
            summary["kept_referenced"] += 1
            for coll_name in referenced_by:
                by = summary["kept_referenced_by"]
                by[coll_name] = by.get(coll_name, 0) + 1
            continue
        if _is_recent(unit, grace_since):
            summary["skipped_recent"] += 1
            continue
        to_delete.append(unit)

    summary["deleted"] = len(to_delete)
    summary["deleted_units"] = [_unit_summary(u) for u in to_delete]
    ids = [u["_id"] for u in to_delete]

    if dry_run:
        for coll_name, field in DERIVED_COLLECTIONS:
            summary[f"{coll_name}_deleted"] = (
                shop_db[coll_name].count_documents({"shop_id": shop_id, field: {"$in": _id_variants(ids)}})
                if ids else 0
            )
        current_app.logger.info(
            "cleanup_system_customer (dry run): %s — would delete %d of %d unit(s) "
            "(kept_referenced=%d, skipped_recent=%d): %s",
            shop_name, summary["deleted"], summary["candidates"],
            summary["kept_referenced"], summary["skipped_recent"],
            [u["id"] for u in summary["deleted_units"]],
        )
        return summary

    if ids:
        # Удаление строго по списку _id (никаких широких фильтров); shop_id —
        # второй предохранитель. Производные записи уходят вместе с юнитом.

        def _tx(session):
            # Повторная проверка ссылок уже внутри транзакции: между первым
            # проходом и удалением механик мог создать WO, офис — запись
            # календаря. Такой юнит выпадает из удаления. Коллбэк
            # идемпотентен — run_atomically может повторить его при
            # транзиентной ошибке: всё считается заново от ids, побочных
            # эффектов вне Mongo нет.
            late = _live_references(shop_db, shop_id, ids, session)
            survivors = [i for i in ids if i not in late]
            derived: dict[str, int] = {}
            if not survivors:
                return {"deleted": 0, "late": late, "derived": derived}
            for coll_name, field in DERIVED_COLLECTIONS:
                res = shop_db[coll_name].delete_many(
                    {"shop_id": shop_id, field: {"$in": _id_variants(survivors)}}, session=session,
                )
                derived[coll_name] = res.deleted_count
            res = shop_db.units.delete_many(
                {"_id": {"$in": survivors}, "shop_id": shop_id}, session=session,
            )
            return {"deleted": res.deleted_count, "late": late, "derived": derived}

        result = run_atomically(shop_db.client, _tx)
        late_ids = {str(i) for i in result["late"]}
        if late_ids:
            summary["kept_late_referenced"] = len(late_ids)
            summary["deleted_units"] = [u for u in summary["deleted_units"] if u["id"] not in late_ids]
        summary["deleted"] = result["deleted"]
        for coll_name, _field in DERIVED_COLLECTIONS:
            summary[f"{coll_name}_deleted"] = result["derived"].get(coll_name, 0)

    if summary["deleted"]:
        primary = system_customers[0]
        # Журнал хранит details через audit_log._plain: списки он режет до
        # 40 элементов, списки словарей схлопывает, а строки пропускает как
        # есть — поэтому полный список id и описания юнитов храним строками
        # (", " между id, "; " между юнитами).
        details = {
            "deleted_unit_ids": ", ".join(u["id"] for u in summary["deleted_units"]),
            "deleted_units": "; ".join(_unit_line(u) for u in summary["deleted_units"]),
            "candidates": summary["candidates"],
            "kept_referenced": summary["kept_referenced"],
            "kept_referenced_by": summary["kept_referenced_by"],
            "kept_late_referenced": summary["kept_late_referenced"],
            "skipped_recent": summary["skipped_recent"],
            "recall_notifications_deleted": summary["recall_notifications_deleted"],
            "grace_hours": float(grace_hours or 0),
        }
        if len(system_customers) > 1:
            details["system_customer_ids"] = ", ".join(str(c["_id"]) for c in system_customers)
        audit_log.log_event(
            "delete", "customer", primary["_id"],
            label=SYSTEM_CUSTOMER_LABEL,
            summary=(
                f"Nightly cleanup: removed {summary['deleted']} stray unit(s) "
                f"without work orders from {SYSTEM_CUSTOMER_LABEL}"
            ),
            details=details,
            shop=shop,
            tenant_id=shop.get("tenant_id"),
            user=CLEANUP_ACTOR,
            source="system",
        )

    current_app.logger.info(
        "cleanup_system_customer: %s — deleted %d of %d unit(s) "
        "(kept_referenced=%d %s, skipped_recent=%d, recall_notifications_deleted=%d): %s",
        shop_name, summary["deleted"], summary["candidates"],
        summary["kept_referenced"], summary["kept_referenced_by"], summary["skipped_recent"],
        summary["recall_notifications_deleted"], [u["id"] for u in summary["deleted_units"]],
    )
    return summary
