from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from flask import render_template, request, redirect, url_for, flash, abort
from werkzeug.security import check_password_hash

from app.extensions import get_master_db
from app.utils.admin_auth import (
    admin_login,
    admin_logout,
    admin_required,
    get_current_admin,
)
from app.utils.admin_audit import log_admin_action
from . import admin_panel_bp


# Subscription plans available in the admin UI. Free-form is allowed in DB,
# but the dropdown limits to these for consistency.
PLAN_CHOICES = ("trial", "basic", "pro", "enterprise")


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        abort(404)



# NOTE: `/` is owned by `main.index`, which dispatches by Host. The admin
# host version of `/` redirects to /admin/login or /admin/dashboard
# depending on session state, so users can just open admin.roobico.com.


@admin_panel_bp.get("/admin/login")
def login_page():
    if get_current_admin():
        return redirect(url_for("admin_panel.dashboard"))
    return render_template("admin_panel/login.html")


@admin_panel_bp.post("/admin/login")
def login():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("admin_panel.login_page"))

    master = get_master_db()
    user = master.admin_users.find_one({"email": email, "is_active": True})
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        flash("Invalid credentials.", "error")
        return redirect(url_for("admin_panel.login_page"))

    admin_login(user)
    master.admin_users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login_at": datetime.utcnow()}},
    )
    next_url = request.args.get("next")
    return redirect(next_url or url_for("admin_panel.dashboard"))


@admin_panel_bp.get("/admin/logout")
def logout():
    admin_logout()
    return redirect(url_for("admin_panel.login_page"))


@admin_panel_bp.get("/admin")
@admin_panel_bp.get("/admin/dashboard")
@admin_required
def dashboard():
    admin = get_current_admin()
    master = get_master_db()
    stats = {
        "tenants_total": master.tenants.count_documents({}),
        "tenants_active": master.tenants.count_documents({"status": "active"}),
        "shops_total": master.shops.count_documents({}),
        "shops_active": master.shops.count_documents({"is_active": True}),
        "users_total": master.users.count_documents({}),
        "users_active": master.users.count_documents({"is_active": True}),
    }
    return render_template("admin_panel/dashboard.html", admin=admin, stats=stats)


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------

@admin_panel_bp.get("/admin/tenants")
@admin_required
def tenants_list():
    admin = get_current_admin()
    master = get_master_db()
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status") or "all"

    query: dict = {}
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"slug": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
        ]
    if status_filter == "active":
        query["status"] = "active"
    elif status_filter == "inactive":
        query["status"] = {"$ne": "active"}

    tenants = list(master.tenants.find(query).sort("created_at", -1))

    # Annotate counts (cheap: indexed by tenant_id).
    for t in tenants:
        tid = t["_id"]
        t["shops_count"] = master.shops.count_documents({"tenant_id": tid})
        t["users_count"] = master.users.count_documents({"tenant_id": tid})
        t["plan"] = t.get("plan") or "—"

    return render_template(
        "admin_panel/tenants_list.html",
        admin=admin,
        tenants=tenants,
        q=q,
        status_filter=status_filter,
    )


@admin_panel_bp.get("/admin/tenants/<tenant_id>")
@admin_required
def tenant_detail(tenant_id: str):
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)

    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    shops = list(master.shops.find({"tenant_id": tid}).sort("created_at", 1))
    users = list(master.users.find({"tenant_id": tid}).sort("created_at", 1))

    # Map shop_id -> name for user shop badges.
    shop_name_by_id = {s["_id"]: s.get("name") or s.get("slug") for s in shops}
    for u in users:
        u["shop_names"] = [shop_name_by_id.get(sid, "?") for sid in (u.get("shop_ids") or [])]

    # Billing breakdown. Mechanics (mechanic + senior_mechanic) are billed
    # separately from "full" users (everyone else with a non-mechanic role).
    MECHANIC_ROLES = {"mechanic", "senior_mechanic"}
    locations_active = sum(1 for s in shops if s.get("is_active"))
    locations_inactive = len(shops) - locations_active

    full_active = full_inactive = mech_active = mech_inactive = 0
    for u in users:
        role = (u.get("role") or "").strip().lower()
        is_active = bool(u.get("is_active"))
        if role in MECHANIC_ROLES:
            if is_active:
                mech_active += 1
            else:
                mech_inactive += 1
        else:
            if is_active:
                full_active += 1
            else:
                full_inactive += 1

    billing = {
        "locations_total": len(shops),
        "locations_active": locations_active,
        "locations_inactive": locations_inactive,
        "users_total": len(users),
        "full_active": full_active,
        "full_inactive": full_inactive,
        "full_total": full_active + full_inactive,
        "mech_active": mech_active,
        "mech_inactive": mech_inactive,
        "mech_total": mech_active + mech_inactive,
    }

    return render_template(
        "admin_panel/tenant_detail.html",
        admin=admin,
        tenant=tenant,
        shops=shops,
        users=users,
        plan_choices=PLAN_CHOICES,
        billing=billing,
    )


@admin_panel_bp.post("/admin/tenants/<tenant_id>/toggle-active")
@admin_required
def tenant_toggle_active(tenant_id: str):
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    new_status = "inactive" if tenant.get("status") == "active" else "active"
    master.tenants.update_one(
        {"_id": tid},
        {"$set": {"status": new_status, "updated_at": datetime.utcnow()}},
    )
    log_admin_action(
        admin,
        action="tenant.toggle_active",
        target_type="tenant",
        target_id=tid,
        before={"status": tenant.get("status")},
        after={"status": new_status},
        extra={"tenant_name": tenant.get("name")},
    )
    flash(f"Tenant '{tenant.get('name')}' is now {new_status}.", "success")
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


@admin_panel_bp.post("/admin/tenants/<tenant_id>/plan")
@admin_required
def tenant_update_plan(tenant_id: str):
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    plan = (request.form.get("plan") or "").strip().lower()
    if plan not in PLAN_CHOICES:
        flash("Invalid plan.", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    old_plan = tenant.get("plan")
    if plan == old_plan:
        flash("Plan unchanged.", "info")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    master.tenants.update_one(
        {"_id": tid},
        {"$set": {"plan": plan, "updated_at": datetime.utcnow()}},
    )
    log_admin_action(
        admin,
        action="tenant.update_plan",
        target_type="tenant",
        target_id=tid,
        before={"plan": old_plan},
        after={"plan": plan},
        extra={"tenant_name": tenant.get("name")},
    )
    flash(f"Plan changed: {old_plan or '—'} → {plan}.", "success")
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


# ---------------------------------------------------------------------------
# Shops (locations)
# ---------------------------------------------------------------------------

@admin_panel_bp.post("/admin/shops/<shop_id>/toggle-active")
@admin_required
def shop_toggle_active(shop_id: str):
    admin = get_current_admin()
    master = get_master_db()
    sid = _oid(shop_id)
    shop = master.shops.find_one({"_id": sid})
    if not shop:
        abort(404)

    new_active = not bool(shop.get("is_active", True))
    master.shops.update_one(
        {"_id": sid},
        {"$set": {
            "is_active": new_active,
            "status": "active" if new_active else "inactive",
            "updated_at": datetime.utcnow(),
        }},
    )
    log_admin_action(
        admin,
        action="shop.toggle_active",
        target_type="shop",
        target_id=sid,
        before={"is_active": shop.get("is_active")},
        after={"is_active": new_active},
        extra={"shop_name": shop.get("name"), "tenant_id": shop.get("tenant_id")},
    )
    flash(
        f"Location '{shop.get('name')}' is now {'active' if new_active else 'inactive'}.",
        "success",
    )
    tenant_id = shop.get("tenant_id")
    if tenant_id:
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=str(tenant_id)))
    return redirect(url_for("admin_panel.tenants_list"))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@admin_panel_bp.post("/admin/users/<user_id>/toggle-active")
@admin_required
def user_toggle_active(user_id: str):
    admin = get_current_admin()
    master = get_master_db()
    uid = _oid(user_id)
    user = master.users.find_one({"_id": uid})
    if not user:
        abort(404)

    new_active = not bool(user.get("is_active", True))
    master.users.update_one(
        {"_id": uid},
        {"$set": {"is_active": new_active, "updated_at": datetime.utcnow()}},
    )
    log_admin_action(
        admin,
        action="user.toggle_active",
        target_type="user",
        target_id=uid,
        before={"is_active": user.get("is_active")},
        after={"is_active": new_active},
        extra={
            "user_email": user.get("email"),
            "tenant_id": user.get("tenant_id"),
        },
    )
    flash(
        f"User {user.get('email')} is now {'active' if new_active else 'inactive'}.",
        "success",
    )
    tenant_id = user.get("tenant_id")
    if tenant_id:
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=str(tenant_id)))
    return redirect(url_for("admin_panel.tenants_list"))

