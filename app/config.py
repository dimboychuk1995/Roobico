import os


def _parse_bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

    # ── Hosts / domain split ──────────────────────────────────────────────────
    # roobico.com  → public host (login / forgot-password / reset / logout)
    # app.roobico.com → application host (everything else, incl. customer portal)
    #
    # In dev (any host that isn't one of these) enforcement is skipped, so
    # localhost / 127.0.0.1 / IP access continue to "just work".
    PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "roobico.com")
    APP_HOST = os.environ.get("APP_HOST", "app.roobico.com")
    ADMIN_HOST = os.environ.get("ADMIN_HOST", "admin.roobico.com")
    PUBLIC_HOST_ALIASES = [
        h.strip().lower()
        for h in os.environ.get("PUBLIC_HOST_ALIASES", "www.roobico.com").split(",")
        if h.strip()
    ]

    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", f"https://{PUBLIC_HOST}")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", f"https://{APP_HOST}")
    ADMIN_BASE_URL = os.environ.get("ADMIN_BASE_URL", f"https://{ADMIN_HOST}")

    # Master switch for the host-split logic. Auto-disabled for non-prod hosts.
    # Defaults to off so local dev (`python run.py`) continues to work without
    # extra env vars. Set ENFORCE_HOST_SPLIT=true in production .env.
    ENFORCE_HOST_SPLIT = _parse_bool(os.environ.get("ENFORCE_HOST_SPLIT"), False)

    # ── Session cookie ────────────────────────────────────────────────────────
    # In production share cookies across roobico.com and app.roobico.com so
    # login on the public host is recognised on the app host. In dev the
    # defaults stay browser-default (host-only, non-secure) so http://localhost
    # keeps working.
    SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN") or None
    SESSION_COOKIE_SECURE = _parse_bool(os.environ.get("SESSION_COOKIE_SECURE"), False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")

    # Admin gets a completely separate cookie scoped only to admin.roobico.com
    # so that being logged into the tenant app (`app.roobico.com`) gives no
    # status whatsoever in the admin panel, and vice versa.
    ADMIN_SESSION_COOKIE_NAME = os.environ.get("ADMIN_SESSION_COOKIE_NAME", "admin_session")

    # MongoDB connection string (server-level URI)
    # Example: mongodb://localhost:27017
    # Or: mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")

    # Master DB where we store tenants/users/shops
    MASTER_DB_NAME = os.environ.get("MASTER_DB_NAME") or os.environ.get("MONGO_DB") or "master_db"

    # Max upload size (16 MB — matches MongoDB BSON document limit)
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    # ── Email (SMTP) ──────────────────────────────────────────────────────────
    # Set these in .env to enable "Email Work Order" and "Email Receipt" features.
    #   SMTP_HOST        smtp server host      (default: smtp.gmail.com)
    #   SMTP_PORT        SMTP port             (default: 587)
    #   SMTP_USER        login / sender address
    #   SMTP_PASS        password / app-password
    #   SMTP_FROM_EMAIL  explicit From address  (defaults to SMTP_USER)
    #   SMTP_FROM_NAME   display name           (default: Roobico)

    # ── OpenAI (Invoice AI parsing) ───────────────────────────────────────────
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    # ── Mapbox (Address autocomplete) ───────────────────────────────────────────
    # Public token (pk.*) — exposed to the browser for the Search Box API.
    MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN", "")

    # ── Stripe (subscription billing) ────────────────────────────────────────
    # Test/live keys. STRIPE_WEBHOOK_SECRET is required to verify webhook
    # signatures — without it the /billing/stripe/webhook endpoint rejects
    # everything.
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    # Toggle Stripe Tax on invoices. Off by default (sandbox can't enable
    # it without a verified head office address). Set STRIPE_AUTOMATIC_TAX=true
    # in env once the live account is ready.
    STRIPE_AUTOMATIC_TAX = os.environ.get("STRIPE_AUTOMATIC_TAX", "false")
    # When true the dashboard URLs we build link to test-mode Stripe.
    STRIPE_TEST_MODE = os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_")
