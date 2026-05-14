"""CLI: create or update an admin panel user.

Usage (run from project root with the venv active):

    python -m app.scripts.create_admin --email you@example.com --password "..."
    python -m app.scripts.create_admin --email you@example.com --password "..." --name "Dima"
    python -m app.scripts.create_admin --email you@example.com --reset-password "..."
    python -m app.scripts.create_admin --email you@example.com --deactivate

If `--password` is omitted on a new user, you'll be prompted interactively.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

# Ensure .env is loaded the same way as run.py.
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import get_master_db


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create or update a Roobico admin user.")
    p.add_argument("--email", required=True, help="Admin email (lowercased).")
    p.add_argument("--password", help="Password (omit to be prompted).")
    p.add_argument("--reset-password", dest="reset_password", help="New password for an existing admin.")
    p.add_argument("--name", help="Display name (optional).")
    p.add_argument("--deactivate", action="store_true", help="Set is_active=false instead of upserting.")
    p.add_argument("--activate", action="store_true", help="Set is_active=true on an existing admin.")
    return p.parse_args()


def _prompt_password(label: str = "Password") -> str:
    p1 = getpass.getpass(f"{label}: ")
    p2 = getpass.getpass(f"{label} (again): ")
    if p1 != p2:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(2)
    if len(p1) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(2)
    return p1


def main() -> int:
    args = _parse_args()
    email = args.email.strip().lower()
    if not email or "@" not in email:
        print(f"Invalid email: {email!r}", file=sys.stderr)
        return 2

    app = create_app()
    with app.app_context():
        master = get_master_db()
        existing = master.admin_users.find_one({"email": email})

        if args.deactivate:
            if not existing:
                print(f"No admin found for {email}.", file=sys.stderr)
                return 1
            master.admin_users.update_one(
                {"_id": existing["_id"]},
                {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
            )
            print(f"Deactivated admin {email}.")
            return 0

        if args.activate:
            if not existing:
                print(f"No admin found for {email}.", file=sys.stderr)
                return 1
            master.admin_users.update_one(
                {"_id": existing["_id"]},
                {"$set": {"is_active": True, "updated_at": datetime.utcnow()}},
            )
            print(f"Activated admin {email}.")
            return 0

        if args.reset_password:
            if not existing:
                print(f"No admin found for {email}.", file=sys.stderr)
                return 1
            if len(args.reset_password) < 8:
                print("Password must be at least 8 characters.", file=sys.stderr)
                return 2
            master.admin_users.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "password_hash": generate_password_hash(args.reset_password),
                    "updated_at": datetime.utcnow(),
                }},
            )
            print(f"Password reset for {email}.")
            return 0

        # Create / upsert
        password = args.password or _prompt_password()
        if len(password) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            return 2

        now = datetime.utcnow()
        update = {
            "$set": {
                "email": email,
                "password_hash": generate_password_hash(password),
                "is_active": True,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        }
        if args.name:
            update["$set"]["name"] = args.name.strip()

        result = master.admin_users.update_one({"email": email}, update, upsert=True)
        action = "Created" if result.upserted_id else "Updated"
        print(f"{action} admin {email}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
