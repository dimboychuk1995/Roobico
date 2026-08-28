from datetime import datetime, timedelta

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
from app.utils.stripe_client import (
    ANNUAL_DISCOUNT_PERCENT,
    PRICE_PER_FULL_USER_CENTS,
    PRICE_PER_LOCATION_CENTS,
    PRICE_PER_MECHANIC_CENTS,
    annual_amount_cents,
    billing_period,
)
from . import admin_panel_bp


# Pricing (USD/month), derived from the cent constants in
# app.utils.stripe_client — the single source of truth. No base fee: the
# first active location and the first active full user are free; extra
# units are billed per piece, mechanics are always per piece.
PRICE_PER_LOCATION = PRICE_PER_LOCATION_CENTS // 100
PRICE_PER_FULL_USER = PRICE_PER_FULL_USER_CENTS // 100
PRICE_PER_MECHANIC = PRICE_PER_MECHANIC_CENTS // 100


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

    # Бесплатный тир: первая локация и первый полный юзер — $0; сверх —
    # поштучно, механики — все поштучно.
    locations_extra = max(0, locations_active - 1)
    full_extra = max(0, full_active - 1)
    locations_cost = locations_extra * PRICE_PER_LOCATION
    full_cost = full_extra * PRICE_PER_FULL_USER
    mech_cost = mech_active * PRICE_PER_MECHANIC
    monthly_total = locations_cost + full_cost + mech_cost

    # Индивидуальная цена (скидка % / фикс) — то, что реально уйдёт в инвойс.
    from app.utils.stripe_client import apply_billing_discount
    effective_cents, discount_desc = apply_billing_discount(monthly_total * 100, tenant)

    # Годовой период: инвойс раз в год на 12 месячных сумм минус 20%.
    period = billing_period(tenant)
    annual_cents = annual_amount_cents(effective_cents)
    due_cents = annual_cents if period == "annual" else effective_cents

    billing = {
        "discount": tenant.get("billing_discount") or {},
        "discount_desc": discount_desc,
        "effective_total": effective_cents / 100,
        "period": period,
        "annual_total": annual_cents / 100,
        "annual_discount_percent": ANNUAL_DISCOUNT_PERCENT,
        "due_total": due_cents / 100,
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
        "locations_extra": locations_extra,
        "full_extra": full_extra,
        "price_per_location": PRICE_PER_LOCATION,
        "price_per_full_user": PRICE_PER_FULL_USER,
        "price_per_mechanic": PRICE_PER_MECHANIC,
        "locations_cost": locations_cost,
        "full_cost": full_cost,
        "mech_cost": mech_cost,
        "monthly_total": monthly_total,
    }

    # Subscription period (manual for now; Stripe will populate this later).
    sub_until = tenant.get("subscription_until")
    sub_status = (tenant.get("subscription_status") or "").lower()
    now = datetime.utcnow()
    if isinstance(sub_until, datetime):
        delta = sub_until - now
        days_left = int(delta.total_seconds() // 86400)
        if delta.total_seconds() <= 0:
            sub_state = "expired"
        elif days_left <= 7:
            sub_state = "ending_soon"
        else:
            sub_state = "active"
    else:
        days_left = None
        sub_state = sub_status or "none"

    subscription = {
        "until": sub_until,
        "days_left": days_left,
        "state": sub_state,
        "status_raw": sub_status,
    }

    # Stripe-side billing info (lightweight: read what the webhooks have
    # cached on the tenant doc; no live Stripe API call here so the page
    # stays fast even if Stripe is down).
    stripe_info = {
        "configured": bool(__import__("flask").current_app.config.get("STRIPE_SECRET_KEY")),
        "customer_id": tenant.get("stripe_customer_id"),
        "default_card": tenant.get("stripe_default_card"),
        "last_invoice_id": tenant.get("last_invoice_id"),
        "last_invoice_amount_cents": tenant.get("last_invoice_amount_cents"),
        "last_paid_at": tenant.get("last_paid_at"),
        "test_mode": bool(__import__("flask").current_app.config.get("STRIPE_TEST_MODE")),
    }
    if stripe_info["customer_id"]:
        base = "https://dashboard.stripe.com" + ("/test" if stripe_info["test_mode"] else "")
        stripe_info["customer_url"] = f"{base}/customers/{stripe_info['customer_id']}"
        if stripe_info["last_invoice_id"]:
            stripe_info["invoice_url"] = f"{base}/invoices/{stripe_info['last_invoice_id']}"

    # История инвойсов из нашего реестра (пишут webhook'и + create_billing_invoice).
    invoices = list(
        master.billing_invoices
        .find({"tenant_id": tid})
        .sort("created_at", -1)
        .limit(15)
    )

    return render_template(
        "admin_panel/tenant_detail.html",
        admin=admin,
        tenant=tenant,
        shops=shops,
        users=users,
        billing=billing,
        subscription=subscription,
        stripe_info=stripe_info,
        invoices=invoices,
    )


@admin_panel_bp.post("/admin/tenants/<tenant_id>/set-discount")
@admin_required
def tenant_set_discount(tenant_id: str):
    """Индивидуальная цена тенанта: скидка в % или фиксированная сумма/мес.
    Применяется ко всем будущим инвойсам (create_billing_invoice)."""
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    mode = (request.form.get("mode") or "").strip().lower()
    note = (request.form.get("note") or "").strip()[:200]
    before = tenant.get("billing_discount")

    if mode == "none":
        new_discount = None
    elif mode in ("percent", "fixed"):
        try:
            value = float(request.form.get("value") or "")
        except ValueError:
            flash("Discount value must be a number.", "error")
            return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))
        if mode == "percent" and not (0 < value <= 100):
            flash("Percent discount must be between 0 and 100.", "error")
            return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))
        if mode == "fixed" and value < 0:
            flash("Fixed price cannot be negative.", "error")
            return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))
        new_discount = {"type": mode, "value": value, "note": note}
    else:
        flash("Invalid discount mode.", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    if new_discount is None:
        master.tenants.update_one(
            {"_id": tid},
            {"$unset": {"billing_discount": ""},
             "$set": {"updated_at": datetime.utcnow()}},
        )
    else:
        master.tenants.update_one(
            {"_id": tid},
            {"$set": {"billing_discount": new_discount,
                      "updated_at": datetime.utcnow()}},
        )

    log_admin_action(
        admin,
        action="tenant.billing.set_discount",
        target_type="tenant",
        target_id=tid,
        before={"billing_discount": before},
        after={"billing_discount": new_discount},
        extra={"tenant_name": tenant.get("name"), "note": note},
    )
    if new_discount is None:
        flash("Custom pricing removed — tenant is billed at the standard rate.", "success")
    elif mode == "percent":
        flash(f"Discount set: {value:g}% off the computed monthly total.", "success")
    else:
        flash(f"Custom price set: ${value:,.2f}/mo regardless of unit counts.", "success")
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


@admin_panel_bp.post("/admin/tenants/<tenant_id>/set-billing-period")
@admin_required
def tenant_set_billing_period(tenant_id: str):
    """Период биллинга тенанта: monthly или annual (годовая −20%).
    Влияет на будущие инвойсы (charge/send-invoice и авто-продление)."""
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    period = (request.form.get("period") or "").strip().lower()
    if period not in ("monthly", "annual"):
        flash("Invalid billing period.", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    before = billing_period(tenant)
    master.tenants.update_one(
        {"_id": tid},
        {"$set": {"billing_period": period, "updated_at": datetime.utcnow()}},
    )
    log_admin_action(
        admin,
        action="tenant.billing.set_period",
        target_type="tenant",
        target_id=tid,
        before={"billing_period": before},
        after={"billing_period": period},
        extra={"tenant_name": tenant.get("name")},
    )
    if period == "annual":
        flash(
            f"Billing period set to annual — 12 × monthly minus "
            f"{ANNUAL_DISCOUNT_PERCENT}%.",
            "success",
        )
    else:
        flash("Billing period set to monthly.", "success")
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


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


# ---------------------------------------------------------------------------
# Subscription period (manual extension)
# ---------------------------------------------------------------------------

def _parse_extend_input(form) -> tuple[datetime | None, str]:
    """
    Returns (new_paid_until, mode) or (None, "") on invalid input.
    Modes: "days", "months", "until".
    """
    mode = (form.get("mode") or "").strip().lower()
    now = datetime.utcnow()

    if mode == "days":
        try:
            days = int(form.get("days") or "0")
        except ValueError:
            return None, ""
        if days <= 0 or days > 3650:
            return None, ""
        return now + timedelta(days=days), "days"

    if mode == "months":
        try:
            months = int(form.get("months") or "0")
        except ValueError:
            return None, ""
        if months <= 0 or months > 120:
            return None, ""
        # Approx 30 days/month — admin sees the resulting date and can correct.
        return now + timedelta(days=30 * months), "months"

    if mode == "until":
        raw = (form.get("until") or "").strip()
        if not raw:
            return None, ""
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None, ""
        # Set to end of that day so the tenant has the full day.
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=0)
        if dt <= now:
            return None, ""
        return dt, "until"

    return None, ""


@admin_panel_bp.post("/admin/tenants/<tenant_id>/extend-subscription")
@admin_required
def tenant_extend_subscription(tenant_id: str):
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    new_until, mode = _parse_extend_input(request.form)
    if not new_until:
        flash("Invalid extension input.", "error")
        return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))

    extend_from_current = (request.form.get("extend_from_current") == "on")
    current_until = tenant.get("subscription_until")
    if extend_from_current and isinstance(current_until, datetime) and current_until > datetime.utcnow():
        # Add the same delta on top of the current end date instead of "from now".
        delta = new_until - datetime.utcnow()
        new_until = current_until + delta

    note = (request.form.get("note") or "").strip()[:500]

    update = {
        "subscription_until": new_until,
        "subscription_status": "active",
        "updated_at": datetime.utcnow(),
    }
    master.tenants.update_one({"_id": tid}, {"$set": update})

    log_admin_action(
        admin,
        action="tenant.extend_subscription",
        target_type="tenant",
        target_id=tid,
        before={"subscription_until": current_until},
        after={"subscription_until": new_until},
        extra={
            "tenant_name": tenant.get("name"),
            "mode": mode,
            "extend_from_current": extend_from_current,
            "note": note,
        },
    )

    flash(
        f"Subscription extended until {new_until.strftime('%Y-%m-%d %H:%M')} UTC.",
        "success",
    )
    return redirect(url_for("admin_panel.tenant_detail", tenant_id=tenant_id))


@admin_panel_bp.post("/admin/tenants/<tenant_id>/clear-subscription")
@admin_required
def tenant_clear_subscription(tenant_id: str):
    admin = get_current_admin()
    master = get_master_db()
    tid = _oid(tenant_id)
    tenant = master.tenants.find_one({"_id": tid})
    if not tenant:
        abort(404)

    before = tenant.get("subscription_until")
    master.tenants.update_one(
        {"_id": tid},
        {
            "$set": {
                "subscription_status": "expired",
                "updated_at": datetime.utcnow(),
            },
            "$unset": {"subscription_until": ""},
        },
    )
    log_admin_action(
        admin,
        action="tenant.clear_subscription",
        target_type="tenant",
        target_id=tid,
        before={"subscription_until": before},
        after={"subscription_until": None},
        extra={"tenant_name": tenant.get("name")},
    )
    flash("Subscription cleared.", "success")
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

