"""
Mobile API — тонкие JSON-эндпоинты для мобильного приложения (Expo).

Аутентификация: та же cookie-сессия, что и веб. /login отдаёт CSRF-токен,
который приложение шлёт в X-CSRFToken на всех POST. Списки — поверх
существующих сервисов, без новой бизнес-логики.
"""
from __future__ import annotations

from functools import wraps

from flask import jsonify, request, session
from flask_wtf.csrf import generate_csrf
from werkzeug.security import check_password_hash

from app.blueprints.mobile_api import mobile_api_bp
from app.blueprints.work_orders.services.listing import get_work_orders_list
from app.extensions import csrf, get_master_db
from app.utils.auth import SESSION_SHOP_ID, is_logged_in, login_user, logout_user
from app.utils.duplicates import (
    customer_display_name,
    duplicate_message,
    find_duplicate_customer,
    find_duplicate_unit,
    find_duplicate_vendor,
    unit_duplicate_message,
)
from app.utils.mongo_search import build_regex_search_filter
from app.utils.pagination import get_pagination_params, paginate_find
from app.utils.permissions import enforce_mechanic_status, has_permission, permission_required
from app.utils.rate_limit import hit_rate_limit
from app.utils.tenant import get_shop_db as _tenant_get_shop_db, oid


def get_shop_db():
    return _tenant_get_shop_db(get_master_db())


def api_login_required(view_func):
    """Как login_required, но для JSON-клиента: 401 вместо redirect на логин."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return view_func(*args, **kwargs)
    return wrapper


def _i32_or_none(value):
    from app.blueprints.work_orders.services.mechanic_editor import parse_mileage

    return parse_mileage(value)


def _pagination_payload(pagination) -> dict:
    p = pagination if isinstance(pagination, dict) else {}
    return {
        "page": int(p.get("page") or 1),
        "pages": int(p.get("pages") or 1),
        "total": int(p.get("total") or 0),
        "per_page": int(p.get("per_page") or 20),
        "has_prev": bool(p.get("has_prev")),
        "has_next": bool(p.get("has_next")),
    }


def _shops_payload(master, tenant_id, allowed_shop_ids: list[str]) -> list[dict]:
    """Магазины тенанта, доступные пользователю (для переключателя)."""
    out = []
    for s in master.shops.find({"tenant_id": tenant_id, "is_active": True}):
        sid = str(s["_id"])
        if allowed_shop_ids and sid not in allowed_shop_ids:
            continue
        out.append({"id": sid, "name": s.get("name") or "—"})
    return out


def _session_payload(master, user: dict, tenant: dict) -> dict:
    allowed = [str(x) for x in (session.get("shop_ids") or [])]
    # Права пересчитываем на каждый запрос (как веб в permission_required):
    # роль могли поменять после логина, кука не должна замораживать старые права.
    from app.blueprints.auth.routes import _compute_effective_permissions
    perms = _compute_effective_permissions(user, tenant)
    session["user_permissions"] = perms
    session.modified = True
    return {
        "ok": True,
        "csrf_token": generate_csrf(),
        "user": {
            "id": str(user["_id"]),
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        },
        "tenant": {"id": str(tenant["_id"]), "name": tenant.get("name") or ""},
        "shops": _shops_payload(master, tenant["_id"], allowed),
        "active_shop_id": str(session.get(SESSION_SHOP_ID) or ""),
        "permissions": perms,
    }


@mobile_api_bp.post("/api/mobile/login")
@csrf.exempt  # мобильный auth-хендшейк: сессии ещё нет, CSRF-токен выдаётся в ответе
def mobile_login():
    master = get_master_db()

    if hit_rate_limit("login", request.remote_addr, max_attempts=10, window_seconds=300):
        return jsonify({"ok": False, "error": "rate_limited", "message": "Too many login attempts. Try again in a few minutes."}), 429

    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")

    if not email or not password:
        return jsonify({"ok": False, "error": "credentials_required", "message": "Fill in email and password."}), 400

    user = master.users.find_one({"email": email, "is_active": True})
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return jsonify({"ok": False, "error": "invalid_credentials", "message": "Wrong email or password."}), 401

    tenant = master.tenants.find_one({"_id": user["tenant_id"], "status": "active"})
    if not tenant:
        return jsonify({"ok": False, "error": "tenant_inactive", "message": "Tenant not found or inactive."}), 403

    from app import _is_tenant_subscription_blocked
    if _is_tenant_subscription_blocked(tenant):
        return jsonify({"ok": False, "error": "subscription_expired", "message": "Your subscription has expired."}), 403

    shop_ids = user.get("shop_ids") if isinstance(user.get("shop_ids"), list) else []
    login_user(
        user_id=user["_id"],
        tenant_id=tenant["_id"],
        tenant_db_name=tenant.get("db_name", ""),
        shop_ids=[str(x) for x in shop_ids],
        shop_id=None,
    )
    # Постоянная сессия: кука с Expires (PERMANENT_SESSION_LIFETIME) переживает
    # перезапуски приложения, срок продлевается каждым запросом — из приложения
    # не выкидывает. Доступ уволенных/неоплаченных режет before_request.
    session.permanent = True

    # user_permissions в сессию кладёт _session_payload (пересчёт на каждый запрос)
    return jsonify(_session_payload(master, user, tenant)), 200


def _load_session_user_tenant(master):
    user_id = oid(session.get("user_id"))
    tenant_id = oid(session.get("tenant_id"))
    if not user_id or not tenant_id:
        return None, None
    user = master.users.find_one({"_id": user_id, "is_active": True})
    tenant = master.tenants.find_one({"_id": tenant_id})
    return user, tenant


@mobile_api_bp.get("/api/mobile/session")
@api_login_required
def mobile_session():
    master = get_master_db()
    user, tenant = _load_session_user_tenant(master)
    if not user or not tenant:
        return jsonify({"ok": False, "error": "session_invalid"}), 401
    # Приложение зовёт /session при каждом старте — апгрейдим до постоянной
    # и сессии, залогиненные до ввода permanent-кук (без перелогина).
    session.permanent = True
    return jsonify(_session_payload(master, user, tenant)), 200


@mobile_api_bp.post("/api/mobile/logout")
@api_login_required
def mobile_logout():
    # Приложение передаёт свой push-токен — устройство больше не должно
    # получать уведомления этого пользователя.
    data = request.get_json(silent=True) or {}
    push_token = str(data.get("push_token") or "").strip()
    if push_token:
        from app.utils.push_notifications import remove_push_token

        remove_push_token(get_master_db(), push_token)
    logout_user()
    return jsonify({"ok": True}), 200


@mobile_api_bp.post("/api/mobile/push-token")
@api_login_required
def mobile_register_push_token():
    """Регистрация Expo-токена устройства для push-уведомлений.

    Токен привязывается к пользователю (upsert по токену): перелогин другого
    пользователя на том же устройстве переносит токен к нему.
    """
    from app.utils.push_notifications import register_push_token

    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    if not token or not token.startswith("ExponentPushToken"):
        return jsonify({"ok": False, "error": "invalid_token"}), 400

    user_id = oid(session.get("user_id"))
    if user_id is None:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    register_push_token(get_master_db(), user_id, token, str(data.get("platform") or ""))
    return jsonify({"ok": True}), 200


@mobile_api_bp.post("/api/mobile/active-shop")
@api_login_required
def mobile_set_active_shop():
    data = request.get_json(silent=True) or {}
    shop_id_raw = str(data.get("shop_id") or "").strip()
    if not shop_id_raw:
        return jsonify({"ok": False, "error": "shop_id_required"}), 400

    allowed = [str(x) for x in (session.get("shop_ids") or [])]
    if shop_id_raw not in allowed:
        return jsonify({"ok": False, "error": "shop_forbidden"}), 403

    session[SESSION_SHOP_ID] = shop_id_raw
    session.modified = True
    return jsonify({"ok": True, "active_shop_id": shop_id_raw}), 200


@mobile_api_bp.get("/api/mobile/work_orders")
@api_login_required
@permission_required("work_orders.view")
def mobile_work_orders():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    paid_status = (request.args.get("paid_status") or "all").strip().lower()
    if paid_status not in ("all", "paid", "unpaid", "in_progress"):
        paid_status = "all"
    # Механик видит только работу в процессе: open в этой системе = «закончен,
    # ждёт менеджера/оплаты», paid скрыт всегда. Форсим серверно независимо от
    # параметров клиента (in_progress включает и mechanic_done-WO — механик
    # видит свой законченный WO до подтверждения менеджером).
    if not has_permission("work_orders.view_costs"):
        paid_status = "in_progress"
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    items, pagination, totals = get_work_orders_list(
        shop_db, shop["_id"], page, per_page, q=q, paid_status=paid_status,
        customer_id=oid(request.args.get("customer_id")),
        unit_id=oid(request.args.get("unit_id")),
    )

    if not has_permission("work_orders.view_costs"):
        from app.blueprints.work_orders.services.mechanic_view import strip_wo_list_item

        items = [strip_wo_list_item(i) for i in items]
        totals = {}

    # Пометка деактивированного клиента прямо в имени — видна и в текущих
    # сборках мобильного приложения без его обновления.
    for i in items:
        if i.get("customer_inactive") and i.get("customer") and i["customer"] != "-":
            i["customer"] = f"{i['customer']} (inactive)"

    return jsonify({
        "ok": True,
        "items": items,
        "pagination": _pagination_payload(pagination),
        "totals": totals or {},
    }), 200


@mobile_api_bp.get("/api/mobile/work_orders/<work_order_id>")
@api_login_required
@permission_required("work_orders.view")
def mobile_work_order_details(work_order_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404

    # Механик-режим: безденежная форма деталей (та же, что на веб-странице
    # механика) — без цен, тоталов, часов и ставок, с таймерами по строкам.
    if not has_permission("work_orders.view_costs"):
        from datetime import timezone as _tz

        from app.blueprints.work_orders.services.common import utcnow
        from app.blueprints.work_orders.services.mechanic_view import mechanic_wo_payload

        # Подтверждённый менеджером WO закрыт для механика полностью.
        if wo.get("manager_confirmed"):
            return jsonify({
                "ok": False,
                "error": "wo_confirmed",
                "message": "This work order was confirmed by a manager and is locked.",
            }), 403

        payload = mechanic_wo_payload(shop_db, shop, wo, oid(session.get("user_id")))
        payload["server_now"] = utcnow().astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
        return jsonify(payload), 200

    # Полный контекст WO уже собирается для PDF — переиспользуем его,
    # убрав PDF-специфику (конфиг дизайна, логотип-base64).
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    ctx = _build_wo_pdf_context(shop_db, shop, wo)
    ctx.pop("pdf_cfg", None)
    ctx.pop("shop_logo_url", None)

    # Трекнутое время механиков по каждой работе: менеджер на ревью видит
    # сумму часов всех механиков, делавших этот лейбор.
    from app.blueprints.work_orders.services.time_tracking import summarize_wo_time

    def _fmt_hm(seconds: int) -> str:
        h, rem = divmod(max(0, int(seconds)), 3600)
        return f"{h}:{rem // 60:02d}"

    time_summary = summarize_wo_time(shop_db, shop["_id"], wo["_id"])
    dict_blocks = [b for b in (wo.get("labors") or []) if isinstance(b, dict)]
    ctx_labors = ctx.get("labors") or []
    for i, block in enumerate(dict_blocks):
        if i >= len(ctx_labors):
            break
        bucket = time_summary.get(str(block.get("labor_id") or "")) or {}
        total = int(bucket.get("total_seconds") or 0)
        ctx_labors[i]["tracked_seconds"] = total
        users = bucket.get("users") if isinstance(bucket.get("users"), dict) else {}
        ctx_labors[i]["tracked_label"] = (
            _fmt_hm(total)
            + (
                " — " + ", ".join(
                    f"{u.get('user_name') or '—'} {_fmt_hm(u.get('seconds') or 0)}"
                    for u in users.values()
                )
                if len(users) > 1 else ""
            )
        ) if total else ""

    # Сырые лейборы для мобильного редактора: исходные поля + сохранённая
    # labor base из totals (чтобы правка не пересчитала ручные суммы).
    from app.blueprints.work_orders.services.totals import normalize_parts_payload

    totals_doc = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    totals_blocks = totals_doc.get("labors") if isinstance(totals_doc.get("labors"), list) else []
    raw_labors = []
    for i, block in enumerate(wo.get("labors") or []):
        if not isinstance(block, dict):
            continue
        labor_src = block.get("labor") if isinstance(block.get("labor"), dict) else {}
        block_totals = totals_blocks[i] if i < len(totals_blocks) and isinstance(totals_blocks[i], dict) else {}
        parts_out = []
        for p in normalize_parts_payload(block.get("parts") or []):
            item = dict(p)
            if item.get("part_id") is not None:
                item["part_id"] = str(item["part_id"])
            parts_out.append(item)
        raw_labors.append({
            "labor_id": str(block.get("labor_id") or ""),
            "description": str(labor_src.get("description") or "").strip(),
            "hours": str(labor_src.get("hours") or "").strip(),
            "rate_code": str(labor_src.get("rate_code") or "").strip(),
            "issue_description": str(labor_src.get("issue_description") or "").strip(),
            "labor_base": float(block_totals.get("labor") or 0),
            "parts": parts_out,
        })

    return jsonify({
        "ok": True,
        "id": str(wo["_id"]),
        "status": (wo.get("status") or "open").strip().lower(),
        "is_estimate": bool(wo.get("is_estimate")),
        "manager_confirmed": bool(wo.get("manager_confirmed")),
        "customer_id": str(wo.get("customer_id") or ""),
        "unit_id": str(wo.get("unit_id") or ""),
        "raw_labors": raw_labors,
        **ctx,
    }), 200


@mobile_api_bp.get("/api/mobile/parts_orders")
@api_login_required
@permission_required("parts.view")
def mobile_parts_orders():
    from app.blueprints.parts.routes import (
        _build_parts_order_payment_summary,
        _sum_active_order_payments,
    )
    from app.blueprints.work_orders.services.common import format_dt_label

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {"shop_id": shop["_id"], "is_active": {"$ne": False}}
    search_filter = build_regex_search_filter(
        q,
        text_fields=["vendor_bill", "status", "payment_status"],
        numeric_fields=["order_number", "total_amount"],
        object_id_fields=["_id", "vendor_id"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]}

    rows, pagination = paginate_find(
        shop_db.parts_orders,
        query,
        [("created_at", -1)],
        page,
        per_page,
    )

    vendor_ids = [r.get("vendor_id") for r in rows if r.get("vendor_id")]
    vendors_map = {}
    if vendor_ids:
        for v in shop_db.vendors.find({"_id": {"$in": vendor_ids}}, {"name": 1}):
            vendors_map[v["_id"]] = v.get("name") or "-"

    items = []
    for r in rows:
        is_return_row = bool(r.get("is_return"))
        if is_return_row:
            # Возврат — кредит вендора: без платёжного цикла, сумма минусом.
            summary = {
                "total_amount": -float(r.get("credit_total") or 0.0),
                "paid_amount": 0.0,
                "remaining_balance": 0.0,
                "payment_status": "credit",
            }
        else:
            paid = _sum_active_order_payments(shop_db.parts_order_payments, r.get("_id"))
            summary = _build_parts_order_payment_summary(r, paid)
        items.append({
            "id": str(r["_id"]),
            "order_number": r.get("order_number"),
            "vendor": vendors_map.get(r.get("vendor_id")) or "-",
            "vendor_bill": str(r.get("vendor_bill") or ""),
            "items_count": len(r.get("items") or []),
            "status": str(r.get("status") or "ordered"),
            "total_amount": summary["total_amount"],
            "paid_amount": summary["paid_amount"],
            "remaining_balance": summary["remaining_balance"],
            "payment_status": summary["payment_status"],
            "created_at": format_dt_label(r.get("created_at")),
            "is_return": is_return_row,
            "return_for_order_number": r.get("return_for_order_number"),
        })

    return jsonify({"ok": True, "items": items, "pagination": _pagination_payload(pagination)}), 200


@mobile_api_bp.get("/api/mobile/labor_rates")
@api_login_required
@permission_required("work_orders.view")
def mobile_labor_rates():
    from app.blueprints.work_orders.services.lookups import get_labor_rates

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    items = get_labor_rates(shop_db, shop["_id"])
    if not has_permission("work_orders.view_costs"):
        # Механик видит названия/коды ставок, но не $/час.
        items = [{k: v for k, v in r.items() if k != "hourly_rate"} for r in items]
    return jsonify({"ok": True, "items": items}), 200


def _mobile_wo_taxable(shop_db, data, existing_totals, customer_id):
    from app.blueprints.work_orders.services.totals import _is_customer_taxable

    if isinstance(data, dict) and "is_taxable" in data:
        return bool(data.get("is_taxable"))
    if isinstance(existing_totals, dict) and "is_taxable" in existing_totals:
        return bool(existing_totals.get("is_taxable"))
    return _is_customer_taxable(shop_db, customer_id)


@mobile_api_bp.get("/api/mobile/part_price")
@api_login_required
@permission_required("work_orders.create")
def mobile_part_price():
    from app.blueprints.work_orders.services.mobile_editor import suggest_part_price

    if not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "forbidden", "required": "work_orders.view_costs"}), 403

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    part_id = oid(request.args.get("part_id"))
    if not part_id:
        return jsonify({"ok": False, "error": "part_id_required"}), 400
    part = shop_db.parts.find_one({"_id": part_id, "shop_id": shop["_id"], "is_active": True})
    if not part:
        return jsonify({"ok": False, "error": "part_not_found"}), 404

    customer_id = oid(request.args.get("customer_id"))
    price = suggest_part_price(shop_db, shop["_id"], customer_id, part)
    return jsonify({
        "ok": True,
        "price": price,
        "cost": float(part.get("average_cost") or 0),
        "core_charge": float(part.get("core_cost") or 0) if part.get("core_has_charge") else 0,
    }), 200


@mobile_api_bp.post("/api/mobile/work_orders")
@api_login_required
@permission_required("work_orders.create")
def mobile_work_order_create():
    from app.blueprints.work_orders.services.common import utcnow
    from app.blueprints.work_orders.services.inventory import (
        deduct_parts_from_inventory,
        sync_work_order_cores,
    )
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        _get_shop_sales_tax_context,
        align_totals_with_labors,
        get_next_wo_number,
        normalize_totals_payload,
    )

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    customer_id = oid(data.get("customer_id"))
    unit_id = oid(data.get("unit_id"))
    if not customer_id or not unit_id:
        return jsonify({"ok": False, "error": "customer_unit_required",
                        "message": "Customer and unit are required."}), 400

    customer = shop_db.customers.find_one({"_id": customer_id, "shop_id": shop["_id"], "is_active": True})
    if not customer:
        return jsonify({"ok": False, "error": "customer_not_found"}), 404
    unit = shop_db.units.find_one({"_id": unit_id, "shop_id": shop["_id"], "is_active": True})
    if not unit or unit.get("customer_id") != customer_id:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    labors_payload = data.get("labors")
    # Механик создаёт WO сразу при выборе клиента и юнита — работы добавляются
    # потом автосейвом, поэтому пустой список работ для него валиден.
    is_mechanic = not has_permission("work_orders.view_costs")
    if not isinstance(labors_payload, list) or (not labors_payload and not is_mechanic):
        return jsonify({"ok": False, "error": "labors_required",
                        "message": "At least one labor is required."}), 400

    # Механик-режим: клиентские цены/часы игнорируются, сервер автозаполняет
    # цены из каталога/прайсинг-правил (как на веб-странице механика).
    if is_mechanic:
        from app.blueprints.work_orders.services.mechanic_editor import build_mechanic_labors_payload

        labors_payload = build_mechanic_labors_payload(shop_db, shop, customer_id, labors_payload)

    labors, totals_raw = compute_labors_and_totals(shop_db, shop, labors_payload)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
    shop_tax = _get_shop_sales_tax_context(shop, shop_db)
    totals = _apply_sales_tax_to_totals(
        totals,
        shop_tax.get("rate") or 0,
        _mobile_wo_taxable(shop_db, data, None, customer_id),
    )

    now = utcnow()
    user_id = oid(session.get("user_id"))

    wo_number = get_next_wo_number(shop_db, shop["_id"])
    inventory_result = deduct_parts_from_inventory(
        shop_db, labors, user_id,
        ref={"kind": "work_order", "label": str(wo_number)},
    )

    status = "in_progress" if enforce_mechanic_status(str(data.get("status") or "").strip().lower()) == "in_progress" else "open"
    doc = {
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "wo_number": wo_number,
        "customer_id": customer_id,
        "unit_id": unit_id,
        "status": status,
        "labors": labors,
        "work_order_date": now,
        "totals": totals,
        "inventory_deducted": len(inventory_result["deducted"]) > 0,
        "inventory_deductions": inventory_result["deducted"],
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    if is_mechanic:
        from app.blueprints.work_orders.services.mechanic_editor import mechanic_done_fields

        doc.update(mechanic_done_fields(data, user_id, now))

    unit_mileage = _i32_or_none(data.get("unit_mileage"))
    if unit_mileage is not None:
        doc["mileage"] = unit_mileage
        shop_db.units.update_one(
            {"_id": unit_id, "shop_id": shop["_id"], "is_active": True},
            {"$set": {"mileage": unit_mileage, "updated_at": now, "updated_by": user_id}},
        )

    res = shop_db.work_orders.insert_one(doc)

    core_sync = sync_work_order_cores(shop_db, shop, [], labors, user_id)

    from app.blueprints.work_orders.services.push_events import notify_after_mechanic_save

    notify_after_mechanic_save(shop, None, doc, res.inserted_id, doc["wo_number"], user_id)

    return jsonify({
        "ok": True,
        "id": str(res.inserted_id),
        "wo_number": doc["wo_number"],
        "status": status,
        "grand_total": totals.get("grand_total"),
        "inventory_warnings": inventory_result.get("errors") or [],
        "core_sync_errors": core_sync.get("errors") or [],
    }), 200


@mobile_api_bp.post("/api/mobile/work_orders/<work_order_id>")
@api_login_required
@permission_required("work_orders.create")
def mobile_work_order_edit(work_order_id):
    from app.blueprints.work_orders.services.common import utcnow
    from app.blueprints.work_orders.services.inventory import (
        adjust_inventory_for_part_changes,
        sync_work_order_cores,
    )
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        _get_shop_sales_tax_context,
        align_totals_with_labors,
        normalize_totals_payload,
    )

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    wo_id = oid(work_order_id)
    if not wo_id:
        return jsonify({"ok": False, "error": "invalid_work_order_id"}), 400

    wo = shop_db.work_orders.find_one({"_id": wo_id, "shop_id": shop["_id"], "is_active": True})
    if not wo:
        return jsonify({"ok": False, "error": "work_order_not_found"}), 404
    if (wo.get("status") or "open") == "paid":
        return jsonify({"ok": False, "error": "paid_cannot_edit",
                        "message": "Paid work orders cannot be edited."}), 400
    # Сметы редактируются в веб-интерфейсе: мобильный save форсит
    # open/in_progress и молча конвертировал бы смету со списанием склада.
    if (wo.get("status") or "open") == "estimate":
        return jsonify({"ok": False, "error": "estimate_web_only",
                        "message": "Estimates are edited in the web interface."}), 400
    # Подтверждённый менеджером WO механик редактировать не может
    # (менеджер может снять подтверждение — тогда правка снова доступна).
    if wo.get("manager_confirmed") and not has_permission("work_orders.view_costs"):
        return jsonify({"ok": False, "error": "wo_confirmed",
                        "message": "This work order was confirmed by a manager and is locked."}), 403

    data = request.get_json(silent=True) or {}
    labors_payload = data.get("labors")
    if not isinstance(labors_payload, list) or not labors_payload:
        return jsonify({"ok": False, "error": "labors_required",
                        "message": "At least one labor is required."}), 400

    # Механик-режим: merge по labor_id — менеджерские hours/rate/цены
    # сохраняются, клиентские деньги игнорируются.
    is_mechanic = not has_permission("work_orders.view_costs")
    if is_mechanic:
        from app.blueprints.work_orders.services.mechanic_editor import merge_mechanic_edit

        labors_payload = merge_mechanic_edit(shop_db, shop, wo, labors_payload)

    labors, totals_raw = compute_labors_and_totals(shop_db, shop, labors_payload)
    totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)

    # Ставка налога зафиксирована при создании WO (как в веб-редакторе).
    existing_totals = wo.get("totals") if isinstance(wo.get("totals"), dict) else {}
    locked_tax_rate = existing_totals.get("sales_tax_rate")
    if locked_tax_rate is None:
        locked_tax_rate = _get_shop_sales_tax_context(shop, shop_db).get("rate") or 0

    totals = _apply_sales_tax_to_totals(
        totals,
        locked_tax_rate,
        _mobile_wo_taxable(shop_db, data, existing_totals, wo.get("customer_id")),
    )

    now = utcnow()
    user_id = oid(session.get("user_id"))
    old_labors = wo.get("labors") or []

    inventory_adjustment = adjust_inventory_for_part_changes(
        shop_db, old_labors, labors, user_id,
        ref={"kind": "work_order", "id": wo["_id"], "label": str(wo.get("wo_number") or "")},
    )
    core_sync = sync_work_order_cores(shop_db, shop, old_labors, labors, user_id)

    set_fields = {
        "labors": labors,
        "totals": totals,
        "inventory_adjusted_at": now,
        "updated_at": now,
        "updated_by": user_id,
    }
    save_status = enforce_mechanic_status(str(data.get("status") or "").strip().lower())
    if save_status in ("open", "in_progress"):
        set_fields["status"] = save_status
    if is_mechanic:
        from app.blueprints.work_orders.services.mechanic_editor import mechanic_done_fields

        set_fields.update(mechanic_done_fields(data, user_id, now))

    # Обновление пробега юнита из формы WO (как в веб-редакторе).
    unit_mileage = _i32_or_none(data.get("unit_mileage"))
    if unit_mileage is not None:
        set_fields["mileage"] = unit_mileage
        shop_db.units.update_one(
            {"_id": wo.get("unit_id"), "shop_id": shop["_id"], "is_active": True},
            {"$set": {"mileage": unit_mileage, "updated_at": now, "updated_by": user_id}},
        )

    shop_db.work_orders.update_one({"_id": wo_id}, {"$set": set_fields})

    from app.blueprints.work_orders.services.push_events import notify_after_mechanic_save

    notify_after_mechanic_save(shop, wo, set_fields, wo_id, wo.get("wo_number"), user_id)

    return jsonify({
        "ok": True,
        "id": str(wo_id),
        "status": set_fields.get("status") or (wo.get("status") or "open"),
        "grand_total": totals.get("grand_total"),
        "inventory_warnings": inventory_adjustment.get("errors") or [],
        "core_sync_errors": core_sync.get("errors") or [],
    }), 200


@mobile_api_bp.get("/api/mobile/customers")
@api_login_required
@permission_required("customers.view")
def mobile_customers():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {"shop_id": shop["_id"]}
    search_filter = build_regex_search_filter(
        q,
        text_fields=[
            "company_name", "first_name", "last_name", "phone", "email",
            "contacts.first_name", "contacts.last_name", "contacts.phone",
            "contacts.email", "address",
        ],
        object_id_fields=["_id"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]}

    rows, pagination = paginate_find(
        shop_db.customers,
        query,
        [("is_active", -1), ("company_name", 1), ("last_name", 1), ("created_at", -1)],
        page,
        per_page,
    )

    items = []
    for c in rows:
        contacts = c.get("contacts") if isinstance(c.get("contacts"), list) else []
        main = next((x for x in contacts if isinstance(x, dict) and x.get("is_main")), contacts[0] if contacts else {})
        main = main if isinstance(main, dict) else {}
        items.append({
            "id": str(c["_id"]),
            "company_name": c.get("company_name") or "",
            "contact_name": " ".join(filter(None, [main.get("first_name"), main.get("last_name")])).strip(),
            "phone": main.get("phone") or c.get("phone") or "",
            "email": main.get("email") or c.get("email") or "",
            "address": c.get("address") or "",
            "is_active": c.get("is_active", True) is not False,
        })

    return jsonify({"ok": True, "items": items, "pagination": _pagination_payload(pagination)}), 200


@mobile_api_bp.get("/api/mobile/customers/<customer_id>")
@api_login_required
@permission_required("customers.view")
def mobile_customer_details(customer_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    cust_id = oid(customer_id)
    if not cust_id:
        return jsonify({"ok": False, "error": "invalid_customer_id"}), 400

    c = shop_db.customers.find_one({"_id": cust_id, "shop_id": shop["_id"]})
    if not c:
        return jsonify({"ok": False, "error": "customer_not_found"}), 404

    from app.blueprints.work_orders.services.lookups import customer_label, unit_label

    contacts = []
    for x in (c.get("contacts") or []):
        if not isinstance(x, dict):
            continue
        contacts.append({
            "name": " ".join(filter(None, [x.get("first_name"), x.get("last_name")])).strip(),
            "first_name": x.get("first_name") or "",
            "last_name": x.get("last_name") or "",
            "phone": x.get("phone") or "",
            "email": x.get("email") or "",
            "is_main": bool(x.get("is_main")),
        })

    units = []
    for u in shop_db.units.find({"customer_id": cust_id, "shop_id": shop["_id"], "is_active": True}).sort("unit_number", 1):
        units.append({
            "id": str(u["_id"]),
            "label": unit_label(u),
            "unit_number": str(u.get("unit_number") or ""),
            "vin": str(u.get("vin") or ""),
            "year": str(u.get("year") or ""),
            "make": str(u.get("make") or ""),
            "model": str(u.get("model") or ""),
            "mileage": u.get("mileage"),
        })

    return jsonify({
        "ok": True,
        "id": str(c["_id"]),
        "label": customer_label(c),
        "company_name": c.get("company_name") or "",
        "address": c.get("address") or "",
        "taxable": bool(c.get("taxable")),
        "is_active": c.get("is_active", True) is not False,
        "contacts": contacts,
        "units": units,
    }), 200


@mobile_api_bp.get("/api/mobile/units")
@api_login_required
@permission_required("work_orders.view")
def mobile_units_search():
    """
    Глобальный поиск юнитов (номер, VIN, make/model) с подписью клиента.
    Нужен механику в списке WO: найти юнит и увидеть, какому кастомеру
    он принадлежит, прежде чем создавать work order.
    """
    from app.blueprints.work_orders.services.lookups import customer_label, unit_label
    from app.utils.entity_search import search_unit_ids

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {"shop_id": shop["_id"], "is_active": True}
    if q:
        # $in по пустому списку корректно вернёт пустую выдачу.
        query["_id"] = {"$in": search_unit_ids(shop_db.units, q)}

    rows, pagination = paginate_find(
        shop_db.units,
        query,
        [("unit_number", 1), ("created_at", -1)],
        page,
        per_page,
    )

    customer_ids = [u.get("customer_id") for u in rows if u.get("customer_id")]
    customers_map = {}
    inactive_customer_ids = set()
    if customer_ids:
        for c in shop_db.customers.find({"_id": {"$in": customer_ids}}):
            # Деактивированный клиент помечается прямо в лейбле — это видно
            # и в текущих сборках мобильного приложения.
            label = customer_label(c)
            if c.get("is_active") is False:
                label += " (inactive)"
                inactive_customer_ids.add(c["_id"])
            customers_map[c["_id"]] = label

    items = [{
        "id": str(u["_id"]),
        "label": unit_label(u),
        "unit_number": str(u.get("unit_number") or ""),
        "vin": str(u.get("vin") or ""),
        "customer_id": str(u.get("customer_id") or ""),
        "customer_label": customers_map.get(u.get("customer_id")) or "-",
        "customer_inactive": u.get("customer_id") in inactive_customer_ids,
    } for u in rows]

    return jsonify({"ok": True, "items": items, "pagination": _pagination_payload(pagination)}), 200


@mobile_api_bp.get("/api/mobile/units/<unit_id>")
@api_login_required
@permission_required("customers.view")
def mobile_unit_details(unit_id):
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    u_id = oid(unit_id)
    if not u_id:
        return jsonify({"ok": False, "error": "invalid_unit_id"}), 400

    u = shop_db.units.find_one({"_id": u_id, "shop_id": shop["_id"]})
    if not u:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    from app.blueprints.work_orders.services.lookups import customer_label, unit_label

    customer = shop_db.customers.find_one({"_id": u.get("customer_id")}) or {}

    from app.blueprints.work_orders.services.inspections import (
        inspection_expiry,
        inspection_expiry_status,
    )

    # У инспекций нет флага is_active — просто последняя по created_at.
    inspection = shop_db.annual_inspections.find_one(
        {"unit_id": u_id, "shop_id": shop["_id"]},
        sort=[("created_at", -1)],
    )
    inspection_expires = inspection_expiry(inspection) if inspection else None

    customer_lbl = customer_label(customer) if customer else ""
    if customer and customer.get("is_active") is False:
        customer_lbl += " (inactive)"

    return jsonify({
        "ok": True,
        "id": str(u["_id"]),
        "label": unit_label(u),
        "customer_id": str(u.get("customer_id") or ""),
        "customer_label": customer_lbl,
        "customer_inactive": bool(customer) and customer.get("is_active") is False,
        "unit_number": str(u.get("unit_number") or ""),
        "vin": str(u.get("vin") or ""),
        "year": str(u.get("year") or ""),
        "make": str(u.get("make") or ""),
        "model": str(u.get("model") or ""),
        "type": str(u.get("type") or ""),
        "mileage": u.get("mileage"),
        "is_active": u.get("is_active", True) is not False,
        "annual_inspection": {
            "date": str(inspection.get("inspection_date") or ""),
            "inspector": str(inspection.get("inspector_name") or ""),
            "expires": str(inspection_expires or ""),
            "expiry_status": inspection_expiry_status(inspection_expires),
        } if inspection else None,
    }), 200


# ── CRUD: создание из мобильного приложения ─────────────────────────
# JSON-варианты веб-форм. Валидация и структура документов — те же
# helpers, что и в веб-роутах (contacts, legacy-поля, search_terms).

@mobile_api_bp.post("/api/mobile/customers")
@api_login_required
@permission_required("customers.edit")
def mobile_customer_create():
    from app.blueprints.customers.routes import (
        _resolve_default_labor_rate_id,
        _validate_customer_pricing_rule_id,
    )
    from app.utils.contacts import (
        build_contacts_from_payload,
        build_customer_legacy_contact_fields,
        has_contact_name,
    )
    from app.utils.entity_search import build_customer_search_terms
    from app.blueprints.work_orders.services.common import utcnow
    from app.blueprints.work_orders.services.lookups import customer_label

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    company_name = str(data.get("company_name") or "").strip()
    contacts = build_contacts_from_payload(data)
    address = str(data.get("address") or "").strip()
    taxable = bool(data.get("taxable"))

    if not company_name and not has_contact_name(contacts):
        return jsonify({"ok": False, "error": "name_required",
                        "message": "Company name or at least one contact name is required."}), 400
    if len(address) < 5:
        return jsonify({"ok": False, "error": "address_required",
                        "message": "Customer address is required."}), 400

    existing = find_duplicate_customer(shop_db, shop["_id"], company_name, contacts)
    if existing:
        msg = duplicate_message("Customer", customer_display_name(existing), existing)
        return jsonify({"ok": False, "error": msg, "message": msg}), 409

    default_rate_id = _resolve_default_labor_rate_id(shop_db, shop["_id"])
    if not default_rate_id:
        return jsonify({"ok": False, "error": "no_labor_rates",
                        "message": "No labor rates configured for this shop."}), 400

    pricing_rule_id, pricing_err = _validate_customer_pricing_rule_id(
        shop_db, shop["_id"], data.get("pricing_rule_id")
    )
    if pricing_err:
        return jsonify({"ok": False, "error": "invalid_pricing_rule", "message": pricing_err}), 400

    now = utcnow()
    user_id = oid(session.get("user_id"))
    doc = {
        "company_name": company_name or None,
        "contacts": contacts,
        "address": address or None,
        "taxable": taxable,
        "default_labor_rate": default_rate_id,
        "pricing_rule_id": pricing_rule_id,
        "override_part_selling_price": bool(data.get("override_part_selling_price")),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
        "deactivated_at": None,
        "deactivated_by": None,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
    }
    doc.update(build_customer_legacy_contact_fields(contacts))
    doc["search_terms"] = build_customer_search_terms(doc)

    res = shop_db.customers.insert_one(doc)
    return jsonify({"ok": True, "id": str(res.inserted_id), "label": customer_label(doc)}), 200


@mobile_api_bp.post("/api/mobile/units")
@api_login_required
@permission_required("work_orders.create")
def mobile_unit_create():
    from app.blueprints.work_orders.services.common import i32, utcnow
    from app.blueprints.work_orders.services.lookups import unit_label
    from app.utils.entity_search import build_unit_search_terms

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    customer_id = oid(data.get("customer_id"))
    if not customer_id:
        return jsonify({"ok": False, "error": "customer_required", "message": "Customer is required."}), 400

    customer = shop_db.customers.find_one({"_id": customer_id, "shop_id": shop["_id"], "is_active": True})
    if not customer:
        return jsonify({"ok": False, "error": "customer_not_found"}), 404

    def _s(key):
        return str(data.get(key) or "").strip() or None

    if not _s("unit_number") and not _s("vin"):
        return jsonify({"ok": False, "error": "unit_identity_required",
                        "message": "Unit number or VIN is required."}), 400

    existing = find_duplicate_unit(shop_db, shop["_id"], customer_id, _s("vin"))
    if existing:
        msg = unit_duplicate_message(existing)
        return jsonify({"ok": False, "error": msg, "message": msg}), 409

    now = utcnow()
    user_id = oid(session.get("user_id"))
    doc = {
        "customer_id": customer_id,
        "vin": _s("vin"),
        "unit_number": _s("unit_number"),
        "make": _s("make"),
        "model": _s("model"),
        "year": i32(data.get("year")),
        "type": _s("type"),
        "mileage": i32(data.get("mileage")),
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
    return jsonify({"ok": True, "id": str(res.inserted_id), "label": unit_label(doc)}), 200


@mobile_api_bp.post("/api/mobile/units/<unit_id>/update")
@api_login_required
@permission_required("customers.edit")
def mobile_unit_update(unit_id):
    from app.blueprints.work_orders.services.common import i32, utcnow
    from app.utils.entity_search import build_unit_search_terms

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    u_id = oid(unit_id)
    if not u_id:
        return jsonify({"ok": False, "error": "invalid_unit_id"}), 400

    existing = shop_db.units.find_one({"_id": u_id, "shop_id": shop["_id"]})
    if not existing:
        return jsonify({"ok": False, "error": "unit_not_found"}), 404

    data = request.get_json(silent=True) or {}

    def _s(key):
        return str(data.get(key) or "").strip() or None

    existing_dup = find_duplicate_unit(
        shop_db, shop["_id"], existing.get("customer_id"), _s("vin"), exclude_id=u_id
    )
    if existing_dup:
        msg = unit_duplicate_message(existing_dup)
        return jsonify({"ok": False, "error": msg, "message": msg}), 409

    update = {
        "vin": _s("vin"),
        "unit_number": _s("unit_number"),
        "make": _s("make"),
        "model": _s("model"),
        "year": i32(data.get("year")),
        "type": _s("type"),
        "mileage": i32(data.get("mileage")),
        "updated_at": utcnow(),
        "updated_by": oid(session.get("user_id")),
    }
    merged = {**existing, **update}
    update["search_terms"] = build_unit_search_terms(merged)

    shop_db.units.update_one({"_id": u_id}, {"$set": update})
    return jsonify({"ok": True, "id": str(u_id)}), 200


@mobile_api_bp.post("/api/mobile/vendors")
@api_login_required
@permission_required("vendors.edit")
def mobile_vendor_create():
    from app.utils.contacts import (
        build_contacts_from_payload,
        build_vendor_legacy_contact_fields,
    )
    from app.blueprints.work_orders.services.common import utcnow

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name_required", "message": "Vendor name is required."}), 400

    existing = find_duplicate_vendor(shop_db, shop["_id"], name)
    if existing:
        msg = duplicate_message("Vendor", existing.get("name"), existing)
        return jsonify({"ok": False, "error": msg, "message": msg}), 409

    contacts = build_contacts_from_payload(data)
    now = utcnow()
    user_id = oid(session.get("user_id"))
    doc = {
        "name": name,
        "contacts": contacts,
        "website": str(data.get("website") or "").strip() or None,
        "address": str(data.get("address") or "").strip() or None,
        "notes": str(data.get("notes") or "").strip() or None,
        "is_active": True,
        "shop_id": shop["_id"],
        "tenant_id": shop.get("tenant_id"),
        "created_at": now,
        "updated_at": now,
        "created_by": user_id,
        "updated_by": user_id,
    }
    doc.update(build_vendor_legacy_contact_fields(contacts))

    res = shop_db.vendors.insert_one(doc)
    return jsonify({"ok": True, "id": str(res.inserted_id), "name": name}), 200


@mobile_api_bp.get("/api/mobile/vendors")
@api_login_required
@permission_required("vendors.view")
def mobile_vendors():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {"shop_id": shop["_id"]}
    search_filter = build_regex_search_filter(
        q,
        text_fields=[
            "name", "phone", "email", "website", "address", "notes",
            "contacts.first_name", "contacts.last_name", "contacts.phone", "contacts.email",
        ],
        object_id_fields=["_id"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]}

    rows, pagination = paginate_find(
        shop_db.vendors,
        query,
        [("is_active", -1), ("name", 1), ("created_at", -1)],
        page,
        per_page,
    )

    items = []
    for v in rows:
        contacts = v.get("contacts") if isinstance(v.get("contacts"), list) else []
        main = next((x for x in contacts if isinstance(x, dict) and x.get("is_main")), contacts[0] if contacts else {})
        main = main if isinstance(main, dict) else {}
        items.append({
            "id": str(v["_id"]),
            "name": v.get("name") or "",
            "contact_name": " ".join(filter(None, [main.get("first_name"), main.get("last_name")])).strip(),
            "phone": main.get("phone") or v.get("phone") or "",
            "email": main.get("email") or v.get("email") or "",
            "website": v.get("website") or "",
            "address": v.get("address") or "",
            "is_active": v.get("is_active", True) is not False,
        })

    return jsonify({"ok": True, "items": items, "pagination": _pagination_payload(pagination)}), 200


@mobile_api_bp.get("/api/mobile/parts")
@api_login_required
@permission_required("parts.view")
def mobile_parts():
    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    # Как на вкладке Stock веб-версии: активные позиции с остатком.
    query = {"shop_id": shop["_id"], "is_active": True, "in_stock": {"$gt": 0}}
    search_filter = build_regex_search_filter(
        q,
        text_fields=["part_number", "description", "reference"],
        numeric_fields=["in_stock", "average_cost"],
        object_id_fields=["_id"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]}

    rows, pagination = paginate_find(
        shop_db.parts,
        query,
        [("part_number", 1)],
        page,
        per_page,
    )

    # Кросс-референсы (взаимозаменяемые парты) для страницы — одним запросом.
    page_groups = {p.get("interchange_group") for p in rows if p.get("interchange_group")}
    members_by_group = {}
    if page_groups:
        cursor = shop_db.parts.find(
            {
                "shop_id": shop["_id"],
                "interchange_group": {"$in": list(page_groups)},
                "is_active": True,
            },
            {"part_number": 1, "description": 1, "in_stock": 1, "interchange_group": 1},
        ).sort([("part_number", 1)])
        for m in cursor:
            members_by_group.setdefault(m.get("interchange_group"), []).append(m)

    show_costs = has_permission("parts.view_costs")
    items = []
    for p in rows:
        item = {
            "id": str(p["_id"]),
            "part_number": p.get("part_number") or "",
            "description": p.get("description") or "",
            "reference": p.get("reference") or "",
            "in_stock": int(p.get("in_stock") or 0),
            "cross_refs": [
                {
                    "id": str(m["_id"]),
                    "part_number": m.get("part_number") or "",
                    "description": m.get("description") or "",
                    "in_stock": int(m.get("in_stock") or 0),
                }
                for m in members_by_group.get(p.get("interchange_group"), [])
                if m.get("_id") != p.get("_id")
            ],
        }
        if show_costs:
            item["average_cost"] = float(p.get("average_cost") or 0)
            item["selling_price"] = float(p.get("selling_price") or 0)
        items.append(item)

    return jsonify({"ok": True, "items": items, "pagination": _pagination_payload(pagination)}), 200


# ── Инвентаризация (stocktakes) ──────────────────────────────────────
# count/complete/cancel мобильный клиент зовёт напрямую в веб-JSON
# роуты /parts/stocktakes/<id>/... — здесь только список/создание/детали.


@mobile_api_bp.get("/api/mobile/stocktakes")
@api_login_required
@permission_required("parts.view")
def mobile_stocktakes():
    from app.utils.display_datetime import format_date_mmddyyyy

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    q = (request.args.get("q") or "").strip()
    page, per_page = get_pagination_params(request.args, default_per_page=20, max_per_page=100)

    query = {"shop_id": shop["_id"], "is_active": {"$ne": False}}
    search_filter = build_regex_search_filter(
        q,
        text_fields=["name", "status"],
        numeric_fields=["number"],
        object_id_fields=["_id"],
    )
    if search_filter:
        query = {"$and": [query, search_filter]}

    rows, pagination = paginate_find(
        shop_db.stocktakes,
        query,
        [("created_at", -1)],
        page,
        per_page,
    )

    items = []
    for st in rows:
        counted_now = None
        if st.get("status") == "open":
            counted_now = shop_db.stocktake_items.count_documents(
                {"stocktake_id": st["_id"], "status": "counted"}
            )
        totals = st.get("totals") or {}
        scope = st.get("scope") or {}
        items.append({
            "id": str(st["_id"]),
            "number": st.get("number"),
            "name": st.get("name") or "",
            "status": st.get("status") or "open",
            "scope_location": scope.get("location_path") or "",
            "scope_category": scope.get("category_name") or "",
            "items_total": int(st.get("items_total") or 0),
            "items_counted": int(
                counted_now if counted_now is not None else (totals.get("items_counted") or 0)
            ),
            "items_adjusted": int(totals.get("items_adjusted") or 0),
            "shortage_value": float(totals.get("shortage_value") or 0.0),
            "overage_value": float(totals.get("overage_value") or 0.0),
            "created_label": format_date_mmddyyyy(st.get("created_at")),
        })

    return jsonify({"ok": True, "items": items, "pagination": _pagination_payload(pagination)}), 200


@mobile_api_bp.get("/api/mobile/stocktake_options")
@api_login_required
@permission_required("parts.view")
def mobile_stocktake_options():
    """Справочники для формы создания инвентаризации: дерево локаций + категории."""
    from app.utils.parts_locations import flatten_location_tree, load_shop_locations

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    locations = [
        {"id": item["id"], "path": item["path"], "depth": item["depth"]}
        for item in flatten_location_tree(load_shop_locations(shop_db, shop["_id"]))
    ]
    categories = [
        {"id": str(c["_id"]), "name": c.get("name") or ""}
        for c in shop_db.parts_categories.find(
            {"shop_id": shop["_id"], "is_active": {"$ne": False}}
        ).sort("name", 1)
    ]
    return jsonify({"ok": True, "locations": locations, "categories": categories}), 200


@mobile_api_bp.post("/api/mobile/stocktakes")
@api_login_required
@permission_required("parts.edit")
def mobile_stocktake_create():
    from app.blueprints.parts.services.stocktake import create_stocktake

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    location_id = oid(str(data.get("location_id") or "")) or None
    category_id = oid(str(data.get("category_id") or "")) or None
    user_id = oid(session.get("user_id"))

    stocktake, err = create_stocktake(
        shop_db, shop, user_id,
        name=name, location_id=location_id, category_id=category_id,
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400

    return jsonify({"ok": True, "id": str(stocktake["_id"]), "number": stocktake["number"]}), 200


@mobile_api_bp.get("/api/mobile/stocktakes/<stocktake_id>")
@api_login_required
@permission_required("parts.view")
def mobile_stocktake_detail(stocktake_id: str):
    from app.blueprints.parts.services.stocktake import build_recount_flags
    from app.utils.display_datetime import format_date_mmddyyyy

    shop_db, shop = get_shop_db()
    if shop_db is None:
        return jsonify({"ok": False, "error": "shop_db_missing"}), 200

    st_id = oid(stocktake_id)
    if not st_id:
        return jsonify({"ok": False, "error": "invalid_stocktake_id"}), 400

    stocktake = shop_db.stocktakes.find_one(
        {"_id": st_id, "shop_id": shop["_id"], "is_active": {"$ne": False}}
    )
    if not stocktake:
        return jsonify({"ok": False, "error": "stocktake_not_found"}), 404

    items = list(
        shop_db.stocktake_items.find({"stocktake_id": stocktake["_id"]})
        .sort([("location_path", 1), ("part_number", 1)])
    )
    recount_flags = (
        build_recount_flags(shop_db, shop, stocktake, items)
        if stocktake.get("status") == "open" else set()
    )

    counted = 0
    discrepancies = 0
    variance_value = 0.0
    items_out = []
    for it in items:
        is_counted = it.get("status") == "counted"
        variance = int(it.get("variance") or 0) if is_counted else None
        item_value = (
            variance * float(it.get("average_cost") or 0.0) if variance is not None else None
        )
        if is_counted:
            counted += 1
            if variance:
                discrepancies += 1
            variance_value += item_value or 0.0
        items_out.append({
            "id": str(it["_id"]),
            "part_number": str(it.get("part_number") or ""),
            "description": str(it.get("description") or ""),
            "location_path": str(it.get("location_path") or ""),
            "expected": int(
                it.get("expected_at_count") if is_counted else (it.get("expected_initial") or 0)
            ),
            "counted_qty": int(it.get("counted_qty")) if it.get("counted_qty") is not None else None,
            "variance": variance,
            "variance_value": round(item_value, 2) if item_value is not None else None,
            "status": str(it.get("status") or "pending"),
            "needs_recount": str(it["_id"]) in recount_flags,
            "auto_zeroed": bool(it.get("auto_zeroed")),
        })

    scope = stocktake.get("scope") or {}
    totals = stocktake.get("totals") or None
    return jsonify({
        "ok": True,
        "id": str(stocktake["_id"]),
        "number": stocktake.get("number"),
        "name": stocktake.get("name") or "",
        "status": stocktake.get("status") or "open",
        "scope_location": scope.get("location_path") or "",
        "scope_category": scope.get("category_name") or "",
        "created_label": format_date_mmddyyyy(stocktake.get("created_at")),
        "items_total": len(items),
        "items_counted": counted,
        "discrepancies": discrepancies,
        "variance_value": round(variance_value, 2),
        "totals": {
            "items_adjusted": int(totals.get("items_adjusted") or 0),
            "items_zeroed": int(totals.get("items_zeroed") or 0),
            "shortage_value": float(totals.get("shortage_value") or 0.0),
            "overage_value": float(totals.get("overage_value") or 0.0),
        } if totals else None,
        "items": items_out,
    }), 200
