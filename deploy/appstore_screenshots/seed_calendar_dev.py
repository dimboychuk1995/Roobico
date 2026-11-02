"""Демо-записи календаря для скриншотов — ТОЛЬКО dev-база (27017) тестового
тенанта. Удаляет сегодняшние тестовые «John Doe» и добавляет реалистичные
записи на неделю 3 августа 2026 (где есть WO и таймеры)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\dimbo\SmallShop")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(r"C:\Users\dimbo\SmallShop\.env")
from pymongo import MongoClient  # noqa: E402

uri = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
assert "27018" not in uri, "refusing to touch PROD"
client = MongoClient(uri, serverSelectionTimeoutMS=8000)
master = client[os.environ.get("MASTER_DB_NAME") or os.environ.get("MONGO_MASTER_DB") or "master_db"]

owner = master.users.find_one({"email": "owner@roobico-test.com"})
tenant_id = owner["tenant_id"]
tv = [tenant_id, str(tenant_id)]
shop = master.shops.find_one({"tenant_id": {"$in": tv}})
db = client[shop["db_name"]]
print("shop", shop["name"], shop["db_name"])

deleted = db.calendar_events.delete_many({"shop_id": shop["_id"], "title": "John Doe"}).deleted_count
print("deleted John Doe test appointments:", deleted)


def customer(part):
    c = db.customers.find_one({"company_name": {"$regex": part, "$options": "i"}, "is_active": True})
    assert c, part
    return c


def unit_of(c):
    u = db.units.find_one({"customer_id": c["_id"], "is_active": True})
    assert u, c["company_name"]
    label = " ".join(str(u.get(k) or "").strip() for k in ("unit_number", "year", "make", "model")).strip()
    return u, " ".join(label.split())


mechs = {}
for u in master.users.find({"tenant_id": {"$in": tv}, "role": {"$in": ["mechanic", "senior_mechanic"]}, "is_active": True}):
    mechs[f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()] = u
print("mechanics:", list(mechs))

skf, sunrise, globeks = customer("SKF"), customer("Sunrise"), customer("Globeks")
plan = [
    ("Oil change + DOT inspection", skf, "Mike Wrench", datetime(2026, 8, 4, 7, 30), datetime(2026, 8, 4, 9, 0), "confirmed"),
    ("Brake pads & rotors", sunrise, "Tony Torque", datetime(2026, 8, 4, 14, 0), datetime(2026, 8, 4, 15, 30), "scheduled"),
    ("Coolant leak follow-up", globeks, "Sam Spanner", datetime(2026, 8, 4, 15, 30), datetime(2026, 8, 4, 17, 0), "scheduled"),
    ("Tire rotation", skf, "Tony Torque", datetime(2026, 8, 5, 9, 0), datetime(2026, 8, 5, 10, 0), "confirmed"),
    ("Annual inspection", globeks, "Mike Wrench", datetime(2026, 8, 6, 10, 0), datetime(2026, 8, 6, 11, 30), "scheduled"),
    ("PM service — 10k miles", sunrise, "Sam Spanner", datetime(2026, 8, 7, 8, 0), datetime(2026, 8, 7, 10, 0), "scheduled"),
]
now = datetime.now(timezone.utc)
docs = []
for title, cust, mech_name, start, end, status in plan:
    unit, unit_label = unit_of(cust)
    mech = mechs.get(mech_name)
    docs.append({
        "title": title,
        "start_time": start,
        "end_time": end,
        "status": status,
        "customer_id": cust["_id"],
        "customer_label": cust.get("company_name") or "",
        "unit_id": unit["_id"],
        "unit_label": unit_label,
        "mechanic_id": mech["_id"] if mech else None,
        "mechanic_name": mech_name if mech else "",
        "presets": [],
        "work_order_id": None,
        "wo_number": None,
        "shop_id": shop["_id"],
        "tenant_id": tenant_id,
        "created_at": now,
        "updated_at": now,
        "created_by": owner["_id"],
        "updated_by": owner["_id"],
        "seed_tag": "appstore_screenshots",
    })
# идемпотентно: прошлые сиды с тем же тегом убираем
db.calendar_events.delete_many({"shop_id": shop["_id"], "seed_tag": "appstore_screenshots"})
res = db.calendar_events.insert_many(docs)
print("inserted", len(res.inserted_ids))
for d in docs:
    print("  ", d["start_time"], d["title"], "|", d["customer_label"], "|", d["unit_label"], "|", d["mechanic_name"])
