"""
Списки work orders / estimates: сборка запроса (поиск, фильтры дат, сортировка),
пагинация и подготовка строк для таблиц.

Единственный сервисный модуль, читающий flask.request (query-параметры
сортировки) — функции по природе request-scoped.
"""
from __future__ import annotations

from bson import ObjectId
from flask import request

from app.utils.entity_search import search_customer_ids, search_unit_ids
from app.utils.mongo_search import build_regex_search_filter
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.blueprints.work_orders.services.common import (
    append_and_filter,
    build_created_at_range_filter,
    build_preferred_date_range_filter,
    f64,
    format_dt_label,
    format_preferred_date_label,
    get_date_range_filters,
    oid,
    round2,
)
from app.blueprints.work_orders.services.lookups import customer_label, unit_label
from app.blueprints.work_orders.services.payments import _sum_active_work_order_payments


def get_work_orders_totals(shop_db, query: dict):
    pipeline = [
        {"$match": query},
        {
            "$lookup": {
                "from": "work_order_payments",
                "let": {"wid": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$work_order_id", "$$wid"]},
                                    {"$eq": ["$is_active", True]},
                                ]
                            }
                        }
                    },
                    {"$group": {"_id": None, "paid": {"$sum": {"$ifNull": ["$amount", 0]}}}},
                ],
                "as": "_pay",
            }
        },
        {
            "$addFields": {
                "_grand": {"$ifNull": ["$totals.grand_total", {"$ifNull": ["$grand_total", 0]}]},
                "_labor": {"$ifNull": ["$totals.labor_total", {"$ifNull": ["$labor_total", 0]}]},
                "_parts": {"$ifNull": ["$totals.parts_total", {"$ifNull": ["$parts_total", 0]}]},
                "_tax": {"$ifNull": ["$totals.sales_tax_total", {"$ifNull": ["$sales_tax_total", 0]}]},
                "_paid": {"$ifNull": [{"$arrayElemAt": ["$_pay.paid", 0]}, 0]},
            }
        },
        {
            "$addFields": {
                # Per-WO floor: unpaid never goes below 0 even if a WO is overpaid.
                "_unpaid": {"$max": [0, {"$subtract": ["$_grand", "$_paid"]}]},
                # Cap paid at grand total to keep paid + unpaid == grand.
                "_paidc": {"$min": ["$_grand", "$_paid"]},
            }
        },
        {
            "$group": {
                "_id": None,
                "labor_total": {"$sum": "$_labor"},
                "parts_total": {"$sum": "$_parts"},
                "sales_tax_total": {"$sum": "$_tax"},
                "grand_total": {"$sum": "$_grand"},
                "paid_total": {"$sum": "$_paidc"},
                "unpaid_total": {"$sum": "$_unpaid"},
            }
        },
    ]

    rows = list(shop_db.work_orders.aggregate(pipeline))
    if not rows:
        return {
            "labor_total": 0.0,
            "parts_total": 0.0,
            "sales_tax_total": 0.0,
            "grand_total": 0.0,
            "paid_total": 0.0,
            "unpaid_total": 0.0,
        }

    row = rows[0] if isinstance(rows[0], dict) else {}
    return {
        "labor_total": round2(row.get("labor_total") or 0),
        "parts_total": round2(row.get("parts_total") or 0),
        "sales_tax_total": round2(row.get("sales_tax_total") or 0),
        "grand_total": round2(row.get("grand_total") or 0),
        "paid_total": round2(row.get("paid_total") or 0),
        "unpaid_total": round2(row.get("unpaid_total") or 0),
    }


# WO «взят механиком в работу и не отмечен done» — условие группы In Work.
IN_WORK_FILTER = {"status": "in_progress", "mechanic_done": {"$ne": True}}


def _build_work_orders_query(
    shop_db,
    shop_id: ObjectId,
    q: str = "",
    paid_status: str = "all",
    created_from=None,
    created_to_exclusive=None,
    customer_id: ObjectId | None = None,
    unit_id: ObjectId | None = None,
) -> dict:
    query = {"shop_id": shop_id, "is_active": True}
    if customer_id:
        query["customer_id"] = customer_id
    if unit_id:
        query["unit_id"] = unit_id

    if paid_status == "paid":
        query["status"] = "paid"
    elif paid_status == "unpaid":
        query["status"] = {"$ne": "paid"}
    elif paid_status == "in_progress":
        query["status"] = "in_progress"

    search_filter = build_regex_search_filter(
        q,
        text_fields=["status"],
        numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
        object_id_fields=["_id", "customer_id", "unit_id", "shop_id", "tenant_id"],
    )

    if q:
        customer_ids = search_customer_ids(shop_db.customers, q)
        unit_ids = search_unit_ids(shop_db.units, q)

        extra = []
        if customer_ids:
            extra.append({"customer_id": {"$in": customer_ids}})
        if unit_ids:
            extra.append({"unit_id": {"$in": unit_ids}})

        if search_filter and extra:
            query = {"$and": [query, {"$or": [search_filter, *extra]}]}
        elif search_filter:
            query = {"$and": [query, search_filter]}
        elif extra:
            query = {"$and": [query, {"$or": extra}]}
    elif search_filter:
        query = {"$and": [query, search_filter]}

    created_at_filter = build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
    if created_at_filter:
        query = append_and_filter(query, created_at_filter)

    return query


def get_work_orders_list(
    shop_db,
    shop_id: ObjectId,
    page: int,
    per_page: int,
    q: str = "",
    paid_status: str = "all",
    created_from=None,
    created_to_exclusive=None,
    customer_id: ObjectId | None = None,
    unit_id: ObjectId | None = None,
    exclude_in_work: bool = False,
):
    query = _build_work_orders_query(
        shop_db,
        shop_id,
        q=q,
        paid_status=paid_status,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
        customer_id=customer_id,
        unit_id=unit_id,
    )

    # Тоталы считаем ДО исключения in-work строк: сводка за период должна
    # включать и те WO, что вынесены в закреплённую группу сверху.
    totals_summary = get_work_orders_totals(shop_db, query)

    if exclude_in_work:
        query = {"$and": [query, {"$nor": [IN_WORK_FILTER]}]}

    sort_params = get_sort_params(
        request.args,
        [("work_order_date", -1), ("created_at", -1)],
        ["wo_number", "status", "work_order_date", "grand_total", "created_at", "customer"],
    )

    if sort_params and sort_params[0][0] == "customer":
        rows, pagination = _paginate_by_customer(
            shop_db,
            shop_db.work_orders,
            query,
            page,
            per_page,
            sort_params[0][1],
        )
    else:
        rows, pagination = paginate_find(
            shop_db.work_orders,
            query,
            sort_params,
            page,
            per_page,
        )

    items = _build_work_order_items(shop_db, shop_id, rows)

    return items, pagination, totals_summary


def get_in_work_orders(
    shop_db,
    shop_id: ObjectId,
    q: str = "",
    paid_status: str = "all",
    created_from=None,
    created_to_exclusive=None,
):
    """
    WO, которые механики взяли в работу и ещё не отметили «done» — верхняя
    выделенная группа списка. Уважает те же фильтры (поиск/даты/статус),
    что и основной список; без пагинации — таких WO единицы.
    """
    base_query = _build_work_orders_query(
        shop_db,
        shop_id,
        q=q,
        paid_status=paid_status,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
    )
    rows = list(
        shop_db.work_orders.find({"$and": [base_query, IN_WORK_FILTER]})
        .sort([("work_order_date", -1), ("created_at", -1)])
    )
    return _build_work_order_items(shop_db, shop_id, rows)


def _wo_mechanic_names(wo_doc: dict) -> list[str]:
    """Механики WO — из assigned_mechanics всех строк (без дублей)."""
    names: list[str] = []
    for block in wo_doc.get("labors") or []:
        if not isinstance(block, dict):
            continue
        labor = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        assigned = labor.get("assigned_mechanics") or block.get("assigned_mechanics") or []
        for a in assigned:
            if not isinstance(a, dict):
                continue
            nm = str(a.get("name") or "").strip()
            if nm and nm not in names:
                names.append(nm)
    return names


def _build_work_order_items(shop_db, shop_id: ObjectId, rows: list) -> list:
    """Строки таблицы WO из raw-документов: лейблы, суммы, оплаты, таймеры."""
    customer_ids = [x.get("customer_id") for x in rows if x.get("customer_id")]
    unit_ids = [x.get("unit_id") for x in rows if x.get("unit_id")]
    wo_ids = [x.get("_id") for x in rows if x.get("_id")]

    customers_map = {}
    inactive_customer_ids: set = set()
    if customer_ids:
        for c in shop_db.customers.find({"_id": {"$in": customer_ids}}):
            customers_map[c.get("_id")] = customer_label(c)
            if c.get("is_active") is False:
                inactive_customer_ids.add(c.get("_id"))

    units_map = {}
    units_mileage_map = {}
    if unit_ids:
        for u in shop_db.units.find({"_id": {"$in": unit_ids}}):
            units_map[u.get("_id")] = unit_label(u)
            units_mileage_map[u.get("_id")] = u.get("mileage")

    paid_map: dict = {}
    if wo_ids:
        pay_pipeline = [
            {"$match": {"work_order_id": {"$in": wo_ids}, "is_active": True}},
            {"$group": {"_id": "$work_order_id", "paid": {"$sum": {"$ifNull": ["$amount", 0]}}}},
        ]
        for r in shop_db.work_order_payments.aggregate(pay_pipeline):
            paid_map[r.get("_id")] = float(r.get("paid") or 0)

    # Кто прямо сейчас работает над WO (открытые таймеры механиков).
    working_map: dict = {}
    if wo_ids:
        open_logs = shop_db.wo_time_logs.find(
            {"shop_id": shop_id, "work_order_id": {"$in": wo_ids}, "stopped_at": None},
            {"work_order_id": 1, "user_name": 1},
        )
        for log in open_logs:
            name = str(log.get("user_name") or "").strip() or "Mechanic"
            names = working_map.setdefault(log.get("work_order_id"), [])
            if name not in names:
                names.append(name)

    items = []
    for x in rows:
        totals = x.get("totals") if isinstance(x.get("totals"), dict) else {}

        labor_total = round2(totals.get("labor_total") if totals.get("labor_total") is not None else x.get("labor_total"))
        parts_total = round2(totals.get("parts_total") if totals.get("parts_total") is not None else x.get("parts_total"))
        sales_tax_total = round2(totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else x.get("sales_tax_total"))
        grand_total = round2(totals.get("grand_total") if totals.get("grand_total") is not None else x.get("grand_total"))

        status = (x.get("status") or "open").strip().lower()
        paid_amount = round2(paid_map.get(x.get("_id"), 0))
        balance = round2(max(0.0, grand_total - paid_amount))

        items.append(
            {
                "id": str(x.get("_id")),
                "wo_number": x.get("wo_number"),
                "customer": customers_map.get(x.get("customer_id")) or "-",
                "customer_inactive": x.get("customer_id") in inactive_customer_ids,
                "date": format_preferred_date_label(x.get("work_order_date"), x.get("created_at")),
                "unit": units_map.get(x.get("unit_id")) or "-",
                "mileage": x.get("mileage") or units_mileage_map.get(x.get("unit_id")),
                "labor_total": labor_total,
                "parts_total": parts_total,
                "sales_tax_total": sales_tax_total,
                "grand_total": grand_total,
                "paid_amount": paid_amount,
                "balance": balance,
                "is_paid": status == "paid",
                "status": status,
                "is_in_progress": status == "in_progress",
                "working_now": working_map.get(x.get("_id")) or [],
                "mechanics": _wo_mechanic_names(x),
                # Флаг «механик закончил» имеет смысл только пока WO в работе.
                "mechanic_done": bool(x.get("mechanic_done")) and status == "in_progress",
                "manager_confirmed": bool(x.get("manager_confirmed")),
            }
        )

    return items


def get_estimates_list(
    shop_db,
    shop_id: ObjectId,
    page: int,
    per_page: int,
    q: str = "",
    created_from=None,
    created_to_exclusive=None,
):
    estimate_statuses = ["estimate", "estimated", "quote", "quoted"]
    query = {"shop_id": shop_id, "status": {"$in": estimate_statuses}, "is_active": True}

    search_filter = build_regex_search_filter(
        q,
        text_fields=["status"],
        numeric_fields=["wo_number", "grand_total", "totals.grand_total", "totals.parts_total", "totals.labor_total"],
        object_id_fields=["_id", "customer_id", "unit_id", "shop_id", "tenant_id"],
    )

    if q:
        customer_ids = search_customer_ids(shop_db.customers, q)
        unit_ids = search_unit_ids(shop_db.units, q)

        extra = []
        if customer_ids:
            extra.append({"customer_id": {"$in": customer_ids}})
        if unit_ids:
            extra.append({"unit_id": {"$in": unit_ids}})

        if search_filter and extra:
            query = {"$and": [query, {"$or": [search_filter, *extra]}]}
        elif search_filter:
            query = {"$and": [query, search_filter]}
        elif extra:
            query = {"$and": [query, {"$or": extra}]}
    elif search_filter:
        query = {"$and": [query, search_filter]}

    created_at_filter = build_preferred_date_range_filter("work_order_date", created_from, created_to_exclusive)
    if created_at_filter:
        query = append_and_filter(query, created_at_filter)

    sort_params = get_sort_params(
        request.args,
        [("work_order_date", -1), ("created_at", -1)],
        ["wo_number", "status", "work_order_date", "grand_total", "created_at", "customer"],
    )

    if sort_params and sort_params[0][0] == "customer":
        rows, pagination = _paginate_by_customer(
            shop_db,
            shop_db.work_orders,
            query,
            page,
            per_page,
            sort_params[0][1],
        )
    else:
        rows, pagination = paginate_find(
            shop_db.work_orders,
            query,
            sort_params,
            page,
            per_page,
        )

    customer_ids = [x.get("customer_id") for x in rows if x.get("customer_id")]
    unit_ids = [x.get("unit_id") for x in rows if x.get("unit_id")]

    customers_map = {}
    inactive_customer_ids: set = set()
    if customer_ids:
        for c in shop_db.customers.find({"_id": {"$in": customer_ids}}):
            customers_map[c.get("_id")] = customer_label(c)
            if c.get("is_active") is False:
                inactive_customer_ids.add(c.get("_id"))

    units_map = {}
    units_mileage_map = {}
    if unit_ids:
        for u in shop_db.units.find({"_id": {"$in": unit_ids}}):
            units_map[u.get("_id")] = unit_label(u)
            units_mileage_map[u.get("_id")] = u.get("mileage")

    items = []
    for x in rows:
        totals = x.get("totals") if isinstance(x.get("totals"), dict) else {}

        labor_total = round2(totals.get("labor_total") if totals.get("labor_total") is not None else x.get("labor_total"))
        parts_total = round2(totals.get("parts_total") if totals.get("parts_total") is not None else x.get("parts_total"))
        sales_tax_total = round2(totals.get("sales_tax_total") if totals.get("sales_tax_total") is not None else x.get("sales_tax_total"))
        grand_total = round2(totals.get("grand_total") if totals.get("grand_total") is not None else x.get("grand_total"))

        status = (x.get("status") or "estimate").strip().lower()

        items.append(
            {
                "id": str(x.get("_id")),
                "wo_number": x.get("wo_number"),
                "customer": customers_map.get(x.get("customer_id")) or "-",
                "customer_inactive": x.get("customer_id") in inactive_customer_ids,
                "date": format_preferred_date_label(x.get("work_order_date"), x.get("created_at")),
                "unit": units_map.get(x.get("unit_id")) or "-",
                "mileage": x.get("mileage"),
                "labor_total": labor_total,
                "parts_total": parts_total,
                "sales_tax_total": sales_tax_total,
                "grand_total": grand_total,
                "status": status,
            }
        )

    return items, pagination


def _paginate_by_customer(
    shop_db,
    collection,
    base_query: dict,
    page: int,
    per_page: int,
    direction: int,
    *,
    customer_id_resolver=None,
    indirect_lookup=None,
):
    """Paginate a collection by customer label (case-insensitive).

    - customer_id_resolver(doc) -> ObjectId|None. If None, defaults to doc["customer_id"].
    - indirect_lookup: optional tuple (target_collection, link_field_on_doc, customer_field_on_target).
      When provided, the lite docs include only _id and link_field_on_doc; we look up the
      target_collection to obtain customer_id (e.g. payments -> work_orders.customer_id).
    """
    import math as _math

    if indirect_lookup is not None:
        target_collection, link_field, target_customer_field = indirect_lookup
        projection = {"_id": 1, link_field: 1}
    else:
        projection = {"_id": 1, "customer_id": 1}

    lite_docs = list(collection.find(base_query, projection))

    if indirect_lookup is not None:
        link_ids = list({d.get(link_field) for d in lite_docs if d.get(link_field)})
        link_map = {}
        if link_ids:
            for t in target_collection.find(
                {"_id": {"$in": link_ids}}, {"_id": 1, target_customer_field: 1}
            ):
                link_map[t.get("_id")] = t.get(target_customer_field)
        get_customer_id = lambda d: link_map.get(d.get(link_field))
    elif customer_id_resolver is not None:
        get_customer_id = customer_id_resolver
    else:
        get_customer_id = lambda d: d.get("customer_id")

    cust_ids = list({get_customer_id(d) for d in lite_docs if get_customer_id(d)})
    label_map: dict = {}
    if cust_ids:
        for c in shop_db.customers.find(
            {"_id": {"$in": cust_ids}},
            {"company_name": 1, "first_name": 1, "last_name": 1, "contacts": 1},
        ):
            label_map[c.get("_id")] = (customer_label(c) or "").strip().lower()

    reverse = direction == -1
    lite_docs.sort(
        key=lambda d: (label_map.get(get_customer_id(d), ""), str(d.get("_id"))),
        reverse=reverse,
    )

    total = len(lite_docs)
    pages = max(1, _math.ceil(total / per_page)) if total else 1
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    page_lite = lite_docs[start:start + per_page]
    page_ids = [d["_id"] for d in page_lite]

    docs_map: dict = {}
    if page_ids:
        for d in collection.find({"_id": {"$in": page_ids}}):
            docs_map[d.get("_id")] = d
    rows = [docs_map[_id] for _id in page_ids if _id in docs_map]

    meta = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
        "prev_page": page - 1 if page > 1 else 1,
        "next_page": page + 1 if page < pages else pages,
    }
    return rows, meta
