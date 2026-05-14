from datetime import datetime

from flask import render_template, request, redirect, url_for, flash
from werkzeug.security import check_password_hash

from app.extensions import get_master_db
from app.utils.admin_auth import (
    admin_login,
    admin_logout,
    admin_required,
    get_current_admin,
)
from . import admin_panel_bp


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
    return render_template("admin_panel/dashboard.html", admin=admin)

