"""
Конкурентные правки WO из офиса (веб-редактор work_order_details): CAS по
labors_rev, трёхсторонний merge строк (OFFICE_SPEC), 3-way полей уровня WO,
быстрый путь тоталов без конфликтов, механик на офисном эндпоинте, describe
issue по labor_id, transfer / delete / статус / live_state.

Первая часть — регрессия «без конфликтов»: Save, при котором WO никто
другой не трогал, даёт ровно те же labors/totals, что и старый алгоритм (его
дословная копия ниже — оракул). Клиент имитируется как work_order_details.js:
рендер страницы → правка → serializeBlocks/serializeTotals → POST.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from tests.conftest import SHOP_A_DB, TENANT_A_DB, TEST_MONGO_URI, get_csrf_token, login

PASSWORD = "password123"
MECH_A = "co.mech.a@test.local"
MECH_B = "co.mech.b@test.local"
FORMER = "co.former@test.local"
DIESEL = "co-diesel"
TAX_RATE = 0.0825


def _now():
    return datetime.now(timezone.utc)


# ───────────────────────── окружение ─────────────────────────


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(TEST_MONGO_URI, serverSelectionTimeoutMS=3000)
    yield client
    client.close()


@pytest.fixture(scope="module")
def co(app, seed, mongo):
    """Механики, налогооблагаемый клиент с юнитами, второй клиент (transfer),
    каталожные запчасти с core и misc-чарджами, вторая ставка; shop supply
    7% (дробные доли по блокам)."""
    from app.constants.permissions import build_default_roles

    master = mongo["roobico_test_master"]
    tdb = mongo[TENANT_A_DB]
    shop_db = mongo[SHOP_A_DB]
    shop = seed["shop_a"]
    now = _now()

    mechanic_role = next(r for r in build_default_roles() if r["key"] == "mechanic")
    tdb.roles.update_one({"key": "mechanic"}, {"$set": mechanic_role}, upsert=True)

    users = {}
    for email, first in ((MECH_A, "Oscar"), (MECH_B, "Olga"), (FORMER, "Fred")):
        u = {
            "_id": ObjectId(), "email": email, "password_hash": generate_password_hash(PASSWORD),
            "first_name": first, "last_name": "Office", "is_active": True,
            "tenant_id": seed["tenant_a"]["_id"], "shop_ids": [str(shop["_id"])],
            "role": "mechanic", "created_at": now,
        }
        master.users.insert_one(u)
        users[email] = u

    customer = {
        "_id": ObjectId(), "shop_id": shop["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Office Concurrent Fleet", "taxable": True, "is_active": True, "created_at": now,
    }
    other = {
        "_id": ObjectId(), "shop_id": shop["_id"], "tenant_id": seed["tenant_a"]["_id"],
        "company_name": "Office Transfer Target", "taxable": False, "is_active": True, "created_at": now,
    }
    shop_db.customers.insert_many([customer, other])
    unit = {
        "_id": ObjectId(), "shop_id": shop["_id"], "customer_id": customer["_id"],
        "vin": "OFFCONC1234567890", "unit_number": "OC-1", "mileage": 80000,
        "is_active": True, "created_at": now,
    }
    unit2 = {
        "_id": ObjectId(), "shop_id": shop["_id"], "customer_id": customer["_id"],
        "vin": "OFFCONC1234567891", "unit_number": "OC-2", "mileage": 40000,
        "is_active": True, "created_at": now,
    }
    shop_db.units.insert_many([unit, unit2])

    parts = {
        "pads": {
            "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "CO-PADS",
            "description": "Brake pads", "average_cost": 40.0, "has_selling_price": True,
            "selling_price": 99.5, "in_stock": 100, "is_active": True, "created_at": now,
        },
        "battery": {
            "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "CO-BATT",
            "description": "Battery", "average_cost": 50.0, "has_selling_price": True,
            "selling_price": 120.0, "core_has_charge": True, "core_cost": 15.0,
            "misc_has_charge": True,
            "misc_charges": [{"description": "EPA fee", "price": 3.35, "taxable": True}],
            "in_stock": 100, "is_active": True, "created_at": now,
        },
        "filter": {
            "_id": ObjectId(), "shop_id": shop["_id"], "part_number": "CO-FILTER",
            "description": "Oil filter", "average_cost": 6.0, "has_selling_price": True,
            "selling_price": 12.49, "in_stock": 100, "is_active": True, "created_at": now,
        },
    }
    shop_db.parts.insert_many(list(parts.values()))

    diesel = {
        "_id": ObjectId(), "shop_id": shop["_id"], "code": DIESEL, "name": "CO Diesel",
        "hourly_rate": 135.5, "is_active": True,
    }
    shop_db.labor_rates.insert_one(diesel)

    supply_prev = shop_db.shop_supply_amount_rules.find_one({"shop_id": shop["_id"]})
    shop_db.shop_supply_amount_rules.update_one(
        {"shop_id": shop["_id"]},
        {"$set": {"shop_supply_procentage": 7, "is_active": True}},
        upsert=True,
    )

    yield {
        "shop": shop, "users": users, "customer": customer, "other": other,
        "unit": unit, "unit2": unit2, "parts": parts, "owner": seed["owner"],
    }

    shop_db.work_orders.update_many(
        {"customer_id": {"$in": [customer["_id"], other["_id"]]}}, {"$set": {"is_active": False}})
    shop_db.wo_time_logs.delete_many({"user_id": {"$in": [u["_id"] for u in users.values()]}})
    master.users.update_many({"_id": {"$in": [u["_id"] for u in users.values()]}}, {"$set": {"is_active": False}})
    shop_db.parts.delete_many({"_id": {"$in": [p["_id"] for p in parts.values()]}})
    shop_db.labor_rates.delete_one({"_id": diesel["_id"]})
    shop_db.units.update_many({"customer_id": {"$in": [customer["_id"], other["_id"]]}}, {"$set": {"is_active": False}})
    if supply_prev is None:
        shop_db.shop_supply_amount_rules.delete_one({"shop_id": shop["_id"]})
    else:
        shop_db.shop_supply_amount_rules.replace_one({"_id": supply_prev["_id"]}, supply_prev)


def _wo(mongo, wo_id):
    return mongo[SHOP_A_DB].work_orders.find_one({"_id": ObjectId(str(wo_id))})


def _stock(mongo, part):
    return mongo[SHOP_A_DB].parts.find_one({"_id": part["_id"]})["in_stock"]


def _owner(app):
    c = app.test_client()
    assert login(c).status_code == 302
    return c


def _post(client, url, payload):
    return client.post(url, json=payload, headers={"X-CSRFToken": get_csrf_token(client)})


def _assign(user, percent=100.0):
    return {"user_id": user["_id"], "name": f"{user['first_name']} {user['last_name']}",
            "role": user["role"], "percent": percent}


def _misc_json(items):
    """Формат, в котором JS страницы деталей пишет misc-позиции (JSON.stringify)."""
    return json.dumps(items, separators=(",", ":"))


def _insert_wo(app, mongo, co, rows, *, customer=None, unit=None, taxable=True, tax_rate=TAX_RATE,
               status="open", supply_override=None, mileage=80000, **extra):
    """WO с totals, посчитанными сервером (как после мобильного/механик
    сохранения); rows — payload compute_labors_and_totals."""
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        align_totals_with_labors,
        normalize_totals_payload,
    )

    shop_db = mongo[SHOP_A_DB]
    with app.app_context():
        labors, totals_raw = compute_labors_and_totals(shop_db, co["shop"], copy.deepcopy(rows))
        totals = align_totals_with_labors(normalize_totals_payload(totals_raw), labors)
        if supply_override is not None:
            totals = align_totals_with_labors({**totals, "shop_supply_total": supply_override}, labors)
        totals = _apply_sales_tax_to_totals(totals, tax_rate, taxable)
    now = _now()
    doc = {
        "_id": ObjectId(), "shop_id": co["shop"]["_id"], "tenant_id": co["shop"].get("tenant_id"),
        "wo_number": int(ObjectId().binary[-3:].hex(), 16) % 800000 + 100000,
        "customer_id": (customer or co["customer"])["_id"], "unit_id": (unit or co["unit"])["_id"],
        "status": status, "labors": labors, "totals": totals, "labors_rev": 1, "mileage": mileage,
        "is_active": True, "work_order_date": now, "created_at": now, "updated_at": now,
    }
    doc.update(extra)
    shop_db.work_orders.insert_one(doc)
    return doc


def _rich_rows(co):
    """Три работы: каталожные запчасти с core, misc-JSON первой строки
    (auto + ручная, облагаемые и нет), вторая ставка, tracked-строка с
    одноразовой запчастью; два механика назначены."""
    pads, batt = co["parts"]["pads"], co["parts"]["battery"]
    a, b = co["users"][MECH_A], co["users"][MECH_B]
    return [
        {
            "description": "Front brakes", "hours": "1.5", "rate_code": "standard",
            "issue_description": "Squeal on stop",
            "assigned_mechanics": [_assign(a, 60.0), _assign(b, 40.0)],
            "parts": [
                {"part_id": pads["_id"], "part_number": "CO-PADS", "description": "Brake pads",
                 "qty": 2, "cost": 40.0, "price": 99.5, "core_charge": 0, "misc_charge": 0, "unit": "each",
                 "misc_charge_description": _misc_json([
                     {"description": "EPA fee", "price": 3.35, "quantity": 1, "partIndex": 1,
                      "manual": False, "taxable": True},
                     {"description": "Shop rag", "price": 4.15, "quantity": 2, "manual": True, "taxable": True},
                     {"description": "Disposal", "price": 2.5, "quantity": 1, "manual": True, "taxable": False},
                 ])},
                {"part_id": batt["_id"], "part_number": "CO-BATT", "description": "Battery",
                 "qty": 1, "cost": 50.0, "price": 120.0, "core_charge": 15.0, "misc_charge": 0,
                 "misc_charge_description": "", "unit": "each"},
            ],
        },
        {"description": "Diagnostics", "hours": "0.35", "rate_code": DIESEL, "issue_description": "",
         "assigned_mechanics": [_assign(a)], "parts": []},
        {"description": "Oil service", "hours": "1.33", "rate_code": "standard", "hours_source": "tracked",
         "issue_description": "", "assigned_mechanics": [_assign(b)],
         "parts": [{"part_id": "", "part_number": "OT-1", "description": "Local filter", "qty": 3,
                    "cost": 5.0, "price": 12.49, "core_charge": 0, "misc_charge": 0,
                    "misc_charge_description": "", "one_time_part": True, "unit": "each"}]},
    ]


# ───────────────────────── клиент: work_order_details.js ─────────────────────────


def _script_json(html, element_id):
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % re.escape(element_id), html, re.S)
    assert m, f"{element_id} not found"
    return json.loads(m.group(1))


def _round2(v):
    return round(float(v or 0) + 1e-12, 2)


def _to_num(v):
    s = str(v if v is not None else "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _js_number(v):
    """Number(str) поля формы: '' → 0, целое → int (JSON.stringify(2) == "2")."""
    n = _to_num(v)
    if n is None:
        return 0
    return int(n) if n.is_integer() else n


def _misc_items(part):
    raw = str((part or {}).get("misc_charge_description") or "").strip()
    if not raw.startswith("["):
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


class OfficeTab:
    """Вкладка веб-редактора WO: то, что отрисовал сервер, + правки
    пользователя; payload() — serializeBlocks/serializeTotals."""

    def __init__(self, client, wo_id, *, new_js=True):
        self.client, self.wo_id, self.new_js = client, str(wo_id), new_js
        self._ck = 0
        self.load()

    def load(self):
        resp = self.client.get(f"/work_orders/details?work_order_id={self.wo_id}")
        assert resp.status_code == 200, resp.status_code
        html = resp.get_data(as_text=True)
        self.created = _script_json(html, "workOrderCreatedData")
        self.snapshot = _script_json(html, "workOrderInitialTotalsData")
        self.pct = float(_script_json(html, "shopSupplyData")["percentage"] or 0)
        self.rates = {r["code"]: float(r["hourly_rate"]) for r in _script_json(html, "laborRatesData")}
        self.tax_rate = float(_script_json(html, "salesTaxData").get("rate") or 0)
        self.view_id = self.created.get("view_id", "")
        self.rev = self.created.get("labors_rev")
        self.date = re.search(r'id="workOrderDateInput"[^>]*value="([^"]*)"', html).group(1)
        self.mileage = re.search(r'data-wo-mileage="([^"]*)"', html).group(1)
        self.customer_id = re.search(r'id="selectedCustomerHidden"[^>]*value="([^"]*)"', html).group(1)
        self.unit_id = re.search(r'id="selectedUnitHidden"[^>]*value="([^"]*)"', html).group(1)
        self.taxable = bool(self.snapshot.get("is_taxable"))
        self.blocks = [self._block(b) for b in _script_json(html, "workOrderInitialData")]
        self.snap_blocks = list(self.snapshot.get("labors") or [])
        self.recalc = False
        # applyTotalsSnapshotToUi: сохранённый supply, расходящийся с процентом, — ручной.
        stored = _round2(self.snapshot.get("shop_supply_total"))
        calculated = _round2(_round2(self.snapshot.get("labor")) * (self.pct / 100)) if self.pct > 0 else 0.0
        self.supply_override = stored if stored > 0 and abs(stored - calculated) > 0.005 else None

    def _block(self, b):
        lab = b["labor"]
        return {
            "labor_id": b["labor_id"],
            "description": lab["description"],
            "hours": lab["hours"],
            # селект ставок не знает кода — значение пустое
            "rate_code": lab["rate_code"] if lab["rate_code"] in self.rates else "",
            "hours_edited": False,
            "assigned": [
                {"user_id": a["user_id"], "name": a["name"], "role": a["role"], "percent": a["percent"]}
                for a in lab["assigned_mechanics"]
            ],
            "issue": lab["issue_description"],
            "parts": [
                {k: p.get(k) for k in ("part_id", "one_time_part", "part_number", "description", "unit", "qty",
                                       "cost", "price", "core_charge", "misc_charge", "misc_charge_description")}
                for p in b["parts"]
            ],
            "client_key": "",
        }

    # ── правки пользователя (любой input → recalcAll) ──
    def edit(self):
        self.recalc = True

    def set_part(self, bi, pi, **fields):
        self.blocks[bi]["parts"][pi].update({k: str(v) for k, v in fields.items()})
        self.edit()

    def set_hours(self, bi, hours):
        self.blocks[bi]["hours"] = str(hours)
        self.blocks[bi]["hours_edited"] = True
        self.edit()

    def set_rate(self, bi, code):
        self.blocks[bi]["rate_code"] = code
        self.edit()

    def set_description(self, bi, text):
        self.blocks[bi]["description"] = text
        self.edit()

    def add_row(self, description, hours="", rate_code="standard", parts=None, issue=""):
        self._ck += 1
        self.blocks.append({
            "labor_id": "", "description": description, "hours": str(hours), "rate_code": rate_code,
            "hours_edited": bool(str(hours)), "assigned": [], "issue": issue,
            "parts": [dict(p) for p in (parts or [])],
            "client_key": f"office-ck-{id(self)}-{self._ck}",
        })
        self.snap_blocks.append({})
        self.edit()

    def remove_row(self, bi):
        del self.blocks[bi]
        if bi < len(self.snap_blocks):
            del self.snap_blocks[bi]
        self.edit()

    def remove_part(self, bi, pi):
        """Удаление строки запчасти (не первой): JS убирает и её auto misc-позиции."""
        assert pi > 0
        parts = self.blocks[bi]["parts"]
        del parts[pi]
        items = [it for it in _misc_items(parts[0]) if it.get("manual") is True or it.get("partIndex") != pi]
        if _misc_items(parts[0]):
            parts[0]["misc_charge_description"] = _misc_json(items)
        self.edit()

    def set_supply(self, value):
        self.supply_override = _round2(value)
        self.edit()

    # ── recalcBlock / serializeTotals ──
    def _calc_block(self, blk):
        hours = _to_num(blk["hours"])
        code = blk["rate_code"]
        rate = self.rates.get(code)
        labor = _round2(hours * rate) if hours is not None and code and rate is not None else 0.0
        parts = core = misc_legacy = 0.0
        for p in blk["parts"]:
            qty = _to_num(p.get("qty"))
            if qty is None or qty <= 0:
                continue
            price = _to_num(p.get("price"))
            if price is not None and price >= 0:
                parts += _round2(price * qty)
            core += _round2((_to_num(p.get("core_charge")) or 0) * qty)
            misc_legacy += _round2((_to_num(p.get("misc_charge")) or 0) * qty)
        items = _misc_items(blk["parts"][0]) if blk["parts"] else []
        if items:
            misc = sum(_round2(float(it.get("price") or 0) * float(it.get("quantity") or 1))
                       for it in items if float(it.get("price") or 0) > 0)
        else:
            misc = misc_legacy
        return _round2(labor), _round2(parts), _round2(core), _round2(misc)

    def _block_money(self, i, blk):
        if self.recalc:
            return self._calc_block(blk)
        snap = self.snap_blocks[i] if i < len(self.snap_blocks) else {}
        return (_round2(snap.get("labor")), _round2(snap.get("parts")),
                _round2(snap.get("core_total")), _round2(snap.get("misc_total")))

    def totals(self):
        out_blocks = []
        labor_sum = parts_sum = core_sum = misc_sum = 0.0
        for i, blk in enumerate(self.blocks):
            labor, parts, core, misc = self._block_money(i, blk)
            out_blocks.append({
                "labor": labor, "labor_total": labor, "parts": parts,
                "parts_total": _round2(parts + core + misc), "core_total": core, "misc_total": misc,
                "cost_total": parts, "shop_supply_total": 0, "labor_full_total": _round2(labor + parts + core + misc),
            })
            labor_sum += labor
            parts_sum += parts
            core_sum += core
            misc_sum += misc
        labor_sum, parts_sum = _round2(labor_sum), _round2(parts_sum)
        if self.supply_override is not None:
            supply = self.supply_override
        elif not self.recalc and _round2(self.snapshot.get("shop_supply_total")) > 0:
            supply = _round2(self.snapshot.get("shop_supply_total"))
        else:
            supply = _round2(labor_sum * (self.pct / 100)) if self.pct > 0 else 0.0
        misc_taxable = 0.0
        for blk in self.blocks:
            for it in (_misc_items(blk["parts"][0]) if blk["parts"] else []):
                if it.get("taxable") is False:
                    continue
                price, qty = float(it.get("price") or 0), float(it.get("quantity") or 1)
                if price > 0 and qty > 0:
                    misc_taxable += _round2(price * qty)
        misc_taxable = _round2(misc_taxable)
        tax = _round2((parts_sum + misc_taxable) * self.tax_rate) if self.taxable and self.tax_rate > 0 else 0.0
        parts_total = _round2(parts_sum + _round2(core_sum) + _round2(misc_sum))
        return {
            "labor": labor_sum, "labor_total": _round2(labor_sum + supply), "parts": parts_sum,
            "parts_total": parts_total, "core_total": _round2(core_sum), "misc_total": _round2(misc_sum),
            "misc_taxable_total": misc_taxable, "cost_total": parts_sum, "shop_supply_total": supply,
            "parts_taxable_total": _round2(parts_sum + misc_taxable), "sales_tax_rate": round(self.tax_rate, 6),
            "sales_tax_total": tax, "is_taxable": self.taxable,
            "grand_total": _round2(labor_sum + supply + parts_total + tax), "labors": out_blocks,
        }

    def labors(self, totals=None):
        totals = totals or self.totals()
        out = []
        for blk, bt in zip(self.blocks, totals["labors"]):
            row = {
                "labor_id": blk["labor_id"] or "",
                "labor_description": blk["description"].strip(),
                "labor_hours": _js_number(blk["hours"]),
                "hours_edited": blk["hours_edited"],
                "labor_rate_code": blk["rate_code"],
                "labor_full_total": bt["labor_full_total"],
                "assigned_mechanics": copy.deepcopy(blk["assigned"]),
                "issue_description": blk["issue"],
                "parts": [{
                    "part_id": p.get("part_id") or "", "one_time_part": bool(p.get("one_time_part")),
                    "part_number": str(p.get("part_number") or "").strip(),
                    "description": str(p.get("description") or "").strip(),
                    "unit": str(p.get("unit") or "").strip(),
                    "qty": _js_number(p.get("qty")), "cost": _js_number(p.get("cost")),
                    "price": _js_number(p.get("price")), "core_charge": _js_number(p.get("core_charge")),
                    "misc_charge": _js_number(p.get("misc_charge")),
                    "misc_charge_description": str(p.get("misc_charge_description") or "").strip(),
                } for p in blk["parts"]],
            }
            if self.new_js and blk["client_key"] and not blk["labor_id"]:
                row["client_key"] = blk["client_key"]
            out.append(row)
        return out

    def payload(self, save_status="open", **extra):
        totals = self.totals()
        payload = {
            "labors": self.labors(totals), "totals": totals,
            "customer_id": self.customer_id, "unit_id": self.unit_id,
            "work_order_date": self.date, "save_status": save_status,
        }
        if self.mileage:
            payload["unit_mileage"] = self.mileage
        if self.new_js:
            payload.update({
                "merge_protocol": 1, "view_id": self.view_id,
                "shop_supply_overridden": self.supply_override is not None,
            })
        payload.update(extra)
        return payload

    def save(self, save_status="open", *, payload=None, expect_ok=True, **extra):
        payload = payload or self.payload(save_status, **extra)
        resp = _post(self.client, f"/work_orders/api/work_orders/{self.wo_id}/update", payload)
        assert resp.status_code in (200, 409), resp.get_data(as_text=True)
        j = resp.get_json()
        if expect_ok:
            assert j.get("ok") is True, j
        if j.get("ok"):
            for blk, lid in zip(self.blocks, j.get("labor_ids") or []):
                if lid:
                    blk["labor_id"] = lid
            for blk in self.blocks:
                blk["hours_edited"] = False
            if "view_id" in j:
                self.view_id = j["view_id"]
            if "labors_rev" in j:
                self.rev = j["labors_rev"]
        return j


# ───────────────────────── оракул: старый офисный алгоритм ─────────────────────────


def _legacy_office_update(shop_db, shop, wo, data):
    """Дословная копия расчёта labors/totals старого api_work_order_update
    (до merge-ядра): строки — из payload, tracked-правило по labor_id."""
    from app.blueprints.work_orders.services.common import ensure_labor_id, round2
    from app.blueprints.work_orders.services.lookups import apply_assignments_to_labors, get_assignable_mechanics
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        _get_shop_sales_tax_context,
        _is_customer_taxable,
        align_totals_with_labors,
        normalize_totals_payload,
    )

    data = copy.deepcopy(data)
    labors = data.get("labors", data.get("blocks"))
    raw_totals = data.get("totals") or {}
    totals = normalize_totals_payload(raw_totals)

    mechanics_by_id = {m["id"]: m for m in get_assignable_mechanics(shop)}
    labors = apply_assignments_to_labors(labors, mechanics_by_id)

    stored_blocks = {
        str(b.get("labor_id") or ""): (i, b)
        for i, b in enumerate(wo.get("labors") or [])
        if isinstance(b, dict)
    }
    stored_totals_blocks = (wo.get("totals") or {}).get("labors")
    if not isinstance(stored_totals_blocks, list):
        stored_totals_blocks = []
    client_totals_blocks = totals.get("labors") if isinstance(totals.get("labors"), list) else []

    def _hours_eq(a, b):
        try:
            return abs(float(a or 0) - float(b or 0)) < 1e-9
        except (TypeError, ValueError):
            return False

    for new_idx, block in enumerate(labors):
        block["labor_id"] = ensure_labor_id(block.get("labor_id"))
        hours_edited = bool(block.pop("hours_edited", False))
        block.pop("hours_source", None)
        labor_doc = block.get("labor") if isinstance(block.get("labor"), dict) else None
        if labor_doc is None:
            continue
        labor_doc.pop("hours_edited", None)
        stored_idx, stored = stored_blocks.get(str(block["labor_id"]), (None, None))
        stored_labor = (stored or {}).get("labor") if isinstance((stored or {}).get("labor"), dict) else {}
        stored_source = str(stored_labor.get("hours_source") or "").strip()
        if hours_edited:
            labor_doc["hours_source"] = ""
            continue
        if stored_source:
            labor_doc["hours_source"] = stored_source
        if stored_source == "tracked":
            stored_hours = str(stored_labor.get("hours") or "").strip()
            if stored_hours and not _hours_eq(labor_doc.get("hours"), stored_hours):
                labor_doc["hours"] = stored_hours
                if (
                    stored_idx is not None
                    and stored_idx < len(stored_totals_blocks)
                    and isinstance(stored_totals_blocks[stored_idx], dict)
                    and new_idx < len(client_totals_blocks)
                    and isinstance(client_totals_blocks[new_idx], dict)
                ):
                    client_totals_blocks[new_idx]["labor"] = round2(
                        stored_totals_blocks[stored_idx].get("labor") or 0
                    )

    totals = align_totals_with_labors(totals, labors)
    existing_totals = wo.get("totals") or {}
    locked_tax_rate = existing_totals.get("sales_tax_rate")
    if locked_tax_rate is None:
        locked_tax_rate = (_get_shop_sales_tax_context(shop, shop_db).get("rate") or 0)
    if isinstance(raw_totals, dict) and "is_taxable" in raw_totals:
        is_taxable_override = bool(raw_totals.get("is_taxable"))
    elif "is_taxable" in existing_totals:
        is_taxable_override = bool(existing_totals.get("is_taxable"))
    else:
        is_taxable_override = _is_customer_taxable(shop_db, wo.get("customer_id"))
    totals = _apply_sales_tax_to_totals(totals, locked_tax_rate, is_taxable_override)
    return labors, totals


def _comparable(labors, new_positions=()):
    """labors без меток merge (minted/client_key) и без случайных id новых строк."""
    out = []
    for i, b in enumerate(labors or []):
        b = copy.deepcopy(b)
        b.pop("minted", None)
        b.pop("client_key", None)
        if i in new_positions:
            b["labor_id"] = "<new>"
        out.append(b)
    return out


def _save_matches_legacy(app, mongo, co, tab, payload):
    """Save вкладки; результат — ровно то, что дал бы старый алгоритм."""
    shop_db = mongo[SHOP_A_DB]
    before = _wo(mongo, tab.wo_id)
    with app.app_context():
        exp_labors, exp_totals = _legacy_office_update(shop_db, co["shop"], before, payload)
    j = tab.save(payload=payload)
    after = _wo(mongo, tab.wo_id)
    new_positions = {i for i, r in enumerate(payload["labors"]) if not str(r.get("labor_id") or "").strip()}
    assert _comparable(after["labors"], new_positions) == _comparable(exp_labors, new_positions)
    assert after["totals"] == exp_totals
    return j, after


# ───────────────────────── регрессия: без конфликтов ─────────────────────────


@pytest.mark.parametrize("new_js", [False, True], ids=["legacy_js", "new_js"])
def test_regression_conflict_free_edit_save_matches_legacy(app, mongo, co, new_js):
    """Правка цены, ручная правка часов, новая работа: облагаемые misc
    первой строки, доли supply по блокам, tracked-строка — totals и labors
    те же, что у старого кода."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
    tab.set_part(0, 0, price="104.25")
    tab.set_hours(1, "0.6")
    tab.add_row("Wash", hours="0.5")
    j, after = _save_matches_legacy(app, mongo, co, tab, tab.payload())
    assert j["ok"] is True
    assert after["totals"]["misc_taxable_total"] == 11.65             # EPA 3.35 + ветошь 2×4.15
    assert after["totals"]["is_taxable"] is True
    assert after["labors"][2]["labor"]["hours_source"] == "tracked"


@pytest.mark.parametrize("new_js", [False, True], ids=["legacy_js", "new_js"])
def test_regression_client_misc_taxable_total_is_kept_without_conflicts(app, mongo, co, new_js):
    """misc-позиция с quantity 0 (auto-позиция строки, у которой qty
    поставили 0): JS считает её ×1 (quantity || 1), сервер — ×0. Save без
    конфликтов (быстрый путь) берёт облагаемый misc клиента, как раньше —
    налог не меняется от того, что WO просто пересохранили."""
    rows = _rich_rows(co)
    items = json.loads(rows[0]["parts"][0]["misc_charge_description"])
    items.append({"description": "Legacy fee", "price": 5.0, "quantity": 0, "manual": True, "taxable": True})
    rows[0]["parts"][0]["misc_charge_description"] = _misc_json(items)
    wo = _insert_wo(app, mongo, co, rows)
    tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
    tab.set_part(0, 0, price="100")
    _, after = _save_matches_legacy(app, mongo, co, tab, tab.payload())
    assert after["totals"]["misc_taxable_total"] == 16.65


@pytest.mark.parametrize("new_js", [False, True], ids=["legacy_js", "new_js"])
def test_regression_save_without_any_edit_matches_legacy(app, mongo, co, new_js):
    """Открыли и сразу Save: в тоталах — снимок сохранённых тоталов."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
    _save_matches_legacy(app, mongo, co, tab, tab.payload())


@pytest.mark.parametrize("new_js", [False, True], ids=["legacy_js", "new_js"])
def test_regression_manual_shop_supply_override_matches_legacy(app, mongo, co, new_js):
    """Сохранённый нестандартный supply (страница фиксирует процент по нему)
    и введённый вручную supply проходят как раньше."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co), supply_override=50.0)
    tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
    # Процент страницы — из сохранённых тоталов (supply / labor), поэтому
    # сохранённое значение ручным не считается.
    assert tab.supply_override is None and tab.pct > 7
    tab.set_part(0, 1, qty="2")
    _save_matches_legacy(app, mongo, co, tab, tab.payload())

    tab.set_supply("12.34")
    _, after = _save_matches_legacy(app, mongo, co, tab, tab.payload())
    assert after["totals"]["shop_supply_total"] == 12.34


@pytest.mark.parametrize("new_js", [False, True], ids=["legacy_js", "new_js"])
def test_regression_estimate_and_conversion_match_legacy(app, mongo, co, new_js):
    """Смета остаётся сметой и конвертируется в WO — те же labors/totals."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co), status="estimate")
    tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
    tab.set_description(1, "Diagnostics (scan)")
    _save_matches_legacy(app, mongo, co, tab, tab.payload("estimate"))
    tab.set_part(0, 0, qty="1")
    _save_matches_legacy(app, mongo, co, tab, tab.payload("open"))
    assert _wo(mongo, wo["_id"])["status"] == "open"


def test_regression_stale_tracked_hours_without_render_match_legacy(app, mongo, co):
    """Старая вкладка без вьюхи: часы tracked-строки, натреканные после
    открытия формы, не откатываются, база — сохранённая (как раньше)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
    shop_db = mongo[SHOP_A_DB]
    # Пока форма открыта, таймеры подняли часы строки до 2.4 (база 240).
    shop_db.work_orders.update_one(
        {"_id": wo["_id"]},
        {"$set": {"labors.2.labor.hours": "2.4", "totals.labors.2.labor": 240.0}, "$inc": {"labors_rev": 1}},
    )
    tab.set_part(0, 0, price="101")
    _, after = _save_matches_legacy(app, mongo, co, tab, tab.payload())
    assert after["labors"][2]["labor"]["hours"] == "2.4"
    assert after["totals"]["labors"][2]["labor"] == 240.0


@pytest.mark.parametrize("new_js", [False, True], ids=["legacy_js", "new_js"])
def test_regression_apply_preset_on_a_preset_row_matches_legacy(app, mongo, co, new_js):
    """Ревью D2-01: «Apply preset» поверх пресетной строки (1 ч) пишет в форму
    описание и часы пресета программно — без флага hours_edited. Часы 2.5,
    база, итог — как у старого кода, метка preset остаётся (раньше merge
    откатывал часы к сохранённому 1 ч: недобор (2.5 − 1) × ставка)."""
    rows = _rich_rows(co)
    rows[1].update({"description": "Brake inspection", "hours": "1", "rate_code": "standard",
                    "hours_source": "preset"})
    wo = _insert_wo(app, mongo, co, rows)
    tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
    # applyPresetToBlock: описание и часы пресета, recalcAll; флаг не ставится.
    tab.blocks[1].update({"description": "Brake service", "hours": "2.5"})
    tab.edit()
    assert tab.blocks[1]["hours_edited"] is False
    j, after = _save_matches_legacy(app, mongo, co, tab, tab.payload())
    lab = after["labors"][1]["labor"]
    assert (lab["description"], lab["hours"], lab["hours_source"]) == ("Brake service", "2.5", "preset")
    assert after["totals"]["labors"][1]["labor"] == 250.0
    # +150 работы и +10.50 shop supply (7%); запчасти и налог те же.
    assert after["totals"]["grand_total"] == _round2(wo["totals"]["grand_total"] + 160.5)
    assert j["merged_foreign"] is False


def test_regression_deleting_a_seen_row_matches_legacy(app, mongo, co):
    """Новый клиент со своей вьюхой: удаление строки, которую он видел, —
    как раньше (склад возвращается)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=True)
    tab.remove_row(1)
    j, after = _save_matches_legacy(app, mongo, co, tab, tab.payload())
    assert [b["labor"]["description"] for b in after["labors"]] == ["Front brakes", "Oil service"]
    assert j["merged_foreign"] is False and j["merge_notes"] == []


def test_regression_render_carries_view_and_revision(app, mongo, co):
    """Страница деталей: id строк персистятся до рендера, в
    workOrderCreatedData — view_id (load-вьюха офиса) и labors_rev."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    assert tab.view_id and tab.rev == 1
    view = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(tab.view_id)})
    assert view["channel"] == "office" and view["kind"] == "load" and view["protocol"] == 1
    assert view["user_id"] == str(co["owner"]["_id"])
    assert [r["labor_id"] for r in view["labors"]] == [b["labor_id"] for b in tab.blocks]
    fields = view["wo_fields"]
    assert fields["customer_id"] == str(co["customer"]["_id"]) and fields["unit_id"] == str(co["unit"]["_id"])
    assert fields["work_order_date_input"] == tab.date and fields["is_taxable"] is True
    assert fields["shop_supply_pct"] == tab.pct and fields["mileage"] == 80000


# ───────────────────────── механики рядом с офисом ─────────────────────────


def _mech_rows(details):
    """Строки формы приложения механика из GET деталей (stripMoney)."""
    return [{
        "labor_id": l["labor_id"], "description": l["description"], "hours": "", "rate_code": "",
        "issue_description": l["issue_description"],
        "parts": [{"part_id": p.get("part_id") or "", "part_number": p.get("part_number") or "",
                   "description": p.get("description") or "", "qty": p.get("qty") or 0,
                   "one_time_part": bool(p.get("one_time_part"))} for p in l["parts"]],
    } for l in details["labors"]]


class Mech:
    """Телефон механика (приложение merge_protocol 1): открыл WO — правит — Save."""

    def __init__(self, app, email):
        self.c = app.test_client()
        resp = self.c.post("/api/mobile/login", json={"email": email, "password": PASSWORD},
                           environ_base={"REMOTE_ADDR": "127.0.0.41"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        self.token = resp.get_json()["csrf_token"]

    def post(self, url, payload):
        return self.c.post(url, json=payload, headers={"X-CSRFToken": self.token})

    def open(self, wo_id):
        resp = self.c.get(f"/api/mobile/work_orders/{wo_id}?mp=1")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        details = resp.get_json()
        self.wo_id, self.view_id, self.rows = str(wo_id), details["view_id"], _mech_rows(details)
        return self.rows

    def save(self, **extra):
        payload = {"merge_protocol": 1, "view_id": self.view_id, "status": "in_progress",
                   "labors": self.rows, "mechanic_state": "in_progress", "unit_mileage": "80000"}
        payload.update(extra)
        resp = self.post(f"/api/mobile/work_orders/{self.wo_id}", payload)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        j = resp.get_json()
        assert j["ok"] is True, j
        return j

    def start(self, wo_id, labor_id):
        resp = self.post("/work_orders/api/mechanic/timers/start", {"work_order_id": str(wo_id), "labor_id": labor_id})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def stop(self):
        self.post("/work_orders/api/mechanic/timers/stop", {})


def _descs(wo):
    return [b["labor"]["description"] for b in wo["labors"]]


def _assert_totals_consistent(app, wo):
    """Тоталы, собранные сервером: сходятся со строками, налог — с облагаемым misc."""
    from app.blueprints.work_orders.services.totals import misc_taxable_total_for_labors

    t = wo["totals"]
    assert len(t["labors"]) == len(wo["labors"])
    with app.app_context():
        assert t["misc_taxable_total"] == misc_taxable_total_for_labors(wo["labors"])
    assert t["parts_taxable_total"] == _round2(t["parts"] + t["misc_taxable_total"])
    if t["is_taxable"]:
        assert t["sales_tax_total"] == _round2(t["parts_taxable_total"] * t["sales_tax_rate"])
    assert t["grand_total"] == _round2(t["labor_total"] + t["parts_total"] + t["sales_tax_total"])


def test_stale_office_save_keeps_mechanics_new_jobs_parts_and_qty(app, mongo, co):
    """Офис открыл WO, механик добавил работу с запчастью, новую запчасть в
    чужую работу и поднял qty; устаревший Save офиса ничего из этого не
    откатывает, склад двигается только на правки офиса."""
    pads, filt = co["parts"]["pads"], co["parts"]["filter"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])

    mech = Mech(app, MECH_A)
    rows = mech.open(wo["_id"])
    rows[0]["parts"][0]["qty"] = 3                                     # колодки 2 → 3
    rows[1]["parts"].append({"part_id": str(filt["_id"]), "part_number": "CO-FILTER", "description": "",
                             "qty": 1, "one_time_part": False})
    rows.append({"labor_id": "", "client_key": "m-ck-1", "description": "Replace wiper blades", "hours": "",
                 "rate_code": "", "issue_description": "",
                 "parts": [{"part_id": str(filt["_id"]), "part_number": "CO-FILTER", "description": "",
                            "qty": 2, "one_time_part": False}]})
    mech.save()
    stock = {k: _stock(mongo, p) for k, p in (("pads", pads), ("filter", filt))}

    tab.set_part(0, 1, price="133.33")                                 # офис правит цену батареи
    tab.set_description(1, "Diagnostics (scan tool)")
    j = tab.save()

    after = _wo(mongo, wo["_id"])
    assert _descs(after) == ["Front brakes", "Diagnostics (scan tool)", "Oil service", "Replace wiper blades"]
    assert after["labors"][0]["parts"][0]["qty"] == 3                   # qty механика цел
    assert after["labors"][0]["parts"][1]["price"] == 133.33            # цена офиса записана
    assert [p["part_number"] for p in after["labors"][1]["parts"]] == ["CO-FILTER"]
    assert after["labors"][3]["parts"][0]["qty"] == 2
    assert j["merged_foreign"] is True
    assert 'Kept job "Replace wiper blades" added by another user.' in j["merge_notes"]
    assert any(n.startswith('Kept part "Oil filter" on "Diagnostics') for n in j["merge_notes"])
    assert _stock(mongo, pads) == stock["pads"] and _stock(mongo, filt) == stock["filter"]
    _assert_totals_consistent(app, after)
    # Облагаемый misc остался в налоге и в merged-пути.
    assert after["totals"]["misc_taxable_total"] == 11.65
    assert j["labors_rev"] == after["labors_rev"]


def _assert_untouched(mongo, wo_id, before):
    after = _wo(mongo, wo_id)
    assert after["labors"] == before["labors"] and after["totals"] == before["totals"]
    assert after.get("labors_rev") == before.get("labors_rev") and after["status"] == before["status"]
    return after


def test_legacy_office_tab_without_view_never_deletes_by_absence(app, mongo, co):
    """Старая вкладка (JS без view_id), в форме нет строки, которая есть в WO:
    удалила её вкладка или добавил кто-то другой — не узнать. Удалить нельзя
    (чужая работа), оставить — тоже (старый JS заметок не показывает: строка
    молча осталась бы в инвойсе). Save отклоняется целиком: 200 page_outdated
    с текстом для тоста старого JS, ничего не записано."""
    from app.blueprints.work_orders.services.office_merge import PAGE_OUTDATED_RESPONSE

    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
    before = _wo(mongo, wo["_id"])
    tab.remove_row(1)
    j = tab.save(expect_ok=False)
    assert j == PAGE_OUTDATED_RESPONSE
    _assert_untouched(mongo, wo["_id"], before)


def test_legacy_office_tab_part_removal_or_swap_is_refused_and_moves_no_stock(app, mongo, co):
    """D2-02: старая вкладка убрала запчасть, заменила каталожную другой или
    сменила номер одноразовой — раньше прежняя строка молча оставалась (двойной
    счёт, склад не возвращался). Теперь page_outdated: WO и склад не тронуты.
    Описание-less строка с часами, которую вкладка убрала, — тоже (тихо
    оставить её нельзя: часы в счёте)."""
    batt, filt = co["parts"]["battery"], co["parts"]["filter"]
    for edit in ("remove", "swap", "one_time_number", "hours_only_row"):
        rows = _rich_rows(co)
        if edit == "hours_only_row":
            rows.append({"description": "", "hours": "2", "rate_code": "standard", "issue_description": "",
                         "assigned_mechanics": [], "parts": []})
        wo = _insert_wo(app, mongo, co, rows)
        tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
        before = _wo(mongo, wo["_id"])
        stock = (_stock(mongo, batt), _stock(mongo, filt))
        if edit == "remove":
            tab.remove_part(0, 1)                                      # батарею убрали
        elif edit == "swap":
            tab.set_part(0, 1, part_id=str(filt["_id"]), part_number="CO-FILTER", description="Oil filter",
                         cost="6", price="12.49", core_charge="0")      # вместо батареи — фильтр
        elif edit == "one_time_number":
            tab.set_part(2, 0, part_number="OT-2")
        else:
            tab.remove_row(3)
        j = tab.save(expect_ok=False)
        assert (j["ok"], j["error"]) == (False, "page_outdated"), edit
        assert "Reload the page" in j["message"]
        _assert_untouched(mongo, wo["_id"], before)
        assert (_stock(mongo, batt), _stock(mongo, filt)) == stock, edit


def test_legacy_tab_never_reaches_the_merged_totals_path_with_a_kept_job(app, mongo, co):
    """D2-03: старая вкладка + чужая работа, добавленная сохранением сервера
    (supply по блокам: 41.35 + 9.49). Раньше Save шёл merged-путём, и
    посчитанный вкладкой supply 41.35 принимался за ручной — доля чужой
    работы терялась. Теперь такой Save — page_outdated, тоталы не тронуты."""
    from app.blueprints.work_orders.services import edit_views as ev
    from app.blueprints.work_orders.services.mobile_editor import compute_labors_and_totals
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        align_totals_with_labors,
        normalize_totals_payload,
    )

    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(app, mongo, co, [{"description": "Engine diag", "hours": "4.36", "rate_code": DIESEL,
                                      "issue_description": "", "assigned_mechanics": [], "parts": []}])
    tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
    assert _wo(mongo, wo["_id"])["totals"]["shop_supply_total"] == 41.35
    # Сохранение сервера (мобильный менеджер / пересчёт): + работа 1.0 ч.
    with app.app_context():
        rows = ev.work_rows_from_wo(_wo(mongo, wo["_id"])) + [
            {"description": "Extra", "hours": "1", "rate_code": DIESEL, "issue_description": "",
             "assigned_mechanics": [], "parts": []}]
        labors, totals_raw = compute_labors_and_totals(shop_db, co["shop"], rows)
        totals = _apply_sales_tax_to_totals(
            align_totals_with_labors(normalize_totals_payload(totals_raw), labors), TAX_RATE, True)
    shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$set": {"labors": labors, "totals": totals},
                                                        "$inc": {"labors_rev": 1}})
    before = _wo(mongo, wo["_id"])
    assert (before["totals"]["labor"], before["totals"]["shop_supply_total"]) == (726.28, 50.84)

    tab.set_description(0, "Engine diag (scan)")
    j = tab.save(expect_ok=False)
    assert j["error"] == "page_outdated"
    assert _assert_untouched(mongo, wo["_id"], before)["totals"]["shop_supply_total"] == 50.84


def test_office_cannot_delete_a_job_someone_is_timing(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    mech = Mech(app, MECH_B)
    mech.start(wo["_id"], tab.blocks[1]["labor_id"])
    try:
        tab.remove_row(1)
        j = tab.save()
    finally:
        mech.stop()
    assert "Diagnostics" in _descs(_wo(mongo, wo["_id"]))
    assert 'Kept job "Diagnostics": someone is working on it.' in j["merge_notes"]


def test_office_deletes_a_seen_job_and_its_parts_return_to_stock(app, mongo, co):
    batt = co["parts"]["battery"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    stock = _stock(mongo, batt)
    tab = OfficeTab(_owner(app), wo["_id"])
    tab.remove_part(0, 1)                                              # батарею убрали из работы
    tab.remove_row(2)
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert _descs(after) == ["Front brakes", "Diagnostics"]
    assert [p["part_number"] for p in after["labors"][0]["parts"]] == ["CO-PADS"]
    assert "EPA fee" not in after["labors"][0]["parts"][0]["misc_charge_description"]
    assert _stock(mongo, batt) == stock + 1
    assert j["merged_foreign"] is False


def test_tracked_hours_win_over_a_stale_form_with_and_without_base(app, mongo, co):
    """Часы tracked-строки, натреканные после открытия формы, не
    откатываются: ни новой вкладкой (base), ни старой (без base)."""
    shop_db = mongo[SHOP_A_DB]
    for new_js in (True, False):
        wo = _insert_wo(app, mongo, co, _rich_rows(co))
        tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
        shop_db.work_orders.update_one(
            {"_id": wo["_id"]},
            {"$set": {"labors.2.labor.hours": "2.4", "totals.labors.2.labor": 240.0}, "$inc": {"labors_rev": 1}},
        )
        tab.set_part(0, 0, price="101")
        tab.save()
        after = _wo(mongo, wo["_id"])
        lab = after["labors"][2]["labor"]
        assert (lab["hours"], lab["hours_source"]) == ("2.4", "tracked"), new_js
        assert after["totals"]["labors"][2]["labor"] == 240.0
        _assert_totals_consistent(app, after)


def test_rate_change_on_a_tracked_row_reprices_the_tracked_hours(app, mongo, co):
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    shop_db.work_orders.update_one(
        {"_id": wo["_id"]},
        {"$set": {"labors.2.labor.hours": "2.4", "totals.labors.2.labor": 240.0}, "$inc": {"labors_rev": 1}},
    )
    tab.set_rate(2, DIESEL)
    tab.save()
    after = _wo(mongo, wo["_id"])
    lab = after["labors"][2]["labor"]
    assert (lab["hours"], lab["rate_code"], lab["hours_source"]) == ("2.4", DIESEL, "tracked")
    assert after["totals"]["labors"][2]["labor"] == 325.2               # 2.4 × 135.5


def test_deliberate_hours_edit_overrides_tracked_hours(app, mongo, co):
    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    shop_db.work_orders.update_one(
        {"_id": wo["_id"]}, {"$set": {"labors.2.labor.hours": "2.4"}, "$inc": {"labors_rev": 1}})
    tab.set_hours(2, "3")
    tab.save()
    lab = _wo(mongo, wo["_id"])["labors"][2]["labor"]
    assert (lab["hours"], lab["hours_source"]) == ("3", "")

    # Флаг hours_edited мог остаться от прошлого Save: часы те же — tracked не снимается.
    wo2 = _insert_wo(app, mongo, co, _rich_rows(co))
    tab2 = OfficeTab(_owner(app), wo2["_id"])
    tab2.blocks[2]["hours_edited"] = True
    tab2.edit()
    tab2.save()
    assert _wo(mongo, wo2["_id"])["labors"][2]["labor"]["hours_source"] == "tracked"


def test_merged_path_shop_supply_and_override(app, mongo, co):
    """Чужая строка влилась — supply считает сервер процентом страницы; ручной
    supply (флаг нового JS) остаётся как ввели. Старая вкладка с чужой
    строкой на merged-путь не попадает: page_outdated, тоталы не тронуты."""
    for new_js, override in ((True, None), (True, "12.34"), (False, "12.34")):
        wo = _insert_wo(app, mongo, co, _rich_rows(co))
        tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
        mech = Mech(app, MECH_A)
        rows = mech.open(wo["_id"])
        rows.append({"labor_id": "", "description": "Extra job", "hours": "", "rate_code": "",
                     "issue_description": "", "parts": []})
        mech.save()
        mongo[SHOP_A_DB].work_orders.update_one(                      # офис поставил часы новой строке
            {"_id": wo["_id"]},
            {"$set": {"labors.3.labor.hours": "1", "totals.labors.3.labor": 100.0}, "$inc": {"labors_rev": 1}})
        if override:
            tab.set_supply(override)
        tab.set_part(0, 0, qty="1")
        if not new_js:
            before = _wo(mongo, wo["_id"])
            j = tab.save(expect_ok=False)
            assert j["error"] == "page_outdated"
            _assert_untouched(mongo, wo["_id"], before)
            continue
        tab.save()
        t = _wo(mongo, wo["_id"])["totals"]
        if override:
            assert t["shop_supply_total"] == float(override), (new_js, override)
        else:
            assert t["shop_supply_total"] == _round2(t["labor"] * tab.pct / 100)
        assert len(t["labors"]) == 4 and t["labors"][3]["labor"] == 100.0


def test_legacy_manual_supply_survives_the_merged_path_of_a_silent_blank_row(app, mongo, co):
    """Старая вкладка флага shop_supply_overridden не шлёт: ручной supply
    узнаётся по расхождению с процентом страницы. Merged-путь у неё остался
    только для тихо оставленной пустой строки (её нечего выставлять в счёт —
    Save не отклоняется): ручной supply остаётся как ввели."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
    mongo[SHOP_A_DB].work_orders.update_one({"_id": wo["_id"]}, {
        "$push": {"labors": {"labor_id": str(ObjectId()), "labor": {
            "description": "", "hours": "", "rate_code": "standard", "assigned_mechanics": [],
            "issue_description": ""}, "parts": []}},
        "$inc": {"labors_rev": 1},
    })
    tab.set_supply("12.34")
    tab.set_part(0, 0, qty="1")
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert len(after["labors"]) == 4 and j["merge_notes"] == []
    assert after["totals"]["shop_supply_total"] == 12.34
    _assert_totals_consistent(app, after)


def test_misc_items_of_mechanic_and_office_both_survive(app, mongo, co):
    """Механик добавил каталожную батарею (её misc — auto-позиция), офис в
    той же работе — ручную misc-позицию: обе на месте, partIndex указывает
    на батарею, облагаемые — в налоге."""
    pads, batt = co["parts"]["pads"], co["parts"]["battery"]
    rows = [{"description": "Brakes", "hours": "1", "rate_code": "standard", "issue_description": "",
             "parts": [{"part_id": pads["_id"], "part_number": "CO-PADS", "description": "Brake pads", "qty": 2,
                        "cost": 40.0, "price": 99.5, "unit": "each",
                        "misc_charge_description": _misc_json([
                            {"description": "Shop rag", "price": 4.15, "quantity": 1, "manual": True,
                             "taxable": True}])}]}]
    wo = _insert_wo(app, mongo, co, rows)
    tab = OfficeTab(_owner(app), wo["_id"])
    mech = Mech(app, MECH_A)
    mrows = mech.open(wo["_id"])
    mrows[0]["parts"].append({"part_id": str(batt["_id"]), "part_number": "CO-BATT", "description": "",
                              "qty": 1, "one_time_part": False})
    mech.save()

    items = _misc_items(tab.blocks[0]["parts"][0])
    items.append({"description": "Towels", "price": 2.0, "quantity": 3, "manual": True, "taxable": True})
    tab.blocks[0]["parts"][0]["misc_charge_description"] = _misc_json(items)
    tab.edit()
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    parts = after["labors"][0]["parts"]
    assert [p["part_number"] for p in parts] == ["CO-PADS", "CO-BATT"]
    final = json.loads(parts[0]["misc_charge_description"])
    by_desc = {it["description"]: it for it in final}
    assert set(by_desc) == {"Shop rag", "Towels", "EPA fee"}
    assert by_desc["EPA fee"]["partIndex"] == 1 and by_desc["EPA fee"].get("manual") is not True
    assert after["totals"]["misc_taxable_total"] == _round2(4.15 + 6.0 + 3.35)
    assert j["merged_foreign"] is True
    _assert_totals_consistent(app, after)


def test_stale_office_save_after_transfer_keeps_new_customer_unit_and_prices(app, mongo, co):
    unit = {"_id": ObjectId(), "shop_id": co["shop"]["_id"], "customer_id": co["customer"]["_id"],
            "vin": "OFFCONCSTALETRF01", "unit_number": "OC-S", "mileage": 40000, "is_active": True,
            "created_at": _now()}
    mongo[SHOP_A_DB].units.insert_one(unit)
    wo = _insert_wo(app, mongo, co, _rich_rows(co), unit=unit, mileage=40000)
    owner = _owner(app)
    tab = OfficeTab(owner, wo["_id"])
    resp = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/transfer", {"customer_id": str(co["other"]["_id"])})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    moved_unit = ObjectId(resp.get_json()["unit_id"])

    tab.set_part(0, 0, qty="1")
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert after["customer_id"] == co["other"]["_id"] and after["unit_id"] == moved_unit
    assert after["totals"]["is_taxable"] is False                      # клиент назначения не облагается
    assert after["labors"][1]["labor"]["rate_code"] == "standard"      # переоценка transfer осталась
    assert after["labors"][0]["parts"][0]["qty"] == 1
    assert j["merged_foreign"] is True and "The customer was changed by another user." in j["merge_notes"]
    _assert_totals_consistent(app, after)


def test_stale_office_tab_does_not_revert_date_or_mileage_moved_by_mechanic(app, mongo, co):
    old_date = datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)
    wo = _insert_wo(app, mongo, co, _rich_rows(co), work_order_date=old_date)
    tab = OfficeTab(_owner(app), wo["_id"])
    mech = Mech(app, MECH_A)
    rows = mech.open(wo["_id"])
    rows[0]["description"] = "Front brakes (pads worn)"
    mech.save(unit_mileage="81000")
    moved = _wo(mongo, wo["_id"])
    assert moved["work_order_date"] != old_date.replace(tzinfo=None) and moved["mileage"] == 81000

    tab.set_part(0, 0, price="100")
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert after["work_order_date"] == moved["work_order_date"] and after["mileage"] == 81000
    assert after["labors"][0]["labor"]["description"] == "Front brakes (pads worn)"
    assert "The work order date was changed by another user." in j["merge_notes"]


def test_office_change_of_customer_unit_date_still_applies(app, mongo, co):
    """3-way: то, что пользователь сам поменял в форме, записывается."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    tab.unit_id, tab.mileage, tab.date = str(co["unit2"]["_id"]), "40500", "2026-10-15"
    tab.taxable = False
    tab.edit()
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert after["unit_id"] == co["unit2"]["_id"] and after["mileage"] == 40500
    assert after["totals"]["is_taxable"] is False and after["totals"]["sales_tax_total"] == 0
    assert tab.date == "2026-10-15" and after["work_order_date"].date().isoformat() in ("2026-10-15", "2026-10-14")
    assert j["merged_foreign"] is False


def test_legacy_wo_without_labor_ids_gets_ids_on_render_and_never_duplicates(app, mongo, co):
    shop_db = mongo[SHOP_A_DB]
    for new_js in (True, False):
        wo = _insert_wo(app, mongo, co, _rich_rows(co))
        shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$unset": {
            "labors.0.labor_id": "", "labors.1.labor_id": "", "labors.2.labor_id": ""}})
        tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
        ids = [b["labor_id"] for b in tab.blocks]
        assert all(ids) and ids == [b["labor_id"] for b in _wo(mongo, wo["_id"])["labors"]]
        tab.set_description(0, "Front brakes - done")
        tab.save()
        after = _wo(mongo, wo["_id"])
        assert [b["labor_id"] for b in after["labors"]] == ids
        assert _descs(after) == ["Front brakes - done", "Diagnostics", "Oil service"]


def _pre_deploy_tab(app, mongo, wo, *, ids_persisted_meanwhile):
    """Вкладка, отрендеренная до деплоя на легаси-WO без labor_id: у строк —
    временные id рендера (прежний код их не сохранял, вьюх не было). После
    деплоя другой рендер (ids_persisted_meanwhile) сохранил в WO настоящие id
    — ни одного id вкладки WO не знает. → (вкладка, id строк в WO)."""
    shop_db = mongo[SHOP_A_DB]
    legacy = [{k: v for k, v in b.items() if k not in ("labor_id", "client_key", "minted")} for b in wo["labors"]]
    to_legacy = {"$set": {"labors": legacy}, "$unset": {"labors_rev": ""}}
    shop_db.work_orders.update_one({"_id": wo["_id"]}, to_legacy)
    tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
    # Рендер прежнего кода: временные id, в WO ничего не записано, вьюхи нет.
    for blk in tab.blocks:
        blk["labor_id"] = str(ObjectId())
    shop_db.work_orders.update_one({"_id": wo["_id"]}, to_legacy)
    shop_db.wo_edit_views.delete_many({"work_order_id": wo["_id"]})
    if ids_persisted_meanwhile:
        OfficeTab(_owner(app), wo["_id"])                           # любой рендер после деплоя
    stored = [b.get("labor_id") for b in _wo(mongo, wo["_id"])["labors"]]
    assert all(stored) if ids_persisted_meanwhile else not any(stored)
    assert not set(stored) & {blk["labor_id"] for blk in tab.blocks}
    return tab, stored


@pytest.mark.parametrize("ids_persisted_meanwhile", [False, True], ids=["ids_not_persisted", "ids_persisted"])
def test_pre_deploy_tab_with_render_ids_saves_without_duplicates(app, mongo, co, ids_persisted_meanwhile):
    """Ревью D4-X1: вкладка, открытая до деплоя, держит временные id рендера;
    промежуточный рендер после деплоя сохранил в WO другие, настоящие id.
    Описания строк те же — строки вкладки сопоставляются со строками WO один
    к одному (как прежняя запись массива): работы не задваиваются (раньше —
    каждая работа ×2, запчасти списаны ещё раз), page_outdated нет, id — из
    WO; тоталы — как у старого кода."""
    shop_db = mongo[SHOP_A_DB]
    pads, batt = co["parts"]["pads"], co["parts"]["battery"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab, stored = _pre_deploy_tab(app, mongo, wo, ids_persisted_meanwhile=ids_persisted_meanwhile)
    before = _wo(mongo, wo["_id"])
    stock = (_stock(mongo, pads), _stock(mongo, batt))
    tab.set_part(0, 0, price="101")
    payload = tab.payload()
    with app.app_context():
        _, exp_totals = _legacy_office_update(shop_db, co["shop"], before, payload)
    j = tab.save(payload=payload)
    after = _wo(mongo, wo["_id"])
    assert _descs(after) == ["Front brakes", "Diagnostics", "Oil service"]
    assert j["labor_ids"] == [b["labor_id"] for b in after["labors"]]
    if ids_persisted_meanwhile:
        assert j["labor_ids"] == stored
    assert [[p["part_number"] for p in b["parts"]] for b in after["labors"]] == [["CO-PADS", "CO-BATT"], [], ["OT-1"]]
    assert after["labors"][0]["parts"][0]["price"] == 101
    assert after["labors"][2]["labor"]["hours_source"] == "tracked"
    assert (_stock(mongo, pads), _stock(mongo, batt)) == stock
    assert j["merge_notes"] == []
    assert after["totals"] == exp_totals


def test_pre_deploy_tab_with_a_fully_renamed_row_gets_page_outdated(app, mongo, co):
    """Ревью F1-RR-01: строки вкладки до деплоя сопоставляются со строками WO
    только по описанию и только один к одному. Строку переименовали целиком
    — какая это строка WO, не узнать (по позиции больше не клеим: так
    чужая работа молча теряла описание и часы). Save отклоняется:
    page_outdated, WO и склад не тронуты."""
    from app.blueprints.work_orders.services.office_merge import PAGE_OUTDATED_RESPONSE

    pads, batt = co["parts"]["pads"], co["parts"]["battery"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab, _ = _pre_deploy_tab(app, mongo, wo, ids_persisted_meanwhile=True)
    before = _wo(mongo, wo["_id"])
    stock = (_stock(mongo, pads), _stock(mongo, batt))
    tab.set_description(1, "Engine scan")
    tab.set_part(0, 0, price="101")
    j = tab.save(expect_ok=False)
    assert j == PAGE_OUTDATED_RESPONSE
    _assert_untouched(mongo, wo["_id"], before)
    assert (_stock(mongo, pads), _stock(mongo, batt)) == stock


def test_former_mechanic_assignment_survives_office_resave(app, mongo, co):
    """Механика уволили / сменили роль: его назначение на строке остаётся при
    Save офиса; заново назначить его на другую строку нельзя."""
    master = mongo["roobico_test_master"]
    former, a = co["users"][FORMER], co["users"][MECH_A]
    rows = _rich_rows(co)
    rows[1]["assigned_mechanics"] = [_assign(former, 50.0), _assign(a, 50.0)]
    wo = _insert_wo(app, mongo, co, rows)
    master.users.update_one({"_id": former["_id"]}, {"$set": {"role": "viewer"}})
    try:
        for new_js in (True, False):
            tab = OfficeTab(_owner(app), wo["_id"], new_js=new_js)
            tab.blocks[2]["assigned"].append(
                {"user_id": str(former["_id"]), "name": "Fred Office", "role": "mechanic", "percent": 10})
            tab.set_part(0, 0, price="99")
            tab.save()
            after = _wo(mongo, wo["_id"])
            row1 = after["labors"][1]["labor"]["assigned_mechanics"]
            assert {x["user_id"] for x in row1} == {former["_id"], a["_id"]}, new_js
            assert next(x for x in row1 if x["user_id"] == former["_id"])["name"] == "Fred Office"
            assert former["_id"] not in {x["user_id"] for x in after["labors"][2]["labor"]["assigned_mechanics"]}
    finally:
        master.users.update_one({"_id": former["_id"]}, {"$set": {"role": "mechanic"}})


# ───────────────────────── механик на офисном эндпоинте ─────────────────────────


def test_mechanic_on_office_endpoint_cannot_change_money_and_respects_confirm_lock(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    mech = app.test_client()
    assert login(mech, email=MECH_A, password=PASSWORD).status_code == 302
    before = _wo(mongo, wo["_id"])
    row = {"labor_id": before["labors"][1]["labor_id"], "labor_description": "Diagnostics - mechanic",
           "labor_hours": 7, "labor_rate_code": "standard", "labor_full_total": 700,
           "assigned_mechanics": [], "issue_description": "", "parts": []}
    payload = {"labors": [row], "totals": {"labor": 700, "grand_total": 700}, "save_status": "open"}
    resp = _post(mech, f"/work_orders/api/work_orders/{wo['_id']}/update", payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    after = _wo(mongo, wo["_id"])
    # Старый клиент без вьюхи: ничего не удаляется по отсутствию; строки,
    # которых он не прислал, идут после его строк.
    assert sorted(_descs(after)) == ["Diagnostics - mechanic", "Front brakes", "Oil service"]
    k = [b["labor_id"] for b in after["labors"]].index(row["labor_id"])
    lab = after["labors"][k]["labor"]
    assert (lab["hours"], lab["rate_code"]) == ("0.35", DIESEL)          # часы и ставка — не механика
    assert after["totals"]["labors"][k]["labor"] == before["totals"]["labors"][1]["labor"]
    assert after["status"] == "in_progress" and "grand_total" not in resp.get_json()

    owner = _owner(app)
    assert _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/confirm", {"confirmed": True}).status_code == 200
    resp = _post(mech, f"/work_orders/api/work_orders/{wo['_id']}/update", payload)
    assert resp.status_code == 403 and resp.get_json()["error"] == "wo_confirmed"


def test_mechanic_office_save_confirmed_between_read_and_write_is_rejected(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import edit_views

    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    mech = app.test_client()
    assert login(mech, email=MECH_A, password=PASSWORD).status_code == 302
    real = edit_views.running_labor_ids
    calls = {"n": 0}

    def confirm_once(shop_db, shop_id, work_order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {"$set": {"manager_confirmed": True}})
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", confirm_once)
    before = _wo(mongo, wo["_id"])
    row = {"labor_id": before["labors"][0]["labor_id"], "labor_description": "Should not be written",
           "labor_hours": 0, "labor_rate_code": "", "labor_full_total": 0, "assigned_mechanics": [],
           "issue_description": "", "parts": []}
    resp = _post(mech, f"/work_orders/api/work_orders/{wo['_id']}/update", {"labors": [row], "totals": {}})
    assert resp.status_code == 403 and resp.get_json()["error"] == "wo_confirmed"
    assert _wo(mongo, wo["_id"])["labors"] == before["labors"]


# ───────────────────────── CAS ─────────────────────────


def test_office_save_retries_and_merges_a_concurrent_write(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import edit_views

    pads = co["parts"]["pads"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    real = edit_views.running_labor_ids
    calls = {"n": 0}

    def race_once(shop_db, shop_id, work_order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {
                "$push": {"labors": {"labor_id": str(ObjectId()), "labor": {
                    "description": "Added by mechanic", "hours": "", "rate_code": "standard",
                    "assigned_mechanics": [], "issue_description": ""}, "parts": []}},
                "$inc": {"labors_rev": 1},
            })
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", race_once)
    stock = _stock(mongo, pads)
    tab.set_part(0, 0, qty="3")
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert calls["n"] == 2
    assert _descs(after)[-1] == "Added by mechanic"
    assert after["labors"][0]["parts"][0]["qty"] == 3
    assert _stock(mongo, pads) == stock - 1                             # разница qty — один раз
    assert j["merged_foreign"] is True and j["labors_rev"] == after["labors_rev"]


def test_office_save_gives_up_with_409_and_no_side_effects(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import edit_views

    pads = co["parts"]["pads"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    real = edit_views.running_labor_ids

    def always_racing(shop_db, shop_id, work_order_id):
        shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {"$inc": {"labors_rev": 1}})
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", always_racing)
    stock = _stock(mongo, pads)
    before = _wo(mongo, wo["_id"])
    tab.set_part(0, 0, qty="5")
    resp = _post(tab.client, f"/work_orders/api/work_orders/{wo['_id']}/update", tab.payload())
    assert resp.status_code == 409 and resp.get_json() == edit_views.CONCURRENT_UPDATE_RESPONSE
    assert _wo(mongo, wo["_id"])["labors"] == before["labors"]
    assert _stock(mongo, pads) == stock


def test_payment_between_read_and_write_is_caught_by_the_cas_filter(app, mongo, co, monkeypatch):
    """Оплата ревизию не двигает — запись ловит фильтр по статусу."""
    from app.blueprints.work_orders.services import edit_views

    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    real = edit_views.running_labor_ids
    calls = {"n": 0}

    def paid_once(shop_db, shop_id, work_order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db.work_orders.update_one({"_id": ObjectId(str(work_order_id))}, {"$set": {"status": "paid"}})
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", paid_once)
    before = _wo(mongo, wo["_id"])
    tab.set_part(0, 0, price="1")
    j = tab.save(expect_ok=False)
    assert j == {"ok": False, "error": "paid_cannot_edit"}
    after = _wo(mongo, wo["_id"])
    assert after["labors"] == before["labors"] and after.get("labors_rev") == before.get("labors_rev")


def test_client_key_retry_after_lost_response_does_not_duplicate(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    tab.add_row("Alignment", hours="1.2")
    payload = tab.payload()
    first = _post(tab.client, f"/work_orders/api/work_orders/{wo['_id']}/update", payload).get_json()
    assert first["ok"] is True
    # Ответ потерян: вкладка шлёт тот же Save ещё раз (тот же view_id, строка без id).
    second = _post(tab.client, f"/work_orders/api/work_orders/{wo['_id']}/update", payload).get_json()
    assert second["ok"] is True
    after = _wo(mongo, wo["_id"])
    assert _descs(after).count("Alignment") == 1
    ck = payload["labors"][3]["client_key"]
    assert first["client_keys"][ck] == second["client_keys"][ck] == after["labors"][3]["labor_id"]


def test_legacy_double_save_of_a_new_row_does_not_duplicate(app, mongo, co):
    """Старый JS без client_key: двойной клик Save — вторая копия строки
    привязывается к созданной первой (метка minted того же устройства)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"], new_js=False)
    tab.add_row("Alignment", hours="1.2")
    payload = tab.payload()
    for _ in range(2):
        assert _post(tab.client, f"/work_orders/api/work_orders/{wo['_id']}/update", payload).get_json()["ok"] is True
    assert _descs(_wo(mongo, wo["_id"])).count("Alignment") == 1


# ───────────────────────── describe issue ─────────────────────────


def test_issue_description_by_labor_id_empty_id_legacy_index_and_gone_row(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    owner = _owner(app)
    ids = [b["labor_id"] for b in wo["labors"]]
    url = f"/work_orders/api/work_orders/{wo['_id']}/labors/0/issue-description"

    # Новый JS: запись по labor_id, индекс в URL не важен.
    j = _post(owner, url, {"labor_id": ids[2], "text": "  Leaks at the drain plug  "}).get_json()
    after = _wo(mongo, wo["_id"])
    assert j["ok"] is True and j["saved"] is True and j["labors_rev"] == after["labors_rev"] == 2
    assert after["labors"][2]["labor"]["issue_description"] == "Leaks at the drain plug"
    assert after["labors"][2]["issue_description"] == "Leaks at the drain plug"
    assert after["labors"][0]["labor"]["issue_description"] == "Squeal on stop"

    # Строка ещё не сохранена — ничего не пишем, текст уйдёт ближайшим Save.
    j = _post(owner, url, {"labor_id": "", "text": "Draft"}).get_json()
    assert j == {"ok": True, "saved": False, "issue_description": "Draft", "labors_rev": 2}
    assert _wo(mongo, wo["_id"])["labors_rev"] == 2

    # Старый JS: только индекс — позиционная запись по ревизии.
    j = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/labors/1/issue-description",
              {"text": "Check engine light"}).get_json()
    after = _wo(mongo, wo["_id"])
    assert j["ok"] is True and j["labors_rev"] == after["labors_rev"] == 3
    assert after["labors"][1]["labor"]["issue_description"] == "Check engine light"

    resp = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/labors/7/issue-description", {"text": "x"})
    assert resp.status_code == 400

    # Строку удалили, пока окно Describe Issue было открыто.
    resp = _post(owner, url, {"labor_id": str(ObjectId()), "text": "Too late"})
    assert resp.status_code == 404 and resp.get_json()["error"] == "labor_not_found"


# ───────────────────────── delete / status / live_state ─────────────────────────


def test_delete_flips_atomically_and_a_stale_save_cannot_touch_the_deleted_wo(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import edit_views

    pads = co["parts"]["pads"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    deleter = _owner(app)                     # другая вкладка удаляет WO, пока идёт Save
    real = edit_views.running_labor_ids
    deleted = {}

    def delete_meanwhile(shop_db, shop_id, work_order_id):
        if not deleted:
            deleted["resp"] = _post(deleter, f"/work_orders/api/work_orders/{work_order_id}/delete", {}).get_json()
        return real(shop_db, shop_id, work_order_id)

    monkeypatch.setattr(edit_views, "running_labor_ids", delete_meanwhile)
    stock = _stock(mongo, pads)
    tab.set_part(0, 0, qty="9")
    resp = _post(tab.client, f"/work_orders/api/work_orders/{wo['_id']}/update", tab.payload())
    assert resp.status_code == 404 and resp.get_json()["error"] == "work_order_not_found"
    assert deleted["resp"]["ok"] is True
    after = _wo(mongo, wo["_id"])
    assert after["is_active"] is False and after["labors_rev"] == 2
    assert after["labors"][0]["parts"][0]["qty"] == 2
    assert _stock(mongo, pads) == stock + 2                             # вернулось то, что было в WO


def test_set_status_in_progress_bumps_revision_only_when_it_clears_done_marks(app, mongo, co):
    a = co["users"][MECH_A]
    wo = _insert_wo(app, mongo, co, _rich_rows(co), status="in_progress", mechanic_done=True,
                    mechanic_done_marks=[{"user_id": a["_id"], "name": "Oscar Office", "at": _now()}])
    owner = _owner(app)
    j = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "in_progress"}).get_json()
    after = _wo(mongo, wo["_id"])
    assert after["mechanic_done_marks"] == [] and after["labors_rev"] == 2 and j["labors_rev"] == 2
    j = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "in_progress"}).get_json()
    assert _wo(mongo, wo["_id"])["labors_rev"] == 2 and j["labors_rev"] == 2


def test_live_state_reports_labors_rev(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    owner = _owner(app)
    body = owner.get(f"/work_orders/api/work_orders/{wo['_id']}/live_state").get_json()
    assert body["ok"] is True and body["labors_rev"] == 1
    mech = Mech(app, MECH_A)
    rows = mech.open(wo["_id"])
    rows[0]["description"] = "Front brakes - checked"
    mech.save()
    body = owner.get(f"/work_orders/api/work_orders/{wo['_id']}/live_state").get_json()
    assert body["labors_rev"] == _wo(mongo, wo["_id"])["labors_rev"] == 2


def test_office_save_response_keys(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    tab.add_row("Alignment", hours="1")
    j = tab.save()
    for key in ("ok", "status", "labor_ids", "inventory_adjusted", "inventory_changes", "inventory_warnings",
                "cores_synced", "core_changes", "core_sync_errors", "view_id", "merged_view_id", "labors_rev",
                "client_keys", "merged_foreign", "merge_notes", "warnings"):
        assert key in j, key
    after = _wo(mongo, wo["_id"])
    assert j["labor_ids"] == [b["labor_id"] for b in after["labors"]]
    assert j["labors_rev"] == after["labors_rev"] and j["merged_view_id"] == j["view_id"]
    sent = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(j["view_id"])})
    assert sent["kind"] == "sent" and sent["channel"] == "office" and sent["minted"] == [j["labor_ids"][3]]
    assert sent["wo_fields"]["shop_supply_pct"] == tab.pct
    # Второй Save этой же вкладки — от sent view: без чужих правок, без заметок.
    tab.set_description(3, "Alignment (4 wheel)")
    j2 = tab.save()
    assert j2["merged_foreign"] is False and j2["merge_notes"] == []
    assert _descs(_wo(mongo, wo["_id"]))[3] == "Alignment (4 wheel)"


def test_unresolvable_view_id_is_conservative_and_forces_a_refresh(app, mongo, co):
    """view_id не нашёлся (истёк / чужой / пустой) — ничего не удаляется по
    отсутствию, клиенту — заметка и merged_foreign (перечитать WO)."""
    from app.blueprints.work_orders.services.edit_views import UNRESOLVED_BASE_NOTE

    for bad in ("", str(ObjectId())):
        wo = _insert_wo(app, mongo, co, _rich_rows(co))
        tab = OfficeTab(_owner(app), wo["_id"])
        tab.view_id = bad
        tab.remove_row(1)
        j = tab.save()
        assert _descs(_wo(mongo, wo["_id"])) == ["Front brakes", "Oil service", "Diagnostics"]
        assert j["merged_foreign"] is True and UNRESOLVED_BASE_NOTE in j["merge_notes"]


def test_office_create_starts_at_revision_one(app, mongo, co):
    owner = _owner(app)
    resp = owner.post("/work_orders/create", data={
        "csrf_token": get_csrf_token(owner),
        "customer_id": str(co["customer"]["_id"]), "unit_id": str(co["unit"]["_id"]),
        "labors[0][labor_description]": "Created in office", "labors[0][labor_hours]": "1",
        "labors[0][labor_rate_code]": "standard",
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
    wo = mongo[SHOP_A_DB].work_orders.find_one(
        {"customer_id": co["customer"]["_id"], "labors.labor.description": "Created in office"})
    assert wo["labors_rev"] == 1
    assert OfficeTab(owner, wo["_id"]).rev == 1


def test_viewer_render_records_no_edit_view(app, mongo, co, seed):
    from app.constants.permissions import build_default_roles

    master, tdb = mongo["roobico_test_master"], mongo[TENANT_A_DB]
    viewer_role = next(r for r in build_default_roles() if r["key"] == "viewer")
    tdb.roles.update_one({"key": "viewer"}, {"$set": viewer_role}, upsert=True)
    viewer = {"_id": ObjectId(), "email": "co.viewer@test.local", "password_hash": generate_password_hash(PASSWORD),
              "first_name": "Vic", "last_name": "Viewer", "is_active": True, "tenant_id": seed["tenant_a"]["_id"],
              "shop_ids": [str(co["shop"]["_id"])], "role": "viewer", "created_at": _now()}
    master.users.insert_one(viewer)
    try:
        wo = _insert_wo(app, mongo, co, _rich_rows(co))
        c = app.test_client()
        assert login(c, email="co.viewer@test.local", password=PASSWORD).status_code == 302
        html = c.get(f"/work_orders/details?work_order_id={wo['_id']}").get_data(as_text=True)
        assert _script_json(html, "workOrderCreatedData")["view_id"] == ""
        assert mongo[SHOP_A_DB].wo_edit_views.count_documents({"user_id": str(viewer["_id"])}) == 0
    finally:
        master.users.update_one({"_id": viewer["_id"]}, {"$set": {"is_active": False}})


# ───────────────────────── transfer ─────────────────────────


def test_transfer_retries_on_a_concurrent_write_without_orphan_units(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import transfer as transfer_mod

    shop_db = mongo[SHOP_A_DB]
    unit = {"_id": ObjectId(), "shop_id": co["shop"]["_id"], "customer_id": co["customer"]["_id"],
            "vin": "OFFCONCTRANSFER01", "unit_number": "OC-T", "mileage": 1000, "is_active": True,
            "created_at": _now()}
    shop_db.units.insert_one(unit)
    wo = _insert_wo(app, mongo, co, _rich_rows(co), unit=unit)
    real = transfer_mod.reprice_wo_for_customer
    calls = {"n": 0}

    def race_once(shop_db_, shop, wo_doc, customer_id):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db_.work_orders.update_one({"_id": wo_doc["_id"]}, {
                "$set": {"labors.0.labor.description": "Mechanic renamed"}, "$inc": {"labors_rev": 1}})
        return real(shop_db_, shop, wo_doc, customer_id)

    monkeypatch.setattr(transfer_mod, "reprice_wo_for_customer", race_once)
    resp = _post(_owner(app), f"/work_orders/api/work_orders/{wo['_id']}/transfer", {"customer_id": str(co["other"]["_id"])})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    after = _wo(mongo, wo["_id"])
    assert calls["n"] == 2
    assert after["labors"][0]["labor"]["description"] == "Mechanic renamed"      # правка не откатилась
    copies = list(shop_db.units.find({"vin": "OFFCONCTRANSFER01", "customer_id": co["other"]["_id"]}))
    assert len(copies) == 1 and after["unit_id"] == copies[0]["_id"] and copies[0]["is_active"] is True
    assert shop_db.units.find_one({"_id": unit["_id"]})["is_active"] is False
    assert after["labors_rev"] == 3


def test_transfer_gives_up_with_409_and_leaves_no_orphans(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import transfer as transfer_mod

    shop_db = mongo[SHOP_A_DB]
    unit = {"_id": ObjectId(), "shop_id": co["shop"]["_id"], "customer_id": co["customer"]["_id"],
            "vin": "OFFCONCTRANSFER02", "unit_number": "OC-T2", "mileage": 1000, "is_active": True,
            "created_at": _now()}
    shop_db.units.insert_one(unit)
    wo = _insert_wo(app, mongo, co, _rich_rows(co), unit=unit)
    real = transfer_mod.reprice_wo_for_customer

    def always_racing(shop_db_, shop, wo_doc, customer_id):
        shop_db_.work_orders.update_one({"_id": wo_doc["_id"]}, {"$inc": {"labors_rev": 1}})
        return real(shop_db_, shop, wo_doc, customer_id)

    monkeypatch.setattr(transfer_mod, "reprice_wo_for_customer", always_racing)
    resp = _post(_owner(app), f"/work_orders/api/work_orders/{wo['_id']}/transfer", {"customer_id": str(co["other"]["_id"])})
    assert resp.status_code == 409 and resp.get_json()["error"] == "concurrent_update"
    after = _wo(mongo, wo["_id"])
    assert after["customer_id"] == co["customer"]["_id"] and after["unit_id"] == unit["_id"]
    assert shop_db.units.count_documents({"vin": "OFFCONCTRANSFER02", "customer_id": co["other"]["_id"]}) == 0
    assert shop_db.units.find_one({"_id": unit["_id"]})["is_active"] is True


# ───────────────────────── назначения и пара клиент/юнит ─────────────────────────


def test_stale_tab_with_a_former_mechanic_does_not_revert_a_reassignment(app, mongo, co):
    """D2-04: на строке Fred (уже не механик) 50% / Oscar 50%. Две вкладки
    открыли WO; вторая переназначила строку на Olga 100%. Первая правит
    только цену: строку она не трогала — остаётся Olga 100% (раньше Fred
    выпадал из формы первой вкладки, mine ≠ base, и Save писал Oscar 50%).
    Настоящая правка назначения устаревшей вкладкой по-прежнему побеждает."""
    master = mongo["roobico_test_master"]
    former, a, b = co["users"][FORMER], co["users"][MECH_A], co["users"][MECH_B]
    rows = _rich_rows(co)
    rows[1]["assigned_mechanics"] = [_assign(former, 50.0), _assign(a, 50.0)]
    olga = {"user_id": str(b["_id"]), "name": "Olga Office", "role": "mechanic", "percent": 100}

    def stored(wo_id):
        return [(x["user_id"], x["percent"]) for x in _wo(mongo, wo_id)["labors"][1]["labor"]["assigned_mechanics"]]

    master.users.update_one({"_id": former["_id"]}, {"$set": {"role": "viewer"}})
    try:
        wo = _insert_wo(app, mongo, co, rows)
        tab1 = OfficeTab(_owner(app), wo["_id"])
        tab2 = OfficeTab(_owner(app), wo["_id"])
        tab2.blocks[1]["assigned"] = [dict(olga)]
        tab2.edit()
        tab2.save()
        assert stored(wo["_id"]) == [(b["_id"], 100.0)]
        tab1.set_part(0, 0, price="101")
        tab1.save()
        assert stored(wo["_id"]) == [(b["_id"], 100.0)]

        # Устаревшая вкладка правит долю Oscar на той же строке — её правка
        # побеждает, и Fred, которого она видела, в ней остаётся.
        wo2 = _insert_wo(app, mongo, co, rows)
        tab1 = OfficeTab(_owner(app), wo2["_id"])
        tab2 = OfficeTab(_owner(app), wo2["_id"])
        tab2.blocks[1]["assigned"] = [dict(olga)]
        tab2.edit()
        tab2.save()
        assert [str(x["user_id"]) for x in tab1.blocks[1]["assigned"]] == [str(former["_id"]), str(a["_id"])]
        tab1.blocks[1]["assigned"][1]["percent"] = 60
        tab1.edit()
        tab1.save()
        assert stored(wo2["_id"]) == [(former["_id"], 50.0), (a["_id"], 60.0)]
    finally:
        master.users.update_one({"_id": former["_id"]}, {"$set": {"role": "mechanic"}})


def test_customer_change_with_the_old_unit_is_rejected(app, mongo, co):
    """D2-05 (b): клиента сменили, а юнит остался прежнего клиента (гонка
    селектов страницы, API) — как раньше, unit_customer_mismatch, ничего не
    записано (3-way решает клиента и юнит по отдельности, пару проверяем)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    before = _wo(mongo, wo["_id"])
    tab.customer_id = str(co["other"]["_id"])
    tab.edit()
    j = tab.save(expect_ok=False)
    assert (j["ok"], j["error"]) == (False, "unit_customer_mismatch")
    assert j["message"] == "The selected unit belongs to a different customer. Select a unit of this customer."
    after = _assert_untouched(mongo, wo["_id"], before)
    assert (after["customer_id"], after["unit_id"]) == (co["customer"]["_id"], co["unit"]["_id"])


def test_unit_change_after_a_transfer_is_rejected_with_a_readable_message(app, mongo, co):
    """D2-05 (a): пока вкладка открыта, WO перенесли другому клиенту; вкладка
    меняет только юнит (на юнит прежнего клиента). Прежнего клиента не
    применяем (строки уже переоценены под нового) — отказ с текстом «WO
    перенесли, перезагрузите», а не машинным кодом."""
    unit = {"_id": ObjectId(), "shop_id": co["shop"]["_id"], "customer_id": co["customer"]["_id"],
            "vin": "OFFCONCPAIRTRF001", "unit_number": "OC-P", "mileage": 40000, "is_active": True,
            "created_at": _now()}
    mongo[SHOP_A_DB].units.insert_one(unit)
    wo = _insert_wo(app, mongo, co, _rich_rows(co), unit=unit, mileage=40000)
    owner = _owner(app)
    tab = OfficeTab(owner, wo["_id"])
    resp = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/transfer", {"customer_id": str(co["other"]["_id"])})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    before = _wo(mongo, wo["_id"])

    tab.unit_id, tab.mileage = str(co["unit2"]["_id"]), "40500"
    tab.edit()
    j = tab.save(expect_ok=False)
    assert (j["ok"], j["error"]) == (False, "unit_customer_mismatch")
    assert j["message"] == ("This work order was moved to another customer by another user. "
                            "Reload the page to see the change.")
    assert _assert_untouched(mongo, wo["_id"], before)["customer_id"] == co["other"]["_id"]


# ───────────────────────── оплаченный WO: Unpaid без перезагрузки ─────────────────────────


def test_paid_page_unpaid_then_save_removes_lines(app, mongo, co):
    """D2-06 / D9-01 / D5-01: страница открыта, пока WO был paid; Unpaid
    (без перезагрузки) → Edit → убрали работу и запчасть → Save. Load-вьюха
    оплаченного WO теперь записывается при рендере — удаление проходит, склад
    возвращается, без ложных заметок (раньше view_id был пуст: консервативный
    merge, «nothing was removed»). Пока WO paid — Save отклоняется."""
    batt = co["parts"]["battery"]
    wo = _insert_wo(app, mongo, co, _rich_rows(co), status="paid")
    owner = _owner(app)
    tab = OfficeTab(owner, wo["_id"])
    assert tab.view_id
    tab.set_part(0, 0, price="1")
    assert tab.save(expect_ok=False) == {"ok": False, "error": "paid_cannot_edit"}
    tab.set_part(0, 0, price="99.5")

    j = _post(owner, f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "open"}).get_json()
    assert j["ok"] is True and j["status"] == "open"
    stock = _stock(mongo, batt)
    tab.remove_part(0, 1)
    tab.remove_row(1)
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert _descs(after) == ["Front brakes", "Oil service"]
    assert [p["part_number"] for p in after["labors"][0]["parts"]] == ["CO-PADS"]
    assert _stock(mongo, batt) == stock + 1
    assert j["merged_foreign"] is False and j["merge_notes"] == []


def test_paid_page_payment_deleted_then_save_removes_lines(app, mongo, co):
    """То же через удаление последнего платежа на странице (WO уходит из paid)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co), status="paid")
    pay_id = mongo[SHOP_A_DB].work_order_payments.insert_one({
        "work_order_id": wo["_id"], "shop_id": co["shop"]["_id"], "tenant_id": co["shop"].get("tenant_id"),
        "amount": wo["totals"]["grand_total"], "payment_method": "cash", "notes": "", "is_active": True,
        "payment_date": _now(), "created_at": _now(),
    }).inserted_id
    owner = _owner(app)
    tab = OfficeTab(owner, wo["_id"])
    j = _post(owner, f"/work_orders/api/payments/{pay_id}/delete", {}).get_json()
    assert j["ok"] is True and j["status"] == "open"
    tab.remove_row(1)
    j = tab.save()
    assert _descs(_wo(mongo, wo["_id"])) == ["Front brakes", "Oil service"]
    assert j["merged_foreign"] is False and j["merge_notes"] == []


# ───────────────────────── записи «сбоку»: Describe Issue, правка часов ─────────────────────────


def _describe(tab, bi, text):
    """Describe Issue вкладки (новый JS): запись по labor_id с базой вкладки;
    текст — в форму (dataset + скрытое поле), производная вьюха — новой базой."""
    blk = tab.blocks[bi]
    resp = _post(tab.client, f"/work_orders/api/work_orders/{tab.wo_id}/labors/{bi}/issue-description",
                 {"labor_id": blk["labor_id"], "text": text, "view_id": tab.view_id})
    j = resp.get_json()
    assert resp.status_code == 200 and j["saved"] is True, j
    blk["issue"] = text
    if j.get("view_id"):
        tab.view_id = j["view_id"]
    return j


def test_describe_issue_derives_the_tab_base_and_a_mechanic_refinement_survives(app, mongo, co):
    """D1-03 / D5-02 (b): офис описал проблему (запись сразу в WO), механик
    дописал диагноз, офис сохраняет форму — текст механика не откатывается
    (раньше база вкладки держала старый текст, и свой же текст формы
    считался правкой — молча побеждал)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    load_view = tab.view_id
    j = _describe(tab, 1, "Customer says: rough idle")
    assert j["view_id"] and j["view_id"] != load_view
    derived = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(j["view_id"])})
    source = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(load_view)})
    assert derived["channel"] == "office" and derived["user_id"] == str(co["owner"]["_id"])
    assert derived["labors_rev"] == j["labors_rev"] == _wo(mongo, wo["_id"])["labors_rev"]
    assert derived["labors"][1]["issue_description"] == "Customer says: rough idle"
    assert [r for i, r in enumerate(derived["labors"]) if i != 1] == [r for i, r in enumerate(source["labors"]) if i != 1]
    assert derived["wo_fields"] == source["wo_fields"]

    mech = Mech(app, MECH_A)
    rows = mech.open(wo["_id"])
    assert rows[1]["issue_description"] == "Customer says: rough idle"
    rows[1]["issue_description"] += " Found: cracked vacuum hose"
    mech.save()

    tab.set_part(0, 0, price="101")
    tab.save()
    after = _wo(mongo, wo["_id"])
    assert after["labors"][1]["labor"]["issue_description"] == "Customer says: rough idle Found: cracked vacuum hose"
    assert after["labors"][0]["parts"][0]["price"] == 101


def test_describe_issue_then_a_mechanic_deletes_the_job_keeps_it_deleted(app, mongo, co):
    """(c): механик удалил работу после Describe Issue офиса — Save офиса её
    не воскрешает (текст формы = база вкладки, правки нет)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    _describe(tab, 1, "Customer says: rough idle")
    mech = Mech(app, MECH_A)
    rows = mech.open(wo["_id"])
    mech.rows = [r for r in rows if r["description"] != "Diagnostics"]
    mech.save()
    assert "Diagnostics" not in _descs(_wo(mongo, wo["_id"]))

    tab.set_part(0, 0, price="101")
    j = tab.save()
    assert _descs(_wo(mongo, wo["_id"])) == ["Front brakes", "Oil service"]
    assert 'Job "Diagnostics" was removed by another user.' in j["merge_notes"]


def test_office_can_remove_a_job_it_just_described(app, mongo, co):
    """(a): офис убирает работу, которую сам только что описал, — удаляется
    (раньше: «Kept job: it was changed by another user»)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    _describe(tab, 1, "Customer says: rough idle")
    tab.remove_row(1)
    j = tab.save()
    assert _descs(_wo(mongo, wo["_id"])) == ["Front brakes", "Oil service"]
    assert j["merged_foreign"] is False and j["merge_notes"] == []


def test_describe_issue_with_an_unknown_view_derives_nothing(app, mongo, co):
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    owner = _owner(app)
    lid = wo["labors"][1]["labor_id"]
    count = mongo[SHOP_A_DB].wo_edit_views.count_documents({"work_order_id": wo["_id"]})
    url = f"/work_orders/api/work_orders/{wo['_id']}/labors/1/issue-description"
    for view_id in (str(ObjectId()), ""):
        j = _post(owner, url, {"labor_id": lid, "text": "Noise", "view_id": view_id}).get_json()
        assert j["saved"] is True and j["view_id"] == ""
    # Чужая вьюха (другой пользователь) — тоже не основа.
    foreign = mongo[SHOP_A_DB].wo_edit_views.insert_one({
        "shop_id": co["shop"]["_id"], "work_order_id": wo["_id"], "user_id": str(ObjectId()), "sid": "x",
        "channel": "office", "kind": "load", "protocol": 1, "labors": [], "labors_rev": 1, "created_at": _now(),
    }).inserted_id
    j = _post(owner, url, {"labor_id": lid, "text": "Noise 2", "view_id": str(foreign)}).get_json()
    assert j["saved"] is True and j["view_id"] == ""
    assert mongo[SHOP_A_DB].wo_edit_views.count_documents({"work_order_id": wo["_id"]}) == count + 1


def _completed_log(mongo, co, wo, labor_id, user, seconds):
    now = _now()
    doc = {
        "_id": ObjectId(), "shop_id": co["shop"]["_id"], "tenant_id": co["shop"].get("tenant_id"),
        "work_order_id": wo["_id"], "wo_number": wo.get("wo_number"), "labor_id": labor_id,
        "user_id": user["_id"], "user_name": f"{user['first_name']} {user['last_name']}",
        "started_at": now - timedelta(seconds=seconds), "stopped_at": now, "seconds": seconds,
        "stop_source": "user", "created_at": now, "updated_at": now,
    }
    mongo[SHOP_A_DB].wo_time_logs.insert_one(doc)
    return doc


def test_time_log_edit_in_edit_mode_does_not_block_removing_that_job(app, mongo, co):
    """D5-02: правка сессии таймера в режиме Edit пересчитала часы строки
    (1.33 → 1.5 tracked). Ответ несёт производную вьюху — Save вкладки знает
    свежие часы и может удалить эту работу (раньше: «Kept job: it was
    changed by another user»)."""
    b = co["users"][MECH_B]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    oil = tab.blocks[2]["labor_id"]
    log = _completed_log(mongo, co, wo, oil, b, 4788)               # 1.33 ч — как сохранено
    j = _post(tab.client, f"/work_orders/api/time_logs/{log['_id']}/update",
              {"seconds": 5400, "view_id": tab.view_id}).get_json()
    assert j["ok"] is True and j["view_id"] and j["view_id"] != tab.view_id
    after = _wo(mongo, wo["_id"])
    lab = after["labors"][2]["labor"]
    assert (lab["hours"], lab["hours_source"]) == ("1.5", "tracked")
    derived = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(j["view_id"])})
    assert derived["labors"][2]["hours"] == "1.5" and derived["labors"][2]["hours_source"] == "tracked"
    assert derived["labors"][2]["labor_total"] == after["totals"]["labors"][2]["labor"]
    assert derived["labors_rev"] == j["labors_rev"] == after["labors_rev"]

    tab.view_id = j["view_id"]
    tab.remove_row(2)
    j = tab.save()
    assert _descs(_wo(mongo, wo["_id"])) == ["Front brakes", "Diagnostics"]
    assert j["merged_foreign"] is False and j["merge_notes"] == []


def test_time_log_delete_with_view_id_derives_the_tab_base(app, mongo, co):
    """Удаление сессии (тело JSON с view_id) — то же: часы 1.33 → 1.0, база
    вкладки свежая, строку можно удалить. Без view_id — view_id "" в ответе."""
    b = co["users"][MECH_B]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    oil = tab.blocks[2]["labor_id"]
    _completed_log(mongo, co, wo, oil, b, 3600)
    extra = _completed_log(mongo, co, wo, oil, b, 1188)             # вместе 4788 с = 1.33 ч
    j = _post(tab.client, f"/work_orders/api/time_logs/{extra['_id']}/delete", {"view_id": tab.view_id}).get_json()
    assert j["ok"] is True and j["view_id"]
    assert _wo(mongo, wo["_id"])["labors"][2]["labor"]["hours"] == "1.0"
    tab.view_id = j["view_id"]
    tab.remove_row(2)
    j = tab.save()
    assert "Oil service" not in _descs(_wo(mongo, wo["_id"]))
    assert j["merged_foreign"] is False and j["merge_notes"] == []

    other = _completed_log(mongo, co, wo, tab.blocks[0]["labor_id"], b, 600)
    resp = tab.client.post(f"/work_orders/api/time_logs/{other['_id']}/delete",
                           headers={"X-CSRFToken": get_csrf_token(tab.client)})
    assert resp.status_code == 200 and resp.get_json()["view_id"] == ""   # старый JS: пустой POST


def test_time_log_edit_does_not_absorb_another_users_manual_hours(app, mongo, co):
    """Производная вьюха переносит в базу вкладки только то, что пересчитала
    сама правка сессии. Ручные часы, которые до этого поставил другой
    менеджер, остаются для вкладки чужой правкой — её Save их не откатывает."""
    a = co["users"][MECH_A]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    other = OfficeTab(_owner(app), wo["_id"])
    other.set_hours(1, "2")                                            # ручные часы «Diagnostics»
    other.save()
    log = _completed_log(mongo, co, wo, tab.blocks[1]["labor_id"], a, 1800)
    j = _post(tab.client, f"/work_orders/api/time_logs/{log['_id']}/update",
              {"seconds": 2400, "view_id": tab.view_id}).get_json()
    assert j["ok"] is True and j["view_id"]
    derived = mongo[SHOP_A_DB].wo_edit_views.find_one({"_id": ObjectId(j["view_id"])})
    assert derived["labors"][1]["hours"] == "0.35"                    # часы вкладки, не чужие
    tab.view_id = j["view_id"]
    tab.set_part(0, 0, price="101")
    tab.save()
    assert _wo(mongo, wo["_id"])["labors"][1]["labor"]["hours"] == "2"


def _edit_oil_time_log(tab, mongo, co, wo, action):
    """Правка сессии таймера «Oil service» (1.33 ч, tracked) из вкладки в режиме
    Edit, тело с view_id: update — сессия 4788 → 5400 с (1.5 ч); delete — из
    сессий 3600 + 1188 с удалена вторая (1.0 ч). Вкладка берёт производную
    вьюху, форма по-прежнему показывает 1.33 без флага hours_edited."""
    b = co["users"][MECH_B]
    oil = tab.blocks[2]["labor_id"]
    if action == "update":
        log = _completed_log(mongo, co, wo, oil, b, 4788)
        j = _post(tab.client, f"/work_orders/api/time_logs/{log['_id']}/update",
                  {"seconds": 5400, "view_id": tab.view_id}).get_json()
        hours = "1.5"
    else:
        _completed_log(mongo, co, wo, oil, b, 3600)
        extra = _completed_log(mongo, co, wo, oil, b, 1188)
        j = _post(tab.client, f"/work_orders/api/time_logs/{extra['_id']}/delete", {"view_id": tab.view_id}).get_json()
        hours = "1.0"
    assert j["ok"] is True and j["view_id"], j
    lab = _wo(mongo, wo["_id"])["labors"][2]["labor"]
    assert (lab["hours"], lab["hours_source"]) == (hours, "tracked")
    tab.view_id = j["view_id"]
    assert (tab.blocks[2]["hours"], tab.blocks[2]["hours_edited"]) == ("1.33", False)


@pytest.mark.parametrize("action", ["update", "delete"])
def test_time_log_edit_then_save_keeps_a_colleagues_typed_hours(app, mongo, co, action):
    """Ревью RR-F2-01: после правки сессии в режиме Edit база вкладки знает
    свежие tracked-часы, форма — прежние 1.33 без флага. Другой менеджер
    тем временем ввёл часы 3. Save вкладки (правка цены колодок) их не
    откатывает: часы и база коллеги остаются, цена вкладки записана,
    вкладке — merged_foreign (раньше часы формы «отличались от base» и
    брались из формы: 1.33 / 133 — недобор в счёте)."""
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    tab = OfficeTab(_owner(app), wo["_id"])
    _edit_oil_time_log(tab, mongo, co, wo, action)
    other = OfficeTab(_owner(app), wo["_id"])
    other.set_hours(2, "3")
    other.save()

    tab.set_part(0, 0, price="101")
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    lab = after["labors"][2]["labor"]
    # Пустую метку офисный Save (как и прежний) не пишет: ключа нет — часы ручные.
    assert (lab["hours"], lab.get("hours_source", "")) == ("3", "")
    assert after["totals"]["labors"][2]["labor"] == 300.0
    assert after["labors"][0]["parts"][0]["price"] == 101.0
    assert j["merged_foreign"] is True
    _assert_totals_consistent(app, after)


@pytest.mark.parametrize("action", ["update", "delete"])
def test_time_log_edit_then_save_does_not_restore_a_job_another_user_removed(app, mongo, co, action):
    """Ревью RW-01: после правки сессии в режиме Edit механик удалил эту работу
    (в ней каталожный фильтр). Save вкладки с теми же, не введёнными руками
    часами её не воскрешает (раньше: «you changed it» — неправда, работа
    снова в счёте, фильтр списан второй раз): заметка «removed by another
    user», склад фильтра не двигается, итог вырос только на цену колодок."""
    filt = co["parts"]["filter"]
    rows = _rich_rows(co)
    rows[2]["parts"].append({"part_id": filt["_id"], "part_number": "CO-FILTER", "description": "Oil filter",
                             "qty": 2, "cost": 6.0, "price": 12.49, "core_charge": 0, "misc_charge": 0,
                             "misc_charge_description": "", "unit": "each"})
    wo = _insert_wo(app, mongo, co, rows)
    tab = OfficeTab(_owner(app), wo["_id"])
    _edit_oil_time_log(tab, mongo, co, wo, action)
    mech = Mech(app, MECH_B)
    mech.rows = [r for r in mech.open(wo["_id"]) if r["description"] != "Oil service"]
    mech.save()
    mid = _wo(mongo, wo["_id"])
    assert _descs(mid) == ["Front brakes", "Diagnostics"]
    stock, grand = _stock(mongo, filt), mid["totals"]["grand_total"]

    tab.set_part(0, 0, price="101")
    j = tab.save()
    after = _wo(mongo, wo["_id"])
    assert _descs(after) == ["Front brakes", "Diagnostics"]
    assert 'Job "Oil service" was removed by another user.' in j["merge_notes"]
    assert _stock(mongo, filt) == stock
    assert 0 < after["totals"]["grand_total"] - grand < 5             # колодки: 2 × 1.5 + налог


# ───────────────────────── статус оплаты и статус in_progress ─────────────────────────


def test_payment_status_follows_totals_that_changed_after_the_payment_read(app, mongo, co, monkeypatch):
    """D3-02: платёж на весь итог; сразу после того, как платёж перечитал WO,
    пересчёт часов таймера поднял итог на 110 (запись тоталов двигает
    labors_rev). Статус пишется CAS по ревизии прочитанного документа: WO
    перечитан — остаток 110, статус не paid (раньше: paid с долгом, WO
    выпадал из списков неоплаченных)."""
    from app.blueprints.work_orders.services import payments

    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    grand = wo["totals"]["grand_total"]
    real = payments._sum_active_work_order_payments
    calls = {"n": 0}

    def racing(shop_db, wo_id, session=None):
        calls["n"] += 1
        if calls["n"] == 1:
            shop_db.work_orders.update_one({"_id": wo["_id"]}, {"$inc": {"totals.grand_total": 110.0, "labors_rev": 1}})
        return real(shop_db, wo_id, session=session)

    monkeypatch.setattr(payments, "_sum_active_work_order_payments", racing)
    j = _post(_owner(app), f"/work_orders/api/work_orders/{wo['_id']}/payment",
              {"amount": grand, "payment_method": "cash"}).get_json()
    monkeypatch.undo()
    assert calls["n"] == 2
    assert j["ok"] is True and j["status"] == "open" and j["is_fully_paid"] is False
    assert j["remaining_balance"] == 110.0 and j["amount_paid"] == grand
    assert _wo(mongo, wo["_id"])["status"] == "open"


def test_payment_status_write_gives_up_after_cas_attempts_and_writes_the_last_read(app, mongo, co, monkeypatch):
    from app.blueprints.work_orders.services import edit_views, payments

    shop_db = mongo[SHOP_A_DB]
    wo = _insert_wo(app, mongo, co, _rich_rows(co))
    shop_db.work_order_payments.insert_one({
        "work_order_id": wo["_id"], "shop_id": co["shop"]["_id"], "amount": wo["totals"]["grand_total"],
        "payment_method": "cash", "is_active": True, "payment_date": _now(), "created_at": _now(),
    })
    real = payments._sum_active_work_order_payments
    calls = {"n": 0}

    def always_racing(shop_db_, wo_id, session=None):
        calls["n"] += 1
        shop_db_.work_orders.update_one({"_id": wo["_id"]}, {"$inc": {"labors_rev": 1}})
        return real(shop_db_, wo_id, session=session)

    monkeypatch.setattr(payments, "_sum_active_work_order_payments", always_racing)
    with app.app_context():
        summary = payments._sync_work_order_payment_state(shop_db, _wo(mongo, wo["_id"]), co["owner"]["_id"], _now())
    assert calls["n"] == edit_views.MAX_CAS_ATTEMPTS + 1
    assert summary["status"] == "paid" and _wo(mongo, wo["_id"])["status"] == "paid"


def test_set_status_in_progress_clears_a_done_mark_that_landed_after_its_read(app, mongo, co, monkeypatch):
    """D3-03: менеджер ставит in_progress («снова в работу у всех»). Между
    чтением set_status и его записью Done механика A коммитится, а
    сохранение B читает WO с отметкой A. Снимать ли отметки и двигать ли
    ревизию, решает фильтр записи — отметка A снята с $inc, устаревшее
    сохранение B не проходит CAS и не возвращает её (раньше: [A, B] и
    mechanic_done снова True)."""
    import app.blueprints.work_orders.routes as wo_routes
    from app.blueprints.work_orders.services import concurrent_save
    from app.blueprints.work_orders.services.common import utcnow

    shop_db = mongo[SHOP_A_DB]
    a, b = co["users"][MECH_A], co["users"][MECH_B]
    wo = _insert_wo(app, mongo, co, _rich_rows(co), status="in_progress")   # A и B назначены на строки
    real = wo_routes.clear_mechanic_done_fields
    seen = {}

    def done_lands_meanwhile():
        if "b_read" not in seen:
            shop_db.work_orders.update_one({"_id": wo["_id"]}, {
                "$set": {"mechanic_done_marks": [{"user_id": a["_id"], "name": "Oscar Office", "at": _now()}]},
                "$inc": {"labors_rev": 1},
            })
            seen["b_read"] = _wo(mongo, wo["_id"])
        return real()

    monkeypatch.setattr(wo_routes, "clear_mechanic_done_fields", done_lands_meanwhile)
    j = _post(_owner(app), f"/work_orders/api/work_orders/{wo['_id']}/status", {"status": "in_progress"}).get_json()
    monkeypatch.undo()
    mid = _wo(mongo, wo["_id"])
    assert j["ok"] is True and mid["mechanic_done_marks"] == [] and mid["mechanic_done"] is False
    assert mid["labors_rev"] == seen["b_read"]["labors_rev"] + 1 == j["labors_rev"]

    rows = [{
        "labor_id": blk["labor_id"], "description": blk["labor"]["description"],
        "issue_description": blk["labor"].get("issue_description") or "",
        "parts": [{"part_id": str(p.get("part_id") or ""), "part_number": p.get("part_number") or "",
                   "description": p.get("description") or "", "qty": p.get("qty"),
                   "one_time_part": bool(p.get("one_time_part"))} for p in blk.get("parts") or []],
    } for blk in mid["labors"]]
    data = {"status": "in_progress", "labors": rows, "mechanic_state": "done", "unit_mileage": "80000"}
    with app.test_request_context():
        payload, code = concurrent_save.save_mechanic_edit(
            shop_db, co["shop"], wo["_id"], data,
            user_id=b["_id"], sid="co-sid-b", request_started_at=utcnow(), channel="mobile", mechanic_mode=True,
            guard=concurrent_save.mobile_edit_guard(data, is_mechanic=True),
            cas_filter=concurrent_save.MECHANIC_CAS_FILTER, status="in_progress", taxable_from_request=True,
            audit_suffix="(mobile)", include_grand_total=True, initial_wo=seen["b_read"],
        )
    assert code == 200, payload
    after = _wo(mongo, wo["_id"])
    assert [m["user_id"] for m in after["mechanic_done_marks"]] == [b["_id"]]
    assert after["mechanic_done"] is False
