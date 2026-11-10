from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from flask import current_app, g, request, session, redirect, url_for, flash, jsonify, render_template, make_response

from app.utils import audit_log
from app.blueprints.work_orders import work_orders_bp
from app.blueprints.main.routes import _render_app_page
from app.extensions import get_master_db, get_mongo_client, csrf
from app.utils.auth import login_required, SESSION_TENANT_ID, SESSION_USER_ID
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.parts_search import build_query_tokens, part_matches_query
from app.utils.parts_interchange import attach_alternates
from app.utils.entity_search import build_unit_search_terms, search_customer_ids, search_unit_ids
from app.utils.duplicates import find_duplicate_unit, unit_duplicate_message
from app.utils.mongo_tx import run_atomically
from app.blueprints.work_orders.services.authorizations import (
    _authorizations_collection,
    _build_authorization_context,
    _load_authorization_attachments,
    _resolve_token,
)
from app.blueprints.work_orders.services.pdf_contexts import (
    _build_annual_inspection_pdf_context,
    _build_wo_pdf_context,
)
from app.blueprints.work_orders.services.inspections import (
    VEHICLE_TYPE_COMPONENT_DEFAULTS,
    annual_inspection_checklist,
)
from app.blueprints.work_orders.services.listing import (
    _paginate_by_customer,
    get_estimates_list,
    get_in_work_orders,
    get_work_orders_list,
    get_work_orders_totals,
)
from app.blueprints.work_orders.services.lookups import (
    _tenant_variants_from_shop,
    customer_label,
    get_assignable_mechanics,
    get_core_charge_default,
    get_customers,
    get_labor_rates,
    get_pricing_rules_json,
    get_shop_supply_percentage,
    get_units,
    normalize_assigned_mechanics,
    unit_label,
)
from app.blueprints.work_orders.services.inventory import (
    _collect_inventory_qty_by_part,
    _resolve_part_for_core_tracking,
    _resolve_part_for_inventory,
    apply_core_delta,
    build_core_delta,
    collect_unpaid_core_requirements,
    deduct_parts_from_inventory,
    restore_parts_to_inventory,
    sync_work_order_cores,
)
from app.blueprints.work_orders.services.payments import (
    _build_work_order_payment_summary,
    _sum_active_work_order_payments,
    _sync_work_order_payment_state,
)
from app.blueprints.work_orders.services.bulk_payments import (
    apply_bulk_payment,
    get_bulk_payment_customers,
    get_unpaid_work_orders_for_customer,
)
from app.blueprints.work_orders.services.totals import (
    _apply_sales_tax_to_totals,
    _calc_misc_total_from_parts,
    _get_shop_sales_tax_context,
    _is_customer_taxable,
    _parse_misc_items,
    _work_order_grand_total,
    _work_order_tax_total,
    align_totals_with_labors,
    get_next_wo_number,
    normalize_parts_payload,
    normalize_saved_labors,
    normalize_totals_payload,
)
from app.blueprints.work_orders.services.common import (
    _date_range_for_preset,
    _fmt_dt_iso,
    _parse_iso_date_utc,
    _start_of_month,
    _start_of_quarter,
    _start_of_week_monday,
    _start_of_year,
    _to_iso_date,
    append_and_filter,
    as_bool,
    build_created_at_range_filter,
    ensure_labor_id,
    build_preferred_date_range_filter,
    f64,
    format_dt_label,
    format_preferred_date_label,
    get_date_range_filters,
    i32,
    oid,
    part_unit_fields,
    round2,
    utcnow,
)
# Офисные записи WO: merge-сохранение, describe issue, transfer (CAS по labors_rev).
from app.blueprints.work_orders.services import edit_views as ev
from app.blueprints.work_orders.services import office_merge
from app.blueprints.work_orders.services.lookups import locked_shop_supply_pct
from app.blueprints.work_orders.services.mechanic_view import ensure_wo_labor_ids
from app.blueprints.work_orders.services.transfer import transfer_work_order
from app.utils.permissions import (
    enforce_mechanic_status,
    has_permission,
    is_mechanic_mode,
    permission_required,
    permission_required_any,
)
from app.utils.display_datetime import (
    format_date_mmddyyyy,
    format_preferred_shop_date,
    get_active_shop_today_iso,
    shop_date_input_value,
    shop_local_date_to_utc,
)
from app.utils.date_filters import build_date_range_filters
from app.utils.contacts import get_contacts, get_main_contact_email, get_main_contact_name, get_main_contact_phone, normalize_contacts
from app.utils.email_sender import send_email
from app.utils.pdf_utils import render_html_to_pdf
from app.utils.sales_tax import get_shop_zip_code, get_zip_sales_tax_rate
from app.utils.issue_describer import polish_issue_description


















































































def current_user_id():
    return oid(session.get(SESSION_USER_ID))


from app.utils.tenant import tenant_id_variants, get_shop_db as _tenant_get_shop_db


def get_shop_db():
    return _tenant_get_shop_db(get_master_db())


def _sync_calendar_with_wo(shop_db, shop, work_order_id, *, identity=False):
    """
    Записи календаря, привязанные к WO, следуют за ним: статус — по факту
    работы (всегда), клиент/юнит — когда их поменяли на WO (identity).
    Бригаду записей за назначениями строк календарь не двигает и сам в WO
    не пишет. Зовётся после записи WO и вне транзакций (у синка нет
    session); ошибки логируются внутри сервиса, наружу не идут.
    """
    from app.blueprints.calendar.services.work_orders import (
        refresh_linked_appointments_identity,
        sync_linked_appointments_status,
    )

    if identity:
        refresh_linked_appointments_identity(shop_db, shop, work_order_id)
    sync_linked_appointments_status(shop_db, shop, work_order_id)




























def render_details(shop_db, shop, customer_id, unit_id, form_state=None):
    from bson import ObjectId as _ObjId
    mechanics = get_assignable_mechanics(shop)

    # Only load the selected customer for initial render; full list loaded via JS.
    # Без фильтра is_active: WO деактивированного клиента должен открываться,
    # клиент помечается "(inactive)" в лейбле.
    selected_customer = None
    if customer_id:
        c = shop_db.customers.find_one({"_id": customer_id})
        if c:
            rate_rows = list(shop_db.labor_rates.find({"is_active": True}, {"_id": 1, "code": 1}))
            rates_by_id = {r.get("_id"): str(r.get("code") or "").strip() for r in rate_rows if r.get("_id")}
            def _resolve_rate(value):
                if isinstance(value, ObjectId):
                    return rates_by_id.get(value, "")
                legacy = str(value or "").strip().lower()
                if legacy == "standart":
                    return "standard"
                return legacy
            _inactive = c.get("is_active") is False
            selected_customer = {
                "id": str(c["_id"]),
                "label": customer_label(c) + (" (inactive)" if _inactive else ""),
                "is_active": not _inactive,
                "default_labor_rate": _resolve_rate(c.get("default_labor_rate")),
                "taxable": bool(c.get("taxable", False)),
                "company_name": (c.get("company_name") or "").strip(),
                "contact_name": get_main_contact_name(c, entity_type="customer"),
                "phone": get_main_contact_phone(c, entity_type="customer"),
                "email": get_main_contact_email(c, entity_type="customer"),
                "address": (c.get("address") or "").strip(),
            }

    customers = [selected_customer] if selected_customer else []

    units = []
    if customer_id:
        units = get_units(shop_db, customer_id)
        if unit_id and not any(u["id"] == str(unit_id) for u in units):
            unit_id = None

    # Generate a temporary ObjectId for attachment uploads before WO is created
    pending_attachment_id = str(_ObjId()) if not (form_state or {}).get("work_order_created") else ""

    # If we are opening an existing WO, lock the displayed tax rate to the value
    # that was saved on the WO itself (so address changes on the shop don't
    # silently re-quote tax for old work orders).
    sales_tax_context = _get_shop_sales_tax_context(shop, shop_db)
    initial_totals_state = (form_state or {}).get("initial_totals") or {}
    locked_rate = initial_totals_state.get("sales_tax_rate") if isinstance(initial_totals_state, dict) else None
    if (form_state or {}).get("work_order_created") and locked_rate is not None:
        # Existing WO: rate is locked, so suppress the "state-only estimate"
        # warning even if the current shop config is api-ninjas based.
        sales_tax_context = {
            **sales_tax_context,
            "rate": float(locked_rate or 0),
            "is_state_only_estimate": False,
            "source": "locked",
        }

    # Lock shop-supply percentage to whatever was applied when this WO was
    # saved, so editing an existing WO doesn't silently re-rate it with the
    # current shop configuration (тот же процент лежит в load-вьюхе офиса —
    # по нему сервер пересчитывает supply, если в Save влились чужие правки).
    if (form_state or {}).get("work_order_created"):
        shop_supply_pct = (form_state or {}).get("shop_supply_pct")
        if shop_supply_pct is None:
            shop_supply_pct = locked_shop_supply_pct(shop_db, shop["_id"], initial_totals_state)
    else:
        shop_supply_pct = get_shop_supply_percentage(shop_db, shop["_id"])

    # Справочники для общей модалки парт-ордера (components/parts_order_modal):
    # те же, что на странице Parts. Только для ролей с ценами — механик модалку
    # не видит вовсе.
    po_vendors, po_categories, po_locations = [], [], []
    if has_permission("work_orders.view_costs"):
        from app.utils.parts_locations import flatten_location_tree

        po_vendors = list(
            shop_db.vendors.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("company_name", 1), ("created_at", -1)])
        )
        po_categories = list(
            shop_db.parts_categories.find({"is_active": {"$ne": False}})
            .sort([("name", 1), ("created_at", -1)])
        )
        po_locations = [
            {"_id": item["oid"], "name": item["path"], "depth": item["depth"]}
            for item in flatten_location_tree(
                list(shop_db.parts_locations.find({"is_active": {"$ne": False}}))
            )
        ]

    # Записи календаря, привязанные к открытому WO (план: когда, кто,
    # пресеты, заметка) — для блока на странице деталей.
    linked_appointments = []
    existing_wo_id = oid((form_state or {}).get("created_work_order_id"))
    if existing_wo_id:
        from app.blueprints.calendar.services.work_orders import linked_appointments_for_wo

        linked_appointments = linked_appointments_for_wo(shop_db, shop, existing_wo_id)

    # Полная модалка парта (components/part_modal.html внутри модалки
    # заказа): селект единиц и видимость себестоимости — как на Parts.
    from app.constants.part_units import part_unit_options

    ctx = {
        "sales_tax_context": sales_tax_context,
        "active_page": "work_orders",
        "vendors": po_vendors,
        "categories": po_categories,
        "locations": po_locations,
        "part_units": part_unit_options(),
        "can_view_part_costs": has_permission("parts.view_costs"),
        "customers": customers,
        "units": units,
        "selected_customer_id": str(customer_id) if customer_id else "",
        "selected_unit_id": str(unit_id) if unit_id else "",
        "labor_rates": get_labor_rates(shop_db, shop["_id"]),
        "mechanics": mechanics,
        "parts_pricing_rules": get_pricing_rules_json(shop_db, shop["_id"], customer_id=customer_id),
        "shop_supply_procentage": shop_supply_pct,
        "charge_for_cores_default": get_core_charge_default(shop_db, shop["_id"]),

        # старые поля (оставляем как у тебя было)
        "labor_description": (form_state or {}).get("labor_description") or "",
        "labor_hours": (form_state or {}).get("labor_hours") or "",
        "labor_rate_code": (form_state or {}).get("labor_rate_code") or "",

        # NEW: флаг, чтобы после create UI стал неактивным
        "work_order_created": bool((form_state or {}).get("work_order_created")),
        "created_work_order_id": (form_state or {}).get("created_work_order_id") or "",
        "wo_number": (form_state or {}).get("wo_number"),
        "work_order_date": (form_state or {}).get("work_order_date") or get_active_shop_today_iso(),
        "today_date_input_value": get_active_shop_today_iso(),
        # Пробег, записанный на самом WO (снимок на момент работы); пусто —
        # фронт подставит текущий пробег юнита (старые WO без снимка).
        "wo_mileage": "" if (form_state or {}).get("mileage") is None else (form_state or {}).get("mileage"),

        "initial_labors": (form_state or {}).get("initial_labors") or [],
        "time_summary": (form_state or {}).get("time_summary") or {},
        "initial_totals": normalize_totals_payload((form_state or {}).get("initial_totals") or {}),
        "work_order_status": (form_state or {}).get("work_order_status") or "open",
        "mechanic_done": bool((form_state or {}).get("mechanic_done")),
        "wo_updated_at": (form_state or {}).get("wo_updated_at") or "",
        # Edit view этой вкладки (база трёхстороннего merge её Save) и
        # ревизия, с которой она начала — JS шлёт view_id обратно.
        "edit_view_id": (form_state or {}).get("view_id") or "",
        "labors_rev": (form_state or {}).get("labors_rev") or 0,
        "pending_attachment_id": pending_attachment_id,
        "calendar_event_id": (form_state or {}).get("calendar_event_id") or "",
        "linked_appointments": linked_appointments,
        "initial_authorizations": (form_state or {}).get("authorizations") or [],
        "avi_checklist": annual_inspection_checklist(),
        "avi_type_defaults": VEHICLE_TYPE_COMPONENT_DEFAULTS,
    }

    return _render_app_page("public/work_orders/work_order_details.html", **ctx)


# -------------------- INVENTORY MANAGEMENT --------------------

















@work_orders_bp.get("/work_orders")
@login_required
@permission_required("work_orders.view")
def work_orders_page():
    if is_mechanic_mode():
        return redirect(url_for("mechanic.work_orders_list"))

    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    q = (request.args.get("q") or "").strip()
    paid_status = (request.args.get("paid_status") or "all").strip().lower()
    if paid_status not in ("all", "paid", "unpaid", "in_progress"):
        paid_status = "all"

    date_filters = get_date_range_filters(request.args)
    date_from = date_filters["date_from"]
    date_to = date_filters["date_to"]
    date_preset = date_filters["date_preset"]
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)
    # In-work WO (механик взял, done не нажал) выносятся в выделенную группу
    # сверху таблицы и исключаются из основного списка — без дублей.
    work_orders, pagination, work_orders_totals = get_work_orders_list(
        shop_db,
        shop["_id"],
        page,
        per_page,
        q=q,
        paid_status=paid_status,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
        exclude_in_work=True,
    )
    # Группа In Work — без фильтра дат: текущая работа цеха видна всегда.
    in_work_orders = get_in_work_orders(
        shop_db,
        shop["_id"],
        q=q,
        paid_status=paid_status,
    )

    return _render_app_page(
        "public/work_orders/work_orders.html",
        active_page="work_orders",
        work_orders=work_orders,
        in_work_orders=in_work_orders,
        pagination=pagination,
        work_orders_totals=work_orders_totals,
        q=q,
        paid_status=paid_status,
        date_from=date_from,
        date_to=date_to,
        date_preset=date_preset,
        today_date_input_value=get_active_shop_today_iso(),
        sort_by=(request.args.get("sort_by") or "").strip(),
        sort_dir=(request.args.get("sort_dir") or "").strip(),
    )


@work_orders_bp.get("/work_orders/api/estimates")
@login_required
@permission_required("work_orders.view")
def estimates_api():
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured."}), 400

    q = (request.args.get("q") or "").strip()
    date_filters = get_date_range_filters(request.args)
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    estimates, pagination = get_estimates_list(
        shop_db,
        shop["_id"],
        page,
        per_page,
        q=q,
        created_from=created_from,
        created_to_exclusive=created_to_exclusive,
    )

    return jsonify({
        "ok": True,
        "estimates": estimates,
        "pagination": {
            "page": pagination.page if hasattr(pagination, "page") else pagination.get("page", 1),
            "pages": pagination.pages if hasattr(pagination, "pages") else pagination.get("pages", 1),
            "total": pagination.total if hasattr(pagination, "total") else pagination.get("total", 0),
            "has_prev": pagination.has_prev if hasattr(pagination, "has_prev") else pagination.get("has_prev", False),
            "has_next": pagination.has_next if hasattr(pagination, "has_next") else pagination.get("has_next", False),
            "prev_page": pagination.prev_page if hasattr(pagination, "prev_page") else pagination.get("prev_page", 1),
            "next_page": pagination.next_page if hasattr(pagination, "next_page") else pagination.get("next_page", 1),
            "per_page": pagination.per_page if hasattr(pagination, "per_page") else pagination.get("per_page", 20),
        },
    })


@work_orders_bp.get("/work_orders/api/customers")
@login_required
@permission_required("work_orders.view")
def customers_api():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured."}), 400

    customers = get_customers(shop_db)
    return jsonify({"ok": True, "customers": customers})


@work_orders_bp.get("/work_orders/details")
@login_required
@permission_required("work_orders.view")
def work_order_details_page():
    if is_mechanic_mode():
        wo_id = (request.args.get("work_order_id") or "").strip()
        if wo_id:
            return redirect(url_for("mechanic.work_order_details", work_order_id=wo_id))
        return redirect(url_for("mechanic.work_order_new"))

    # Существующий WO открывается любому с work_orders.view (read-only, если
    # нет edit — кнопки действий прячет шаблон по правам); форма нового WO
    # — только с work_orders.create.
    if not (request.args.get("work_order_id") or "").strip() and not has_permission("work_orders.create"):
        flash("Access denied.", "error")
        return redirect(url_for("work_orders.work_orders_page"))

    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    customer_id = oid(request.args.get("customer_id"))
    unit_id = oid(request.args.get("unit_id"))

    work_order_id = oid(request.args.get("work_order_id"))
    if work_order_id:
        wo = shop_db.work_orders.find_one({"_id": work_order_id, "shop_id": shop["_id"], "is_active": True})
        if not wo:
            flash("Work order not found.", "error")
            return redirect(url_for("work_orders.work_orders_page"))

        # Легаси-строки без labor_id получают постоянный id (CAS-запись), и
        # страница, и load-вьюха строятся из документа, который лежит в базе:
        # случайные id на каждый рендер плодили бы дубли строк при Save.
        wo = ensure_wo_labor_ids(shop_db, wo)

        customer_id = wo.get("customer_id")
        unit_id = wo.get("unit_id")
        work_order_status = (wo.get("status") or "open").strip().lower()
        if work_order_status not in ("open", "paid", "estimate"):
            work_order_status = "open"

        from app.blueprints.work_orders.services.time_tracking import _fmt_iso, summarize_wo_time

        time_summary = summarize_wo_time(shop_db, shop["_id"], wo["_id"])
        initial_totals = wo.get("totals") or {
            "labor": wo.get("labor_total") or 0,
            "labor_total": wo.get("labor_total") or 0,
            "parts": wo.get("parts_total") or 0,
            "parts_total": wo.get("parts_total") or 0,
            "core_total": 0,
            "misc_total": 0,
            "shop_supply_total": 0,
            "parts_taxable_total": wo.get("parts_total") or 0,
            "sales_tax_rate": 0,
            "sales_tax_total": wo.get("sales_tax_total") or 0,
            "is_taxable": False,
            "cost_total": wo.get("parts_total") or 0,
            "grand_total": wo.get("grand_total") or 0,
            "labors": [],
        }
        shop_supply_pct = locked_shop_supply_pct(shop_db, shop["_id"], initial_totals)

        # Имена механиков в назначениях строк — живые по user_id: снимок в WO
        # устаревает после переименования пользователя (он — лишь запас для
        # удалённых аккаунтов). На Save не влияет: имена сервер берёт из
        # списка механиков / сохранённой строки, не из формы.
        from app.utils.user_names import load_user_names, relabel_people, tenant_values_for_shop

        initial_labors = normalize_saved_labors(wo.get("labors") or wo.get("blocks") or [], shop_db=shop_db)
        assigned_people = [
            person
            for block in initial_labors
            for person in (block.get("labor") or {}).get("assigned_mechanics") or []
        ]
        relabel_people(assigned_people, load_user_names(
            [person.get("user_id") for person in assigned_people], tenant_values=tenant_values_for_shop(shop),
        ))

        # Load-вьюха — что показала эта вкладка (строки + поля уровня WO):
        # её id JS шлёт в Save, и сервер сливает правки вкладки с чужими.
        # Пишется только тому, кто может сохранить WO (work_orders.edit), —
        # и для оплаченного WO (allow_paid): после Unpaid / удаления платежа
        # страница не перезагружается, и без базы её Save ничего бы не
        # удалял. Пока WO paid, сохранения отклоняют guard'ы и CAS-фильтр.
        view_id = ev.record_view(
            shop_db, shop["_id"], wo,
            user_id=current_user_id(), sid=ev.edit_session_id(), channel=office_merge.CHANNEL,
            kind="load", protocol=1, can_edit=has_permission("work_orders.edit"), allow_paid=True,
            wo_fields=office_merge.office_view_wo_fields(shop_db, shop, wo, supply_pct=shop_supply_pct),
        )

        return render_details(
            shop_db,
            shop,
            customer_id,
            unit_id,
            form_state={
                "work_order_created": True,
                "created_work_order_id": str(wo.get("_id")),
                "wo_number": wo.get("wo_number"),
                "work_order_date": shop_date_input_value(wo.get("work_order_date") or wo.get("created_at"), default_today=True),
                "mileage": wo.get("mileage"),
                "initial_labors": initial_labors,
                "initial_totals": initial_totals,
                "shop_supply_pct": shop_supply_pct,
                "view_id": view_id,
                "labors_rev": ev.labors_rev(wo),
                "work_order_status": work_order_status,
                # Механик нажал done — менеджер открывает WO сразу в режиме
                # правки с кнопкой Confirm вместо Edit→Save.
                "mechanic_done": bool(wo.get("mechanic_done"))
                and (wo.get("status") or "").strip().lower() == "in_progress",
                "authorizations": list(wo.get("authorizations") or []),
                "time_summary": time_summary,
                # Baseline для live-поллинга: страница перезагружается, когда
                # updated_at на сервере уходит вперёд (см. api_work_order_live_state).
                "wo_updated_at": _fmt_iso(wo.get("updated_at")),
            },
        )

    # Открытие из календаря: id записи, которую привяжем к созданному WO.
    calendar_event_id = oid(request.args.get("calendar_event_id"))
    return render_details(
        shop_db, shop, customer_id, unit_id,
        form_state={"calendar_event_id": str(calendar_event_id) if calendar_event_id else ""},
    )


@work_orders_bp.get("/work_orders/api/work_orders/live_signature")
@login_required
@permission_required("work_orders.view")
def api_work_orders_live_signature():
    """Сигнатура изменений для live-поллинга страницы списка WO.

    Только таймстемпы, без данных — клиент сравнивает строку и перечитывает
    страницу при сдвиге (см. services/live_state.work_orders_live_signature).
    """
    from app.blueprints.work_orders.services.live_state import work_orders_live_signature

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({"ok": True, "signature": work_orders_live_signature(shop_db, shop["_id"])}), 200


@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/live_state")
@login_required
@permission_required("work_orders.create")
def api_work_order_live_state(work_order_id):
    """Живое состояние WO для поллинга страницы деталей (services/live_state)."""
    from app.blueprints.work_orders.services.live_state import build_live_state

    # Менеджерский эндпоинт: страница деталей механиков редиректит, а статус
    # paid механику не отдаём нигде — проще не отдавать payload вовсе.
    if is_mechanic_mode():
        return jsonify({"ok": False, "error": "forbidden"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        # labors_rev обязан быть в проекции: без него ревизия читалась бы как 0.
        with_rev({"status": 1, "mechanic_done": 1, "updated_at": 1, "updated_by": 1}),
    )
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    return jsonify({"ok": True, **build_live_state(shop_db, shop, wo, current_user_id())}), 200


@work_orders_bp.post("/work_orders/units/create")
@login_required
@permission_required("work_orders.create")
def create_unit():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    customer_id = oid(request.form.get("customer_id"))
    if not customer_id:
        flash("Customer is required.", "error")
        return redirect(url_for("work_orders.work_order_details_page"))

    customer = shop_db.customers.find_one({"_id": customer_id, "is_active": True})
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("work_orders.work_order_details_page"))

    vin = (request.form.get("vin") or "").strip()
    existing = find_duplicate_unit(shop_db, shop["_id"], customer_id, vin)
    if existing:
        flash(unit_duplicate_message(existing), "error")
        return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id)))

    now = utcnow()
    user_id = current_user_id()

    doc = {
        "customer_id": customer_id,
        "vin": (request.form.get("vin") or "").strip() or None,
        "unit_number": (request.form.get("unit_number") or "").strip() or None,
        "make": (request.form.get("make") or "").strip() or None,
        "model": (request.form.get("model") or "").strip() or None,
        "year": i32(request.form.get("year")),
        "type": (request.form.get("type") or "").strip() or None,
        "mileage": i32(request.form.get("mileage")),
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    doc["search_terms"] = build_unit_search_terms(doc)

    res = shop_db.units.insert_one(doc)
    audit_log.log_event("create", "unit", res.inserted_id, label=audit_log.unit_label(doc),
                        details={"customer_id": str(customer_id), "customer": audit_log.customer_label(customer)})
    unit_id = res.inserted_id

    flash("Unit created.", "success")
    return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id), unit_id=str(unit_id)))


@work_orders_bp.post("/work_orders/create")
@login_required
@permission_required("work_orders.create")
def create_work_order():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        flash("Shop database not configured.", "error")
        return redirect(url_for("dashboard.dashboard"))

    customer_id = oid(request.form.get("customer_id"))
    unit_id = oid(request.form.get("unit_id"))

    if not customer_id:
        flash("Customer is required.", "error")
        return redirect(url_for("work_orders.work_order_details_page"))

    if not unit_id:
        flash("Unit is required.", "error")
        return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id)))

    # ---- parse labors ----
    # inputs come like:
    # labors[0][labor_description], labors[0][labor_hours], labors[0][labor_rate_code]
    # labors[0][parts][0][part_number] ... etc
    import re
    import json

    labors_map: dict[int, dict] = {}

    # labor
    labor_re = re.compile(r"^(?:labors|blocks)\[(\d+)\]\[(labor_id|labor_description|labor_hours|labor_rate_code|labor_total_ui|labor_full_total|assigned_mechanics_json|issue_description)\]$")
    # parts
    parts_re = re.compile(
        r"^(?:labors|blocks)\[(\d+)\]\[parts\]\[(\d+)\]\[(part_id|part_number|description|qty|cost|price|core_charge|misc_charge|misc_charge_description|one_time_part|unit)\]$"
    )

    for key, val in request.form.items():
        m = labor_re.match(key)
        if m:
            bidx = int(m.group(1))
            field = m.group(2)
            b = labors_map.setdefault(bidx, {"labor": {}, "parts": []})

            if field == "labor_id":
                b["labor_id"] = (val or "").strip()
            elif field == "labor_description":
                b["labor"]["description"] = (val or "").strip()
            elif field == "labor_hours":
                b["labor"]["hours"] = (val or "").strip()
            elif field == "labor_rate_code":
                b["labor"]["rate_code"] = (val or "").strip()
            elif field in ("labor_total_ui", "labor_full_total"):
                b["labor"]["labor_full_total"] = round2(val)
            elif field == "assigned_mechanics_json":
                b["labor"]["assigned_mechanics_json"] = (val or "").strip()
            elif field == "issue_description":
                b["labor"]["issue_description"] = (val or "").strip()
            continue

        m = parts_re.match(key)
        if m:
            bidx = int(m.group(1))
            ridx = int(m.group(2))
            field = m.group(3)

            b = labors_map.setdefault(bidx, {"labor": {}, "parts": []})
            while len(b["parts"]) <= ridx:
                b["parts"].append({})

            if field in ("part_id", "part_number", "description", "unit"):
                b["parts"][ridx][field] = (val or "").strip()
            elif field == "qty":
                b["parts"][ridx]["qty"] = (val or "").strip()
            elif field == "cost":
                b["parts"][ridx]["cost"] = (val or "").strip()
            elif field == "price":
                b["parts"][ridx]["price"] = (val or "").strip()
            elif field == "core_charge":
                b["parts"][ridx]["core_charge"] = (val or "").strip()
            elif field == "misc_charge":
                b["parts"][ridx]["misc_charge"] = (val or "").strip()
            elif field == "misc_charge_description":
                b["parts"][ridx]["misc_charge_description"] = (val or "").strip()
            elif field == "one_time_part":
                raw = str(val or "").strip().lower()
                b["parts"][ridx]["one_time_part"] = raw in ("1", "true", "yes", "on")
            continue

    # normalize labors list in order
    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    labors = []
    for bidx in sorted(labors_map.keys()):
        b = labors_map[bidx]

        parts_clean = normalize_parts_payload(b.get("parts") or [])

        labor = b.get("labor") or {}
        assigned_mechanics = []
        assigned_raw = (labor.get("assigned_mechanics_json") or "").strip()
        if assigned_raw:
            try:
                assigned_data = json.loads(assigned_raw)
            except Exception:
                assigned_data = []
            assigned_mechanics = normalize_assigned_mechanics(assigned_data, mechanics_by_id)

        labors.append({
            "labor_id": ensure_labor_id(b.get("labor_id")),
            "labor": {
                "description": (labor.get("description") or "").strip(),
                "hours": (labor.get("hours") or "").strip(),
                "rate_code": (labor.get("rate_code") or "").strip(),
                "labor_full_total": round2(labor.get("labor_full_total")),
                "assigned_mechanics": assigned_mechanics,
                "issue_description": (labor.get("issue_description") or "").strip(),
            },
            "parts": parts_clean,
        })

    # ✅ totals from front (we just store)
    totals = {}
    totals_raw = (request.form.get("totals_json") or "").strip()
    if totals_raw:
        try:
            totals = json.loads(totals_raw)
            if not isinstance(totals, dict):
                totals = {}
        except Exception:
            totals = {}

    # Honor an explicit per-WO taxable override coming from the UI; fall back to
    # the customer's taxable flag when the front-end didn't send one.
    if isinstance(totals, dict) and "is_taxable" in totals:
        is_taxable_override = bool(totals.get("is_taxable"))
    else:
        is_taxable_override = _is_customer_taxable(shop_db, customer_id)

    totals = align_totals_with_labors(normalize_totals_payload(totals), labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        is_taxable_override,
    )
    work_order_date = shop_local_date_to_utc(request.form.get("work_order_date"), default_today=True)

    now = utcnow()
    user_id = current_user_id()

    # Статус: open (дефолт) / in_progress / estimate. Механик всегда получает
    # in_progress (enforce_mechanic_status), т.е. estimate — офисная фича.
    raw_create_status = enforce_mechanic_status(
        (request.form.get("create_status") or "").strip().lower()
    )
    create_status = raw_create_status if raw_create_status in ("in_progress", "estimate") else "open"

    # Номер WO нужен до списания — он уходит в ref журнала движений склада.
    wo_number = get_next_wo_number(shop_db, shop["_id"])

    # ✅ Deduct parts from inventory before creating work order.
    # Estimate — смета: склад не трогаем, спишем при конверсии в реальный WO.
    if create_status == "estimate":
        inventory_result = {"success": True, "deducted": [], "errors": []}
    else:
        inventory_result = deduct_parts_from_inventory(
            shop_db, labors, user_id,
            ref={"kind": "work_order", "label": str(wo_number)},
        )
    if not inventory_result["success"] and inventory_result["errors"]:
        for error in inventory_result["errors"]:
            flash(f"Inventory error: {error}", "warning")
        # Still proceed with creating work order, but flag it
        # If you prefer to cancel, uncomment the redirect below
        # return redirect(url_for("work_orders.work_order_details_page", customer_id=str(customer_id), unit_id=str(unit_id)))

    # ✅ Пробег: снимок на WO (его показывает страница и PDF этого WO);
    # одометр юнита двигаем только вперёд.
    unit_mileage = parse_mileage(request.form.get("unit_mileage"))
    if unit_mileage is not None:
        unit_doc = shop_db.units.find_one({"_id": unit_id, "shop_id": shop["_id"]}, {"mileage": 1}) or {}
        if odometer_moves_forward(unit_doc.get("mileage"), unit_mileage):
            shop_db.units.update_one(
                {"_id": unit_id, "shop_id": shop["_id"], "is_active": True},
                {"$set": {"mileage": unit_mileage, "updated_at": now, "updated_by": user_id}},
            )

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "wo_number": wo_number,
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": create_status,
        "labors": labors,
        # Ревизия редактируемого состояния (CAS сохранений, см. edit_views).
        "labors_rev": 1,
        "work_order_date": work_order_date,
        "mileage": unit_mileage,

        # ✅ store totals from UI
        "totals": totals,

        # ✅ track inventory deductions
        "inventory_deducted": len(inventory_result["deducted"]) > 0,
        "inventory_deductions": inventory_result["deducted"],

        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }

    res = shop_db.work_orders.insert_one(doc)
    new_wo_id = res.inserted_id
    _cust = shop_db.customers.find_one({"_id": customer_id}, {"company_name": 1, "contacts": 1}) or {}
    _unit = shop_db.units.find_one({"_id": unit_id}, {"unit_number": 1, "year": 1, "make": 1, "model": 1, "vin": 1}) or {}
    audit_log.log_event("create", "estimate" if create_status == "estimate" else "work_order", new_wo_id,
                        label=audit_log.wo_label(doc),
                        summary=f"{audit_log.wo_label(doc)} created for {audit_log.customer_label(_cust)} ({audit_log.unit_label(_unit)})",
                        details={"customer_id": str(customer_id), "customer": audit_log.customer_label(_cust),
                                 "unit_id": str(unit_id), "unit": audit_log.unit_label(_unit),
                                 "jobs": audit_log.labor_summary(doc), "grand_total": totals.get("grand_total"), "status": create_status})

    # WO создан из календаря — привязываем запись календаря к WO, чтобы
    # на сетке вместо «запланировано» был живой статус WO.
    calendar_event_id = oid(request.form.get("calendar_event_id"))
    if calendar_event_id:
        from app.blueprints.calendar.services.work_orders import link_event_to_work_order

        link_event_to_work_order(shop_db, shop, calendar_event_id, new_wo_id, wo_number)

    # ✅ Reassign pending attachments to the real work order ID
    pending_att_id = oid(request.form.get("pending_attachment_id"))
    if pending_att_id:
        shop_db.attachments.update_many(
            {"entity_id": pending_att_id},
            {"$set": {"entity_id": new_wo_id}},
        )
        # Парт-ордера, созданные со страницы ДО сохранения WO, висели на том
        # же временном id — перевешиваем на настоящий и проставляем номер.
        shop_db.parts_orders.update_many(
            {"shop_id": shop["_id"], "work_order_id": pending_att_id},
            {"$set": {"work_order_id": new_wo_id, "work_order_number": wo_number}},
        )

    # Sync cores collection using unpaid-core logic from this work order.
    # Для estimate физического движения партов нет — cores не создаём.
    if create_status == "estimate":
        core_sync = {"changes": []}
    else:
        core_sync = sync_work_order_cores(shop_db, shop, [], labors, user_id)

    flash("Estimate created." if create_status == "estimate" else "Work order created.", "success")

    if inventory_result["deducted"]:
        deducted_info = ", ".join([f"{d['part_number']} (qty: {d['qty_used']})" for d in inventory_result["deducted"]])
        flash(f"Inventory deducted: {deducted_info}", "info")

    if core_sync.get("changes"):
        added_cores = [x for x in core_sync["changes"] if int(x.get("qty_delta") or 0) > 0]
        if added_cores:
            cores_info = ", ".join([f"{c['part_number']} (qty: {c['qty_delta']})" for c in added_cores])
            flash(f"Cores added: {cores_info}", "info")
    if core_sync.get("errors"):
        for err in core_sync["errors"]:
            flash(f"Core sync warning: {err}", "warning")

    return redirect(url_for("work_orders.work_order_details_page", work_order_id=str(new_wo_id)))


# -----------------------------
# FAST PARTS SEARCH API
# -----------------------------

@work_orders_bp.get("/work_orders/api/parts/search")
@login_required
@permission_required("work_orders.create")
def api_parts_search():
    """
    Very fast search for parts in active shop DB.
    Query params:
      q: search string (min 3)
      limit: default 20 (max 50)
    Returns:
      {"items":[{id, part_number, description, reference, average_cost, in_stock}]}
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"items": [], "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"items": []}), 200

    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 50))

    import re

    parts_col = shop_db.parts
    normalized_query, query_tokens = build_query_tokens(q)
    if not normalized_query:
        return jsonify({"items": []}), 200

    query = {
        "shop_id": shop["_id"],
        "is_active": True,
    }
    if len(query_tokens) <= 1:
        query["search_terms"] = normalized_query
    else:
        query["search_terms"] = {"$all": query_tokens}

    projection = {
        "part_number": 1,
        "description": 1,
        "reference": 1,
        "average_cost": 1,
        "in_stock": 1,
        "do_not_track_inventory": 1,
        "has_selling_price": 1,
        "selling_price": 1,
        "core_has_charge": 1,
        "core_cost": 1,
        "misc_has_charge": 1,
        "misc_charges": 1,
        "unit_of_measure": 1,
    }

    def _serialize_item(p):
        misc_items = []
        for m in (p.get("misc_charges") or []):
            if not isinstance(m, dict):
                continue
            misc_items.append({
                "description": str(m.get("description") or "").strip(),
                "price": float(m.get("price") or 0),
            })

        return {
            "id": str(p.get("_id")),
            "part_number": p.get("part_number") or "",
            "description": p.get("description") or "",
            "reference": p.get("reference") or "",
            "average_cost": float(p.get("average_cost") or 0),
            "in_stock": int(p.get("in_stock") or 0),
            "do_not_track_inventory": bool(p.get("do_not_track_inventory")),
            "has_selling_price": bool(p.get("has_selling_price")),
            "selling_price": float(p.get("selling_price") or 0),
            "core_has_charge": bool(p.get("core_has_charge")),
            "core_cost": float(p.get("core_cost") or 0),
            "misc_has_charge": bool(p.get("misc_has_charge")),
            "misc_charges": misc_items,
            # unit (ключ) + unit_short — подпись у количества в строке WO
            **part_unit_fields(p),
        }

    fetch_limit = min(300, max(50, limit * 6))
    cursor = parts_col.find(query, projection).sort([("part_number", 1)]).limit(fetch_limit)

    items = []
    seen_ids = set()
    for p in cursor:
        if not part_matches_query(
            normalized_query,
            p.get("part_number"),
            p.get("description"),
            p.get("reference"),
        ):
            continue

        part_id = p.get("_id")
        if part_id in seen_ids:
            continue
        seen_ids.add(part_id)

        items.append(_serialize_item(p))

        if len(items) >= limit:
            break

    if len(items) < limit:
        contains = re.escape(q)
        # Fallback must not be limited to docs without search_terms,
        # because many legacy/seeded docs may have incomplete token arrays.
        fallback_query = {
            "shop_id": shop["_id"],
            "is_active": True,
            "$or": [
                {"part_number": {"$regex": contains, "$options": "i"}},
                {"description": {"$regex": contains, "$options": "i"}},
                {"reference": {"$regex": contains, "$options": "i"}},
            ],
        }

        fallback_cursor = (
            parts_col.find(fallback_query, projection)
            .sort([("part_number", 1)])
            .limit(fetch_limit)
        )

        for p in fallback_cursor:
            if not part_matches_query(
                normalized_query,
                p.get("part_number"),
                p.get("description"),
                p.get("reference"),
            ):
                continue

            part_id = p.get("_id")
            if part_id in seen_ids:
                continue
            seen_ids.add(part_id)

            items.append(_serialize_item(p))

            if len(items) >= limit:
                break

    attach_alternates(parts_col, shop["_id"], items, _serialize_item)

    if not has_permission("work_orders.view_costs"):
        items = [strip_part_search_item(i) for i in items]
        for i in items:
            if i.get("alternates"):
                i["alternates"] = [strip_part_search_item(a) for a in i["alternates"]]

    return jsonify({"items": items}), 200


@work_orders_bp.post("/work_orders/api/parse-handwritten")
@login_required
@permission_required("work_orders.create")
def api_parse_handwritten_work_order():
    """
    Upload a handwritten Work Order blank (PDF or image) and use AI to extract
    labors with their parts. Customer / unit info is intentionally ignored.

    For each handwritten part the backend returns a ranked list of CANDIDATE
    matches from the shop DB (top 5). The user reviews and picks the right one
    in the UI before anything is applied to the work order.

    Response shape:
      {
        "ok": True,
        "labors": [
          {
            "labor_description": str,
            "labor_hours": float,
            "parts": [
              {
                "written_part_number": str,
                "written_description": str,
                "qty": int,
                "candidates": [
                  {
                    "part_id": str,
                    "part_number": str,
                    "description": str,
                    "average_cost": float,
                    "selling_price": float,
                    "has_selling_price": bool,
                    "in_stock": int,
                    "core_has_charge": bool,
                    "core_cost": float,
                    "score": int,
                    "reason": str
                  }, ...
                ]
              }, ...
            ]
          }, ...
        ]
      }
    """
    from app.utils.wo_parser import parse_work_order

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop database not configured."}), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    allowed_types = {
        "application/pdf",
        "image/jpeg", "image/png", "image/gif", "image/webp",
        "image/bmp", "image/tiff",
    }
    ct = f.content_type or ""
    if ct not in allowed_types:
        return jsonify({"ok": False, "error": f"Unsupported file type: {ct}"}), 400

    file_bytes = f.read()
    if len(file_bytes) > 16 * 1024 * 1024:
        return jsonify({"ok": False, "error": "File too large (max 16 MB)."}), 400

    try:
        parsed = parse_work_order(file_bytes, ct)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": "Failed to parse work order. Please try again."}), 500

    parts_col = shop_db.parts
    shop_id = shop["_id"]

    # ----- Build a fast in-memory fuzzy index over the active catalog -----
    from app.utils.parts_matcher import build_index, match_part

    catalog_proj = {
        "part_number": 1, "description": 1,
        "average_cost": 1, "selling_price": 1, "has_selling_price": 1,
        "in_stock": 1, "do_not_track_inventory": 1,
        "core_has_charge": 1, "core_cost": 1,
        "misc_has_charge": 1, "misc_charges": 1,
        "unit_of_measure": 1,
    }
    catalog_cursor = parts_col.find(
        {"shop_id": shop_id, "is_active": True},
        catalog_proj,
    )
    index = build_index(catalog_cursor)

    def _hydrate(cands: list[dict]) -> list[dict]:
        out = []
        for c in cands:
            d = c["doc"]
            has_selling = bool(d.get("has_selling_price"))
            misc_items = []
            for m in (d.get("misc_charges") or []):
                if not isinstance(m, dict):
                    continue
                misc_items.append({
                    "description": str(m.get("description") or "").strip(),
                    "price": float(m.get("price") or 0),
                    "taxable": m.get("taxable") is not False,
                })
            out.append({
                "part_id": str(d.get("_id")),
                "part_number": d.get("part_number") or "",
                "description": d.get("description") or "",
                "average_cost": float(d.get("average_cost") or 0),
                "selling_price": float(d.get("selling_price") or 0) if has_selling else 0.0,
                "has_selling_price": has_selling,
                "in_stock": int(d.get("in_stock") or 0),
                "do_not_track_inventory": bool(d.get("do_not_track_inventory")),
                "core_has_charge": bool(d.get("core_has_charge")),
                "core_cost": float(d.get("core_cost") or 0),
                "misc_has_charge": bool(d.get("misc_has_charge")),
                "misc_charges": misc_items,
                **part_unit_fields(d),
                "score": int(c.get("score") or 0),
                "reason": c.get("reason") or "",
            })
        return out

    out_labors = []
    for block in (parsed.get("labors") or []):
        out_parts = []
        for p in (block.get("parts") or []):
            written_pn = p.get("part_number") or ""
            written_desc = p.get("description") or ""
            try:
                qty = int(p.get("qty") or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                qty = 1

            # Suggest top candidates from the catalog (rapidfuzz). The user
            # also has a live search box in the UI to override.
            cands: list[dict] = []
            if written_pn.strip() or written_desc.strip():
                cands = match_part(written_pn, written_desc, index, limit=5, min_score=60)

            out_parts.append({
                "written_part_number": written_pn,
                "written_description": written_desc,
                "qty": qty,
                "candidates": _hydrate(cands),
            })

        out_labors.append({
            "labor_description": block.get("labor_description") or "",
            "labor_hours": float(block.get("labor_hours") or 0),
            "parts": out_parts,
        })

    return jsonify({
        "ok": True,
        "labors": out_labors,
        "preview_image_urls": parsed.get("preview_image_urls") or [],
    }), 200


@work_orders_bp.get("/work_orders/api/units")
@login_required
@permission_required("work_orders.create")
def api_units_for_customer():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"items": [], "error": "shop_db_missing"}), 200

    customer_id = oid(request.args.get("customer_id"))
    if not customer_id:
        return jsonify({"items": []}), 200

    rows = list(
        shop_db.units.find({"customer_id": customer_id, "is_active": True})
        .sort([("created_at", -1)])
        .limit(500)
    )

    items = [{"id": str(u["_id"]), "label": unit_label(u), "mileage": u.get("mileage")} for u in rows]
    return jsonify({"items": items}), 200


@work_orders_bp.get("/work_orders/api/parts_pricing")
@login_required
@permission_required("work_orders.create")
def api_parts_pricing_for_customer():
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"pricing": None, "error": "shop_db_missing"}), 200

    customer_id = oid(request.args.get("customer_id")) if request.args.get("customer_id") else None
    pricing = get_pricing_rules_json(shop_db, shop["_id"], customer_id=customer_id)
    return jsonify({"pricing": pricing}), 200


@work_orders_bp.get("/work_orders/api/unit")
@login_required
@permission_required("work_orders.create")
def api_unit_details():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "item": None, "error": "shop_db_missing"}), 200

    unit_id = oid(request.args.get("id"))
    if not unit_id:
        return jsonify({"ok": False, "item": None}), 200

    u = shop_db.units.find_one({"_id": unit_id, "is_active": True})
    if not u:
        return jsonify({"ok": False, "item": None}), 200

    item = {
        "id": str(u.get("_id")),
        "customer_id": str(u.get("customer_id")) if u.get("customer_id") else "",
        "vin": u.get("vin") or "",
        "unit_number": u.get("unit_number") or "",
        "make": u.get("make") or "",
        "model": u.get("model") or "",
        "year": u.get("year") or "",
        "type": u.get("type") or "",
        "mileage": u.get("mileage") or "",
    }
    return jsonify({"ok": True, "item": item}), 200


@work_orders_bp.post("/work_orders/api/annual_inspections/create")
@login_required
@permission_required("work_orders.create")
def api_create_annual_inspection():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}

    # Юнит опционален: со страницы Perks инспекция делается «быстро» —
    # только VIN + тип + кастомер текстом, без заведения юнита в систему.
    unit_id = oid(data.get("unit_id"))
    unit = None
    if unit_id:
        unit = shop_db.units.find_one({"_id": unit_id, "shop_id": shop["_id"], "is_active": True})
        if not unit:
            return jsonify({"ok": False, "error": "unit_not_found"}), 200
    elif not (data.get("vin") or "").strip():
        return jsonify({"ok": False, "error": "vin_required"}), 200

    customer_id = oid(data.get("customer_id")) or (unit.get("customer_id") if unit else None)
    inspection_date = shop_local_date_to_utc(data.get("date"), default_today=True)

    now = utcnow()
    user_id = current_user_id()

    from app.blueprints.work_orders.services.inspections import sanitize_components

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "unit_id": unit_id,
        "customer_id": customer_id,
        "work_order_id": oid(data.get("work_order_id")),
        "report_number": (data.get("report_number") or "").strip(),
        "inspection_date": inspection_date,
        "motor_carrier_operator": (data.get("motor_carrier_operator") or "").strip(),
        "address": (data.get("address") or "").strip(),
        "city_state_zip": (data.get("city_state_zip") or "").strip(),
        "inspector_name": (data.get("inspector_name") or "").strip(),
        "inspector_qualified": bool(data.get("inspector_qualified", True)),
        "vehicle_id_type": "vin",
        "vin": (data.get("vin") or "").strip().upper(),
        "inspection_agency": (data.get("inspection_agency") or "").strip(),
        "vehicle_type": (data.get("vehicle_type") or "").strip().lower() or ((unit.get("type") or "").strip().lower() if unit else ""),
        # Component checklist marks: {"1a": {"status": "ok"|"repair"|"na", ...}}
        "components": sanitize_components(data.get("components")),
        "created_at": now,
        "created_by": user_id,
    }

    # История инспекций хранится целиком (DOT требует retention);
    # последняя по created_at считается действующей.
    res = shop_db.annual_inspections.insert_one(doc)

    audit_log.log_event("create", "inspection", res.inserted_id,
                        label=f"Inspection - {audit_log.unit_label(unit) if unit else 'unit'}",
                        details={"unit_id": str(unit_id or ""), "customer_id": str(customer_id or "")})
    return jsonify({"ok": True, "id": str(res.inserted_id)}), 200


@work_orders_bp.post("/work_orders/api/annual_inspections/<inspection_id>/delete")
@login_required
@permission_required("work_orders.delete")
def api_delete_annual_inspection(inspection_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    insp_id = oid(inspection_id)
    if not insp_id:
        return jsonify({"ok": False, "error": "invalid_inspection_id"}), 200

    res = shop_db.annual_inspections.delete_one({"_id": insp_id, "shop_id": shop["_id"]})
    if not res.deleted_count:
        return jsonify({"ok": False, "error": "inspection_not_found"}), 200
    audit_log.log_event("delete", "inspection", insp_id, label="Annual inspection")
    return jsonify({"ok": True}), 200






@work_orders_bp.route("/work_orders/api/annual_inspections/preview-pdf", methods=["GET", "POST"])
@login_required
@permission_required("work_orders.create")
def api_preview_annual_inspection_pdf():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 404

    from app.blueprints.work_orders.services.inspections import sanitize_components

    # POST с JSON — основной путь (чеклист не помещается в query string);
    # GET оставлен для обратной совместимости.
    if request.method == "POST":
        args = request.get_json(silent=True) or {}
    else:
        args = request.args
    draft = {
        "_id": "",
        "unit_id": oid(args.get("unit_id")),
        "report_number": (args.get("report_number") or "").strip(),
        "inspection_date": shop_local_date_to_utc(args.get("date"), default_today=True),
        "motor_carrier_operator": (args.get("motor_carrier_operator") or "").strip(),
        "address": (args.get("address") or "").strip(),
        "city_state_zip": (args.get("city_state_zip") or "").strip(),
        "inspector_name": (args.get("inspector_name") or "").strip(),
        "inspector_qualified": str(args.get("inspector_qualified") if args.get("inspector_qualified") is not None else "1").lower() not in ("0", "false", ""),
        "vin": (args.get("vin") or "").strip().upper(),
        "inspection_agency": (args.get("inspection_agency") or "").strip(),
        "vehicle_type": (args.get("vehicle_type") or "").strip().lower(),
        "components": sanitize_components(args.get("components")),
    }

    # report_number в контексте берётся из draft'а (пустой — пустая клетка).
    ctx = _build_annual_inspection_pdf_context(shop_db, draft)
    pdf_html = render_template("emails/annual_inspection_pdf.html", **ctx)

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        return jsonify({"ok": False, "error": "pdf_generation_failed"}), 500

    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = 'inline; filename="AnnualInspection-Preview.pdf"'
    return resp


@work_orders_bp.get("/work_orders/api/annual_inspections/<inspection_id>/download-pdf")
@login_required
@permission_required("work_orders.view")
def api_download_annual_inspection_pdf(inspection_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 404

    insp_id = oid(inspection_id)
    if not insp_id:
        return jsonify({"ok": False, "error": "invalid_inspection_id"}), 400

    inspection = shop_db.annual_inspections.find_one({"_id": insp_id, "shop_id": shop["_id"]})
    if not inspection:
        return jsonify({"ok": False, "error": "inspection_not_found"}), 404

    ctx = _build_annual_inspection_pdf_context(shop_db, inspection)
    pdf_html = render_template("emails/annual_inspection_pdf.html", **ctx)

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        return jsonify({"ok": False, "error": "pdf_generation_failed"}), 500

    date_label = ctx["inspection_date_label"].replace("/", "-")
    filename = f"AnnualInspection-{ctx['fleet_unit_number'] or ctx['report_number']}-{date_label}.pdf"
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers.set("Content-Disposition", "attachment", filename=filename)
    return resp


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/update")
@login_required
@permission_required_any("work_orders.edit", "work_orders.create")
def api_work_order_update(work_order_id):
    # До всего остального: base для merge выбирается по вьюхам, записанным
    # раньше начала этого запроса.
    request_started_at = utcnow()
    # Правка существующего WO — work_orders.edit. Исключение — механик-режим:
    # у механика есть только create, его сохранение форсится в in_progress и
    # не трогает цены (enforce_mechanic_status / автоцены сервером).
    mechanic_mode = is_mechanic_mode()
    if not has_permission("work_orders.edit") and not mechanic_mode:
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.edit"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    labors = data.get("labors", data.get("blocks"))
    if not isinstance(labors, list):
        return jsonify({"ok": False, "error": "labors_required"}), 200

    if mechanic_mode:
        # Механик и здесь сохраняет как на своей странице: деньги, часы и
        # назначения из формы не принимаются, подтверждённый менеджером WO
        # закрыт, тоталы считает сервер (services/concurrent_save.py).
        mechanic_data = {k: v for k, v in data.items() if k not in ("labors", "blocks", "totals")}
        mechanic_data["labors"] = office_merge.office_rows_as_mechanic_rows(labors)
        payload, code = concurrent_save.save_mechanic_edit(
            shop_db, shop, wo_id, mechanic_data,
            user_id=current_user_id(),
            sid=edit_session_id(),
            request_started_at=request_started_at,
            channel=office_merge.CHANNEL,
            mechanic_mode=True,
            guard=office_merge.office_mechanic_guard(),
            cas_filter=concurrent_save.MECHANIC_CAS_FILTER,
            status="in_progress",
            taxable_from_request=False,
            audit_suffix="by mechanic",
            initial_wo=wo,
        )
        return jsonify(payload), code

    # Merge с тем, что видела эта вкладка, CAS по labors_rev, 3-way полей
    # уровня WO, побочные эффекты после записи — services/office_merge.py.
    payload, code = office_merge.save_office_edit(
        shop_db, shop, wo_id, data,
        user_id=current_user_id(),
        sid=edit_session_id(),
        request_started_at=request_started_at,
        initial_wo=wo,
    )
    return jsonify(payload), code



@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/payment")
@login_required
@permission_required("work_orders.manage_payments")
def api_work_order_payment(work_order_id):
    """
    Record a payment for a work order.
    Request body: {amount, payment_method, notes}
    Saves to work_order_payments collection and updates work_order status if fully paid.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    if (wo.get("status") or "open") in ("estimate", "estimated", "quote", "quoted"):
        return jsonify({"ok": False, "error": "estimate_cannot_receive_payments",
                        "message": "This is an estimate — convert it to a work order first."}), 200

    data = request.get_json(silent=True) or {}
    amount = f64(data.get("amount"))
    payment_method = (data.get("payment_method") or "").strip() or "cash"
    notes = (data.get("notes") or "").strip()
    payment_date = shop_local_date_to_utc(data.get("payment_date"), default_today=True)

    if amount is None or not (isinstance(amount, (int, float)) and amount > 0):
        return jsonify({"ok": False, "error": "invalid_amount"}), 200

    grand_total = _work_order_grand_total(wo)
    paid_amount = _sum_active_work_order_payments(shop_db, wo_id)

    # Calculate new balance
    new_paid_amount = round2(paid_amount + amount)
    remaining_balance = round2(grand_total - new_paid_amount)

    # If payment exceeds total, return error
    if new_paid_amount > grand_total:
        return jsonify({
            "ok": False,
            "error": "overpayment",
            "message": f"Payment would exceed invoice total. Current balance: ${round2(grand_total - paid_amount)}"
        }), 200

    now = utcnow()
    user_id = current_user_id()

    # Save payment record
    payment_doc = {
        "work_order_id": wo_id,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "amount": round2(amount),
        "payment_method": payment_method,
        "notes": notes,
        "payment_date": payment_date,
        "is_active": True,
        "created_at": now,
        "created_by": user_id,
    }

    pending_att_id = oid(data.get("pending_attachment_id"))

    # Платёж + перенос вложений + статус WO — одна логическая операция.
    # На replica set выполняется транзакцией; на standalone — последовательно
    # (session=None), как раньше. См. app/utils/mongo_tx.py.
    def _apply_payment(session):
        result = shop_db.work_order_payments.insert_one(payment_doc, session=session)
        new_payment_id = result.inserted_id

        # Reassign pending attachments to the real payment ID
        if pending_att_id:
            shop_db.attachments.update_many(
                {"entity_id": pending_att_id},
                {"$set": {"entity_id": new_payment_id}},
                session=session,
            )

        refreshed_wo = shop_db.work_orders.find_one(
            {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, session=session
        ) or wo
        summary = _sync_work_order_payment_state(
            shop_db, refreshed_wo, user_id, now, session=session
        ) or {"status": "open", "is_fully_paid": False}
        return new_payment_id, summary

    payment_id, payment_summary = run_atomically(get_mongo_client(), _apply_payment)
    # Остаток и сумма оплат — по тому документу WO, по которому записан
    # статус: тоталы могли поменяться после первого чтения (пересчёт часов
    # таймера, сохранение) — см. _sync_work_order_payment_state.
    if payment_summary.get("remaining_balance") is not None:
        remaining_balance = round2(payment_summary["remaining_balance"])
    if payment_summary.get("paid_amount") is not None:
        new_paid_amount = round2(payment_summary["paid_amount"])
    # Статус WO мог стать paid — привязанные записи календаря следом (после транзакции).
    _sync_calendar_with_wo(shop_db, shop, wo_id)
    audit_log.log_event("payment", "work_order", wo_id, label=audit_log.wo_label(wo),
                        summary=f"Payment ${round2(amount):.2f} ({payment_method}) recorded for {audit_log.wo_label(wo)} - balance ${remaining_balance:.2f}",
                        details={"amount": round2(amount), "method": payment_method, "remaining_balance": remaining_balance,
                                 "payment_id": str(payment_id), "status": payment_summary.get("status")})

    return jsonify({
        "ok": True,
        "payment_id": str(payment_id),
        "amount_paid": round2(new_paid_amount),
        "remaining_balance": remaining_balance,
        "status": payment_summary["status"],
        "is_fully_paid": payment_summary["is_fully_paid"],
    }), 200


@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/payments")
@login_required
@permission_required_any("work_orders.manage_payments", "work_orders.view_costs")
def api_get_work_order_payments(work_order_id):
    """
    Get all payments for a work order with balance info.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    payments = list(
        shop_db.work_order_payments.find({"work_order_id": wo_id, "is_active": True})
        .sort([("payment_date", -1), ("created_at", -1)])
    )

    summary = _build_work_order_payment_summary(wo, sum(round2(p.get("amount") or 0) for p in payments))

    payment_list = [
        {
            "id": str(p.get("_id")),
            "amount": round2(p.get("amount") or 0),
            "payment_method": p.get("payment_method") or "cash",
            "notes": p.get("notes") or "",
            "payment_date": _fmt_dt_iso(p.get("payment_date") or p.get("created_at")),
            "payment_date_label": format_preferred_date_label(p.get("payment_date"), p.get("created_at")),
            "created_at": _fmt_dt_iso(p.get("created_at")),
        }
        for p in payments
    ]

    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}, {"email": 1, "contacts": 1, "first_name": 1, "last_name": 1, "phone": 1}) or {}
    customer_email = get_main_contact_email(customer, entity_type="customer")
    customer_contacts = get_contacts(customer, entity_type="customer")

    return jsonify({
        "ok": True,
        "work_order_date": _fmt_dt_iso(wo.get("work_order_date") or wo.get("created_at")),
        "work_order_date_label": format_preferred_date_label(wo.get("work_order_date"), wo.get("created_at")),
        "created_at": _fmt_dt_iso(wo.get("created_at")),
        "created_at_label": format_dt_label(wo.get("created_at")),
        "grand_total": summary["grand_total"],
        "paid_amount": summary["paid_amount"],
        "remaining_balance": summary["remaining_balance"],
        "status": summary["status"],
        "customer_email": customer_email,
        "customer_contacts": customer_contacts,
        "payments": payment_list
    }), 200


@work_orders_bp.post("/work_orders/api/payments/<payment_id>/delete")
@login_required
@permission_required("work_orders.manage_payments")
def api_delete_work_order_payment(payment_id):
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    pay_id = oid(payment_id)
    if not pay_id:
        return jsonify({"ok": False, "error": "invalid_payment_id"}), 200

    payment = shop_db.work_order_payments.find_one({"_id": pay_id, "shop_id": shop["_id"], "is_active": True})
    if not payment:
        return jsonify({"ok": False, "error": "payment_not_found"}), 200

    wo_id = payment.get("work_order_id")
    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    now = utcnow()
    user_id = current_user_id()

    # Деактивация платежа + пересчёт статуса WO — атомарно (см. mongo_tx).
    def _apply_delete(session):
        shop_db.work_order_payments.update_one(
            {"_id": pay_id},
            {
                "$set": {
                    "is_active": False,
                    "deleted_at": now,
                    "deleted_by": user_id,
                    "updated_at": now,
                    "updated_by": user_id,
                }
            },
            session=session,
        )

        refreshed_wo = shop_db.work_orders.find_one(
            {"_id": wo_id, "shop_id": shop["_id"], "is_active": True}, session=session
        ) or wo
        return _sync_work_order_payment_state(
            shop_db, refreshed_wo, user_id, now, session=session
        ) or {
            "status": "open",
            "paid_amount": 0.0,
            "remaining_balance": _work_order_grand_total(wo),
            "is_fully_paid": False,
        }

    summary = run_atomically(get_mongo_client(), _apply_delete)
    # Оплата снята — WO снова open, привязанные записи календаря следом.
    _sync_calendar_with_wo(shop_db, shop, wo_id)
    audit_log.log_event("payment_delete", "work_order", wo_id, label=audit_log.wo_label(wo),
                        summary=f"Payment ${float(payment.get('amount') or 0):.2f} removed from {audit_log.wo_label(wo)}",
                        details={"amount": payment.get("amount"), "payment_id": str(pay_id), "status": summary.get("status")})

    return jsonify(
        {
            "ok": True,
            "payment_id": str(pay_id),
            "work_order_id": str(wo_id) if wo_id else "",
            "status": summary["status"],
            "amount_paid": summary["paid_amount"],
            "remaining_balance": summary["remaining_balance"],
            "is_fully_paid": summary["is_fully_paid"],
        }
    ), 200


@work_orders_bp.get("/work_orders/api/bulk-payments/customers")
@login_required
@permission_required("work_orders.view")
def api_bulk_payment_customers():
    """Customers that have unpaid invoices (for the bulk payment modal)."""
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({"ok": True, "customers": get_bulk_payment_customers(shop_db, shop["_id"])}), 200


@work_orders_bp.get("/work_orders/api/bulk-payments/customers/<customer_id>/unpaid")
@login_required
@permission_required("work_orders.view")
def api_bulk_payment_unpaid_work_orders(customer_id):
    """Unpaid invoices of one customer with balances, oldest first."""
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    cust_id = oid(customer_id)
    if not cust_id:
        return jsonify({"ok": False, "error": "invalid_customer_id"}), 200

    return jsonify({
        "ok": True,
        "work_orders": get_unpaid_work_orders_for_customer(shop_db, shop["_id"], cust_id),
    }), 200


@work_orders_bp.post("/work_orders/api/bulk-payments")
@login_required
@permission_required("work_orders.manage_payments")
def api_bulk_payment():
    """
    Record one payment split across several work orders.
    Request body: {customer_id, payment_method, notes, payment_date,
                   allocations: [{work_order_id, amount}, ...]}
    All-or-nothing: any invalid allocation rejects the whole request.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    result = apply_bulk_payment(
        shop_db,
        shop,
        get_mongo_client(),
        allocations=data.get("allocations"),
        payment_method=data.get("payment_method"),
        notes=data.get("notes"),
        payment_date=shop_local_date_to_utc(data.get("payment_date"), default_today=True),
        user_id=current_user_id(),
        customer_id=oid(data.get("customer_id")),
    )
    if isinstance(result, dict) and result.get("ok"):
        # Оплаченные WO — привязанные записи календаря следом (после транзакции).
        for item in result.get("results") or []:
            _sync_calendar_with_wo(shop_db, shop, item.get("work_order_id"))
        audit_log.log_event("payment", "customer", data.get("customer_id"),
                            summary=f"Bulk payment recorded ({data.get('payment_method') or 'cash'}) for {len(data.get('allocations') or [])} invoice(s)",
                            details={"allocations": len(data.get("allocations") or []), "method": data.get("payment_method")})
    return jsonify(result), 200


@work_orders_bp.get("/work_orders/api/work_orders/all-payments")
@login_required
@permission_required("work_orders.view")
def api_get_all_payments():
    """
    Get all payments for the current shop.
    """
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    shop_id = shop["_id"]
    q = (request.args.get("q") or "").strip()

    date_filters = get_date_range_filters(request.args)
    created_from = date_filters["created_from"]
    created_to_exclusive = date_filters["created_to_exclusive"]

    payments_query = {"shop_id": shop_id, "is_active": True}

    payments_search = build_regex_search_filter(
        q,
        text_fields=["payment_method", "notes"],
        numeric_fields=["amount"],
        object_id_fields=["_id", "work_order_id", "shop_id", "created_by"],
    )

    if q:
        customer_ids = search_customer_ids(shop_db.customers, q)

        wo_base = {"shop_id": shop_id}
        wo_search = build_regex_search_filter(
            q,
            text_fields=["status"],
            numeric_fields=["wo_number"],
            object_id_fields=["_id", "customer_id", "unit_id", "shop_id"],
        )
        if customer_ids and wo_search:
            wo_query = {"$and": [wo_base, {"$or": [wo_search, {"customer_id": {"$in": customer_ids}}]}]}
        elif customer_ids:
            wo_query = {"$and": [wo_base, {"customer_id": {"$in": customer_ids}}]}
        elif wo_search:
            wo_query = {"$and": [wo_base, wo_search]}
        else:
            wo_query = wo_base

        wo_ids = [wo.get("_id") for wo in shop_db.work_orders.find(wo_query, {"_id": 1}) if wo.get("_id")]

        extra = []
        if wo_ids:
            extra.append({"work_order_id": {"$in": wo_ids}})

        if payments_search and extra:
            payments_query = {"$and": [payments_query, {"$or": [payments_search, *extra]}]}
        elif payments_search:
            payments_query = {"$and": [payments_query, payments_search]}
        elif extra:
            payments_query = {"$and": [payments_query, {"$or": extra}]}
    elif payments_search:
        payments_query = {"$and": [payments_query, payments_search]}

    created_at_filter = build_preferred_date_range_filter("payment_date", created_from, created_to_exclusive)
    if created_at_filter:
        payments_query = append_and_filter(payments_query, created_at_filter)

    page, per_page = get_pagination_params(
        request.args, default_per_page=50, max_per_page=200,
        page_key="payments_page", per_page_key="payments_per_page",
    )

    # Тоталы по ВСЕЙ выборке (те же фильтры поиска/дат), не только по странице.
    totals_rows = list(shop_db.work_order_payments.aggregate([
        {"$match": payments_query},
        {
            "$group": {
                "_id": {"$toLower": {"$ifNull": ["$payment_method", "cash"]}},
                "amount": {"$sum": {"$ifNull": ["$amount", 0]}},
                "count": {"$sum": 1},
            }
        },
    ]))
    totals_by_method = {
        str(r.get("_id") or "cash"): round2(r.get("amount") or 0)
        for r in sorted(totals_rows, key=lambda r: -(r.get("amount") or 0))
    }
    payments_totals = {
        "amount_total": round2(sum((r.get("amount") or 0) for r in totals_rows)),
        "count": sum(int(r.get("count") or 0) for r in totals_rows),
        "by_method": totals_by_method,
    }

    sort_params = get_sort_params(
        request.args,
        [("payment_date", -1), ("created_at", -1)],
        ["payment_date", "amount", "payment_method", "created_at", "customer"],
    )

    if sort_params and sort_params[0][0] == "customer":
        payments, payments_pagination = _paginate_by_customer(
            shop_db,
            shop_db.work_order_payments,
            payments_query,
            page,
            per_page,
            sort_params[0][1],
            indirect_lookup=(shop_db.work_orders, "work_order_id", "customer_id"),
        )
    else:
        payments, payments_pagination = paginate_find(
            shop_db.work_order_payments,
            payments_query,
            sort=sort_params,
            page=page,
            per_page=per_page,
        )

    work_order_ids = [p.get("work_order_id") for p in payments if p.get("work_order_id")]
    work_orders_map = {}
    customer_ids = []
    if work_order_ids:
        work_orders = list(
            shop_db.work_orders.find(
                {"_id": {"$in": work_order_ids}, "shop_id": shop_id},
                {"customer_id": 1, "wo_number": 1},
            )
        )
        for wo in work_orders:
            wo_id = wo.get("_id")
            if wo_id:
                work_orders_map[wo_id] = wo
            customer_id = wo.get("customer_id")
            if customer_id:
                customer_ids.append(customer_id)

    customers_map = {}
    inactive_customer_ids = set()
    if customer_ids:
        customers = list(
            shop_db.customers.find(
                {"_id": {"$in": customer_ids}},
                {"company_name": 1, "first_name": 1, "last_name": 1, "contacts": 1, "is_active": 1},
            )
        )
        for c in customers:
            c_id = c.get("_id")
            if c_id:
                customers_map[c_id] = customer_label(c)
                if c.get("is_active") is False:
                    inactive_customer_ids.add(c_id)

    # Batch-fetch attachment counts for all payments on this page.
    payment_ids = [p["_id"] for p in payments if p.get("_id")]
    att_counts_map: dict = {}
    if payment_ids:
        for doc in shop_db.attachments.aggregate([
            {"$match": {"entity_type": "work_order_payment", "entity_id": {"$in": payment_ids}}},
            {"$group": {"_id": "$entity_id", "count": {"$sum": 1}}},
        ]):
            att_counts_map[doc["_id"]] = doc["count"]

    payment_list = [
        {
            "id": str(p.get("_id")),
            "work_order_id": str(p.get("work_order_id")) if p.get("work_order_id") else "",
            "wo_number": (work_orders_map.get(p.get("work_order_id")) or {}).get("wo_number") or "-",
            "customer": customers_map.get((work_orders_map.get(p.get("work_order_id")) or {}).get("customer_id")) or "-",
            "customer_inactive": (work_orders_map.get(p.get("work_order_id")) or {}).get("customer_id") in inactive_customer_ids,
            "amount": round2(p.get("amount") or 0),
            "payment_method": p.get("payment_method") or "cash",
            "notes": p.get("notes") or "",
            "payment_date": (p.get("payment_date") or p.get("created_at")).isoformat() if (p.get("payment_date") or p.get("created_at")) else "",
            "created_at": p.get("created_at").isoformat() if p.get("created_at") else "",
            "att_count": att_counts_map.get(p.get("_id"), 0),
        }
        for p in payments
    ]

    return jsonify({
        "ok": True,
        "payments": payment_list,
        "pagination": payments_pagination,
        "totals": payments_totals,
    }), 200


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/status")
@login_required
@permission_required_any("work_orders.change_status", "work_orders.create")
def api_work_order_set_status(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()

    if status not in ("open", "paid", "in_progress"):
        return jsonify({"ok": False, "error": "invalid_status"}), 200

    # Смена бизнес-статуса (open/paid) — work_orders.change_status. «Взять в
    # работу» (in_progress) может любой, кто вообще сохраняет WO (create):
    # это рабочее состояние, его же выставляют таймеры механиков.
    if status != "in_progress" and not has_permission("work_orders.change_status"):
        return jsonify({"ok": False, "error": "forbidden_status", "required": "work_orders.change_status"}), 403
    # Уход из paid (в т.ч. в in_progress) удаляет записи платежей ниже — это
    # денежное действие, без change_status его не делает никто.
    if (wo.get("status") or "").strip().lower() == "paid" and not has_permission("work_orders.change_status"):
        return jsonify({"ok": False, "error": "forbidden_status", "required": "work_orders.change_status"}), 403

    # Механик-режим: разрешён только in_progress, и нельзя трогать paid WO —
    # иначе смена статуса удалит записи платежей (delete_many ниже).
    if is_mechanic_mode():
        if status != "in_progress":
            return jsonify({"ok": False, "error": "forbidden_status"}), 403
        if (wo.get("status") or "").strip().lower() == "paid":
            return jsonify({"ok": False, "error": "paid_cannot_change"}), 403

    now = utcnow()
    user_id = current_user_id()

    # If changing to "open" (unpaid) or "in_progress", delete all payment records for this work order
    if status in ("open", "in_progress"):
        shop_db.work_order_payments.delete_many({"work_order_id": wo_id})

    status_fields = {"status": status, "updated_at": now, "updated_by": user_id}
    wo_filter = {"_id": wo_id, "shop_id": shop["_id"]}
    if status == "in_progress" and not is_mechanic_mode():
        # Менеджер вернул WO в работу — отметки Done механиков снимаются.
        # Отметки — часть редактируемого состояния (сохранения механиков
        # пересобирают их из своего чтения), поэтому сброс двигает
        # labors_rev: устаревший «Done» не вернёт снятую отметку (CAS). Есть
        # ли что снимать, решает фильтр самой записи, а не чтение выше:
        # «Done», успевший между чтением и записью, снимается вместе с $inc.
        res = shop_db.work_orders.update_one(
            {**wo_filter, "$or": [{"mechanic_done": True}, {"mechanic_done_marks.0": {"$exists": True}}]},
            {"$set": {**status_fields, **clear_mechanic_done_fields()}, "$inc": {"labors_rev": 1}},
        )
        if not res.matched_count:
            # Снимать нечего — только статус, ревизия не двигается. «Done»,
            # пришедший уже после этой проверки, — новая отметка, её не трогаем.
            shop_db.work_orders.update_one(wo_filter, {"$set": status_fields})
    else:
        shop_db.work_orders.update_one(wo_filter, {"$set": status_fields})
    # Привязанные записи календаря: paid → completed, in_progress → in_progress,
    # open (не начат) → обратно в scheduled.
    _sync_calendar_with_wo(shop_db, shop, wo_id)

    audit_log.log_event("status", "work_order", wo_id, label=audit_log.wo_label(wo),
                        summary=f"{audit_log.wo_label(wo)}: status {wo.get('status') or 'open'} -> {status}",
                        changes=[audit_log.change("status", wo.get("status"), status)])
    return jsonify({"ok": True, "status": status, "labors_rev": wo_labors_rev(shop_db, shop["_id"], wo_id)}), 200


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/confirm")
@login_required
@permission_required("work_orders.view_costs")
def api_work_order_set_confirmed(work_order_id):
    """
    Менеджер подтверждает работу механика по WO (или снимает подтверждение,
    body: {"confirmed": true|false}). Подтверждённый WO для механика закрыт:
    не открывается и не редактируется, пока менеджер не снимет подтверждение.
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    data = request.get_json(silent=True) or {}
    confirmed = bool(data.get("confirmed"))

    now = utcnow()
    user_id = current_user_id()
    shop_db.work_orders.update_one(
        {"_id": wo_id},
        {
            "$set": {
                "manager_confirmed": confirmed,
                "manager_confirmed_at": now if confirmed else None,
                "manager_confirmed_by": user_id if confirmed else None,
                "updated_at": now,
                "updated_by": user_id,
            }
        },
    )

    # Подтверждение закрывает работу (completed для календаря), снятие — возвращает план.
    _sync_calendar_with_wo(shop_db, shop, wo_id)

    audit_log.log_event("confirm", "work_order", wo_id, label=audit_log.wo_label(wo),
                        summary=f"{audit_log.wo_label(wo)}: mechanic work {'confirmed' if confirmed else 'un-confirmed'} by manager")
    return jsonify({"ok": True, "manager_confirmed": confirmed}), 200


@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/parts_orders")
@login_required
@permission_required("work_orders.view_costs")
def api_work_order_parts_orders(work_order_id):
    """Привязанные к WO парт-ордера: статусы + сверка использования позиций.

    Права view_costs: блок показывает цены/балансы, механику недоступен.
    """
    from app.blueprints.work_orders.services.parts_orders_link import (
        linked_parts_orders_payload,
    )
    from app.utils.display_datetime import format_date_mmddyyyy

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400
    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": {"$ne": False}}
    )
    # Сверка использования показывается только для принятого WO: смета и
    # страница создания (pending-id, самого WO ещё нет) живут без «не
    # использовано» — состав работ ещё не финален.
    estimate_statuses = ("estimate", "estimated", "quote", "quoted")
    show_usage = bool(wo) and (wo.get("status") or "open").strip().lower() not in estimate_statuses
    if not wo:
        wo = {"_id": wo_id, "labors": []}

    orders = linked_parts_orders_payload(
        shop_db, shop, wo, fmt_date=format_date_mmddyyyy, show_usage=show_usage
    )
    return jsonify({"ok": True, "orders": orders}), 200




@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/delete")
@login_required
@permission_required("work_orders.delete")
def api_work_order_delete(work_order_id):
    """
    Delete a work order and restore parts to inventory.
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 200

    now = utcnow()
    user_id = current_user_id()

    # Soft delete — одной атомарной записью, а склад/коры/платежи/вложения —
    # от документа, каким он был в момент удаления (ReturnDocument.BEFORE):
    # сохранение, успевшее между чтением и удалением, иначе двигало бы склад
    # мимо возврата. $inc labors_rev — устаревший CAS-Save на удалённый WO
    # не пройдёт.
    from pymongo import ReturnDocument

    wo = shop_db.work_orders.find_one_and_update(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        {
            "$set": {
                "is_active": False,
                "deleted_at": now,
                "deleted_by": user_id,
                "updated_at": now,
                "updated_by": user_id,
            },
            "$inc": {"labors_rev": 1},
        },
        return_document=ReturnDocument.BEFORE,
    )
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 200

    warnings = []
    labors = wo.get("labors") or []
    # Смета склад не списывала и коры не заводила (создание, Save сметы,
    # WO → смета возвращает всё): её удалению возвращать нечего — иначе
    # склад и учёт коров росли бы на детали, которые не уходили с полки.
    is_estimate = (wo.get("status") or "open").strip().lower() in ("estimate", "estimated", "quote", "quoted")
    restore_result = {"restored": [], "errors": []}
    if not is_estimate:
        try:
            # ✅ Restore parts to inventory
            restore_result = restore_parts_to_inventory(
                shop_db, labors, user_id,
                ref={"kind": "work_order", "id": wo["_id"], "label": str(wo.get("wo_number") or "")},
            )
        except Exception:
            current_app.logger.exception("WO %s deleted, but restoring its parts to inventory failed", wo_id)
            warnings.append("Deleted, but stock levels could not be restored. Please check the parts in inventory.")

    core_sync = {"changes": [], "errors": []}
    if not is_estimate:
        try:
            # Remove cores generated by this work order unpaid-core logic.
            core_sync = sync_work_order_cores(shop_db, shop, labors, [], user_id)
        except Exception:
            current_app.logger.exception("WO %s deleted, but the core sync failed", wo_id)
            warnings.append("Deleted, but core charges could not be synced. Please check cores.")

    payments_deleted = attachments_deleted = 0
    try:
        # Delete all payments associated with this work order
        payments_deleted = shop_db.work_order_payments.delete_many({"work_order_id": wo_id}).deleted_count

        # Delete all attachments associated with this work order:
        #   - attachments on the WO itself        (entity_type="work_order",         entity_id=wo_id)
        #   - attachments on labor blocks         (entity_type="work_order_labor",   parent_id=wo_id)
        #   - attachments on payments             (entity_type="work_order_payment", parent_id=wo_id)
        attachments_deleted = shop_db.attachments.delete_many({
            "$or": [
                {"entity_type": "work_order", "entity_id": wo_id},
                {"entity_type": {"$in": ["work_order_labor", "work_order_payment"]}, "parent_id": wo_id},
            ]
        }).deleted_count
    except Exception:
        current_app.logger.exception("WO %s deleted, but its payments/attachments cleanup failed", wo_id)
        warnings.append("Deleted, but payments or attachments could not be cleaned up.")

    try:
        # Записи календаря, привязанные к этому WO, остаются обычными записями.
        from app.blueprints.calendar.services.work_orders import unlink_work_order_events

        unlink_work_order_events(shop_db, shop, wo_id)
    except Exception:
        current_app.logger.exception("WO %s deleted, but unlinking calendar events failed", wo_id)
        warnings.append("Deleted, but linked calendar events could not be updated.")
    audit_log.log_event("delete", "work_order", wo_id, label=audit_log.wo_label(wo),
                        details={"payments_deleted": payments_deleted, "attachments_deleted": attachments_deleted,
                                 "jobs": audit_log.labor_summary(wo)})

    return jsonify({
        "ok": True,
        "inventory_restored": len(restore_result.get("restored") or []) > 0,
        "inventory_changes": restore_result.get("restored") or [],
        "cores_synced": len(core_sync.get("changes") or []) > 0,
        "core_changes": core_sync.get("changes") or [],
        "core_sync_errors": core_sync.get("errors") or [],
        "payments_deleted": payments_deleted,
        "attachments_deleted": attachments_deleted,
        "warnings": warnings,
    }), 200


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def _write_customer_contacts(shop_db, customer, contacts):
    """
    Записать contacts кастомера + производные: легаси-поля главного контакта
    (first_name/phone/email на корне документа) и search_terms — контакты
    участвуют в поиске кастомеров.
    """
    from app.utils.contacts import build_customer_legacy_contact_fields
    from app.utils.entity_search import build_customer_search_terms

    normalized = normalize_contacts(contacts)
    update = {"contacts": normalized}
    update.update(build_customer_legacy_contact_fields(normalized))
    update["search_terms"] = build_customer_search_terms({**customer, **update})
    shop_db.customers.update_one({"_id": customer["_id"]}, {"$set": update})
    return normalized


def _save_new_contact_to_customer(shop_db, customer_id, new_contact):
    """Append a new contact to a customer's contacts array."""
    if not isinstance(new_contact, dict):
        return
    email = str(new_contact.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return

    customer = shop_db.customers.find_one({"_id": customer_id})
    if not customer:
        return

    existing = get_contacts(customer, entity_type="customer")
    # Don't add duplicate emails
    if any(c.get("email", "").lower() == email for c in existing):
        return

    existing.append({
        "first_name": str(new_contact.get("first_name") or "").strip(),
        "last_name": str(new_contact.get("last_name") or "").strip(),
        "phone": str(new_contact.get("phone") or "").strip(),
        "email": email,
        "is_main": False,
    })

    _write_customer_contacts(shop_db, customer, existing)


def _find_contact_index(contacts: list[dict], index, original_email: str):
    """
    Контакт в списке по индексу с проверкой email (клиент видит нормализованный
    get_contacts-список, индексы совпадают); если документ успел измениться —
    fallback-поиск по email.
    """
    if isinstance(index, int) and 0 <= index < len(contacts):
        if str(contacts[index].get("email") or "").lower() == original_email:
            return index
    for i, c in enumerate(contacts):
        if str(c.get("email") or "").lower() == original_email:
            return i
    return None


@work_orders_bp.post("/work_orders/api/customers/<customer_id>/email-contacts/update")
@login_required
@permission_required("work_orders.manage_email_contacts")
def api_update_email_contact(customer_id):
    """Правка контакта кастомера из email-модалки WO."""
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    cid = oid(customer_id)
    customer = shop_db.customers.find_one({"_id": cid, "shop_id": shop["_id"]}) if cid else None
    if not customer:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    data = request.get_json(silent=True) or {}
    original_email = str(data.get("original_email") or "").strip().lower()
    new_email = str(data.get("email") or "").strip().lower()
    if not original_email:
        return jsonify({"ok": False, "error": "original_email required"}), 400
    if not new_email or "@" not in new_email:
        return jsonify({"ok": False, "error": "Valid email address required"}), 400

    contacts = get_contacts(customer, entity_type="customer")
    idx = _find_contact_index(contacts, data.get("index"), original_email)
    if idx is None:
        return jsonify({"ok": False, "error": "Contact not found"}), 404
    if any(i != idx and str(c.get("email") or "").lower() == new_email for i, c in enumerate(contacts)):
        return jsonify({"ok": False, "error": "Another contact already uses this email"}), 400

    contacts[idx] = {
        **contacts[idx],
        "first_name": str(data.get("first_name") or "").strip(),
        "last_name": str(data.get("last_name") or "").strip(),
        "phone": str(data.get("phone") or "").strip(),
        "email": new_email,
    }
    normalized = _write_customer_contacts(shop_db, customer, contacts)
    return jsonify({"ok": True, "contacts": normalized})


@work_orders_bp.post("/work_orders/api/customers/<customer_id>/email-contacts/delete")
@login_required
@permission_required("work_orders.manage_email_contacts")
def api_delete_email_contact(customer_id):
    """Удаление контакта кастомера из email-модалки WO."""
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    cid = oid(customer_id)
    customer = shop_db.customers.find_one({"_id": cid, "shop_id": shop["_id"]}) if cid else None
    if not customer:
        return jsonify({"ok": False, "error": "Customer not found"}), 404

    data = request.get_json(silent=True) or {}
    original_email = str(data.get("original_email") or "").strip().lower()
    if not original_email:
        return jsonify({"ok": False, "error": "original_email required"}), 400

    contacts = get_contacts(customer, entity_type="customer")
    idx = _find_contact_index(contacts, data.get("index"), original_email)
    if idx is None:
        return jsonify({"ok": False, "error": "Contact not found"}), 404

    contacts.pop(idx)
    # normalize_contacts сам назначит главного из оставшихся, если удалили main.
    normalized = _write_customer_contacts(shop_db, customer, contacts)
    return jsonify({"ok": True, "contacts": normalized})


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/download-pdf")
@login_required
@permission_required("work_orders.export_pdf")
def api_download_work_order_pdf(work_order_id):
    # PDF содержит все цены — недоступен без права видеть стоимости.
    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    ctx = _build_wo_pdf_context(shop_db, shop, wo)
    pdf_html = render_template("emails/work_order_pdf.html", **ctx)

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        return jsonify({"ok": False, "error": "PDF generation failed"}), 500

    wo_number = ctx["wo_number"]
    doc_prefix = "Estimate" if ctx.get("is_estimate") else "WorkOrder"
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{doc_prefix}-{wo_number}.pdf"'
    return resp


# ---------------------------------------------------------------------------
# Email routes
# ---------------------------------------------------------------------------

@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/send-email")
@login_required
@permission_required("work_orders.send_to_customer")
def api_send_work_order_email(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    data = request.get_json(silent=True) or {}

    # Accept either single "email" or "emails" array
    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False, "error": "At least one valid email address required"}), 400

    # Optionally save new contact to customer
    new_contact = data.get("new_contact")
    if isinstance(new_contact, dict) and wo.get("customer_id"):
        _save_new_contact_to_customer(shop_db, wo["customer_id"], new_contact)

    shared_ctx = _build_wo_pdf_context(shop_db, shop, wo)
    wo_number = shared_ctx["wo_number"]
    shop_name = shared_ctx["shop_name"]

    # Add a customer-portal link so the recipient can browse all their data.
    try:
        if wo.get("customer_id"):
            from app.blueprints.customer_portal.routes import (
                get_or_create_portal_token,
                _customer_portal_url,
            )
            _portal_doc = get_or_create_portal_token(shop, wo["customer_id"])
            shared_ctx["portal_url"] = _customer_portal_url(_portal_doc["token"])
    except Exception:
        pass

    html_body = render_template("emails/work_order_email.html", **shared_ctx)
    pdf_html = render_template("emails/work_order_pdf.html", **shared_ctx)

    doc_label = shared_ctx.get("doc_type_label") or "Work Order"
    subject = f"{doc_label} #{wo_number} — {shop_name}" if shop_name else f"{doc_label} #{wo_number}"

    try:
        pdf_bytes = render_html_to_pdf(pdf_html)
    except Exception:
        pdf_bytes = None

    attachments = None
    if pdf_bytes:
        doc_prefix = "Estimate" if shared_ctx.get("is_estimate") else "WorkOrder"
        attachments = [{
            "filename": f"{doc_prefix}-{wo_number}.pdf",
            "data": pdf_bytes,
            "content_type": "application/pdf",
        }]

    user_email = (g.user.get("email") or "").strip() if g.user else ""

    try:
        send_email(to_emails, subject, html_body, attachments=attachments,
                   reply_to=user_email or None)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    audit_log.log_event("send", "work_order", wo_id, label=audit_log.wo_label(wo),
                        summary=f"{audit_log.wo_label(wo)} e-mailed to {', '.join(to_emails)}", details={"to": to_emails})
    return jsonify({"ok": True, "sent_to": to_emails}), 200


@work_orders_bp.post("/work_orders/api/payments/<payment_id>/send-receipt")
@login_required
@permission_required("work_orders.send_to_customer")
def api_send_payment_receipt(payment_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    pay_id = oid(payment_id)
    if not pay_id:
        return jsonify({"ok": False, "error": "Invalid payment ID"}), 400

    payment = shop_db.work_order_payments.find_one({"_id": pay_id, "is_active": True})
    if not payment:
        return jsonify({"ok": False, "error": "Payment not found"}), 404

    data = request.get_json(silent=True) or {}

    # Accept either single "email" or "emails" array
    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False, "error": "At least one valid email address required"}), 400

    wo = shop_db.work_orders.find_one({"_id": payment.get("work_order_id")}) or {}

    # Optionally save new contact to customer
    new_contact = data.get("new_contact")
    if isinstance(new_contact, dict) and wo.get("customer_id"):
        _save_new_contact_to_customer(shop_db, wo["customer_id"], new_contact)

    customer = shop_db.customers.find_one({"_id": wo.get("customer_id")}) or {}
    unit = shop_db.units.find_one({"_id": wo.get("unit_id")}) or {}

    cust_name = customer_label(customer)
    unit_lbl = unit_label(unit)

    shop_name = str(shop.get("name") or "").strip()
    addr_full = str(shop.get("address") or "").strip()
    addr_parts = [shop.get("address_line"), shop.get("city"), shop.get("state"), shop.get("zip")]
    shop_address = addr_full or ", ".join(str(p).strip() for p in addr_parts if p and str(p).strip())
    contact_parts = [str(shop.get("phone") or "").strip(), str(shop.get("email") or "").strip()]
    shop_contact = " · ".join(p for p in contact_parts if p)

    pay_date = payment.get("payment_date") or payment.get("created_at")
    pay_date_label = format_date_mmddyyyy(pay_date) if pay_date else ""

    amount = round2(payment.get("amount") or 0)
    method = str(payment.get("payment_method") or "cash").replace("_", " ").title()
    notes = str(payment.get("notes") or "").strip()
    payment_ref = str(pay_id)[-8:].upper()

    paid_total = _sum_active_work_order_payments(shop_db, payment.get("work_order_id"))
    summary = _build_work_order_payment_summary(wo, paid_total)

    pdf_cfg = shop_db.pdf_design.find_one({"shop_id": shop["_id"]}) or {}
    shop_logo_url = ""
    if pdf_cfg.get("show_logo", True) and shop.get("logo_data"):
        ct = shop.get("logo_content_type") or "image/png"
        b64 = base64.b64encode(bytes(shop["logo_data"])).decode("ascii")
        shop_logo_url = f"data:{ct};base64,{b64}"

    html_body = render_template(
        "emails/payment_receipt_email.html",
        shop_name=shop_name,
        shop_address=shop_address,
        shop_contact=shop_contact,
        shop_logo_url=shop_logo_url,
        wo_number=str(wo.get("wo_number") or ""),
        pay_date_label=pay_date_label,
        cust_name=cust_name,
        unit_label=unit_lbl,
        amount=amount,
        method=method,
        notes=notes,
        payment_ref=payment_ref,
        grand_total=summary["grand_total"],
        paid_total=summary["paid_amount"],
        remaining=summary["remaining_balance"],
        is_fully_paid=summary["is_fully_paid"],
    )

    wo_number = str(wo.get("wo_number") or "")
    subject = (
        f"Payment Receipt — WO #{wo_number} — {shop_name}"
        if wo_number
        else f"Payment Receipt — {shop_name}"
    )

    user_email = (g.user.get("email") or "").strip() if g.user else ""

    try:
        send_email(to_emails, subject, html_body,
                   reply_to=user_email or None)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    audit_log.log_event("send", "work_order", wo.get("_id"), label=audit_log.wo_label(wo),
                        summary=f"Payment receipt for {audit_log.wo_label(wo)} e-mailed to {', '.join(to_emails)}",
                        details={"payment_id": str(pay_id), "to": to_emails})
    return jsonify({"ok": True, "sent_to": to_emails}), 200


# ---------------------------------------------------------------------------
# Customer Authorization (email-based approve/decline)
# ---------------------------------------------------------------------------









@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/send-authorization")
@login_required
# Запрос авторизации у клиента — часть работы над WO: доступен и механику
# (create), который сам просит одобрить найденную работу. PDF/чеки с ценами
# (send-email, send-receipt) — только work_orders.send_to_customer.
@permission_required_any("work_orders.send_to_customer", "work_orders.create")
def api_send_authorization_email(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "Work order not found"}), 404

    data = request.get_json(silent=True) or {}

    raw_emails = data.get("emails") or []
    if not raw_emails:
        single = str(data.get("email") or "").strip().lower()
        if single:
            raw_emails = [single]
    to_emails = [e.strip().lower() for e in raw_emails if isinstance(e, str) and "@" in e.strip()]
    if not to_emails:
        return jsonify({"ok": False, "error": "At least one valid email address required"}), 400

    scope = str(data.get("scope") or "work_order").strip().lower()
    if scope not in ("work_order", "labor"):
        return jsonify({"ok": False, "error": "Invalid scope"}), 400

    labor_index = None
    if scope == "labor":
        try:
            labor_index = int(data.get("labor_index"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "labor_index required for scope=labor"}), 400
        labors_count = len(wo.get("labors") or [])
        if labor_index < 0 or labor_index >= labors_count:
            return jsonify({"ok": False, "error": "labor_index out of range"}), 400

    new_contact = data.get("new_contact")
    if isinstance(new_contact, dict) and wo.get("customer_id"):
        _save_new_contact_to_customer(shop_db, wo["customer_id"], new_contact)

    ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
    wo_number = ctx["wo_number"]
    shop_name = ctx["shop_name"]

    # Build a PDF of the full work order to attach (same as regular send-email).
    pdf_attachment = None
    try:
        pdf_html = render_template("emails/work_order_pdf.html", **ctx)
        pdf_bytes = render_html_to_pdf(pdf_html)
        if pdf_bytes:
            doc_prefix = "Estimate" if ctx.get("is_estimate") else "WorkOrder"
            pdf_attachment = {
                "filename": f"{doc_prefix}-{wo_number}.pdf",
                "data": pdf_bytes,
                "content_type": "application/pdf",
            }
    except Exception:
        pdf_attachment = None

    # WO/labor uploaded attachments (already capped in size).
    uploaded_attachments = ctx.get("attachment_files") or []

    email_attachments = []
    if pdf_attachment:
        email_attachments.append(pdf_attachment)
    for att in uploaded_attachments:
        email_attachments.append({
            "filename": att["filename"],
            "data": att["data"],
            "content_type": att.get("content_type") or "application/octet-stream",
        })

    db_name = str(shop.get("db_name") or "")
    auth_col = _authorizations_collection()
    user_email = (g.user.get("email") or "").strip() if g.user else ""
    user_id = g.user.get("_id") if g.user else None
    now = utcnow()

    sent_to = []
    failed = []
    for email in to_emails:
        token = secrets.token_urlsafe(32)
        auth_doc = {
            "token": token,
            "shop_id": shop["_id"],
            "db_name": db_name,
            "work_order_id": wo_id,
            "wo_number": wo_number,
            "scope": scope,
            "labor_index": labor_index,
            "recipient_email": email,
            "customer_id": wo.get("customer_id"),
            "status": "pending",
            "created_at": now,
            "created_by": user_id,
            "responded_at": None,
            "response_ip": None,
            "response_comment": None,
        }
        try:
            auth_col.insert_one(auth_doc)
        except Exception:
            failed.append(email)
            continue

        approve_url = url_for("work_orders.public_authorization_page",
                              token=token, _external=True)

        email_ctx = dict(ctx)
        email_ctx["approve_url"] = approve_url
        email_ctx["recipient_email"] = email
        try:
            if wo.get("customer_id"):
                from app.blueprints.customer_portal.routes import (
                    get_or_create_portal_token,
                    _customer_portal_url,
                )
                _portal_doc = get_or_create_portal_token(shop, wo["customer_id"])
                email_ctx["portal_url"] = _customer_portal_url(_portal_doc["token"])
        except Exception:
            pass
        html_body = render_template("emails/authorization_email.html", **email_ctx)

        if scope == "labor":
            subject_prefix = f"Authorization needed — Work Order #{wo_number} (Labor #{(labor_index or 0) + 1})"
        else:
            subject_prefix = f"Authorization needed — Work Order #{wo_number}"
        subject = f"{subject_prefix} — {shop_name}" if shop_name else subject_prefix

        try:
            send_email([email], subject, html_body,
                       attachments=email_attachments or None,
                       reply_to=user_email or None)
            sent_to.append(email)
        except RuntimeError:
            # Cleanup the token we just created so user isn't left with a
            # dangling record.
            auth_col.delete_one({"token": token})
            failed.append(email)

    if not sent_to:
        return jsonify({"ok": False, "error": "Failed to send authorization email",
                        "failed": failed}), 500

    audit_log.log_event("send", "authorization", wo_id, label=audit_log.wo_label(wo),
                        summary=f"Authorization request for {audit_log.wo_label(wo)} sent to {', '.join(sent_to)}",
                        details={"scope": scope, "to": sent_to})
    return jsonify({"ok": True, "sent_to": sent_to, "failed": failed}), 200


# ---------------------------------------------------------------------------
# Describe Issue — AI polish + per-labor save
# ---------------------------------------------------------------------------

@work_orders_bp.post("/work_orders/api/ai/polish-issue")
@login_required
@permission_required("work_orders.create")
def api_polish_issue_description():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Text is empty."}), 400
    try:
        result = polish_issue_description(text)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"AI request failed: {exc}"}), 500
    return jsonify({"ok": True, **result}), 200


@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/labors/<int:labor_index>/issue-description")
@login_required
@permission_required("work_orders.edit")
def api_save_labor_issue_description(work_order_id, labor_index):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "Shop not found"}), 404

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "Invalid work order ID"}), 400

    # Новый JS шлёт labor_id строки (запись по id, а не по позиции), старый —
    # только индекс в URL (CAS по ревизии). См. office_merge.save_labor_issue_description.
    data = request.get_json(silent=True)
    payload, code = office_merge.save_labor_issue_description(
        shop_db, shop, wo_id, labor_index, data if isinstance(data, dict) else {},
        user_id=current_user_id(),
    )
    return jsonify(payload), code




@work_orders_bp.get("/authorize/<token>")
def public_authorization_page(token):
    auth_doc, shop_db, shop, wo, err = _resolve_token(token)
    if err and not (auth_doc and shop_db and shop and wo):
        return render_template("public/work_orders/authorization_page.html",
                               error=err, auth=None, ctx=None), 404

    scope = auth_doc.get("scope") or "work_order"
    labor_index = auth_doc.get("labor_index")
    ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
    return render_template(
        "public/work_orders/authorization_page.html",
        error=None,
        auth=auth_doc,
        ctx=ctx,
        token=token,
        already_responded=auth_doc.get("status") in ("approved", "declined"),
    )


@work_orders_bp.post("/authorize/<token>")
@csrf.exempt
def public_authorization_submit(token):
    auth_doc, shop_db, shop, wo, err = _resolve_token(token)
    if err and not (auth_doc and shop_db and shop and wo):
        return render_template("public/work_orders/authorization_page.html",
                               error=err, auth=None, ctx=None), 404

    if auth_doc.get("status") in ("approved", "declined"):
        # Already responded — re-render the page.
        scope = auth_doc.get("scope") or "work_order"
        labor_index = auth_doc.get("labor_index")
        ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
        return render_template(
            "public/work_orders/authorization_page.html",
            error=None,
            auth=auth_doc,
            ctx=ctx,
            token=token,
            already_responded=True,
        )

    decision = (request.form.get("decision") or "").strip().lower()
    if decision not in ("approve", "decline"):
        flash("Please choose Approve or Decline.", "error")
        return redirect(url_for("work_orders.public_authorization_page", token=token))

    comment = (request.form.get("comment") or "").strip()[:2000]
    # Best-effort client IP: honor X-Forwarded-For first hop when present.
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")
    client_ip = (forwarded[0].strip() if forwarded and forwarded[0].strip()
                 else (request.remote_addr or ""))
    new_status = "approved" if decision == "approve" else "declined"
    now = utcnow()

    auth_col = _authorizations_collection()
    auth_col.update_one(
        {"_id": auth_doc["_id"], "status": "pending"},
        {"$set": {
            "status": new_status,
            "responded_at": now,
            "response_ip": client_ip,
            "response_comment": comment,
            "updated_at": now,
        }},
    )

    # Mirror onto the work order itself for easy display in shop UI.
    scope = auth_doc.get("scope") or "work_order"
    labor_index = auth_doc.get("labor_index")
    record = {
        "token": auth_doc.get("token"),
        "scope": scope,
        "labor_index": labor_index,
        "recipient_email": auth_doc.get("recipient_email"),
        "status": new_status,
        "responded_at": now,
        "response_ip": client_ip,
        "response_comment": comment,
    }
    shop_db.work_orders.update_one(
        {"_id": wo["_id"]},
        {"$push": {"authorizations": record}},
    )

    auth_doc = auth_col.find_one({"_id": auth_doc["_id"]}) or auth_doc
    _what = f"job #{int(labor_index) + 1}" if scope == "labor" and labor_index is not None else "the work order"
    audit_log.log_event("confirm" if new_status == "approved" else "update", "authorization", wo.get("_id"),
                        label=audit_log.wo_label(wo),
                        summary=f"Customer {new_status} {_what} on {audit_log.wo_label(wo)}",
                        details={"email": auth_doc.get("recipient_email"), "comment": comment, "ip": client_ip},
                        user={"name": f"Customer ({auth_doc.get('recipient_email') or 'portal'})"}, shop=shop, source="portal")
    ctx = _build_authorization_context(shop_db, shop, wo, scope, labor_index)
    return render_template(
        "public/work_orders/authorization_page.html",
        error=None,
        auth=auth_doc,
        ctx=ctx,
        token=token,
        already_responded=True,
        just_submitted=True,
    )


# ──────────────── WO Presets API ────────────────

@work_orders_bp.get("/work_orders/api/presets")
@login_required
@permission_required("work_orders.create")
def api_presets_list():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify([]), 200

    rows = list(
        shop_db.wo_presets.find(
            {"shop_id": shop["_id"], "is_active": True},
            {"name": 1},
        ).sort([("name", 1)])
    )
    return jsonify([{"id": str(r["_id"]), "name": r.get("name") or ""} for r in rows]), 200


@work_orders_bp.get("/work_orders/api/presets/<preset_id>")
@login_required
@permission_required("work_orders.create")
def api_preset_detail(preset_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"error": "no_shop"}), 400

    pid = oid(preset_id)
    if not pid:
        return jsonify({"error": "bad_id"}), 400

    doc = shop_db.wo_presets.find_one({"_id": pid, "shop_id": shop["_id"], "is_active": True})
    if not doc:
        return jsonify({"error": "not_found"}), 404

    # Enrich parts with current data from the parts collection.
    # Resolve by part_id first (stable); fall back to part_number if the id is
    # stale (part re-imported). Self-heal the stored snapshot so a preset never
    # keeps a non-existent part_number, and always serve dynamic cost/price.
    raw_parts = doc.get("parts") or []
    part_ids = []
    fallback_numbers = []
    for p in raw_parts:
        pid_str = p.get("part_id")
        if pid_str:
            o = oid(pid_str)
            if o:
                part_ids.append(o)
        pn = str(p.get("part_number") or "").strip()
        if pn:
            fallback_numbers.append(pn)

    proj = {
        "_id": 1,
        "part_number": 1,
        "average_cost": 1,
        "core_has_charge": 1,
        "core_cost": 1,
        "misc_has_charge": 1,
        "misc_charges": 1,
        "has_selling_price": 1,
        "selling_price": 1,
        "unit_of_measure": 1,
    }

    by_id = {}
    if part_ids:
        for pdoc in shop_db.parts.find(
            {"_id": {"$in": part_ids}, "is_active": True}, proj
        ):
            by_id[str(pdoc["_id"])] = pdoc

    by_number = {}
    if fallback_numbers:
        for pdoc in shop_db.parts.find(
            {"part_number": {"$in": fallback_numbers}, "is_active": True}, proj
        ):
            key = str(pdoc.get("part_number") or "").strip()
            if key and key not in by_number:
                by_number[key] = pdoc

    patches = {}  # "parts.<i>.<field>" -> value; persisted back to heal the doc
    enriched_parts = []
    for idx, p in enumerate(raw_parts):
        ep = dict(p)
        pid_str = p.get("part_id")
        live = by_id.get(pid_str) if pid_str else None

        # Stale/missing part_id but a matching part_number is still in catalog.
        if live is None:
            pn_key = str(p.get("part_number") or "").strip()
            if pn_key and pn_key in by_number:
                live = by_number[pn_key]
                fresh_id = str(live.get("_id"))
                ep["part_id"] = fresh_id
                if pid_str != fresh_id:
                    patches[f"parts.{idx}.part_id"] = fresh_id

        if live is not None:
            # Self-heal the stored part_number if the part was renumbered.
            live_pn = str(live.get("part_number") or "").strip()
            stored_pn = str(p.get("part_number") or "").strip()
            if live_pn and live_pn != stored_pn:
                ep["part_number"] = live_pn
                patches[f"parts.{idx}.part_number"] = live_pn

            ep["core_has_charge"] = bool(live.get("core_has_charge"))
            ep["core_cost"] = float(live.get("core_cost") or 0)
            misc_items = []
            for m in (live.get("misc_charges") or []):
                if isinstance(m, dict):
                    misc_items.append({
                        "description": str(m.get("description") or "").strip(),
                        "price": float(m.get("price") or 0),
                        "taxable": m.get("taxable", True),
                    })
            ep["misc_has_charge"] = bool(live.get("misc_has_charge"))
            ep["misc_charges"] = misc_items
            # Единица измерения из каталога — подпись у количества строки из пресета
            ep.update(part_unit_fields(live))

            # Dynamic pricing: always reflect the part's current cost/price.
            # A price manually pinned in the preset wins over the part's selling
            # price and the pricing matrix (customer-level override_part_selling_price
            # still takes precedence — the WO front-end nulls the price then).
            if live.get("average_cost") is not None:
                ep["cost"] = float(live["average_cost"])
            if bool(p.get("price_overridden")) and p.get("price") is not None:
                ep["price"] = float(p.get("price") or 0)
            elif bool(live.get("has_selling_price")):
                ep["price"] = float(live.get("selling_price") or 0)
            else:
                # No fixed selling price -> let the WO markup recompute from cost.
                ep["price"] = None
        enriched_parts.append(ep)

    # Persist healed identity snapshots (fresh part_id / current part_number) so
    # the stored preset never drifts to a non-existent part_number.
    if patches:
        try:
            shop_db.wo_presets.update_one({"_id": doc["_id"]}, {"$set": patches})
        except Exception:
            pass

    # Механик-режим: пресет отдаём без денег (цены проставит сервер при
    # сохранении WO) — только парт/описание/количество.
    if not has_permission("work_orders.view_costs"):
        money_keys = ("cost", "price", "price_overridden", "core_has_charge", "core_cost", "misc_has_charge", "misc_charges")
        enriched_parts = [
            {k: v for k, v in ep.items() if k not in money_keys}
            for ep in enriched_parts
        ]

    return jsonify({
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "labor_hours": doc.get("labor_hours"),
        "labor_rate_code": doc.get("labor_rate_code"),
        "allow_discount": bool(doc.get("allow_discount")),
        "parts": enriched_parts,
    }), 200

# -------------------- MECHANIC MODE (безденежные эндпоинты + таймеры) --------------------
# Общий API для веб-страницы механика и мобильного приложения. Доступны любому
# пользователю с соответствующим правом (менеджерам тоже) — просто никогда не
# возвращают цены/тоталы. Логика — в services/mechanic_view.py,
# services/mechanic_editor.py и services/time_tracking.py.

from app.blueprints.work_orders.services.mechanic_view import (
    active_unit_work_order_message,
    find_active_unit_work_order,
    mechanic_rev_payload,
    mechanic_unit_history,
    mechanic_wo_payload,
    strip_part_search_item,
    strip_wo_list_item,
    wo_labors_rev,
)
from app.blueprints.work_orders.services.mechanic_editor import (
    odometer_moves_forward,
    build_mechanic_labors_payload,
    parse_mileage,
)
from app.blueprints.work_orders.services.mechanic_done import (
    clear_mechanic_done_fields,
    mechanic_done_fields,
    personalize_wo_list_item,
)
from app.blueprints.work_orders.services import concurrent_save
from app.blueprints.work_orders.services import time_tracking
from app.blueprints.work_orders.services.edit_views import edit_session_id, with_rev


def _server_now_iso() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


def _current_user_display_name() -> str:
    user = get_master_db().users.find_one(
        {"_id": current_user_id()},
        {"first_name": 1, "last_name": 1, "name": 1, "email": 1},
    ) or {}
    full = f"{str(user.get('first_name') or '').strip()} {str(user.get('last_name') or '').strip()}".strip()
    return full or str(user.get("name") or "").strip() or str(user.get("email") or "").strip()


@work_orders_bp.get("/work_orders/api/mechanic/work_orders")
@login_required
@permission_required("work_orders.view")
def api_mechanic_work_orders():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    paid_status = status if status in ("in_progress",) else ("unpaid" if status == "open" else "all")
    # Механик видит только работу в процессе: open в этой системе = «закончен,
    # ждёт менеджера/оплаты», paid скрыт всегда. Форсим серверно независимо от
    # параметров клиента (in_progress включает и mechanic_done-WO — механик
    # видит свой законченный WO до подтверждения менеджером).
    if is_mechanic_mode():
        paid_status = "in_progress"
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    items, pagination, _totals = get_work_orders_list(
        shop_db, shop["_id"], page, per_page, q=q, paid_status=paid_status,
    )

    running = time_tracking.get_running_timer(shop_db, shop, current_user_id())
    running_wo_id = str(running.get("work_order_id")) if running else ""

    out_items = []
    for item in items:
        stripped = personalize_wo_list_item(strip_wo_list_item(item), current_user_id())
        stripped["my_timer_running"] = bool(running_wo_id) and item.get("id") == running_wo_id
        out_items.append(stripped)

    return jsonify({
        "ok": True,
        "items": out_items,
        "pagination": pagination,
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.get("/work_orders/api/mechanic/work_orders/<work_order_id>")
@login_required
@permission_required("work_orders.view")
def api_mechanic_work_order_details(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    # Сметы механик не редактирует (иначе автосейв конвертировал бы их в WO).
    if (wo.get("status") or "open") == "estimate" and is_mechanic_mode():
        return jsonify({"ok": False, "error": "estimate_locked_for_mechanics"}), 400

    # Подтверждённый менеджером WO закрыт для механика полностью.
    if wo.get("manager_confirmed") and is_mechanic_mode():
        return jsonify({
            "ok": False,
            "error": "wo_confirmed",
            "message": "This work order was confirmed by a manager and is locked.",
        }), 403

    # Детали строятся из документа после ensure_wo_labor_ids, и из него же —
    # load-вьюха (база трёхстороннего merge следующего Save этой страницы).
    # Вьюху пишем только тому, кто этим каналом может сохранить WO.
    mechanic_mode = is_mechanic_mode()
    payload = mechanic_wo_payload(shop_db, shop, wo, current_user_id(), view={
        "channel": "mechanic_web",
        "sid": edit_session_id(),
        "protocol": 1 if request.args.get("mp") == "1" else 0,
        "can_edit": has_permission("work_orders.create") and (mechanic_mode or has_permission("work_orders.edit")),
    })
    # Механик обязан подтвердить пробег до работы с WO (менеджера на этой
    # странице не спрашиваем).
    payload["mileage_required"] = bool(wo.get("unit_id")) and mechanic_mode
    payload["server_now"] = _server_now_iso()
    return jsonify(payload), 200


@work_orders_bp.get("/work_orders/api/mechanic/work_orders/<work_order_id>/rev")
@login_required
@permission_required("work_orders.view")
def api_mechanic_work_order_rev(work_order_id):
    """Дешёвый опрос страницы механика: сдвинулась ли ревизия WO (labors_rev).
    Вьюх не пишет. Механику статус не отдаётся (paid — никогда): только
    locked и его Done."""
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        with_rev({"status": 1, "manager_confirmed": 1, "mechanic_done": 1, "mechanic_done_marks": 1}),
    )
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404
    return jsonify(mechanic_rev_payload(wo, current_user_id(), mechanic=is_mechanic_mode())), 200


@work_orders_bp.get("/work_orders/api/mechanic/units/<unit_id>/history")
@login_required
@permission_required("work_orders.view")
def api_mechanic_unit_history(unit_id):
    """История юнита для механика: прошлые WO без цен (работы + запчасти)."""
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    uid = oid(unit_id)
    if not uid:
        return jsonify({"ok": False, "error": "invalid_unit_id"}), 400

    unit = shop_db.units.find_one({"_id": uid, "shop_id": shop["_id"]})
    if not unit:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    exclude_wo_id = oid((request.args.get("exclude") or "").strip())
    payload = mechanic_unit_history(shop_db, shop, unit, exclude_wo_id=exclude_wo_id)
    return jsonify({"ok": True, **payload}), 200


@work_orders_bp.get("/work_orders/api/mechanic/units/<unit_id>/active_work_order")
@login_required
@permission_required("work_orders.view")
def api_mechanic_unit_active_work_order(unit_id):
    """
    WO, над которым сейчас работают по этому юниту (in_progress, не
    подтверждён менеджером), с именами механиков с идущим таймером.
    Страница механика и мобильное приложение зовут его при выборе юнита
    (VIN-скан или руками): есть такой WO — открывают его вместо создания
    нового. {"work_order": null} — юнит свободен.
    """
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    uid = oid(unit_id)
    if not uid:
        return jsonify({"ok": False, "error": "invalid_unit_id"}), 400

    unit = shop_db.units.find_one({"_id": uid, "shop_id": shop["_id"]}, {"_id": 1})
    if not unit:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    payload = find_active_unit_work_order(shop_db, shop, unit["_id"])
    return jsonify({
        "ok": True,
        "work_order": payload,
        "message": active_unit_work_order_message(payload),
    }), 200


@work_orders_bp.post("/work_orders/api/mechanic/work_orders")
@login_required
@permission_required("work_orders.create")
def api_mechanic_work_order_create():
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    customer_id = oid(data.get("customer_id"))
    unit_id = oid(data.get("unit_id"))
    # WO создаётся из записи календаря («Start work» у механика) — запись
    # привяжем к WO, её содержимое (пресеты, бригада, заметка) — в новый WO.
    calendar_event_id = oid(data.get("calendar_event_id"))
    if not customer_id or not unit_id:
        return jsonify({"ok": False, "error": "customer_unit_required"}), 400

    customer = shop_db.customers.find_one({"_id": customer_id, "shop_id": shop["_id"], "is_active": True})
    if not customer:
        return jsonify({"ok": False, "error": "customer_not_found"}), 404
    unit = shop_db.units.find_one({"_id": unit_id, "shop_id": shop["_id"], "is_active": True})
    if not unit or unit.get("customer_id") != customer_id:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    # Один юнит в цеху — один WO: механик не заводит дубль, пока по юниту
    # идёт работа (страховка к проверке при выборе юнита на клиенте).
    # allow_duplicate_unit_wo — явный обход для автоматизации/тестов;
    # интерфейсы механика его не шлют.
    if is_mechanic_mode() and not bool(data.get("allow_duplicate_unit_wo")):
        existing = find_active_unit_work_order(shop_db, shop, unit_id)
        if existing:
            if calendar_event_id:
                # Старт работы из записи, а по юниту WO уже идёт: запись
                # привязываем к нему — клиент откроет этот WO.
                from app.blueprints.calendar.services.work_orders import link_event_to_work_order

                link_event_to_work_order(shop_db, shop, calendar_event_id, existing.get("id"), existing.get("wo_number"))
            return jsonify({
                "ok": False,
                "error": "unit_has_active_work_order",
                "message": active_unit_work_order_message(existing),
                "work_order": existing,
            }), 409

    labors_payload = data.get("labors")

    # Старт работы из записи календаря: пресеты, бригада и заметка записи —
    # в новый WO (как в мобильном create), если запись про этот юнит или без
    # юнита; иначе запись лишь привяжем после вставки. Best effort: ошибки
    # предзаполнения логируются, WO создаётся всё равно.
    appointment = None
    server_built = False  # работы собраны сервером из пресетов записи
    if calendar_event_id:
        from app.blueprints.work_orders.services.appointment_prefill import (
            appointment_prefill_applies,
            appointment_preset_labors,
            apply_appointment_defaults,
            crew_assignments,
            load_appointment,
        )

        appointment = load_appointment(shop_db, shop, calendar_event_id)
        if not appointment_prefill_applies(appointment, unit_id):
            appointment = None
        if appointment and not labors_payload:
            labors_payload = appointment_preset_labors(shop_db, shop, customer_id, appointment["presets"])
            server_built = True

    # Без работ WO не создаём — кроме случая, когда у записи без пресетов есть
    # заметка: она станет строкой работы (см. apply_appointment_defaults).
    if not isinstance(labors_payload, list) or not (labors_payload or (appointment and appointment["note"])):
        return jsonify({"ok": False, "error": "labors_required"}), 400

    # Клиентские цены/часы/статус игнорируются; сервер автозаполняет цены.
    # Собранные из пресетов работы уже прошли через это — второй раз не гоняем.
    enriched = labors_payload if server_built else build_mechanic_labors_payload(shop_db, shop, customer_id, labors_payload)
    if appointment:
        # Бригада записи — на строки без назначений; заметка — в описание
        # проблемы, если его ещё никто не заполнил.
        set_note = server_built or not any(
            str(l.get("issue_description") or "").strip() for l in enriched if isinstance(l, dict)
        )
        enriched = apply_appointment_defaults(
            enriched, crew_assignments(shop, appointment["mechanics"]), appointment["note"], set_note,
            shop_db=shop_db, shop=shop, customer_id=customer_id,
        )
    labors, totals_raw = compute_labors_and_totals(shop_db, shop, enriched)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        _is_customer_taxable(shop_db, customer_id),
    )

    now = utcnow()
    user_id = current_user_id()

    wo_number = get_next_wo_number(shop_db, shop["_id"])
    inventory_result = deduct_parts_from_inventory(
        shop_db, labors, user_id,
        ref={"kind": "work_order", "label": str(wo_number)},
    )

    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "wo_number": wo_number,
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": "in_progress",
        "labors": labors,
        # Полночь по времени магазина — как у даты, введённой в офисе.
        "work_order_date": shop_local_date_to_utc(now),
        "totals": totals,
        "inventory_deducted": len(inventory_result["deducted"]) > 0,
        "inventory_deductions": inventory_result["deducted"],
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
        # Ревизия редактируемого состояния (CAS сохранений, edit_views.py).
        "labors_rev": 1,
        **mechanic_done_fields(shop_db, shop, None, data, user_id, now),
    }
    # Пробег: снимок на WO; одометр юнита — только вперёд.
    unit_mileage = parse_mileage(data.get("unit_mileage"))
    if unit_mileage is not None:
        doc["mileage"] = unit_mileage
        if odometer_moves_forward(unit.get("mileage"), unit_mileage):
            shop_db.units.update_one(
                {"_id": unit_id, "shop_id": shop["_id"], "is_active": True},
                {"$set": {"mileage": unit_mileage, "updated_at": now, "updated_by": user_id}},
            )
    res = shop_db.work_orders.insert_one(doc)
    audit_log.log_event("create", "work_order", res.inserted_id, label=audit_log.wo_label(doc),
                        summary=f"{audit_log.wo_label(doc)} created by mechanic for {audit_log.customer_label(customer)} ({audit_log.unit_label(unit)})",
                        details={"customer_id": str(customer_id), "customer": audit_log.customer_label(customer),
                                 "unit_id": str(unit_id), "unit": audit_log.unit_label(unit), "jobs": audit_log.labor_summary(doc),
                                 "calendar_event_id": str(calendar_event_id or "")})
    core_sync = sync_work_order_cores(shop_db, shop, [], labors, user_id)

    from app.blueprints.work_orders.services.push_events import notify_after_mechanic_save

    notify_after_mechanic_save(shop, None, doc, res.inserted_id, doc["wo_number"], user_id)

    if calendar_event_id:
        # Запись календаря теперь следует за этим WO (статус — по факту работы).
        from app.blueprints.calendar.services.work_orders import link_event_to_work_order

        link_event_to_work_order(shop_db, shop, calendar_event_id, res.inserted_id, doc["wo_number"])

    return jsonify({
        "ok": True,
        "id": str(res.inserted_id),
        "wo_number": doc["wo_number"],
        "status": "in_progress",
        "inventory_warnings": inventory_result.get("errors") or [],
        "core_sync_errors": core_sync.get("errors") or [],
        "labors_rev": doc["labors_rev"],
        # Новые строки с client_key клиента → их labor_id.
        "client_keys": {
            b["client_key"]: b["labor_id"] for b in labors if isinstance(b, dict) and b.get("client_key")
        },
    }), 200


@work_orders_bp.post("/work_orders/api/mechanic/work_orders/<work_order_id>")
@login_required
@permission_required("work_orders.create")
def api_mechanic_work_order_update(work_order_id):
    # До всего остального: base для merge выбирается по вьюхам, записанным
    # раньше начала этого запроса.
    request_started_at = utcnow()
    mechanic_mode = is_mechanic_mode()

    # Эндпоинт механик-режима, но доступен и менеджерам: для них правка
    # существующего WO — work_orders.edit, как на обычном апдейте.
    if not mechanic_mode and not has_permission("work_orders.edit"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.edit"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    # Merge с тем, что клиент видел, CAS по labors_rev, побочные эффекты после
    # записи — services/concurrent_save.py. Деньги клиента игнорируются.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    payload, code = concurrent_save.save_mechanic_edit(
        shop_db, shop, wo_id, data,
        user_id=current_user_id(),
        sid=edit_session_id(),
        request_started_at=request_started_at,
        channel="mechanic_web",
        mechanic_mode=mechanic_mode,
        guard=concurrent_save.mechanic_web_guard(data, mechanic_mode=mechanic_mode),
        cas_filter=(
            concurrent_save.MECHANIC_CAS_FILTER if mechanic_mode else concurrent_save.NON_MECHANIC_CAS_FILTER
        ),
        status="in_progress",
        taxable_from_request=False,
        audit_suffix="by mechanic",
        initial_wo=wo,
    )
    return jsonify(payload), code


# HTTP-статусы отказов таймера; тело — time_tracking.timer_error_payload
# (код + message: старые сборки приложения показывают message в тосте;
# текст механику — без статуса paid).
_TIMER_ERROR_HTTP_STATUS = {
    "work_order_not_found": 404,
    "labor_not_found": 404,
    "wo_confirmed": 403,
    "concurrent_update": 409,
}


def _refresh_wos_after_timer_stop(shop_db, shop, logs):
    """
    Закрытые сессии (стоп, auto_switch) поменяли время своих WO: assigned =
    кто фактически работал, часы работы = суммарное время механиков (кроме
    пресетных/ручных), totals пересчитаны; статус привязанных записей
    календаря — по факту работы. Бригаду по таймерам не двигаем: план
    остаётся планом, переезжает он только когда офис переназначил механика.
    Логов может быть несколько (фантомы после гонки start) — все их WO.
    """
    from app.blueprints.work_orders.services.mechanic_editor import refresh_time_derived_fields

    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    for stopped_wo_id in {l.get("work_order_id") for l in logs if l.get("work_order_id")}:
        refresh_time_derived_fields(
            shop_db, shop, stopped_wo_id, mechanics_by_id=mechanics_by_id,
        )
        _sync_calendar_with_wo(shop_db, shop, stopped_wo_id)


def _timer_start_error_response(error: str, stopped_prev_logs, *, mechanic_mode: bool):
    """Отказ старта. Если до отказа auto_switch уже остановил прежний таймер
    (старт сорвался гонкой после переключения) — сказать и об этом:
    пользователь иначе считал бы, что прежний таймер всё ещё идёт."""
    body = time_tracking.timer_error_payload(error, mechanic_mode=mechanic_mode)
    if stopped_prev_logs:
        prev = stopped_prev_logs[-1]
        body["stopped_previous"] = time_tracking._log_payload(prev)
        number = prev.get("wo_number")
        body["message"] += (
            f" Your timer on WO #{number} was stopped." if number not in (None, "")
            else " Your previous timer was stopped."
        )
    return jsonify(body), _TIMER_ERROR_HTTP_STATUS.get(error, 400)


@work_orders_bp.post("/work_orders/api/mechanic/timers/start")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timer_start():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    user_id = current_user_id()
    mechanic_mode = is_mechanic_mode()

    # Проверки — в start_timer: до переключения таймера и ещё раз атомарно с
    # фиксацией старта (после вставки лога, +labors_rev). Механику закрыты
    # подтверждённый менеджером WO и смета; менеджер трекает время и на
    # смете, не конвертируя её.
    log, stopped_prev_logs, error = time_tracking.start_timer(
        shop_db, shop, user_id, _current_user_display_name(),
        data.get("work_order_id"), data.get("labor_id"),
        mechanic_mode=mechanic_mode,
    )

    # auto_switch закрыл предыдущие сессии (после гонки их может быть
    # несколько на разных WO) — у тех WO обновились время, назначения и
    # биллинговые часы. Пересчёт и тогда, когда сам старт после
    # переключения сорвался.
    if stopped_prev_logs:
        _refresh_wos_after_timer_stop(shop_db, shop, stopped_prev_logs)
    if error:
        return _timer_start_error_response(error, stopped_prev_logs, mechanic_mode=mechanic_mode)

    stopped_prev = stopped_prev_logs[-1] if stopped_prev_logs else None
    audit_log.log_event("timer", "time_log", log.get("_id"), label=f"WO #{log.get('wo_number')}",
                        summary=f"Timer started on WO #{log.get('wo_number')}",
                        details={"work_order_id": str(log.get("work_order_id")), "labor_id": log.get("labor_id")},
                        url=f"/work_orders/details?work_order_id={log.get('work_order_id')}")
    return jsonify({
        "ok": True,
        "timer": time_tracking._log_payload(log),
        "stopped_previous": time_tracking._log_payload(stopped_prev) if stopped_prev else None,
        "time_summary": time_tracking.summarize_wo_time(
            shop_db, shop["_id"], log.get("work_order_id"), user_id=user_id
        ),
        # Ревизия WO после всех записей старта (снятие Done, пересчёт
        # предыдущих WO) — клиент не примет свой старт за чужую правку.
        "labors_rev": wo_labors_rev(shop_db, shop["_id"], log.get("work_order_id")),
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.post("/work_orders/api/mechanic/timers/stop")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timer_stop():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    user_id = current_user_id()
    logs, error = time_tracking.stop_timer(shop_db, shop, user_id)
    if error:
        return jsonify(time_tracking.timer_error_payload(error, mechanic_mode=is_mechanic_mode())), 400

    # Пересчёт всех затронутых WO (логов может быть несколько — фантомы
    # после гонки start); в ответ идёт последняя (актуальная) сессия.
    log = logs[-1]
    audit_log.log_event("timer", "time_log", log.get("_id"), label=f"WO #{log.get('wo_number')}",
                        summary=f"Timer stopped on WO #{log.get('wo_number')} - {int(log.get('seconds') or 0) // 60} min",
                        details={"work_order_id": str(log.get("work_order_id")), "labor_id": log.get("labor_id"), "seconds": log.get("seconds")},
                        url=f"/work_orders/details?work_order_id={log.get('work_order_id')}")
    _refresh_wos_after_timer_stop(shop_db, shop, logs)

    return jsonify({
        "ok": True,
        "timer": time_tracking._log_payload(log),
        "time_summary": time_tracking.summarize_wo_time(
            shop_db, shop["_id"], log.get("work_order_id"), user_id=user_id
        ),
        # Ревизия WO после пересчёта часов/назначений этим стопом.
        "labors_rev": wo_labors_rev(shop_db, shop["_id"], log.get("work_order_id")),
        "server_now": _server_now_iso(),
    }), 200


# ─── Перенос WO на другого клиента ───────────────────────────────────────────

@work_orders_bp.post("/work_orders/api/work_orders/<work_order_id>/transfer")
@login_required
@permission_required("work_orders.edit")
def api_work_order_transfer(work_order_id):
    """
    Перенос WO на другого клиента (кейс: механик завёл WO на нового клиента
    с новым юнитом, а WO должен принадлежать существующему клиенту).

    Юнит следует за WO:
      - у целевого клиента уже есть юнит с тем же VIN → WO перевешивается на
        него (деактивированный дубль реактивируется);
      - нет → юнит «переезжает»: у исходного клиента деактивируется, целевому
        создаётся копия со всеми полями (включая пробег).

    Деньги переоцениваются под нового клиента (reprice_wo_for_customer):
    ставки работ, цены запчастей по его прайс-матрице и флаг налога —
    иначе WO, заведённый под "NEW Customer", уехал бы с чужими ценами.
    Механик может продолжать работать: его сохранения читают WO заново и
    подхватывают новые ставки/цены. Запись — CAS по labors_rev с повтором
    всего тела (services/transfer.py); попытки кончились — 409.
    """
    if is_mechanic_mode():
        return jsonify({"ok": False, "error": "forbidden"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    data = request.get_json(silent=True)
    payload, code = transfer_work_order(
        shop_db, shop, wo_id, data if isinstance(data, dict) else {}, user_id=current_user_id(),
    )
    return jsonify(payload), code


# ─── Правка фактических часов механиков (менеджерская, отдельное право) ─────
# Механик забыл остановить таймер / случайный старт — менеджер с правом
# work_orders.edit_time_logs корректирует или удаляет завершённые сессии.
# После каждой правки пересчитываются производные поля WO (назначения,
# tracked-часы, totals). Оплаченные WO не трогаем.

def _refresh_after_time_log_change(shop_db, shop, log: dict, data: dict) -> str:
    """Пересчёт полей WO после правки/удаления сессии лога log. data — тело
    запроса: view_id в нём — база вкладки офиса; из неё выводится новая
    вьюха со свежими часами строки (office_merge.derive_view_after_time_log),
    иначе следующий Save вкладки принял бы их за чужую правку. Возвращает
    id производной вьюхи ("" — не просили / вьюха не нашлась)."""
    from app.blueprints.work_orders.services.mechanic_editor import refresh_time_derived_fields

    work_order_id = log.get("work_order_id")
    view_id = (data or {}).get("view_id")
    before_wo = None
    if view_id:
        before_wo = shop_db.work_orders.find_one(
            {"_id": work_order_id, "shop_id": shop["_id"]}, ev.with_rev(office_merge.TIME_LOG_ROW_PROJECTION),
        )
    refresh_time_derived_fields(
        shop_db, shop, work_order_id,
        mechanics_by_id={m["id"]: m for m in get_assignable_mechanics(shop)},
    )
    # Правка/удаление сессии меняет факт работы — статус записей календаря следом
    # (бригаду будущих записей двигает только явное переназначение офисом).
    _sync_calendar_with_wo(shop_db, shop, work_order_id)
    if not view_id:
        return ""
    return office_merge.derive_view_after_time_log(
        shop_db, shop["_id"], work_order_id, view_id,
        user_id=current_user_id(), labor_id=log.get("labor_id"), before_wo=before_wo,
    )


def _load_time_log_for_edit(shop_db, shop, log_id):
    """(log, wo, error): завершённый лог + его WO, с guard'ом на paid."""
    lid = oid(log_id)
    log = shop_db.wo_time_logs.find_one({"_id": lid, "shop_id": shop["_id"]}) if lid else None
    if not log:
        return None, None, "log_not_found"
    wo = shop_db.work_orders.find_one(
        {"_id": log.get("work_order_id"), "shop_id": shop["_id"]},
        {"status": 1},
    )
    if wo and (wo.get("status") or "") == "paid":
        return None, None, "paid_wo_locked"
    return log, wo, None


@work_orders_bp.get("/work_orders/api/work_orders/<work_order_id>/time_logs")
@login_required
@permission_required("work_orders.edit_time_logs")
def api_wo_time_logs(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400
    wo = shop_db.work_orders.find_one(
        {"_id": wo_id, "shop_id": shop["_id"], "is_active": True},
        {"status": 1},
    )
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    sessions = time_tracking.list_wo_time_sessions(
        shop_db, shop["_id"], wo_id,
        labor_id=(request.args.get("labor_id") or "").strip() or None,
    )
    return jsonify({
        "ok": True,
        "sessions": sessions,
        "wo_status": wo.get("status") or "open",
    }), 200


@work_orders_bp.post("/work_orders/api/time_logs/<log_id>/update")
@login_required
@permission_required("work_orders.edit_time_logs")
def api_time_log_update(log_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    log, _, error = _load_time_log_for_edit(shop_db, shop, log_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    data = request.get_json(silent=True) or {}
    log, error = time_tracking.update_time_log_seconds(
        shop_db, shop["_id"], log_id, data.get("seconds"), current_user_id(),
    )
    if error:
        return jsonify({"ok": False, "error": error}), 400

    audit_log.log_event("update", "time_log", log.get("_id"), label=f"WO #{log.get('wo_number')}",
                        summary=f"Mechanic hours edited on WO #{log.get('wo_number')}: {log.get('user_name')} -> {int(log.get('seconds') or 0) // 60} min",
                        details={"seconds": log.get("seconds"), "mechanic": log.get("user_name")},
                        url=f"/work_orders/details?work_order_id={log.get('work_order_id')}")
    view_id = _refresh_after_time_log_change(shop_db, shop, log, data)
    return jsonify({
        "ok": True,
        "log": time_tracking._log_payload(log),
        "time_summary": time_tracking.summarize_wo_time(
            shop_db, shop["_id"], log.get("work_order_id"), user_id=current_user_id()
        ),
        "labors_rev": wo_labors_rev(shop_db, shop["_id"], log.get("work_order_id")),
        # Производная вьюха вкладки (body.view_id) — "" если не просили / не нашлась.
        "view_id": view_id,
    }), 200


@work_orders_bp.post("/work_orders/api/time_logs/<log_id>/delete")
@login_required
@permission_required("work_orders.edit_time_logs")
def api_time_log_delete(log_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    log, _, error = _load_time_log_for_edit(shop_db, shop, log_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # Тело необязательное (старый JS шлёт пустой POST): view_id — база вкладки.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    log, error = time_tracking.delete_time_log(shop_db, shop["_id"], log_id)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    audit_log.log_event("delete", "time_log", log.get("_id"), label=f"WO #{log.get('wo_number')}",
                        summary=f"Mechanic timer session deleted on WO #{log.get('wo_number')}: {log.get('user_name')} ({int(log.get('seconds') or 0) // 60} min)",
                        details={"seconds": log.get("seconds"), "mechanic": log.get("user_name")},
                        url=f"/work_orders/details?work_order_id={log.get('work_order_id')}")
    view_id = _refresh_after_time_log_change(shop_db, shop, log, data)
    return jsonify({
        "ok": True,
        "time_summary": time_tracking.summarize_wo_time(
            shop_db, shop["_id"], log.get("work_order_id"), user_id=current_user_id()
        ),
        "labors_rev": wo_labors_rev(shop_db, shop["_id"], log.get("work_order_id")),
        # Производная вьюха вкладки (body.view_id) — "" если не просили / не нашлась.
        "view_id": view_id,
    }), 200


@work_orders_bp.get("/work_orders/api/mechanic/timers/current")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timer_current():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({
        "ok": True,
        "timer": time_tracking.running_timer_payload(shop_db, shop, current_user_id()),
        "server_now": _server_now_iso(),
    }), 200


@work_orders_bp.get("/work_orders/api/mechanic/timers/active")
@login_required
@permission_required("work_orders.view")
def api_mechanic_timers_active():
    """Все идущие таймеры магазина — блок «сейчас в работе» в списке WO."""
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    return jsonify({
        "ok": True,
        "items": time_tracking.active_timers(shop_db, shop, current_user_id()),
        "server_now": _server_now_iso(),
    }), 200
