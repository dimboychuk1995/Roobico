"""
Создание ПОЛНОГО тестового тенанта: тенант + магазин + пользователи всех
ролей + вендоры + запчасти (core/misc charges, остатки) + клиенты с юнитами +
пресеты + work orders во всех состояниях (paid / unpaid / in_progress /
mechanic_done / estimate) + платежи + тайм-логи механиков.

Запуск (из корня проекта, с активным .env — Mongo берётся оттуда):
    python -m app.scripts.seed_test_tenant

Скрипт только ДОБАВЛЯЕТ данные (новый тенант и его собственные базы),
чужие тенанты не трогает. Повторный запуск безопасен: если owner-email уже
существует — скрипт завершается без изменений.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from bson import ObjectId  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402

COMPANY_NAME = "Roobico Test Fleet Services"
COMPANY_ADDRESS = "4801 W Irving Park Rd, Chicago, IL 60641"
COMPANY_PHONE = "+1 (555) 010-2026"
PASSWORD = "RoobicoTest2026!"

USERS = [
    # (email, first, last, role)
    ("owner@roobico-test.com", "Oliver", "Owner", "owner"),
    ("gm@roobico-test.com", "Henry", "Boss", "general_manager"),
    ("manager@roobico-test.com", "Grace", "Manager", "manager"),
    ("partsman@roobico-test.com", "Peter", "Parts", "parts_manager"),
    ("mech1@roobico-test.com", "Mike", "Wrench", "mechanic"),
    ("mech2@roobico-test.com", "Tony", "Torque", "mechanic"),
    ("mech3@roobico-test.com", "Sam", "Spanner", "mechanic"),
]


def _contact(first, last, phone, email, is_main=True):
    return {
        "first_name": first, "last_name": last,
        "phone": phone, "email": email, "is_main": is_main,
    }


def main() -> int:
    app = create_app()
    with app.app_context():
        from app.extensions import get_master_db, get_mongo_client
        from app.blueprints.tenant.routes import (
            init_shop_database,
            init_tenant_database,
            make_shop_db_name,
            make_tenant_db_name,
            slugify_company_name,
            slugify_shop_name,
        )
        from app.blueprints.parts.services.stock import apply_stock_change
        from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
        from app.blueprints.work_orders.services.totals import (
            _apply_sales_tax_to_totals,
            align_totals_with_labors,
            get_next_wo_number,
            normalize_totals_payload,
        )
        from app.utils.contacts import (
            build_customer_legacy_contact_fields,
            build_vendor_legacy_contact_fields,
        )
        from app.utils.entity_search import (
            build_customer_search_terms,
            build_unit_search_terms,
        )
        from app.utils.parts_search import build_parts_search_terms
        from app.blueprints.tenant.routes import utcnow

        master = get_master_db()

        if master.users.find_one({"email": USERS[0][0]}):
            print(f"ABORT: user {USERS[0][0]} already exists — тенант уже создан.")
            return 1

        now = utcnow()
        tenant_slug = slugify_company_name(COMPANY_NAME)
        tenant_db_name = make_tenant_db_name(COMPANY_NAME)
        shop_slug = slugify_shop_name(COMPANY_NAME)
        shop_db_name = make_shop_db_name(tenant_slug, shop_slug)

        if master.tenants.find_one({"slug": tenant_slug}):
            print(f"ABORT: tenant slug '{tenant_slug}' already exists.")
            return 1

        # ── tenant + shop + базы (тот же путь, что /tenant/register) ──
        tenant_doc = {
            "name": COMPANY_NAME, "slug": tenant_slug, "db_name": tenant_db_name,
            "address": COMPANY_ADDRESS, "zip": "60641", "phone": COMPANY_PHONE,
            "email": USERS[0][0], "contact_name": "Oliver Owner",
            "contact_email": USERS[0][0], "contact_phone": COMPANY_PHONE,
            "billing_email": USERS[0][0], "billing_phone": COMPANY_PHONE,
            "billing_address": COMPANY_ADDRESS, "timezone": "America/Chicago",
            "status": "active", "subscription_status": "trial",
            "subscription_until": now + timedelta(days=30),
            "trial_started_at": now, "created_at": now, "updated_at": now,
        }
        tenant_id = master.tenants.insert_one(tenant_doc).inserted_id

        shop_doc = {
            "tenant_id": tenant_id, "name": COMPANY_NAME, "slug": shop_slug,
            "db_name": shop_db_name, "address": COMPANY_ADDRESS, "zip": "60641",
            "phone": COMPANY_PHONE, "email": USERS[0][0],
            "billing_address": COMPANY_ADDRESS, "status": "active",
            "is_active": True, "is_primary": True,
            "dashboard_goals": {"labor": 60000.0, "parts_sales": 80000.0, "total": 140000.0},
            "created_at": now, "updated_at": now,
        }
        shop_id = master.shops.insert_one(shop_doc).inserted_id
        shop_doc["_id"] = shop_id

        # ── пользователи всех ролей ──
        user_ids: dict[str, ObjectId] = {}
        pwd_hash = generate_password_hash(PASSWORD)
        for email, first, last, role in USERS:
            uid = master.users.insert_one({
                "tenant_id": tenant_id, "shop_ids": [shop_id],
                "first_name": first, "last_name": last,
                "name": f"{first} {last}", "email": email,
                "password_hash": pwd_hash, "role": role, "is_active": True,
                "created_at": now, "updated_at": now,
            }).inserted_id
            user_ids[email] = uid
        owner_id = user_ids[USERS[0][0]]

        init_tenant_database(tenant_db_name, tenant_doc)
        init_shop_database(shop_db_name, tenant_doc, shop_doc, actor_user_id=owner_id)

        sdb = get_mongo_client()[shop_db_name]

        def _audit(doc):
            doc.update({
                "is_active": True, "created_at": now, "updated_at": now,
                "created_by": owner_id, "updated_by": owner_id,
                "deactivated_at": None, "deactivated_by": None,
                "shop_id": shop_id, "tenant_id": tenant_id,
            })
            return doc

        # ── вендоры ──
        vendors = [
            ("FleetPride", "https://www.fleetpride.com", _contact("Dan", "Sales", "+1 (555) 210-0001", "dan@fleetpride.example")),
            ("NAPA Auto Parts", "https://www.napaonline.com", _contact("Nina", "Counter", "+1 (555) 210-0002", "nina@napa.example")),
            ("Rush Truck Centers", "https://www.rushtruckcenters.com", _contact("Ray", "Desk", "+1 (555) 210-0003", "ray@rush.example")),
        ]
        vendor_ids = {}
        for name, site, contact in vendors:
            doc = _audit({"name": name, "website": site, "address": None,
                          "contacts": [contact], "notes": None})
            doc.update(build_vendor_legacy_contact_fields(doc["contacts"]))
            vendor_ids[name] = sdb.vendors.insert_one(doc).inserted_id

        # ── запчасти (часть — с core/misc charges) ──
        cat = {c["name"]: c["_id"] for c in sdb.parts_categories.find({"shop_id": shop_id})}
        PARTS = [
            # pn, desc, category, cost, sell, core, misc, stock, vendor
            ("OF-1052", "Oil filter HD", "Filters", 8.5, 19.99, None, None, 60, "NAPA Auto Parts"),
            ("FF-2200", "Fuel filter kit", "Filters", 14.0, 32.5, None, None, 40, "NAPA Auto Parts"),
            ("AF-3310", "Air filter", "Filters", 22.0, 49.0, None, None, 35, "FleetPride"),
            ("BP-4405", "Brake pad set (front)", "Body", 38.0, 89.99, None, None, 24, "FleetPride"),
            ("BR-4406", "Brake rotor", "Body", 55.0, 129.0, None, None, 16, "FleetPride"),
            ("BAT-9901", "Battery 31-series AGM", "Electrical", 145.0, 289.0, 22.0,
             [{"description": "EPA battery fee", "price": 3.5, "taxable": False}], 12, "NAPA Auto Parts"),
            ("ALT-7702", "Alternator 160A", "Electrical", 180.0, 379.0, 45.0, None, 6, "Rush Truck Centers"),
            ("BLT-5501", "Serpentine belt", "Exhaust", 19.0, 44.0, None, None, 30, "NAPA Auto Parts"),
            ("CL-6600", "Coolant concentrate 1G", "DEF", 12.0, 27.5, None, None, 48, "FleetPride"),
            ("DEF-2G", "DEF fluid 2.5G", "DEF", 9.0, 21.0, None, None, 80, "FleetPride"),
            ("HL-8801", "LED headlamp assembly", "Electrical", 95.0, 219.0, None, None, 8, "Rush Truck Centers"),
            ("CAL-4501", "Brake caliper reman", "Body", 88.0, 199.0, 35.0,
             [{"description": "Hazmat handling fee", "price": 5.0, "taxable": True}], 10, "FleetPride"),
        ]
        part_ids = {}
        for pn, desc, cname, cost, sell, core, misc, stock, vendor in PARTS:
            doc = _audit({
                "part_number": pn, "description": desc, "reference": None,
                "search_terms": build_parts_search_terms(pn, desc, None),
                "vendor_id": vendor_ids.get(vendor), "category_id": cat.get(cname),
                "location_id": None, "do_not_track_inventory": False,
                "average_cost": cost, "has_selling_price": True, "selling_price": sell,
                "core_has_charge": bool(core), "core_cost": core,
                "misc_has_charge": bool(misc), "misc_charges": misc or [],
                "in_stock": 0,
            })
            pid = sdb.parts.insert_one(doc).inserted_id
            part_ids[pn] = pid
            apply_stock_change(sdb, shop_id, pid, stock, "initial", user_id=owner_id)

        # ── клиенты + юниты ──
        CUSTOMERS = [
            ("Globeks Test Corp", _contact("John", "Fleet", "+1 (555) 300-0001", "john@globeks-test.example"), [
                ("2209", "1FT8W3DT9TST21123", 2022, "Ford", "F-350", "Truck", 61234),
                ("2603", "1FT8W3DT8TST32119", 2026, "Ford", "F-350", "Truck", 68109),
                ("3236", "16V3F4820TST43236", 2024, "Big Tex", "Big Tex", "Trailer", None),
            ]),
            ("SKF Test Trucking LLC", _contact("Mary", "Dispatch", "+1 (555) 300-0002", "mary@skf-test.example"), [
                ("5387", "4V5RC9EJ7TST85387", 2015, "Volvo", "VNL", "Truck", 940603),
                ("2698", "1FT8W3DT5TST25996", 2026, "Ford", "F-350", "Truck", 52911),
            ]),
            ("Redline Test Logistics Inc", _contact("Alex", "Ops", "+1 (555) 300-0003", "alex@redline-test.example"), [
                ("2216", "1XKYDP9X1TST32048", 2023, "Kenworth", "T680", "Truck", 489276),
                ("7041", "3AKJHHDR2TST47041", 2022, "Freightliner", "Cascadia", "Truck", 196918),
            ]),
            ("Sunrise Test Transport LLC", _contact("Igor", "Owner", "+1 (555) 300-0004", "igor@sunrise-test.example"), [
                ("0008", "7SLE1EM39TST00008", 2022, "Elite Cargo", "Trailer", "Trailer", None),
            ]),
            (None, _contact("John", "Doe", "+1 (555) 300-0005", "john.doe@walkin.example"), [
                ("JD-1", "1GNSKCKC0TST11223", 2019, "Chevrolet", "Tahoe", "SUV", 88450),
            ]),
        ]
        customer_ids = []
        unit_ids = {}
        for company, contact, units in CUSTOMERS:
            cdoc = _audit({
                "company_name": company, "contacts": [contact],
                "address": "Chicago, IL", "taxable": False,
                "default_labor_rate": None, "pricing_rule_id": None,
                "override_part_selling_price": False,
            })
            cdoc.update(build_customer_legacy_contact_fields(cdoc["contacts"]))
            cdoc["search_terms"] = build_customer_search_terms(cdoc)
            cid = sdb.customers.insert_one(cdoc).inserted_id
            customer_ids.append(cid)
            for unit_number, vin, year, make, model, utype, mileage in units:
                udoc = _audit({
                    "customer_id": cid, "unit_number": unit_number, "vin": vin,
                    "year": year, "make": make, "model": model, "type": utype,
                    "mileage": mileage,
                })
                udoc["search_terms"] = build_unit_search_terms(udoc)
                unit_ids[vin] = sdb.units.insert_one(udoc).inserted_id

        # ── пресеты работ ──
        PRESETS = [
            ("PM Service", "Full PM: oil, filters, grease", 2.0, "standard",
             [("OF-1052", 1), ("FF-2200", 1)]),
            ("Brake Job (front axle)", "Pads + rotors front axle", 3.0, "standard",
             [("BP-4405", 1), ("BR-4406", 2)]),
            ("DOT Inspection", "Annual DOT inspection", 1.5, "standard", []),
        ]
        for name, desc, hours, rate, plist in PRESETS:
            parts = []
            for pn, qty in plist:
                pdoc = sdb.parts.find_one({"_id": part_ids[pn]})
                parts.append({
                    "part_id": str(part_ids[pn]), "part_number": pn,
                    "description": pdoc.get("description") or "", "qty": qty,
                    "cost": pdoc.get("average_cost") or 0,
                    "price": pdoc.get("selling_price") or 0,
                    "misc_charges": [],
                })
            sdb.wo_presets.insert_one(_audit({
                "name": name, "description": desc, "labor_hours": hours,
                "labor_rate_code": rate, "allow_discount": False, "parts": parts,
            }))

        # ── work orders ──
        mech1 = user_ids["mech1@roobico-test.com"]
        mech2 = user_ids["mech2@roobico-test.com"]
        mech3 = user_ids["mech3@roobico-test.com"]

        def _wo_part(pn, qty):
            pdoc = sdb.parts.find_one({"_id": part_ids[pn]})
            core = pdoc.get("core_cost") if pdoc.get("core_has_charge") else 0
            return {
                "part_id": str(part_ids[pn]), "part_number": pn,
                "description": pdoc.get("description") or "", "qty": qty,
                "cost": pdoc.get("average_cost") or 0,
                "price": pdoc.get("selling_price") or 0,
                "core_charge": core or 0, "misc_charge": 0,
                "misc_charge_description": "", "one_time_part": False,
            }

        def _make_wo(customer_i, vin, days_ago, labors_payload, status,
                     mechanic_done=False, paid=None, mileage=None):
            labors, totals_raw = compute_labors_and_totals(sdb, shop_doc, labors_payload)
            totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
            totals = _apply_sales_tax_to_totals(totals, 0, False)
            wo_date = now - timedelta(days=days_ago)
            wo_number = get_next_wo_number(sdb, shop_id)
            doc = {
                "shop_id": shop_id, "tenant_id": tenant_id,
                "customer_id": customer_ids[customer_i], "unit_id": unit_ids[vin],
                "wo_number": wo_number, "work_order_date": wo_date,
                "labors": labors, "totals": totals, "status": status,
                "mileage": mileage, "is_active": True,
                "created_at": wo_date, "updated_at": wo_date,
                "created_by": owner_id, "updated_by": owner_id,
                "mechanic_done": mechanic_done,
                "mechanic_done_at": wo_date if mechanic_done else None,
                "mechanic_done_by": mech3 if mechanic_done else None,
                "manager_confirmed": False,
            }
            wo_id = sdb.work_orders.insert_one(doc).inserted_id
            if paid:
                amount, method = paid
                amount = amount if amount is not None else totals.get("grand_total") or 0
                sdb.work_order_payments.insert_one({
                    "shop_id": shop_id, "tenant_id": tenant_id,
                    "work_order_id": wo_id, "amount": float(amount),
                    "payment_method": method, "payment_date": wo_date + timedelta(days=1),
                    "notes": "", "is_active": True,
                    "created_at": wo_date + timedelta(days=1), "created_by": owner_id,
                })
            return wo_id, wo_number, doc

        def _labor(desc, hours, parts, assigned=None, hours_source="", rate="standard"):
            return {
                "description": desc, "hours": str(hours) if hours else "",
                "rate_code": rate, "labor_total": None, "issue_description": "",
                "hours_source": hours_source,
                "assigned_mechanics": assigned or [], "parts": parts,
            }

        def _asg(uid, name, percent):
            return {"user_id": str(uid), "name": name, "role": "Mechanic", "percent": percent}

        # 1) paid: PM service (Globeks F-350 2209)
        _make_wo(0, "1FT8W3DT9TST21123", 12, [
            _labor("PM Service — full service", 2, [_wo_part("OF-1052", 1), _wo_part("FF-2200", 1)],
                   assigned=[_asg(mech1, "Mike Wrench", 100.0)]),
        ], "paid", paid=(None, "cash"), mileage=61234)

        # 2) paid: brake job (SKF Volvo VNL)
        _make_wo(1, "4V5RC9EJ7TST85387", 9, [
            _labor("Front brake job — pads and rotors", 3,
                   [_wo_part("BP-4405", 1), _wo_part("BR-4406", 2)],
                   assigned=[_asg(mech2, "Tony Torque", 100.0)]),
        ], "paid", paid=(None, "check"), mileage=940603)

        # 3) open/unpaid c частичной оплатой: alternator + core charge
        _make_wo(2, "1XKYDP9X1TST32048", 6, [
            _labor("Replace alternator, test charging system", 2.5,
                   [_wo_part("ALT-7702", 1), _wo_part("BLT-5501", 1)],
                   assigned=[_asg(mech1, "Mike Wrench", 100.0)]),
        ], "open", paid=(200.0, "ach"), mileage=489276)

        # 4) open/unpaid: battery с EPA misc fee (misc — JSON первой строки)
        wo4_parts = [_wo_part("BAT-9901", 1)]
        import json as _json
        wo4_parts[0]["misc_charge_description"] = _json.dumps([
            {"description": "EPA battery fee", "price": 3.5, "taxable": False,
             "quantity": 1, "partIndex": 0},
        ])
        _make_wo(0, "1FT8W3DT8TST32119", 4, [
            _labor("Replace battery, check parasitic draw", 1, wo4_parts,
                   assigned=[_asg(mech3, "Sam Spanner", 100.0)]),
        ], "open", mileage=68109)

        # 5) in_progress: двое механиков, затреканное время
        wo5_id, wo5_num, wo5 = _make_wo(1, "1FT8W3DT5TST25996", 1, [
            _labor("Diagnose coolant leak, pressure test", 1.5,
                   [_wo_part("CL-6600", 2)],
                   assigned=[_asg(mech1, "Mike Wrench", 75.0), _asg(mech2, "Tony Torque", 25.0)],
                   hours_source="tracked"),
        ], "in_progress", mileage=52911)
        labor5_id = wo5["labors"][0]["labor_id"]
        for uid, uname, secs in ((mech1, "Mike Wrench", 4050), (mech2, "Tony Torque", 1350)):
            t0 = now - timedelta(hours=6)
            sdb.wo_time_logs.insert_one({
                "shop_id": shop_id, "tenant_id": tenant_id, "work_order_id": wo5_id,
                "wo_number": wo5_num, "labor_id": labor5_id, "user_id": uid,
                "user_name": uname, "started_at": t0,
                "stopped_at": t0 + timedelta(seconds=secs), "seconds": secs,
                "stop_source": "user", "created_at": t0, "updated_at": t0,
            })

        # 6) in_progress + mechanic_done: ждёт подтверждения менеджером
        wo6_id, wo6_num, wo6 = _make_wo(3, "7SLE1EM39TST00008", 0, [
            _labor("Trailer LED headlamp replacement", 1,
                   [_wo_part("HL-8801", 1)],
                   assigned=[_asg(mech3, "Sam Spanner", 100.0)],
                   hours_source="tracked"),
        ], "in_progress", mechanic_done=True)
        labor6_id = wo6["labors"][0]["labor_id"]
        t0 = now - timedelta(hours=3)
        sdb.wo_time_logs.insert_one({
            "shop_id": shop_id, "tenant_id": tenant_id, "work_order_id": wo6_id,
            "wo_number": wo6_num, "labor_id": labor6_id, "user_id": mech3,
            "user_name": "Sam Spanner", "started_at": t0,
            "stopped_at": t0 + timedelta(seconds=3600), "seconds": 3600,
            "stop_source": "user", "created_at": t0, "updated_at": t0,
        })

        # 7) estimate: смета на тормоза (walk-in)
        _make_wo(4, "1GNSKCKC0TST11223", 2, [
            _labor("Front brake job — estimate", 3,
                   [_wo_part("BP-4405", 1), _wo_part("BR-4406", 2), _wo_part("CAL-4501", 1)]),
        ], "estimate", mileage=88450)

        print("=" * 60)
        print("DONE — test tenant created")
        print(f"  tenant:  {COMPANY_NAME}  (slug={tenant_slug})")
        print(f"  tenant_db: {tenant_db_name}")
        print(f"  shop_db:   {shop_db_name}")
        print(f"  password (all users): {PASSWORD}")
        for email, first, last, role in USERS:
            print(f"  {role:16s} {first} {last:10s} {email}")
        print(f"  vendors: {len(vendors)}, parts: {len(PARTS)}, "
              f"customers: {len(CUSTOMERS)}, units: {len(unit_ids)}, "
              f"presets: {len(PRESETS)}, WOs: 7 (2 paid, 2 open, 2 in-progress, 1 estimate)")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
