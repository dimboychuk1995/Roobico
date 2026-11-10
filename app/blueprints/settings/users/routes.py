from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from flask import current_app, render_template, request, redirect, url_for, flash, session

from app.utils import audit_log
from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import (
    login_required,
    SESSION_USER_ID,
    SESSION_TENANT_ID,
    SESSION_TENANT_DB,
    SESSION_SHOP_ID,
)
from app.constants.permissions import ALL_PERMISSIONS, PROTECTED_ROLE_KEYS
from app.utils.permissions import (
    filter_nav_items,
    get_effective_permissions,
    has_permission,
    permission_required,
)
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context
from app.utils.pagination import get_pagination_params, get_sort_params, paginate_find
from app.utils.mongo_search import build_regex_search_filter
from app.utils.user_roles import group_users_by_role, load_role_catalog, role_keys_matching
from app.utils.user_names import refresh_user_name_snapshots, user_display_name


# -----------------------------
# Helpers
# -----------------------------

def utcnow():
    return datetime.now(timezone.utc)


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _id_variants(value):
    """
    Варианты для надёжного поиска tenant_id:
    - как есть
    - ObjectId(.) если возможно
    - str(.)
    """
    if value is None:
        return []

    variants = [value]

    oid = _maybe_object_id(value)
    if oid is not None:
        variants.append(oid)

    variants.append(str(value))

    out = []
    seen = set()
    for v in variants:
        # str и ObjectId с одинаковым hex — РАЗНЫЕ значения для Mongo,
        # поэтому ключ дедупликации обязан учитывать тип.
        key = (type(v).__name__, str(v))
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _get_tenant_db():
    db_name = session.get(SESSION_TENANT_DB)
    if not db_name:
        return None
    client = get_mongo_client()
    return client[db_name]


def _load_current_user(master):
    user_id = _maybe_object_id(session.get(SESSION_USER_ID))
    if not user_id:
        return None
    return master.users.find_one({"_id": user_id, "is_active": True})


def _is_protected_role(role_key) -> bool:
    return str(role_key or "").strip().lower() in PROTECTED_ROLE_KEYS


def _actor_is_owner(current_user) -> bool:
    """Владелец (protected-роль) — единственный, кто может трогать
    owner-аккаунты и раздавать роль Owner. Иначе любой с
    settings.manage_users мог бы сделать владельцем себя."""
    return _is_protected_role((current_user or {}).get("role"))


def _role_beyond_actor(role_doc: dict | None) -> list[str]:
    """Права назначаемой роли, которых нет у самого актёра.

    Менеджер с settings.manage_users, но без settings.manage_roles, не должен
    обходить ограничение через назначение коллеге роли General manager (в
    ней есть manage_roles) — иначе любой «кадровик» раздаёт права, которых
    у него нет. Владелец (protected-роль) назначает любые роли.
    """
    if not role_doc:
        return []
    if str(role_doc.get("key") or "").strip().lower() in PROTECTED_ROLE_KEYS:
        role_perms = set(ALL_PERMISSIONS)
    else:
        role_perms = {str(p) for p in (role_doc.get("permissions") or []) if isinstance(p, str)}
    return sorted(role_perms - get_effective_permissions())


def _can_manage_pay() -> bool:
    return has_permission("settings.manage_pay")


def _render_settings_page(template_name: str, **ctx):
    """
    Один общий рендер для settings-страниц через единый layout builder.
    """
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")

    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session expired. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    layout.update(ctx)
    return render_template(template_name, **layout)


def _safe_str_list(v):
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if x is not None]


PAY_TYPES = {"salary", "hourly"}


def _parse_pay_fields(form) -> tuple[str, float | None]:
    """Extract pay_type ∈ {salary, hourly} and salary_amount (None for hourly)."""
    pt = (form.get("pay_type") or "salary").strip().lower()
    if pt not in PAY_TYPES:
        pt = "salary"
    if pt == "hourly":
        return "hourly", None
    raw = (form.get("salary_amount") or "").strip()
    if not raw:
        return "salary", None
    try:
        val = float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return "salary", None
    if val < 0:
        return "salary", None
    return "salary", round(val, 2)


def _load_shops_for_tenant(master, tenant_id):
    """
    Отдаём список шап (для чекбоксов) в формате:
      [{id, name}, ...]
    """
    shops = []
    if not tenant_id:
        return shops

    for s in master.shops.find({"tenant_id": tenant_id}).sort("created_at", 1):
        shops.append({
            "id": str(s["_id"]),
            "name": s.get("name") or "—",
        })
    return shops


# Список пользователей сгруппирован по ролям, поэтому показывается одной
# страницей (группа не должна рваться между страницами); пагинация остаётся
# только предохранителем. Сортировка — лишь по полям внутри группы.
USERS_PAGE_SIZE = 500
USERS_SORT_FIELDS = ["name", "email", "created_at"]


def _parse_status_filter(raw) -> str:
    """Общий фильтр списков: active (по умолчанию) / inactive / all."""
    status = (raw or "active").strip().lower()
    return status if status in ("active", "inactive", "all") else "active"


def _redirect_users_index():
    q = (request.values.get("q") or "").strip()
    page = (request.values.get("page") or "").strip()
    per_page = (request.values.get("per_page") or "").strip()
    status = (request.values.get("status") or "").strip()

    params = {}
    if q:
        params["q"] = q
    if status and status != "active":
        params["status"] = status
    if page:
        params["page"] = page
    if per_page:
        params["per_page"] = per_page
    return redirect(url_for("settings.users_index", **params))


def _refresh_name_snapshots_after_rename(master, target: dict, update_doc: dict) -> None:
    """
    Пользователь переименован (сменилось отображаемое имя) — переписать
    снимки его имени в shop-базах тенанта: назначения на WO, отметки Done,
    тайм-логи, бригады календаря (app/utils/user_names). Экраны и так берут
    живое имя по id; снимки нужны «сырым» читателям — AI-ассистенту и старым
    сборкам приложения. Best effort: правка пользователя уже сохранена, сбой
    рефреша — только в лог. labors_rev не двигается (это не правка WO):
    Save, прочитавший WO до переименования, может вернуть в снимок старое
    имя — косметика, экраны его не показывают.
    """
    old_name = user_display_name(target)
    new_name = user_display_name({**target, **update_doc})
    if not new_name or new_name == old_name:
        return
    try:
        refresh_user_name_snapshots(master, target.get("tenant_id"), target["_id"], new_name)
    except Exception:
        current_app.logger.exception("Users: failed to refresh name snapshots of user %s", target.get("_id"))


# -----------------------------
# UI Routes
# -----------------------------

@settings_bp.route("/users", methods=["GET", "POST"])
@login_required
@permission_required("settings.manage_users")
def users_index():
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    current_user = _load_current_user(master)
    if not current_user:
        flash("User session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    # tenant id (нормализуем)
    tenant_from_user = current_user.get("tenant_id") or tenant_id_raw
    tenant_oid = _maybe_object_id(tenant_from_user) or _maybe_object_id(tenant_id_raw)

    if request.method == "POST":
        return _handle_create_user(master, current_user, tenant_oid, tenant_id_raw)

    tenant_values = _id_variants(tenant_from_user)

    q = (request.args.get("q") or "").strip()
    status = _parse_status_filter(request.args.get("status"))
    sort_by = (request.args.get("sort_by") or "").strip()

    # Роли тенанта (системные + кастомные): подписи групп и селекты ролей.
    role_catalog = load_role_catalog(_get_tenant_db())

    # per_page из URL не слушаем: группы по ролям не должны рваться между
    # страницами (старые ссылки и редиректы несли per_page=20).
    page, _ = get_pagination_params(request.args, default_per_page=USERS_PAGE_SIZE, max_per_page=USERS_PAGE_SIZE)
    per_page = USERS_PAGE_SIZE
    query_filter = {"tenant_id": {"$in": tenant_values}}
    if status == "active":
        query_filter["is_active"] = {"$ne": False}
    elif status == "inactive":
        query_filter["is_active"] = False
    search_filter = build_regex_search_filter(
        q,
        text_fields=["name", "first_name", "last_name", "email", "phone", "role"],
        object_id_fields=["_id"],
    )
    if search_filter:
        # В users.role лежит ключ, а список показывает название роли —
        # «Parts manager» или новое имя переименованной роли тоже ищутся.
        role_keys = role_keys_matching(role_catalog, q)
        if role_keys:
            search_filter["$or"].append({"role": {"$in": role_keys}})
        query_filter = {"$and": [query_filter, search_filter]}

    users, pagination = paginate_find(
        master.users,
        query_filter,
        get_sort_params(request.args, [("created_at", -1)], USERS_SORT_FIELDS),
        page,
        per_page,
    )

    for u in users:
        u["_id_str"] = str(u.get("_id") or "")
        raw_shop_ids = u.get("shop_ids") if isinstance(u.get("shop_ids"), list) else []
        u["shop_ids_str"] = [str(x) for x in raw_shop_ids if x is not None]

    # Группы по ролям; явная сортировка из URL (клик по Name/Email)
    # действует внутри каждой группы, без неё — по имени.
    user_groups = group_users_by_role(users, role_catalog, keep_order=sort_by in USERS_SORT_FIELDS)

    actor_is_owner = _actor_is_owner(current_user)
    # Селекты ролей в формах — роли тенанта в том же порядке, кастомные тоже;
    # Owner — только владельцу. Эскалацию сервер проверяет при сохранении.
    role_options = [r for r in role_catalog if actor_is_owner or not _is_protected_role(r["key"])]

    # ✅ шапы для чекбоксов (только текущий tenant)
    shops_for_form = _load_shops_for_tenant(master, tenant_oid)

    return _render_settings_page(
        "public/settings/users.html",
        users=users,
        user_groups=user_groups,
        role_options=role_options,
        pagination=pagination,
        q=q,
        status=status,
        shops_for_form=shops_for_form,
        active_shop_id=str(session.get(SESSION_SHOP_ID) or ""),
        sort_by=sort_by,
        sort_dir=(request.args.get("sort_dir") or "").strip(),
        actor_is_owner=actor_is_owner,
        can_manage_pay=_can_manage_pay(),
        protected_role_keys=sorted(PROTECTED_ROLE_KEYS),
    )


@settings_bp.route("/users/<user_id>/edit", methods=["POST"])
@login_required
@permission_required("settings.manage_users")
def users_edit(user_id):
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    current_user = _load_current_user(master)
    if not current_user:
        flash("User session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    target_id = _maybe_object_id(user_id)
    if not target_id:
        flash("Invalid user id.", "error")
        return _redirect_users_index()

    tenant_values = _id_variants(current_user.get("tenant_id") or tenant_id_raw)
    target = master.users.find_one({"_id": target_id, "tenant_id": {"$in": tenant_values}})
    if not target:
        flash("User not found.", "error")
        return _redirect_users_index()

    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    role = (request.form.get("role") or "viewer").strip().lower()
    is_active = bool(request.form.get("is_active"))

    pay_type, salary_amount = _parse_pay_fields(request.form)

    if not first_name or not last_name or not email:
        flash("Please fill first name, last name and email.", "error")
        return _redirect_users_index()

    actor_is_owner = _actor_is_owner(current_user)
    is_self = str(target_id) == str(session.get(SESSION_USER_ID))
    current_role = str(target.get("role") or "viewer").strip().lower()
    if _is_protected_role(current_role) and not actor_is_owner:
        flash("Only an owner can edit an owner account.", "error")
        return _redirect_users_index()
    if _is_protected_role(role) and not actor_is_owner:
        flash("Only an owner can assign the Owner role.", "error")
        return _redirect_users_index()
    if is_self and role != current_role:
        flash("You cannot change your own role.", "error")
        return _redirect_users_index()

    existing = master.users.find_one({"email": email})
    if existing and existing.get("_id") != target_id:
        flash("User with this email already exists.", "error")
        return _redirect_users_index()

    tdb = _get_tenant_db()
    if tdb is None:
        flash("Tenant DB not found in session. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    role_doc = tdb.roles.find_one({"key": role})
    if not role_doc:
        flash("Selected role does not exist in tenant roles.", "error")
        return _redirect_users_index()

    # Смена роли — только на роль, чьи права есть у самого актёра
    # (владелец — любые). Правка профиля без смены роли не блокируется,
    # даже если роль пользователя шире актёрской.
    if role != current_role and not actor_is_owner:
        beyond = _role_beyond_actor(role_doc)
        if beyond:
            flash(
                "You cannot assign a role with permissions you don't have: " + ", ".join(beyond),
                "error",
            )
            return _redirect_users_index()

    selected_shop_ids_raw = request.form.getlist("shop_ids")
    allowed_shop_ids = set(_safe_str_list(session.get("shop_ids")))

    selected_shop_oids = []
    for sid in selected_shop_ids_raw:
        sid_str = str(sid).strip()
        if not sid_str:
            continue
        if allowed_shop_ids and sid_str not in allowed_shop_ids:
            continue
        oid = _maybe_object_id(sid_str)
        if oid and oid not in selected_shop_oids:
            selected_shop_oids.append(oid)

    if not selected_shop_oids:
        selected_shop_oids = target.get("shop_ids") if isinstance(target.get("shop_ids"), list) else []

    update_doc = {
        "first_name": first_name,
        "last_name": last_name,
        "name": f"{first_name} {last_name}".strip(),
        "email": email,
        "phone": phone or None,
        "role": role,
        "shop_ids": selected_shop_oids,
        "is_active": is_active,
        "updated_at": utcnow(),
    }

    # Зарплата/тип оплаты — отдельное право settings.manage_pay, и никто
    # не меняет оплату самому себе (иначе менеджер с manage_users поднял бы
    # себе оклад). Без права поля формы игнорируются — текущие значения
    # остаются как есть.
    if _can_manage_pay() and not is_self:
        update_doc["pay_type"] = pay_type
        update_doc["salary_amount"] = salary_amount

    # Пароль здесь НЕ меняется даже владельцем: аккаунт и пароль — строго
    # на стороне пользователя (приглашение / forgot password).

    if str(target_id) == str(session.get(SESSION_USER_ID)) and not is_active:
        flash("You cannot deactivate your own account.", "error")
        return _redirect_users_index()

    master.users.update_one({"_id": target_id}, {"$set": update_doc})
    audit_log.log_event("update", "user", target_id, label=audit_log.user_label(target),
                        changes=audit_log.diff_fields(target, update_doc, {"first_name": "First name", "last_name": "Last name", "email": "Email",
                                                                       "phone": "Phone", "role": "Role", "is_active": "Active",
                                                                       "shop_ids": "Locations", "pay_type": "Pay type", "salary_amount": "Salary"}))
    # Новое имя — и в снимках старых WO / тайм-логов / календаря.
    _refresh_name_snapshots_after_rename(master, target, update_doc)
    flash("User updated successfully.", "success")
    return _redirect_users_index()


@settings_bp.route("/users/<user_id>/deactivate", methods=["POST"])
@login_required
@permission_required("settings.manage_users")
def users_deactivate(user_id):
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    target_id = _maybe_object_id(user_id)
    if not target_id:
        flash("Invalid user id.", "error")
        return _redirect_users_index()

    if str(target_id) == str(session.get(SESSION_USER_ID)):
        flash("You cannot deactivate your own account.", "error")
        return _redirect_users_index()

    tenant_values = _id_variants(tenant_id_raw)
    target = master.users.find_one({"_id": target_id, "tenant_id": {"$in": tenant_values}}, {"role": 1})
    if target and _is_protected_role(target.get("role")) and not _actor_is_owner(_load_current_user(master)):
        flash("Only an owner can deactivate an owner account.", "error")
        return _redirect_users_index()

    res = master.users.update_one(
        {"_id": target_id, "tenant_id": {"$in": tenant_values}},
        {"$set": {"is_active": False, "updated_at": utcnow()}},
    )

    if res.matched_count == 0:
        flash("User not found.", "error")
        return _redirect_users_index()

    audit_log.log_event("deactivate", "user", target_id, label=audit_log.user_label(target or {}) or str(target_id))
    flash("User deactivated.", "success")
    return _redirect_users_index()


@settings_bp.route("/users/<user_id>/activate", methods=["POST"])
@login_required
@permission_required("settings.manage_users")
def users_activate(user_id):
    """Обратная операция к deactivate — кнопка Activate в списке (фильтр
    Inactive users). То же, что галочка «Active user» в форме редактирования."""
    master = get_master_db()

    tenant_id_raw = session.get(SESSION_TENANT_ID)
    if not tenant_id_raw:
        flash("Tenant session missing. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    target_id = _maybe_object_id(user_id)
    if not target_id:
        flash("Invalid user id.", "error")
        return _redirect_users_index()

    tenant_values = _id_variants(tenant_id_raw)
    target = master.users.find_one({"_id": target_id, "tenant_id": {"$in": tenant_values}}, {"role": 1})
    if target and _is_protected_role(target.get("role")) and not _actor_is_owner(_load_current_user(master)):
        flash("Only an owner can activate an owner account.", "error")
        return _redirect_users_index()

    res = master.users.update_one(
        {"_id": target_id, "tenant_id": {"$in": tenant_values}},
        {"$set": {"is_active": True, "updated_at": utcnow()}},
    )

    if res.matched_count == 0:
        flash("User not found.", "error")
        return _redirect_users_index()

    _activated = master.users.find_one({"_id": target_id}, {"name": 1, "first_name": 1, "last_name": 1, "email": 1}) or {}
    audit_log.log_event("activate", "user", target_id, label=audit_log.user_label(_activated))
    flash("User activated.", "success")
    return _redirect_users_index()


def _handle_create_user(master, current_user, tenant_oid, tenant_id_raw):
    first_name = (request.form.get("first_name") or "").strip()
    last_name = (request.form.get("last_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    role = (request.form.get("role") or "viewer").strip().lower()
    is_active = bool(request.form.get("is_active"))

    pay_type, salary_amount = _parse_pay_fields(request.form)

    if not first_name or not last_name or not email:
        flash("Please fill first name, last name and email.", "error")
        return redirect(url_for("settings.users_index"))

    if "@" not in email:
        flash("Valid email is required.", "error")
        return redirect(url_for("settings.users_index"))

    if _is_protected_role(role) and not _actor_is_owner(current_user):
        flash("Only an owner can assign the Owner role.", "error")
        return redirect(url_for("settings.users_index"))

    tenant_id = tenant_oid or _maybe_object_id(current_user.get("tenant_id")) or _maybe_object_id(tenant_id_raw)

    # Занятый email: активный пользователь или пользователь чужого тенанта —
    # ошибка. Деактивированный пользователь НАШЕГО тенанта — реактивируем его
    # с новыми данными из формы вместо создания дубликата.
    existing = master.users.find_one({"email": email})
    reactivate_target = None
    if existing is not None:
        same_tenant = str(existing.get("tenant_id")) == str(tenant_id)
        if existing.get("is_active", True) or not same_tenant:
            flash("User with this email already exists.", "error")
            return redirect(url_for("settings.users_index"))
        reactivate_target = existing

    # Validate role exists in tenant DB
    tdb = _get_tenant_db()
    if tdb is None:
        flash("Tenant DB not found in session. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    role_doc = tdb.roles.find_one({"key": role})
    if not role_doc:
        flash("Selected role does not exist in tenant roles.", "error")
        return redirect(url_for("settings.users_index"))

    if not _actor_is_owner(current_user):
        beyond = _role_beyond_actor(role_doc)
        if beyond:
            flash(
                "You cannot assign a role with permissions you don't have: " + ", ".join(beyond),
                "error",
            )
            return redirect(url_for("settings.users_index"))

    if not _can_manage_pay():
        # Без права на зарплату новый сотрудник заводится с дефолтной
        # оплатой — поля формы не учитываются.
        pay_type, salary_amount = "salary", None

    creator_id = _maybe_object_id(session.get(SESSION_USER_ID))

    # ✅ Чекбоксы шап:
    # name="shop_ids" -> request.form.getlist("shop_ids")
    selected_shop_ids_raw = request.form.getlist("shop_ids")

    # Разрешаем выдавать доступ ТОЛЬКО к тем шапам, которые доступны создателю (из сессии)
    allowed_shop_ids = set(_safe_str_list(session.get("shop_ids")))

    selected_shop_oids = []
    for sid in selected_shop_ids_raw:
        sid_str = str(sid).strip()
        if not sid_str:
            continue
        if allowed_shop_ids and sid_str not in allowed_shop_ids:
            # попытка выдать доступ к “чужой” шапе — игнорим
            continue
        oid = _maybe_object_id(sid_str)
        if oid and oid not in selected_shop_oids:
            selected_shop_oids.append(oid)

    # Если ничего не выбрали — fallback на активную шапу
    if not selected_shop_oids:
        active_shop_oid = _maybe_object_id(session.get(SESSION_SHOP_ID))
        creator_shop_ids = current_user.get("shop_ids") if isinstance(current_user.get("shop_ids"), list) else []
        if active_shop_oid is None and creator_shop_ids:
            active_shop_oid = creator_shop_ids[0]
        selected_shop_oids = [active_shop_oid] if active_shop_oid is not None else []

    # Пароль — строго на стороне пользователя: аккаунт всегда создаётся
    # приглашением, пароль человек задаёт сам по ссылке из письма.
    # Пустой password_hash логин не пропустит.
    user_doc = {
        "tenant_id": tenant_id,
        "shop_ids": selected_shop_oids,  # ✅ только shop_ids
        "email": email,
        "password_hash": "",
        "first_name": first_name,
        "last_name": last_name,
        "name": f"{first_name} {last_name}".strip(),
        "phone": phone or None,
        "role": role,
        "is_active": is_active,
        "pay_type": pay_type,
        "salary_amount": salary_amount,
        "must_reset_password": False,
        "invite_pending": True,
        "invited_at": utcnow(),
        "invited_by": creator_id,
        "allow_permissions": [],
        "deny_permissions": [],
        "updated_at": utcnow(),
    }

    if reactivate_target is not None:
        # Реактивация: полностью перезаписываем профиль новыми данными формы,
        # created_at/created_by остаются от исходной записи. Старый пароль
        # затирается — человек задаст новый по приглашению.
        master.users.update_one({"_id": reactivate_target["_id"]}, {"$set": user_doc})
        user_id = reactivate_target["_id"]
        flash("User with this email was deactivated — reactivated with the new data.", "success")
    else:
        user_doc["created_at"] = utcnow()
        user_doc["created_by"] = creator_id
        user_id = master.users.insert_one(user_doc).inserted_id

    _send_invite_or_warn(master, {**user_doc, "_id": user_id})
    audit_log.log_event("create", "user", user_id, label=audit_log.user_label(user_doc),
                        summary=f"User invited: {audit_log.user_label(user_doc)} ({role})" + (" (reactivated)" if reactivate_target is not None else ""),
                        details={"role": role, "email": email})

    return redirect(url_for("settings.users_index"))


def _send_invite_or_warn(master, user_doc):
    """Отправить приглашение; при неудаче — предупредить (есть кнопка Resend)."""
    from flask import current_app

    from app.blueprints.auth.routes import send_invite_email

    tenant = master.tenants.find_one({"_id": user_doc.get("tenant_id")}) or {}
    try:
        send_invite_email(master, user_doc, shop_name=tenant.get("name") or "")
        flash(f"Invitation sent to {user_doc['email']} — the user will set their own password.", "success")
    except Exception:
        current_app.logger.exception("Failed to send invite email")
        flash(
            "User created, but the invitation email failed to send. "
            "Use the Resend invite button to try again.",
            "error",
        )


@settings_bp.route("/users/<user_id>/resend-invite", methods=["POST"])
@login_required
@permission_required("settings.manage_users")
def users_resend_invite(user_id):
    master = get_master_db()

    target_id = _maybe_object_id(user_id)
    if not target_id:
        flash("Invalid user id.", "error")
        return _redirect_users_index()

    tenant_values = _id_variants(session.get(SESSION_TENANT_ID))
    target = master.users.find_one({"_id": target_id, "tenant_id": {"$in": tenant_values}})
    if not target:
        flash("User not found.", "error")
        return _redirect_users_index()

    if not target.get("invite_pending"):
        flash("This user has already set a password.", "info")
        return _redirect_users_index()

    _send_invite_or_warn(master, target)
    audit_log.log_event("send", "user", target_id, label=audit_log.user_label(target), summary=f"Invitation re-sent to {target.get('email')}")
    return _redirect_users_index()
