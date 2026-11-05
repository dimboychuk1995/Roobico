"""
Клиентские документы — инвойс/PDF, письмо по WO, клиентский портал, страница
и письмо авторизации — показывают каждую надбавку ОДИН раз: Labor — только
работа, Parts — только запчасти, а Shop Supplies, Core Charges, Misc и Sales
Tax — своими строками перед Total (в БД totals.labor_total = работа + supply,
totals.parts_total = запчасти + коры + misc). Если дизайн PDF скрывает строку
Shop Supplies, supply складывается обратно в Labor (включая строки Labor в
таблице позиций) и в Subtotal блоков; скрытые Core Charges / Misc
складываются в Parts — колонка PDF всегда сходится с Total.
"""
from __future__ import annotations

import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from flask import render_template

from tests.conftest import SHOP_A_DB, login


@pytest.fixture()
def logged_in(client):
    assert login(client).status_code == 302
    return client


def _totals():
    """Сохранённые totals одного блока: работа $100 + supply $5, парты $50."""
    return {
        "labor": 100, "labor_total": 105, "shop_supply_total": 5,
        "parts": 50, "parts_total": 50, "core_total": 0, "misc_total": 0,
        "misc_taxable_total": 0, "cost_total": 30, "parts_taxable_total": 50,
        "sales_tax_rate": 0, "sales_tax_total": 0, "is_taxable": False,
        "grand_total": 155,
        "labors": [{
            "labor": 100, "labor_total": 105, "parts": 50, "parts_total": 50,
            "core_total": 0, "misc_total": 0, "misc_taxable_total": 0,
            "cost_total": 30, "shop_supply_total": 5, "labor_full_total": 155,
        }],
    }


def _wo_doc(shop_id, customer_id, unit_id, totals, wo_number=77001, labors=None):
    now = datetime.now(timezone.utc)
    if labors is None:
        labors = [{
            "labor_id": "L1",
            "labor": {"description": "Brake job", "hours": "2", "rate_code": "standard"},
            "parts": [{
                "part_number": "P1", "description": "Pad", "qty": 1, "price": 50,
                "cost": 30, "core_charge": 0, "misc_charge": 0, "misc_charge_description": "",
            }],
        }]
    return {
        "_id": ObjectId(), "shop_id": shop_id, "customer_id": customer_id,
        "unit_id": unit_id, "wo_number": wo_number, "status": "open", "is_active": True,
        "work_order_date": now, "created_at": now,
        "labors": labors,
        "totals": totals,
    }


def _multi_job_wo(env, wo_number=77010):
    """WO из трёх работ, totals — как при сохранении из редактора:
    align_totals_with_labors + _apply_sales_tax_to_totals (8 %, taxable).

    Работа 100 / 33.33 / 66.67, shop supply WO $10 → 5.00 / 1.67 / 3.33
    (остаток — последнему блоку); работа 1: деталь 1 × $50 + кор $20;
    работа 2: деталь 2 × $10 + misc $5 (JSON-список в первой строке парта);
    работа 3: без деталей. Налог 8 % с (запчасти 70 + taxable misc 5) = $6.
    """
    from app.blueprints.work_orders.services.totals import (
        _apply_sales_tax_to_totals,
        align_totals_with_labors,
    )

    misc_json = json.dumps([{"description": "Disposal fee", "quantity": 1, "price": 5,
                             "manual": True, "taxable": True}])
    labors = [
        {"labor_id": "L1", "labor": {"description": "Brake job", "hours": "2", "rate_code": "standard"},
         "parts": [{"part_number": "P1", "description": "Pad", "qty": 1, "price": 50, "cost": 30,
                    "core_charge": 20, "misc_charge": 0, "misc_charge_description": ""}]},
        {"labor_id": "L2", "labor": {"description": "Oil change", "hours": "1", "rate_code": "standard"},
         "parts": [{"part_number": "P2", "description": "Filter", "qty": 2, "price": 10, "cost": 4,
                    "core_charge": 0, "misc_charge": 0, "misc_charge_description": misc_json}]},
        {"labor_id": "L3", "labor": {"description": "Inspection", "hours": "", "rate_code": "standard"},
         "parts": []},
    ]
    # Что присылает редактор WO: работа по блокам, supply на весь WO, taxable misc.
    requested = {
        "shop_supply_total": 10, "misc_taxable_total": 5,
        "labors": [{"labor": 100}, {"labor": 33.33}, {"labor": 66.67}],
    }
    totals = _apply_sales_tax_to_totals(align_totals_with_labors(requested, labors), 0.08, True)
    wo = _wo_doc(env["shop_id"], env["customer_id"], env["unit_id"], totals,
                 wo_number=wo_number, labors=labors)
    env["db"].work_orders.insert_one(wo)
    return wo


@pytest.fixture()
def env(app, seed):
    from app.extensions import get_master_db, get_mongo_client

    with app.app_context():
        db = get_mongo_client()[SHOP_A_DB]
        master = get_master_db()
        shop_id = seed["shop_a"]["_id"]
        now = datetime.now(timezone.utc)

        customer_id = ObjectId()
        db.customers.insert_one({
            "_id": customer_id, "shop_id": shop_id, "company_name": "Supply Fleet",
            "contacts": [], "is_active": True, "created_at": now,
        })
        unit_id = ObjectId()
        db.units.insert_one({
            "_id": unit_id, "shop_id": shop_id, "customer_id": customer_id,
            "unit_number": "U-SUP", "make": "Volvo", "model": "VNL", "year": "2020",
            "vin": "VINSUPPLY000001", "is_active": True, "created_at": now,
        })
        wo = _wo_doc(shop_id, customer_id, unit_id, _totals())
        db.work_orders.insert_one(wo)

        yield {"db": db, "shop_id": shop_id, "customer_id": customer_id,
               "unit_id": unit_id, "wo": wo, "shop": seed["shop_a"]}

        db.work_orders.delete_many({"customer_id": customer_id})
        db.units.delete_one({"_id": unit_id})
        db.customers.delete_one({"_id": customer_id})
        master.work_order_authorizations.delete_many({"customer_id": customer_id})
        master.customer_portal_tokens.delete_many({"customer_id": customer_id})


@pytest.fixture()
def multi(env):
    """WO из трёх работ с корами, misc и налогом (см. _multi_job_wo)."""
    return _multi_job_wo(env)


@contextmanager
def _pdf_design(env, **flags):
    """Временно выставить флаги дизайна PDF магазина; после — вернуть как было."""
    db = env["db"]
    prev = db.pdf_design.find_one({"shop_id": env["shop_id"]})
    db.pdf_design.update_one({"shop_id": env["shop_id"]}, {"$set": flags}, upsert=True)
    try:
        yield
    finally:
        if prev is None:
            db.pdf_design.delete_many({"shop_id": env["shop_id"]})
        else:
            db.pdf_design.replace_one({"_id": prev["_id"]}, prev)


def _pdf_html(client, wo_id, monkeypatch):
    """GET download-pdf с перехватом HTML, из которого рендерится PDF."""
    import app.blueprints.work_orders.routes as wo_routes

    captured = {}

    def fake_render(html):
        captured["html"] = html
        return b"%PDF-fake"

    monkeypatch.setattr(wo_routes, "render_html_to_pdf", fake_render)
    resp = client.get(f"/work_orders/api/work_orders/{wo_id}/download-pdf")
    assert resp.status_code == 200
    return captured["html"]


def _tot_row(html, label):
    """Сумма строки тоталов PDF: <td class="tot-lbl">Label</td><td class="tot-val">$X</td>."""
    match = re.search(
        r'<td class="tot-lbl">' + re.escape(label) + r'</td>\s*<td class="tot-val">\$([\d.]+)</td>',
        html,
    )
    return match.group(1) if match else None


def _item_row(html, qty, rate, amount):
    """Строка таблицы позиций PDF: Quantity / Rate / Amount."""
    return re.search(
        r'<td class="tr">' + re.escape(qty) + r'</td>\s*<td class="tr">\$' + re.escape(rate)
        + r'</td>\s*<td class="tr">\$' + re.escape(amount) + r'</td>',
        html,
    )


def _subtotals(html):
    """Subtotal каждого блока PDF по порядку."""
    return re.findall(r'Subtotal</td>\s*<td class="tr">\$([\d.]+)</td>', html)


def _auth_token(app, env, scope, labor_index=None, wo=None):
    """Токен авторизации как его создаёт send-authorization (master-коллекция)."""
    from app.extensions import get_master_db

    wo = wo or env["wo"]
    token = secrets.token_urlsafe(32)
    with app.app_context():
        get_master_db().work_order_authorizations.insert_one({
            "token": token, "shop_id": env["shop_id"], "db_name": SHOP_A_DB,
            "work_order_id": wo["_id"], "wo_number": wo["wo_number"],
            "scope": scope, "labor_index": labor_index,
            "recipient_email": "fleet@example.com", "customer_id": env["customer_id"],
            "status": "pending", "created_at": datetime.now(timezone.utc),
        })
    return token


# ---------------- контекст ----------------

def test_context_splits_shop_supply_from_labor(app, env):
    """labor_total в клиентском контексте — работа без supply; supply и
    «блок с supply» — отдельными ключами."""
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    with app.app_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], env["wo"])

    block = ctx["labors"][0]
    assert block["labor_base"] == 100
    assert block["labor_total"] == 100
    assert block["labor_total_fmt"] == "$100.00"
    assert block["labor_total_with_supply"] == 105
    assert block["shop_supply"] == 5
    assert block["parts_subtotal"] == 50
    assert block["block_total"] == 150
    assert block["block_total_with_supply"] == 155

    t = ctx["t"]
    assert t["labor_total"] == 100
    assert t["labor_total_with_supply"] == 105
    assert t["shop_supply_total"] == 5
    assert t["parts_total"] == 50
    assert t["parts_total_with_extras"] == 50
    assert t["grand_total"] == 155


def test_context_legacy_totals_without_labor_key(app, env):
    """Старые WO без ключа "labor": сохранённый labor_total — это работа БЕЗ
    supply (как в totals.normalize_totals_payload); supply прибавляется."""
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    legacy = {
        "labor_total": 100, "shop_supply_total": 5, "parts_total": 50, "grand_total": 155,
        "labors": [{"labor_total": 100, "shop_supply_total": 5, "parts_total": 50,
                    "labor_full_total": 155}],
    }
    wo = _wo_doc(env["shop_id"], env["customer_id"], env["unit_id"], legacy, wo_number=77002)
    env["db"].work_orders.insert_one(wo)

    with app.app_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], wo)

    block = ctx["labors"][0]
    assert block["labor_base"] == block["labor_total"] == 100
    assert block["labor_total_with_supply"] == 105
    assert block["block_total"] == 150
    assert block["block_total_with_supply"] == 155
    assert ctx["t"]["labor_total"] == 100
    assert ctx["t"]["labor_total_with_supply"] == 105
    assert ctx["t"]["parts_total"] == 50
    assert ctx["t"]["grand_total"] == 155


def test_context_without_shop_supply_is_unchanged(app, env):
    """WO без shop supply: обе формы labor совпадают, блок = работа + парты."""
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    totals = _totals()
    totals.update({"labor_total": 100, "shop_supply_total": 0, "grand_total": 150})
    totals["labors"][0].update({"labor_total": 100, "shop_supply_total": 0, "labor_full_total": 150})
    wo = _wo_doc(env["shop_id"], env["customer_id"], env["unit_id"], totals, wo_number=77003)
    env["db"].work_orders.insert_one(wo)

    with app.app_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], wo)

    assert ctx["labors"][0]["block_total"] == ctx["labors"][0]["block_total_with_supply"] == 150
    assert ctx["labors"][0]["labor_total"] == ctx["labors"][0]["labor_total_with_supply"] == 100
    assert ctx["t"]["labor_total"] == ctx["t"]["labor_total_with_supply"] == 100
    assert ctx["t"]["shop_supply_total"] == 0


def test_context_parts_total_is_parts_only(app, env):
    """Клиенту Parts — только запчасти, коры и misc идут своими строками;
    как в БД (запчасти + коры + misc) — parts_total_with_extras. Без ключа
    "parts" база = parts_total − коры − misc, не ниже нуля."""
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    totals = _totals()
    totals.update({"parts": 50, "parts_total": 75, "core_total": 20, "misc_total": 5, "grand_total": 180})
    totals["labors"][0].update({"parts_total": 75, "core_total": 20, "misc_total": 5, "labor_full_total": 180})
    wo = _wo_doc(env["shop_id"], env["customer_id"], env["unit_id"], totals, wo_number=77004)
    legacy = {
        "labor_total": 100, "parts_total": 75, "core_total": 20, "misc_total": 5, "grand_total": 175,
        "labors": [{"labor_total": 100, "parts_total": 75, "core_total": 20, "misc_total": 5}],
    }
    wo_legacy = _wo_doc(env["shop_id"], env["customer_id"], env["unit_id"], legacy, wo_number=77005)
    broken = {
        "labor_total": 100, "parts_total": 10, "core_total": 20, "grand_total": 110,
        "labors": [{"labor_total": 100, "parts_total": 10, "core_total": 20}],
    }
    wo_broken = _wo_doc(env["shop_id"], env["customer_id"], env["unit_id"], broken, wo_number=77006)
    env["db"].work_orders.insert_many([wo, wo_legacy, wo_broken])

    with app.app_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], wo)
        ctx_legacy = _build_wo_pdf_context(env["db"], env["shop"], wo_legacy)
        ctx_broken = _build_wo_pdf_context(env["db"], env["shop"], wo_broken)

    assert (ctx["t"]["parts_total"], ctx["t"]["parts_total_with_extras"]) == (50, 75)
    assert (ctx["t"]["core_total"], ctx["t"]["misc_total"]) == (20, 5)
    # Subtotal блока — работа + запчасти блока с его корами и misc, без supply
    assert ctx["labors"][0]["block_total"] == 175
    assert ctx["labors"][0]["block_total_with_supply"] == 180
    assert (ctx_legacy["t"]["parts_total"], ctx_legacy["t"]["parts_total_with_extras"]) == (50, 75)
    assert ctx_broken["t"]["parts_total"] == 0
    assert ctx_broken["t"]["parts_total_with_extras"] == 10


# ---------------- PDF / инвойс ----------------

def test_pdf_lists_shop_supply_once_as_own_line(logged_in, env, monkeypatch):
    html = _pdf_html(logged_in, env["wo"]["_id"], monkeypatch)

    assert _tot_row(html, "Labor") == "100.00"
    assert _tot_row(html, "Parts") == "50.00"
    assert _tot_row(html, "Shop Supplies") == "5.00"
    assert _tot_row(html, "Total") == "155.00"
    # Subtotal блока — работа + парты, без supply
    assert _subtotals(html) == ["150.00"]
    # Строка Labor в таблице позиций — тоже только работа: 2 ч × $50.00
    assert _item_row(html, "2", "50.00", "100.00")
    assert "$105.00" not in html


def test_pdf_folds_supply_into_labor_when_design_hides_it(logged_in, env, monkeypatch):
    """PDF design: show_shop_supply=False — строки нет, supply внутри Labor
    (в тоталах и в строке позиции) и в Subtotal, чтобы колонка сходилась с Total."""
    with _pdf_design(env, show_shop_supply=False):
        html = _pdf_html(logged_in, env["wo"]["_id"], monkeypatch)

    assert "Shop Supplies" not in html
    assert _tot_row(html, "Labor") == "105.00"
    assert _tot_row(html, "Total") == "155.00"
    assert _subtotals(html) == ["155.00"]
    # Строка Labor в таблице позиций — работа + supply: 2 ч × $52.50 = $105.00
    assert _item_row(html, "2", "52.50", "105.00")
    assert "$100.00" not in html


# ---------------- клиентский портал ----------------

def test_portal_page_shows_labor_and_shop_supply_separately(client, app, seed, env):
    from app.blueprints.customer_portal.routes import get_or_create_portal_token

    with app.app_context():
        token = get_or_create_portal_token(seed["shop_a"], env["customer_id"])["token"]
    resp = client.get(f"/portal/{token}/work-orders/{env['wo']['_id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert '<tr><td>Labor</td><td class="text-end">$100.00</td></tr>' in html
    assert '<tr><td>Shop Supply</td><td class="text-end">$5.00</td></tr>' in html
    assert '<td>Grand Total</td><td class="text-end">$155.00</td>' in html
    # Без налога строки Sales Tax нет
    assert "Sales Tax" not in html
    # Карточка работы: Labor без supply, сумма блока = работа + парты
    assert "Labor $100.00" in html
    assert "$150.00" in html
    assert "$105.00" not in html


# ---------------- письмо по WO ----------------

def test_work_order_email_summary_splits_shop_supply(app, env):
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    with app.test_request_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], env["wo"])
        html = render_template("emails/work_order_email.html", **ctx)

    assert re.search(r'Labor</td>\s*<td[^>]*>\$100\.00</td>', html)
    assert re.search(r'Shop Supplies</td>\s*<td[^>]*>\$5\.00</td>', html)
    assert re.search(r'Total Due</td>\s*<td[^>]*>\$155\.00</td>', html)
    assert "Sales Tax" not in html
    assert "$105.00" not in html


# ---------------- авторизация ----------------

def test_authorization_single_labor_shows_supply_line_and_full_block_total(app, env, client):
    """Один лейбор: Labor total — работа, Shop supply — своей строкой,
    Block total — полная сумма работы (с supply), которую одобряет клиент."""
    from app.blueprints.work_orders.services.authorizations import _build_authorization_context

    with app.test_request_context():
        ctx = _build_authorization_context(env["db"], env["shop"], env["wo"], "labor", 0)
        assert ctx["single_labor"]["labor_total"] == 100
        assert ctx["single_labor"]["shop_supply"] == 5
        assert ctx["single_labor"]["block_total_with_supply"] == 155
        email_html = render_template(
            "emails/authorization_email.html", **ctx,
            approve_url="https://example.test/authorize/x", recipient_email="fleet@example.com",
        )

    assert re.search(r'Labor total</td>\s*<td><strong>\$100\.00</strong></td>', email_html)
    assert re.search(r'Shop supply</td>\s*<td>\$5\.00</td>', email_html)
    assert re.search(r'Block total</td>\s*<td><strong>\$155\.00</strong></td>', email_html)

    token = _auth_token(app, env, "labor", 0)
    resp = client.get(f"/authorize/{token}")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert '<td class="label">Labor total</td><td class="value">$100.00</td>' in page
    assert '<td class="label">Shop supply</td><td class="value">$5.00</td>' in page
    assert '<td class="label">Block total</td><td class="value"><strong>$155.00</strong></td>' in page


def test_authorization_whole_wo_lists_supply_before_grand_total(app, env, client):
    """Весь WO: карточки работ без supply, «Shop supply» одной строкой перед
    Grand total — блоки + supply сходятся с итогом."""
    from app.blueprints.work_orders.services.authorizations import _build_authorization_context

    with app.test_request_context():
        ctx = _build_authorization_context(env["db"], env["shop"], env["wo"], "work_order", None)
        email_html = render_template(
            "emails/authorization_email.html", **ctx,
            approve_url="https://example.test/authorize/x", recipient_email="fleet@example.com",
        )

    assert re.search(r'Shop supply</td>\s*<td[^>]*>\$5\.00</td>', email_html)
    assert re.search(r'Grand total</td>\s*<td[^>]*>\s*\$155\.00', email_html)
    assert "$150.00" in email_html
    assert "$105.00" not in email_html
    assert "Sales tax" not in email_html

    token = _auth_token(app, env, "work_order")
    resp = client.get(f"/authorize/{token}")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "Shop supply: $5.00" in page
    assert "Grand total: $155.00" in page
    assert "Labor $100.00" in page
    assert "$150.00" in page
    assert "$105.00" not in page
    assert "Sales tax" not in page


# ---------------- несколько работ: коры, misc, налог ----------------

def test_multi_job_context_reconciles(app, env, multi):
    """Три работы: supply делится пропорционально работе (остаток — последнему
    блоку), Parts — только запчасти, коры/misc/налог — отдельно; все формы
    сходятся с grand_total."""
    from app.blueprints.work_orders.services.common import round2
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    stored = multi["totals"]
    # Контракт БД не меняется: labor_total с supply, parts_total с корами и misc.
    assert (stored["labor"], stored["labor_total"], stored["shop_supply_total"]) == (200.0, 210.0, 10.0)
    assert (stored["parts"], stored["parts_total"], stored["core_total"], stored["misc_total"]) == (70.0, 95.0, 20.0, 5.0)
    assert (stored["sales_tax_total"], stored["grand_total"]) == (6.0, 311.0)
    assert [b["shop_supply_total"] for b in stored["labors"]] == [5.0, 1.67, 3.33]

    with app.app_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], multi)

    blocks = ctx["labors"]
    assert [b["shop_supply"] for b in blocks] == [5.0, 1.67, 3.33]
    assert [b["labor_total"] for b in blocks] == [100.0, 33.33, 66.67]
    assert all(b["labor_total"] == b["labor_base"] for b in blocks)
    assert [b["labor_total_with_supply"] for b in blocks] == [105.0, 35.0, 70.0]
    assert [b["parts_subtotal"] for b in blocks] == [70.0, 25.0, 0.0]
    assert [b["core_total"] for b in blocks] == [20.0, 0.0, 0.0]
    assert [b["misc_total"] for b in blocks] == [0.0, 5.0, 0.0]
    assert [b["block_total"] for b in blocks] == [170.0, 58.33, 66.67]
    assert [b["block_total_with_supply"] for b in blocks] == [175.0, 60.0, 70.0]

    t = ctx["t"]
    assert t["labor_total"] == 200.0
    assert t["labor_total_with_supply"] == 210.0
    assert t["parts_total"] == 70.0                # только запчасти
    assert t["parts_total_with_extras"] == 95.0    # как в БД
    assert t["core_total"] == 20.0
    assert t["misc_total"] == 5.0
    assert t["shop_supply_total"] == 10.0
    assert t["sales_tax_total"] == 6.0
    assert t["grand_total"] == 311.0

    # Тождества сходимости
    assert round2(sum(b["block_total"] for b in blocks) + t["shop_supply_total"]
                  + t["sales_tax_total"]) == t["grand_total"]
    assert round2(sum(b["block_total_with_supply"] for b in blocks) + t["sales_tax_total"]) == t["grand_total"]
    assert round2(t["labor_total"] + t["parts_total"] + t["core_total"] + t["shop_supply_total"]
                  + t["misc_total"] + t["sales_tax_total"]) == t["grand_total"]


def test_multi_job_pdf_lists_each_charge_once(logged_in, env, multi, monkeypatch):
    html = _pdf_html(logged_in, multi["_id"], monkeypatch)

    assert _item_row(html, "2", "50.00", "100.00")
    assert _item_row(html, "1", "33.33", "33.33")
    assert _item_row(html, "1", "66.67", "66.67")
    assert _subtotals(html) == ["170.00", "58.33", "66.67"]
    rows = {label: _tot_row(html, label) for label in
            ("Labor", "Parts", "Core Charges", "Shop Supplies", "Misc", "Sales Tax", "Total")}
    assert rows == {"Labor": "200.00", "Parts": "70.00", "Core Charges": "20.00",
                    "Shop Supplies": "10.00", "Misc": "5.00", "Sales Tax": "6.00", "Total": "311.00"}
    assert sum(float(v) for k, v in rows.items() if k != "Total") == pytest.approx(311.0)


def test_multi_job_pdf_folds_supply_into_labor_rows_and_subtotals(logged_in, env, multi, monkeypatch):
    with _pdf_design(env, show_shop_supply=False):
        html = _pdf_html(logged_in, multi["_id"], monkeypatch)

    assert "Shop Supplies" not in html
    assert _item_row(html, "2", "52.50", "105.00")
    assert _item_row(html, "1", "35.00", "35.00")
    assert _item_row(html, "1", "70.00", "70.00")
    assert _subtotals(html) == ["175.00", "60.00", "70.00"]
    rows = {label: _tot_row(html, label) for label in
            ("Labor", "Parts", "Core Charges", "Misc", "Sales Tax", "Total")}
    assert rows == {"Labor": "210.00", "Parts": "70.00", "Core Charges": "20.00",
                    "Misc": "5.00", "Sales Tax": "6.00", "Total": "311.00"}
    assert sum(float(v) for k, v in rows.items() if k != "Total") == pytest.approx(311.0)


def test_multi_job_pdf_folds_hidden_cores_and_misc_into_parts(logged_in, env, multi, monkeypatch):
    with _pdf_design(env, show_core_charges=False, show_misc_charges=False):
        html = _pdf_html(logged_in, multi["_id"], monkeypatch)

    assert "Core Charge" not in html
    assert "Disposal fee" not in html
    assert _tot_row(html, "Core Charges") is None
    assert _tot_row(html, "Misc") is None
    assert _tot_row(html, "Parts") == "95.00"      # 70 + 20 + 5
    assert _tot_row(html, "Labor") == "200.00"
    assert _tot_row(html, "Shop Supplies") == "10.00"
    assert _tot_row(html, "Sales Tax") == "6.00"
    assert _tot_row(html, "Total") == "311.00"
    # Subtotal блоков не меняется: строки парта/кора/misc блока уже внутри
    assert _subtotals(html) == ["170.00", "58.33", "66.67"]

    # Скрыты только коры: в Parts складываются только они, Misc остаётся строкой
    with _pdf_design(env, show_core_charges=False, show_misc_charges=True):
        html = _pdf_html(logged_in, multi["_id"], monkeypatch)
    assert _tot_row(html, "Parts") == "90.00"
    assert _tot_row(html, "Misc") == "5.00"
    assert _tot_row(html, "Core Charges") is None
    assert _tot_row(html, "Total") == "311.00"


def test_multi_job_email_summary_adds_up(app, env, multi):
    from app.blueprints.work_orders.services.pdf_contexts import _build_wo_pdf_context

    with app.test_request_context():
        ctx = _build_wo_pdf_context(env["db"], env["shop"], multi)
        html = render_template("emails/work_order_email.html", **ctx)

    rows = dict(re.findall(
        r'>(Labor|Parts|Core Charges|Shop Supplies|Misc Charges|Sales Tax)</td>\s*<td[^>]*>\$([\d.]+)</td>',
        html,
    ))
    assert rows == {"Labor": "200.00", "Parts": "70.00", "Core Charges": "20.00",
                    "Shop Supplies": "10.00", "Misc Charges": "5.00", "Sales Tax": "6.00"}
    assert re.search(r'Total Due</td>\s*<td[^>]*>\$311\.00</td>', html)
    assert sum(float(v) for v in rows.values()) == pytest.approx(311.0)


def test_multi_job_portal_totals_add_up(client, app, seed, env, multi):
    from app.blueprints.customer_portal.routes import get_or_create_portal_token

    with app.app_context():
        token = get_or_create_portal_token(seed["shop_a"], env["customer_id"])["token"]
    resp = client.get(f"/portal/{token}/work-orders/{multi['_id']}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    rows = dict(re.findall(
        r'<tr><td>(Labor|Parts|Shop Supply|Misc|Cores|Sales Tax)</td><td class="text-end">\$([\d.]+)</td></tr>',
        html,
    ))
    assert rows == {"Labor": "200.00", "Parts": "70.00", "Shop Supply": "10.00",
                    "Misc": "5.00", "Cores": "20.00", "Sales Tax": "6.00"}
    assert '<td>Grand Total</td><td class="text-end">$311.00</td>' in html
    assert sum(float(v) for v in rows.values()) == pytest.approx(311.0)
    # Карточки работ: работа + запчасти блока (с корами/misc), без supply
    for amount in ("$170.00", "$58.33", "$66.67"):
        assert amount in html


def test_multi_job_authorization_whole_wo_adds_up(app, env, client, multi):
    """Весь WO: блоки + Shop supply + Sales tax = Grand total на странице и в письме."""
    from app.blueprints.work_orders.services.authorizations import _build_authorization_context

    with app.test_request_context():
        ctx = _build_authorization_context(env["db"], env["shop"], multi, "work_order", None)
        email_html = render_template(
            "emails/authorization_email.html", **ctx,
            approve_url="https://example.test/authorize/x", recipient_email="fleet@example.com",
        )

    for amount in ("$170.00", "$58.33", "$66.67"):
        assert amount in email_html
    assert re.search(r'Shop supply</td>\s*<td[^>]*>\$10\.00</td>', email_html)
    assert re.search(r'Sales tax</td>\s*<td[^>]*>\$6\.00</td>', email_html)
    assert re.search(r'Grand total</td>\s*<td[^>]*>\s*\$311\.00', email_html)

    token = _auth_token(app, env, "work_order", wo=multi)
    resp = client.get(f"/authorize/{token}")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    for amount in ("$170.00", "$58.33", "$66.67"):
        assert amount in page
    assert "Shop supply: $10.00" in page
    assert "Sales tax: $6.00" in page
    assert "Grand total: $311.00" in page
    # 170 + 58.33 + 66.67 + 10 + 6 = 311
