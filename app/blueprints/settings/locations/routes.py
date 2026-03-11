from __future__ import annotations

import re
import hashlib
from datetime import datetime, timezone
from zoneinfo import available_timezones

from bson import ObjectId
from flask import render_template, request, redirect, url_for, flash, session, jsonify

from app.blueprints.settings import settings_bp
from app.extensions import get_master_db, get_mongo_client
from app.utils.auth import login_required, SESSION_USER_ID, SESSION_TENANT_ID
from app.utils.permissions import permission_required, filter_nav_items
from app.blueprints.main.routes import NAV_ITEMS
from app.utils.layout import build_app_layout_context


COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "America/Toronto",
    "America/Vancouver",
    "America/Mexico_City",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "Europe/Warsaw",
    "Europe/Kyiv",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Bangkok",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
]


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


def _load_current_user(master):
    user_id = _maybe_object_id(session.get(SESSION_USER_ID))
    if not user_id:
        return None
    return master.users.find_one({"_id": user_id, "is_active": True})


def _load_current_tenant(master):
    tenant_id = _maybe_object_id(session.get(SESSION_TENANT_ID))
    if not tenant_id:
        return None
    return master.tenants.find_one({"_id": tenant_id, "status": "active"})


def _render_settings_page(template_name: str, **ctx):
    """
    Общий рендер для settings-страниц через единый layout builder.
    """
    layout = build_app_layout_context(filter_nav_items(NAV_ITEMS), "settings")

    if not layout.get("_current_user") or not layout.get("_current_tenant"):
        flash("Session data mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    layout.update(ctx)
    return render_template(template_name, **layout)


def slugify_shop_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) < 2:
        s = (s + "-shop").strip("-")
    return s[:32]


def make_shop_db_name(tenant_slug: str, shop_slug: str) -> str:
    """
    Atlas limit: max 38 bytes for db name.
    Format: shop_<tenant10>_<shop10>_<hash6>
    """
    t10 = (tenant_slug or "")[:10]
    s10 = (shop_slug or "")[:10]
    raw = f"{tenant_slug}:{shop_slug}"
    h6 = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    return f"shop_{t10}_{s10}_{h6}"[:38]


# -----------------------------
# parts categories seeding (shop DB)
# -----------------------------

DEFAULT_PARTS_CATEGORIES = [
    "Filters",
    "Electrical",
    "Exhaust",
    "Body",
    "Interior",
    "DEF",
    "Cores",
]


def _slugify_simple(name: str) -> str:
    s = (name or "").strip().lower()
    out = []
    last_dash = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        else:
            if not last_dash:
                out.append("-")
                last_dash = True
    slug = "".join(out).strip("-")
    return slug or "category"


def seed_parts_categories(shop_db, shop_id: ObjectId):
    """
    Ensure default parts categories exist in shop DB (idempotent).
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.parts_categories

    # Prevent duplicates per shop
    try:
        col.create_index([("shop_id", 1), ("slug", 1)], unique=True, name="uniq_parts_categories_shop_slug")
    except Exception:
        pass

    now = utcnow()

    for name in DEFAULT_PARTS_CATEGORIES:
        slug = _slugify_simple(name)
        col.update_one(
            {"shop_id": shop_id, "slug": slug},
            {
                "$setOnInsert": {
                    "name": name,
                    "slug": slug,
                    "shop_id": shop_id,
                    "is_active": True,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                },
            },
            upsert=True,
        )


# -----------------------------
# NEW: labor rates seeding (shop DB)
# -----------------------------

DEFAULT_LABOR_RATES = [
    {"code": "standard", "name": "Standard", "hourly_rate": 100.0},
    {"code": "after_hours", "name": "After Hours", "hourly_rate": 150.0},
]


def seed_labor_rates(shop_db, shop_id: ObjectId):
    """
    Ensure default labor rates exist in shop DB (idempotent, per shop).
    Collection: labor_rates
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.labor_rates

    # Prevent duplicates per shop
    try:
        col.create_index([("shop_id", 1), ("code", 1)], unique=True, name="uniq_labor_rates_shop_code")
    except Exception:
        pass

    now = utcnow()

    for item in DEFAULT_LABOR_RATES:
        code = item["code"]
        col.update_one(
            {"shop_id": shop_id, "code": code},
            {
                "$setOnInsert": {
                    "shop_id": shop_id,
                    "code": code,
                    "name": item.get("name") or code,
                    "hourly_rate": float(item.get("hourly_rate") or 0),
                    "is_active": True,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                }
            },
            upsert=True,
        )


# -----------------------------
# NEW: shop supply amount rules seeding (shop DB)
# -----------------------------

def seed_shop_supply_amount_rules(shop_db, shop_id: ObjectId):
    """
    Ensure default shop supply amount rules exist in shop DB (idempotent).
    Collection: shop_supply_amount_rules
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.shop_supply_amount_rules

    try:
        col.create_index([("shop_id", 1)], unique=True, name="uniq_shop_supply_amount_rules_shop")
    except Exception:
        pass

    now = utcnow()

    col.update_one(
        {"shop_id": shop_id},
        {
            "$setOnInsert": {
                "shop_id": shop_id,
                "shop_supply_procentage": 5,
                "is_active": True,
                "created_at": now,
            },
            "$set": {
                "updated_at": now,
            },
        },
        upsert=True,
    )


def seed_core_charge_rules(shop_db, shop_id: ObjectId, created_by=None, updated_by=None):
    """
    Ensure default core charge rules exist in shop DB (idempotent).
    Collection: core_charge_rules
    """
    if shop_db is None or shop_id is None:
        return

    col = shop_db.core_charge_rules

    try:
        col.create_index([("shop_id", 1)], unique=True, name="uniq_core_charge_rules_shop")
    except Exception:
        pass

    now = utcnow()

    col.update_one(
        {"shop_id": shop_id},
        {
            "$setOnInsert": {
                "shop_id": shop_id,
                "charge_for_cores_default": False,
                "created_at": now,
                "created_by": created_by,
            },
            "$set": {
                "updated_at": now,
                "updated_by": updated_by,
            },
        },
        upsert=True,
    )


def init_shop_database(shop_db_name: str, tenant_doc: dict, shop_doc: dict, actor_user_id=None):
    """
    Creates shop DB and seeds minimal defaults:
    - settings (idempotent upsert)
    - default parts categories (idempotent)
    - default parts pricing rules (margin/markup ranges) (idempotent)
    - default labor rates (idempotent)   <-- NEW
    - default shop supply amount rules (idempotent)
    """
    client = get_mongo_client()
    sdb = client[shop_db_name]

    now = utcnow()

    # -----------------------------
    # settings (idempotent)
    # -----------------------------
    sdb.settings.update_one(
        {"key": "shop"},
        {"$setOnInsert": {
            "key": "shop",
            "shop_name": shop_doc.get("name"),
            "shop_slug": shop_doc.get("slug"),
            "created_at": now,
        }},
        upsert=True
    )

    sdb.settings.update_one(
        {"key": "tenant_ref"},
        {"$setOnInsert": {
            "key": "tenant_ref",
            "tenant_name": tenant_doc.get("name"),
            "tenant_slug": tenant_doc.get("slug"),
            "timezone": tenant_doc.get("timezone", "UTC"),
            "created_at": now,
        }},
        upsert=True
    )

    try:
        sdb.settings.create_index("key", unique=True, name="uniq_settings_key")
    except Exception:
        pass

    # -----------------------------
    # resolve shop_id (ObjectId)
    # -----------------------------
    shop_id = shop_doc.get("_id")
    shop_oid = None

    if isinstance(shop_id, ObjectId):
        shop_oid = shop_id
    elif shop_id is not None:
        try:
            shop_oid = ObjectId(str(shop_id))
        except Exception:
            shop_oid = None

    if not shop_oid:
        # without shop_id we cannot seed shop-scoped collections
        return

    # -----------------------------
    # seed default categories
    # -----------------------------
    try:
        seed_parts_categories(sdb, shop_oid)
    except Exception:
        pass

    # -----------------------------
    # seed default parts pricing rules
    # -----------------------------
    col = sdb.parts_pricing_rules

    # one rules doc per shop
    try:
        col.create_index([("shop_id", 1)], unique=True, name="uniq_parts_pricing_rules_shop")
    except Exception:
        pass

    default_rules = [
        {"from": 0, "to": 20, "value_percent": 100},
        {"from": 20, "to": 100, "value_percent": 60},
        {"from": 100, "to": None, "value_percent": 50},  # None = infinity
    ]

    # idempotent upsert
    col.update_one(
        {"shop_id": shop_oid},
        {
            "$setOnInsert": {
                "shop_id": shop_oid,
                "mode": "margin",          # "margin" or "markup"
                "rules": default_rules,
                "is_active": True,
                "created_at": now,
            },
            "$set": {
                "updated_at": now,
            }
        },
        upsert=True
    )

    # -----------------------------
    # seed default labor rates (NEW)
    # -----------------------------
    try:
        seed_labor_rates(sdb, shop_oid)
    except Exception:
        pass

    # -----------------------------
    # seed default shop supply amount rules (NEW)
    # -----------------------------
    try:
        seed_shop_supply_amount_rules(sdb, shop_oid)
    except Exception:
        pass

    # -----------------------------
    # seed default core charge rules (NEW)
    # -----------------------------
    try:
        seed_core_charge_rules(sdb, shop_oid, created_by=actor_user_id, updated_by=actor_user_id)
    except Exception:
        pass


def _grant_shop_to_owners(master, tenant_id, new_shop_id):
    """
    Добавляем новый shop только всем пользователям role=owner (в этом tenant).
    shop_id поля больше нет — только shop_ids[].
    """
    master.users.update_many(
        {
            "tenant_id": tenant_id,
            "role": "owner",
            "is_active": True,
            "$or": [
                {"shop_ids": {"$exists": False}},
                {"shop_ids": {"$ne": new_shop_id}},
            ],
        },
        [
            {
                "$set": {
                    "shop_ids": {
                        "$cond": [
                            {"$isArray": "$shop_ids"},
                            {
                                "$cond": [
                                    {"$in": [new_shop_id, "$shop_ids"]},
                                    "$shop_ids",
                                    {"$concatArrays": ["$shop_ids", [new_shop_id]]},
                                ]
                            },
                            [new_shop_id],
                        ]
                    },
                    "updated_at": utcnow(),
                }
            }
        ]
    )


# -----------------------------
# UI Routes
# -----------------------------

@settings_bp.route("/locations", methods=["GET", "POST"])
@login_required
@permission_required("settings.manage_org")
def locations_index():
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)

    if not user or not tenant:
        flash("Session mismatch. Please login again.", "error")
        session.clear()
        return redirect(url_for("main.index"))

    active_shop_raw = (session.get("shop_id") or "").strip()
    active_shop_oid = _maybe_object_id(active_shop_raw)

    # -----------------------------
    # Update timezone for active shop (POST)
    # -----------------------------
    if request.method == "POST":
        selected_tz = (request.form.get("timezone") or "").strip()
        if not active_shop_oid:
            flash("Active shop is not selected.", "error")
            return redirect(url_for("settings.locations_index"))

        if not selected_tz:
            flash("Timezone is required.", "error")
            return redirect(url_for("settings.locations_index"))

        # On some environments timezone database may be unavailable.
        # Validate against IANA set only when it can be loaded.
        valid_timezones = set()
        try:
            valid_timezones = set(available_timezones())
        except Exception:
            valid_timezones = set()
        if valid_timezones and selected_tz not in valid_timezones:
            flash("Invalid timezone selected.", "error")
            return redirect(url_for("settings.locations_index"))

        active_shop_doc = master.shops.find_one({"_id": active_shop_oid, "tenant_id": tenant["_id"]})
        if not active_shop_doc:
            flash("Active shop not found.", "error")
            return redirect(url_for("settings.locations_index"))

        now = utcnow()
        tenant_oid = tenant["_id"]
        shop_oid = active_shop_doc["_id"]
        tenant_variants = [tenant_oid, str(tenant_oid)]
        shop_variants = [shop_oid, str(shop_oid)]

        # Primary storage: master DB collection timezone_location
        master.timezone_location.update_one(
            {
                "$or": [
                    {"tenant_id": tv, "shop_id": sv}
                    for tv in tenant_variants
                    for sv in shop_variants
                ]
            },
            {
                "$set": {
                    "shop_id": shop_oid,
                    "tenant_id": tenant_oid,
                    "timezone": selected_tz,
                    "is_active": True,
                    "updated_at": now,
                    "updated_by": user.get("_id"),
                },
                "$setOnInsert": {
                    "created_at": now,
                    "created_by": user.get("_id"),
                },
            },
            upsert=True,
        )

        # Backward compatibility: also mirror into shop DB when available.
        db_name = (
            active_shop_doc.get("db_name")
            or active_shop_doc.get("database")
            or active_shop_doc.get("db")
            or active_shop_doc.get("mongo_db")
            or active_shop_doc.get("shop_db")
        )
        if db_name:
            shop_db = get_mongo_client()[str(db_name)]
            shop_db.timezone_location.update_one(
                {
                    "$or": [
                        {"shop_id": shop_oid},
                        {"shop_id": str(shop_oid)},
                        {"location_id": shop_oid},
                        {"location_id": str(shop_oid)},
                    ]
                },
                {
                    "$set": {
                        "shop_id": shop_oid,
                        "tenant_id": tenant_oid,
                        "timezone": selected_tz,
                        "is_active": True,
                        "updated_at": now,
                        "updated_by": user.get("_id"),
                    },
                    "$setOnInsert": {
                        "created_at": now,
                        "created_by": user.get("_id"),
                    },
                },
                upsert=True,
            )

        flash("Timezone updated for active shop.", "success")
        return redirect(url_for("settings.locations_index"))

    # -----------------------------
    # List (GET)
    # -----------------------------
    allowed_shop_ids = session.get("shop_ids") if isinstance(session.get("shop_ids"), list) else []
    allowed_shop_ids = [str(x) for x in allowed_shop_ids]

    shops = []
    active_shop = None
    for s in master.shops.find({"tenant_id": tenant["_id"]}).sort("created_at", 1):
        sid = str(s["_id"])
        item = {
            "_id": sid,
            "name": s.get("name"),
            "slug": s.get("slug"),
            "db_name": s.get("db_name"),
            "phone": s.get("phone"),
            "email": s.get("email"),
            "address": s.get("address"),
            "address_line": s.get("address_line"),
            "city": s.get("city"),
            "state": s.get("state"),
            "zip": s.get("zip"),
            "status": s.get("status") or ("active" if s.get("is_active", True) else "disabled"),
            "is_active": bool(s.get("is_active", True)),
            "is_primary": False,  # primary = shop_ids[0], можно дорисовать позже
            "has_access": (sid in allowed_shop_ids),
            "is_current": (sid == active_shop_raw),
        }
        shops.append(item)
        if sid == active_shop_raw:
            active_shop = s

    current_timezone = "America/Chicago"

    def _extract_timezone_from_doc(doc):
        if not isinstance(doc, dict):
            return ""
        value = (doc.get("timezone") or "").strip()
        return value

    if active_shop:
        active_shop_oid = active_shop.get("_id")
        active_shop_id_str = str(active_shop_oid)
        tenant_oid = tenant["_id"]

        # Primary read path: master DB timezone_location
        tz_doc = master.timezone_location.find_one(
            {
                "is_active": {"$ne": False},
                "$or": [
                    {"tenant_id": tenant_oid, "shop_id": active_shop_oid},
                    {"tenant_id": tenant_oid, "shop_id": active_shop_id_str},
                    {"tenant_id": str(tenant_oid), "shop_id": active_shop_oid},
                    {"tenant_id": str(tenant_oid), "shop_id": active_shop_id_str},
                ],
            },
            {"timezone": 1, "updated_at": 1, "created_at": 1},
            sort=[("updated_at", -1), ("created_at", -1)],
        )

        tz_value = _extract_timezone_from_doc(tz_doc)
        if tz_value:
            current_timezone = tz_value

        db_name = (
            active_shop.get("db_name")
            or active_shop.get("database")
            or active_shop.get("db")
            or active_shop.get("mongo_db")
            or active_shop.get("shop_db")
        )
        if db_name and not tz_value:
            shop_db = get_mongo_client()[str(db_name)]

            # Primary read path: timezone bound to active shop id.
            tz_doc = shop_db.timezone_location.find_one(
                {
                    "is_active": {"$ne": False},
                    "$or": [
                        {"shop_id": active_shop.get("_id")},
                        {"shop_id": active_shop_id_str},
                        {"location_id": active_shop.get("_id")},
                        {"location_id": active_shop_id_str},
                    ],
                },
                {"timezone": 1, "updated_at": 1, "created_at": 1},
                sort=[("updated_at", -1), ("created_at", -1)],
            )

            # Legacy fallback: some older records may not store shop_id/location_id.
            if not tz_doc:
                tz_doc = shop_db.timezone_location.find_one(
                    {"is_active": {"$ne": False}},
                    {"timezone": 1, "updated_at": 1, "created_at": 1},
                    sort=[("updated_at", -1), ("created_at", -1)],
                )

            tz_value = _extract_timezone_from_doc(tz_doc)
            if tz_value:
                current_timezone = tz_value

    timezone_options = sorted(set(COMMON_TIMEZONES))
    if current_timezone and current_timezone not in timezone_options:
        timezone_options.append(current_timezone)
        timezone_options = sorted(set(timezone_options))

    active_shop_info = None
    if isinstance(active_shop, dict):
        active_shop_info = {
            "id": str(active_shop.get("_id") or ""),
            "name": str(active_shop.get("name") or "").strip(),
            "slug": str(active_shop.get("slug") or "").strip(),
            "db_name": str(
                active_shop.get("db_name")
                or active_shop.get("database")
                or active_shop.get("db")
                or active_shop.get("mongo_db")
                or active_shop.get("shop_db")
                or ""
            ).strip(),
            "status": str(active_shop.get("status") or ("active" if active_shop.get("is_active", True) else "disabled")),
            "is_active": bool(active_shop.get("is_active", True)),
            "phone": str(active_shop.get("phone") or "").strip(),
            "email": str(active_shop.get("email") or "").strip(),
            "address_line": str(active_shop.get("address_line") or "").strip(),
            "address": str(active_shop.get("address") or "").strip(),
            "city": str(active_shop.get("city") or "").strip(),
            "state": str(active_shop.get("state") or "").strip(),
            "zip": str(active_shop.get("zip") or "").strip(),
        }

    return _render_settings_page(
        "public/settings/locations.html",
        shops=shops,
        active_shop_id=active_shop_raw,
        active_shop_info=active_shop_info,
        current_timezone=current_timezone,
        timezone_options=timezone_options,
    )


# -----------------------------
# API (optional, for future JS)
# -----------------------------

@settings_bp.get("/api/locations")
@login_required
@permission_required("settings.manage_org")
def api_locations_list():
    master = get_master_db()
    tenant = _load_current_tenant(master)
    user = _load_current_user(master)

    if not user or not tenant:
        return jsonify({"ok": False, "errors": ["Session mismatch"]}), 401

    items = []
    for s in master.shops.find({"tenant_id": tenant["_id"]}).sort("created_at", 1):
        items.append({
            "id": str(s["_id"]),
            "name": s.get("name"),
            "slug": s.get("slug"),
            "db_name": s.get("db_name"),
            "phone": s.get("phone"),
            "address": s.get("address"),
            "is_active": bool(s.get("is_active", True)),
            "status": s.get("status") or "active",
        })

    return jsonify({"ok": True, "shops": items})


@settings_bp.post("/api/locations")
@login_required
@permission_required("settings.manage_org")
def api_locations_create():
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        return jsonify({"ok": False, "errors": ["Session mismatch"]}), 401

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    address = (data.get("address") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()

    if len(name) < 2:
        return jsonify({"ok": False, "errors": ["Shop name is required."]}), 400

    tenant_slug = tenant.get("slug") or slugify_shop_name(tenant.get("name") or "tenant")
    shop_slug = slugify_shop_name(name)

    if master.shops.find_one({"tenant_id": tenant["_id"], "slug": shop_slug}):
        return jsonify({"ok": False, "errors": ["Shop already exists (slug conflict)."]}), 409

    shop_db_name = make_shop_db_name(tenant_slug, shop_slug)

    shop_doc = {
        "tenant_id": tenant["_id"],
        "name": name,
        "slug": shop_slug,
        "db_name": shop_db_name,
        "address": address or None,
        "phone": phone or None,
        "email": email or None,
        "status": "active",
        "is_active": True,
        "is_primary": False,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }

    try:
        res = master.shops.insert_one(shop_doc)
        new_shop_id = res.inserted_id

        # ✅ IMPORTANT: нужен _id для seed parts_categories / labor_rates
        shop_doc["_id"] = new_shop_id

        # ✅ доступ выдаём только owners
        _grant_shop_to_owners(master, tenant["_id"], new_shop_id)

        # Also grant access to the current user immediately.
        # This prevents requiring re-login to see/select the new shop.
        current_user_id = user.get("_id")
        if current_user_id:
            master.users.update_one(
                {"_id": current_user_id},
                {"$addToSet": {"shop_ids": new_shop_id}},
            )

        # Keep current session in sync right away.
        shop_ids_in_session = session.get("shop_ids") if isinstance(session.get("shop_ids"), list) else []
        new_shop_id_str = str(new_shop_id)
        normalized = [str(x) for x in shop_ids_in_session]
        if new_shop_id_str not in normalized:
            normalized.append(new_shop_id_str)
            session["shop_ids"] = normalized
            session.modified = True

        # ✅ создать shop DB + seed parts_categories + pricing rules + labor rates
        init_shop_database(shop_db_name, tenant, shop_doc, actor_user_id=user.get("_id"))

        return jsonify({
            "ok": True,
            "shop": {
                "id": str(new_shop_id),
                "name": name,
                "slug": shop_slug,
                "db_name": shop_db_name,
                "address": address or "",
                "phone": phone or "",
                "email": email or "",
            }
        }), 201

    except Exception as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 500


@settings_bp.route("/api/locations/<shop_id>", methods=["PUT", "PATCH"])
@login_required
@permission_required("settings.manage_org")
def api_locations_update(shop_id: str):
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        return jsonify({"ok": False, "errors": ["Session mismatch"]}), 401

    shop_oid = _maybe_object_id(shop_id)
    if not shop_oid:
        return jsonify({"ok": False, "errors": ["Invalid shop id"]}), 400

    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": tenant["_id"]})
    if not shop:
        return jsonify({"ok": False, "errors": ["Shop not found"]}), 404

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    address = (data.get("address") or "").strip()
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()

    if len(name) < 2:
        return jsonify({"ok": False, "errors": ["Shop name is required."]}), 400

    now = utcnow()
    master.shops.update_one(
        {"_id": shop_oid},
        {
            "$set": {
                "name": name,
                "address": address or None,
                "phone": phone or None,
                "email": email or None,
                "updated_at": now,
                "updated_by": user.get("_id"),
            },
            "$unset": {
                "address_line": "",
                "city": "",
                "state": "",
                "zip": "",
            },
        },
    )

    return jsonify({"ok": True})


@settings_bp.post("/api/locations/<shop_id>/inactive")
@login_required
@permission_required("settings.manage_org")
def api_locations_inactive(shop_id: str):
    master = get_master_db()
    user = _load_current_user(master)
    tenant = _load_current_tenant(master)
    if not user or not tenant:
        return jsonify({"ok": False, "errors": ["Session mismatch"]}), 401

    shop_oid = _maybe_object_id(shop_id)
    if not shop_oid:
        return jsonify({"ok": False, "errors": ["Invalid shop id"]}), 400

    shop = master.shops.find_one({"_id": shop_oid, "tenant_id": tenant["_id"]})
    if not shop:
        return jsonify({"ok": False, "errors": ["Shop not found"]}), 404

    now = utcnow()
    master.shops.update_one(
        {"_id": shop_oid},
        {
            "$set": {
                "status": "disabled",
                "is_active": False,
                "updated_at": now,
                "updated_by": user.get("_id"),
            }
        },
    )

    return jsonify({"ok": True})


