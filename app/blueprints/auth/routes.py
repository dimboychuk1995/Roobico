from __future__ import annotations

from flask import request, redirect, url_for, flash, session, render_template
from werkzeug.security import check_password_hash, generate_password_hash
from bson import ObjectId
import secrets, time

from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_user, logout_user, SESSION_USER_ID, SESSION_TENANT_ID, SESSION_TENANT_DB
from app.utils.email_sender import send_email
from . import auth_bp


def _maybe_object_id(value):
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _safe_list(v):
    if not isinstance(v, list):
        return []
    return [x for x in v if x is not None]


def _compute_effective_permissions(user_doc: dict, tenant_doc: dict) -> list[str]:
    """
    Effective permissions for login. Делегируем общему компьютеру в utils.permissions:
      - role из user.role (key в tenant_db.roles)
      - allow_permissions добавляются
      - deny_permissions вычитаются (deny > allow > role)
      - owner всегда полный доступ (синхронизируется автоматически)
    """
    from app.utils.permissions import compute_user_permissions

    tenant_db_name = (tenant_doc.get("db_name") or "").strip()
    if not tenant_db_name:
        return []

    client = get_mongo_client()
    tdb = client[tenant_db_name]
    return sorted(compute_user_permissions(user_doc, tdb))


@auth_bp.post("/login")
def login():
    master = get_master_db()

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Fill in email and password.", "error")
        return redirect(url_for("main.index"))

    user = master.users.find_one({"email": email, "is_active": True})
    if not user:
        flash("User not found or inactive.", "error")
        return redirect(url_for("main.index"))

    if not check_password_hash(user.get("password_hash", ""), password):
        flash("Wrong password.", "error")
        return redirect(url_for("main.index"))

    tenant = master.tenants.find_one({"_id": user["tenant_id"], "status": "active"})
    if not tenant:
        flash("Tenant not found or inactive.", "error")
        return redirect(url_for("main.index"))

    # ✅ only shop_ids from DB
    shop_ids = user.get("shop_ids") if isinstance(user.get("shop_ids"), list) else []
    shop_ids_str = [str(x) for x in shop_ids]

    # ✅ do NOT pass shop_id -> login_user will set session["shop_id"] = shop_ids_str[0]
    login_user(
        user_id=user["_id"],
        tenant_id=tenant["_id"],
        tenant_db_name=tenant.get("db_name", ""),
        shop_ids=shop_ids_str,
        shop_id=None,
    )

    # ✅ Сохраняем effective permissions в session, чтобы UI везде мог их читать
    perms = _compute_effective_permissions(user, tenant)
    session["user_permissions"] = perms
    session.modified = True

    return redirect(url_for("dashboard.dashboard"))


@auth_bp.get("/logout")
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("main.index"))


# ── Forgot / Reset password ───────────────────────────────────────────────

_RESET_TOKEN_TTL = 3600  # 1 hour


@auth_bp.get("/forgot-password")
def forgot_password_page():
    return render_template("public/forgot_password.html")


@auth_bp.post("/forgot-password")
def forgot_password():
    master = get_master_db()
    email = (request.form.get("email") or "").strip().lower()

    if not email:
        flash("Please enter your email.", "error")
        return redirect(url_for("auth.forgot_password_page"))

    user = master.users.find_one({"email": email})
    if user:
        token = secrets.token_urlsafe(48)
        master.users.update_one(
            {"_id": user["_id"]},
            {"$set": {
                "reset_token": token,
                "reset_token_created": time.time(),
            }},
        )
        reset_url = url_for("auth.reset_password_page", token=token, _external=True)
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:2rem;">
            <h2 style="color:#0f172a;">Password Reset</h2>
            <p>We received a request to reset your password. Click the button below to set a new password:</p>
            <p style="text-align:center;margin:2rem 0;">
                <a href="{reset_url}"
                   style="background:#16a34a;color:#fff;padding:12px 32px;border-radius:6px;
                          text-decoration:none;font-weight:700;display:inline-block;">
                    Reset Password
                </a>
            </p>
            <p style="color:#64748b;font-size:0.85rem;">This link expires in 1 hour. If you didn't request this, ignore this email.</p>
        </div>
        """
        try:
            send_email(
                to_address=email,
                subject="Password Reset — Roobico",
                html_body=html,
                from_email="password_recovery@roobico.com",
                from_name="Roobico",
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()  # log the error to console

    flash("If that email is registered, a reset link has been sent.", "info")
    return redirect(url_for("auth.forgot_password_page"))


@auth_bp.get("/reset-password/<token>")
def reset_password_page(token):
    master = get_master_db()
    user = master.users.find_one({"reset_token": token})
    if not user:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("main.index"))

    created = user.get("reset_token_created", 0)
    if time.time() - created > _RESET_TOKEN_TTL:
        flash("This reset link has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password_page"))

    return render_template("public/reset_password.html", token=token)


@auth_bp.post("/reset-password/<token>")
def reset_password(token):
    master = get_master_db()
    user = master.users.find_one({"reset_token": token})
    if not user:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("main.index"))

    created = user.get("reset_token_created", 0)
    if time.time() - created > _RESET_TOKEN_TTL:
        flash("This reset link has expired.", "error")
        return redirect(url_for("auth.forgot_password_page"))

    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")

    if len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return redirect(url_for("auth.reset_password_page", token=token))

    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("auth.reset_password_page", token=token))

    master.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": generate_password_hash(password)},
         "$unset": {"reset_token": "", "reset_token_created": ""}},
    )

    flash("Password has been reset. You can now log in.", "success")
    return redirect(url_for("main.index"))
