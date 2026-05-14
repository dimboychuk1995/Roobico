import os
import time

from flask import Flask, g, session, request, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
from bson import ObjectId

from app.config import Config
from app.extensions import init_mongo, get_master_db
from app.utils.auth import SESSION_USER_ID, SESSION_TENANT_ID
from app.blueprints.reports.audit.journal import build_request_id, write_audit_journal

# Версия статики, общая для всего процесса. Пересчитывается при рестарте
# приложения (на сервере gunicorn перезапускается каждым деплоем) — это
# гарантирует, что после деплоя браузеры подтянут свежие CSS/JS вместо
# старых из локального кеша.
ASSET_VERSION = str(int(time.time()))


# Endpoints that MUST live on the public host (roobico.com).
# Everything else MUST live on the app host (app.roobico.com).
PUBLIC_HOST_ENDPOINTS = frozenset({
    "main.index",
    "auth.login",
    "auth.logout",
    "auth.forgot_password_page",
    "auth.forgot_password",
    "auth.reset_password_page",
    "auth.reset_password",
})


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # We sit behind Cloudflare → nginx → gunicorn (unix socket). Trust the
    # X-Forwarded-Proto / X-Forwarded-For headers from one proxy hop so that
    # request.is_secure and url_for(_external=True) report HTTPS correctly
    # (otherwise emailed links — customer portal, password reset, WO PDFs —
    # would all be built with http://).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)

    init_mongo(app)

    # Автоматически добавляем ?v=<ASSET_VERSION> ко всем url_for('static', ...)
    # — единый cache-buster, чтобы не плодить ручные ?v=... в шаблонах.
    @app.url_defaults
    def _add_static_version(endpoint, values):
        if endpoint == "static" or endpoint.endswith(".static"):
            values.setdefault("v", ASSET_VERSION)

    # Expose selected config keys to all templates.
    @app.context_processor
    def inject_public_config():
        return {
            "mapbox_access_token": app.config.get("MAPBOX_ACCESS_TOKEN", ""),
        }

    # каждый запрос: если есть сессия — поднимем g.user и g.tenant
    @app.before_request
    def enforce_host_split():
        """
        Production routing rule:
          * roobico.com  → only login / forgot-password / reset / logout
          * app.roobico.com → everything else (dashboard, customer portal,
            public auth-token pages sent by email, etc.)
        Anything reaching the wrong host is 301'd to the right one. Disabled
        by default in dev (ENFORCE_HOST_SPLIT=false) and never engages for
        hosts we don't recognise (localhost, IP access, etc.).
        """
        if not app.config.get("ENFORCE_HOST_SPLIT"):
            return None

        endpoint = request.endpoint or ""
        # Static / no-endpoint requests pass through; nginx serves /static
        # directly anyway.
        if endpoint == "static" or endpoint.endswith(".static") or not endpoint:
            return None

        # Pull just the hostname (strip port, lowercase).
        host = (request.host or "").split(":", 1)[0].lower()
        public_host = (app.config.get("PUBLIC_HOST") or "").lower()
        app_host = (app.config.get("APP_HOST") or "").lower()
        public_aliases = {
            h.lower() for h in app.config.get("PUBLIC_HOST_ALIASES") or []
        }

        if host not in {public_host, app_host} | public_aliases:
            # Unknown host (dev, IP, preview) → no rewriting.
            return None

        is_public_endpoint = endpoint in PUBLIC_HOST_ENDPOINTS
        on_public_host = host in {public_host} | public_aliases
        on_app_host = host == app_host

        target_base = None
        if on_public_host and not is_public_endpoint:
            target_base = app.config.get("APP_BASE_URL")
        elif on_app_host and is_public_endpoint:
            target_base = app.config.get("PUBLIC_BASE_URL")

        if not target_base:
            return None

        # Preserve the original path + query string when redirecting.
        target = target_base.rstrip("/") + request.full_path.rstrip("?")
        # 302 (not 301): browsers cache 301s indefinitely, which makes any
        # future change to the host split impossible to recover from
        # without users manually clearing their cache.
        return redirect(target, code=302)

    @app.before_request
    def load_current_context():
        g._request_start_time = time.perf_counter()
        g.request_id = build_request_id()
        g._audit_journal_written = False
        g.user = None
        g.tenant = None

        user_id = session.get(SESSION_USER_ID)
        tenant_id = session.get(SESSION_TENANT_ID)
        if not user_id or not tenant_id:
            return

        master = get_master_db()

        try:
            uid = ObjectId(user_id)
            tid = ObjectId(tenant_id)
        except Exception:
            # битая сессия
            session.clear()
            return

        user = master.users.find_one({"_id": uid, "is_active": True})
        if not user:
            session.clear()
            return

        tenant = master.tenants.find_one({"_id": tid, "status": "active"})
        if not tenant:
            session.clear()
            return

        # защита: tenant из сессии должен совпадать с tenant у user
        if user.get("tenant_id") != tenant["_id"]:
            session.clear()
            return

        g.user = user
        g.tenant = tenant

    @app.after_request
    def journal_after_request(response):
        elapsed = (time.perf_counter() - getattr(g, '_request_start_time', time.perf_counter())) * 1000
        from flask import request as _req
        if elapsed > 50:
            app.logger.info(f"[PERF] {_req.method} {_req.path} → {response.status_code} in {elapsed:.0f}ms")
        write_audit_journal(response=response)

        # Запрещаем браузеру кешировать динамический HTML, иначе после
        # деплоя пользователи продолжают видеть старую разметку (без
        # новых блоков, кнопок и т.п.) пока не сделают жёсткий refresh.
        # Для статики (/static/*) этот хук тоже срабатывает, но она
        # отдаётся nginx-ом напрямую (location /static), поэтому не
        # пересекается. На всякий случай не трогаем уже выставленные
        # заголовки кеша.
        ctype = (response.mimetype or "").lower()
        path = _req.path or ""
        if (
            ctype.startswith("text/html")
            and not path.startswith("/static/")
            and "Cache-Control" not in response.headers
        ):
            response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response

    @app.teardown_request
    def journal_teardown_request(exc):
        if exc is not None:
            write_audit_journal(error=exc)

    # Blueprints
    from app.blueprints.main import main_bp
    from app.blueprints.reports import reports_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.tenant import tenant_bp
    from app.blueprints.settings import settings_bp
    from app.blueprints.vendors import vendors_bp
    from app.blueprints.parts import parts_bp
    from app.blueprints.customers import customers_bp
    from app.blueprints.work_orders import work_orders_bp
    from app.blueprints.calendar import calendar_bp
    from app.blueprints.attachments import attachments_bp
    from app.blueprints.import_export import import_export_bp
    from app.blueprints.customer_portal import customer_portal_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(tenant_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(vendors_bp)
    app.register_blueprint(parts_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(work_orders_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(import_export_bp)
    app.register_blueprint(customer_portal_bp)

    return app
