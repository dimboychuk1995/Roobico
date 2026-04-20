import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

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

