"""Скриншоты для Education Center (Playwright, headless Chromium).

Снимает ~150 кадров всех разделов приложения с ТЕСТОВОГО тенанта и кладёт их
в app/static/img/education/<id>.webp. Подписи к кадрам — единственный
источник для app/education/shots.py (генерируется флагом --write-catalog):
их читают уроки (alt/figcaption) и AI-помощник.

Запуск (dev-сервер уже поднят на BASE_URL, тестовый тенант есть в базе):

    set EDU_EMAIL=owner@roobico-test.com
    set EDU_PASSWORD=...
    python scripts/education_screenshots.py --write-catalog
    python scripts/education_screenshots.py --only parts-        # префикс id
    python scripts/education_screenshots.py --list                # что умеет

Переменные: BASE_URL (http://127.0.0.1:5000), EDU_EMAIL / EDU_PASSWORD
(owner), EDU_MECH_EMAIL / EDU_MECH_PASSWORD (механик; пароль по умолчанию —
как у owner). Скрипт создаёт немного demo-данных (parts order, stocktake,
тайм-лог механика) — запускать только на dev-базе.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image
from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "app" / "static" / "img" / "education"
CATALOG_PATH = ROOT / "app" / "education" / "shots.py"

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")
OWNER_EMAIL = os.environ.get("EDU_EMAIL", "owner@roobico-test.com")
OWNER_PASSWORD = os.environ.get("EDU_PASSWORD", "")
MECH_EMAIL = os.environ.get("EDU_MECH_EMAIL", "mech1@roobico-test.com")
MECH_PASSWORD = os.environ.get("EDU_MECH_PASSWORD", OWNER_PASSWORD)

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 430, "height": 932}
MAX_WIDTH = 1440
WEBP_QUALITY = 82

HL_CSS = """
.edu-hl { outline: 3px solid #ef4444 !important; outline-offset: 3px !important;
          border-radius: 6px; box-shadow: 0 0 0 7px rgba(239,68,68,.22) !important; }
"""

state: dict = {}  # discovered ids: customer, unit, wo, wo_paid, estimate, order...


# ── Shot model ────────────────────────────────────────────────────────

Prep = Callable[[Page], None]


@dataclass
class Shot:
    id: str
    caption: str
    path: str | None = None            # None → продолжаем на текущей странице
    who: str = "owner"                 # owner | anon | mechanic | owner-dark
    prep: Prep | None = None
    element: str | None = None         # селектор для обрезки (иначе viewport)
    full_page: bool = False
    hl: list[str] = field(default_factory=list)
    wait: int = 700
    viewport: dict | None = None       # None → по контексту (desktop/mobile)
    optional: bool = False             # не ругаться, если элемента нет


SHOTS: list[Shot] = []


def S(id: str, caption: str, path: str | None = None, **kw) -> Shot:
    shot = Shot(id=id, caption=caption, path=path, **kw)
    SHOTS.append(shot)
    return shot


# ── Prep helpers ──────────────────────────────────────────────────────

def seq(*fns: Prep) -> Prep:
    def run(page: Page):
        for fn in fns:
            fn(page)
    return run


def click(sel: str, timeout: int = 20000) -> Prep:
    return lambda page: page.locator(sel).first.click(timeout=timeout)


def click_text(text: str, tag: str = "button", timeout: int = 20000) -> Prep:
    return lambda page: page.locator(f"{tag}:has-text(\"{text}\"):visible").first.click(timeout=timeout)


def jsclick(sel: str, timeout: int = 20000) -> Prep:
    """Клик через DOM (el.click()): для кнопок в широких таблицах, которые
    уехали за правый край окна внутри .table-responsive — Playwright считает
    их невидимыми и не кликает, а bootstrap-обработчики DOM-клик принимают."""
    def run(page: Page):
        loc = page.locator(sel).first
        loc.wait_for(state="attached", timeout=timeout)
        loc.evaluate("el => { el.scrollIntoView({block: 'center', inline: 'center'}); el.click(); }")
    return run


def fill(sel: str, value: str) -> Prep:
    return lambda page: page.locator(sel).first.fill(value)


def type_slow(sel: str, value: str, delay: int = 40) -> Prep:
    def run(page: Page):
        loc = page.locator(sel).first
        loc.click()
        loc.type(value, delay=delay)
    return run


def check(sel: str) -> Prep:
    return lambda page: page.locator(sel).first.check()


def select(sel: str, value: str) -> Prep:
    return lambda page: page.locator(sel).first.select_option(value)


def wait(ms: int) -> Prep:
    return lambda page: page.wait_for_timeout(ms)


def wait_for(sel: str, timeout: int = 20000) -> Prep:
    return lambda page: page.wait_for_selector(sel, timeout=timeout)


def js(code: str) -> Prep:
    return lambda page: page.evaluate(code)


def scroll_to(sel: str) -> Prep:
    return lambda page: page.locator(sel).first.scroll_into_view_if_needed()


def modal(modal_id: str, trigger: str | None = None) -> Prep:
    """Открыть bootstrap-модалку: кликом по триггеру или напрямую через JS."""
    def run(page: Page):
        if trigger:
            page.locator(trigger).first.click(timeout=20000)
        else:
            page.evaluate(
                "id => bootstrap.Modal.getOrCreateInstance(document.getElementById(id)).show()",
                modal_id,
            )
        page.wait_for_selector(f"#{modal_id}.show", timeout=20000)
        page.wait_for_timeout(700)
    return run


def dropdown_item(toggle_sel: str, item_sel: str) -> Prep:
    def run(page: Page):
        page.locator(toggle_sel).first.click()
        page.wait_for_timeout(250)
        page.locator(item_sel).first.click()
    return run


def edit_mode(page: Page):
    btn = page.locator("#editWorkOrderBtn")
    if btn.count() and btn.first.is_visible():
        btn.first.click()
        page.wait_for_timeout(600)


def st(key: str, suffix: str = "") -> Callable[[], str]:
    return lambda: state.get(key, "") + suffix


# ── Discovery of demo ids ─────────────────────────────────────────────

def discover(page: Page) -> None:
    page.goto(f"{BASE}/customers?status=active", wait_until="load")
    hrefs = page.eval_on_selector_all("a[href^='/customers/']", "els => els.map(e => e.getAttribute('href'))")
    cust_ids = [re.match(r"^/customers/([0-9a-f]{24})$", h or "") for h in hrefs]
    cust_ids = [m.group(1) for m in cust_ids if m]
    # Предпочитаем компанию (не walk-in), чтобы карточка выглядела наполненной.
    chosen = None
    for cid in dict.fromkeys(cust_ids):
        row_text = page.locator(f"a[href='/customers/{cid}']").first.locator("xpath=ancestor::tr").inner_text() if page.locator(f"a[href='/customers/{cid}']").count() else ""
        if "Globeks" in row_text or "Trucking" in row_text or "Logistics" in row_text:
            chosen = cid
            break
    state["customer"] = chosen or (cust_ids[0] if cust_ids else "")

    if state["customer"]:
        page.goto(f"{BASE}/customers/{state['customer']}?tab=units", wait_until="load")
        page.wait_for_timeout(500)
        unit_hrefs = page.eval_on_selector_all("a[href*='/units/']", "els => els.map(e => e.getAttribute('href'))")
        for h in unit_hrefs:
            m = re.search(r"/units/([0-9a-f]{24})", h or "")
            if m:
                state["unit"] = m.group(1)
                break

    page.goto(f"{BASE}/work_orders?date_preset=all_time", wait_until="load")
    page.wait_for_timeout(800)
    rows = page.evaluate("""() => [...document.querySelectorAll('#content-work-orders tbody tr')].map(tr => {
        const a = tr.querySelector("a[href*='work_order_id=']");
        return a ? { href: a.getAttribute('href'), text: tr.innerText } : null; }).filter(Boolean)""")
    for r in rows:
        m = re.search(r"work_order_id=([0-9a-f]{24})", r["href"])
        if not m:
            continue
        wid = m.group(1)
        state.setdefault("wo_any", wid)
        if "1007" in r["text"] and "wo" not in state:
            state["wo"] = wid
        if re.search(r"\bPaid\b", r["text"]) and "Unpaid" not in r["text"] and "wo_paid" not in state:
            state["wo_paid"] = wid
        if "In Progress" in r["text"] and "wo_inprogress" not in state:
            state["wo_inprogress"] = wid
        # Открытый WO без механиков/таймеров: на нём в режиме Edit видна
        # кнопка Assign (при наличии tracked time она прячется).
        if ("Unpaid" in r["text"] or "Open" in r["text"]) and not re.search(r"[●✓]|Mechanic done|In Progress", r["text"]) \
                and "wo_open" not in state:
            state["wo_open"] = wid
    state.setdefault("wo", state.get("wo_any", ""))
    state.setdefault("wo_open", state["wo"])

    # Клиент и юнит берём с выбранного WO: у них гарантированно есть история
    # (карточка клиента и страница юнита выглядят наполненными).
    if state.get("wo"):
        page.goto(f"{BASE}/work_orders/details?work_order_id={state['wo']}", wait_until="load")
        page.wait_for_timeout(1500)
        try:
            cust = page.eval_on_selector("#customerSelect", "e => e.value") or ""
            unit = page.eval_on_selector("#unitSelect", "e => e.value") or ""
            if re.match(r"^[0-9a-f]{24}$", cust):
                state["customer"] = cust
            if re.match(r"^[0-9a-f]{24}$", unit):
                state["unit"] = unit
        except Exception:
            pass

    # Вкладка Estimates грузится fetch'ем после клика (URL-параметр tab её не открывает).
    page.goto(f"{BASE}/work_orders?date_preset=all_time", wait_until="load")
    page.wait_for_timeout(800)
    try:
        page.locator("#tab-estimates").click(timeout=10000)
        page.wait_for_selector("#content-estimates a[href*='work_order_id=']", timeout=15000)
    except PWTimeout:
        pass
    page.wait_for_timeout(500)
    est = page.eval_on_selector_all("#content-estimates a[href*='work_order_id=']", "els => els.map(e => e.getAttribute('href'))")
    for h in est:
        m = re.search(r"work_order_id=([0-9a-f]{24})", h or "")
        if m:
            state["estimate"] = m.group(1)
            break
    print("discovered:", {k: v for k, v in state.items()})


# ── Demo data helpers (dev only) ──────────────────────────────────────

def fill_order_modal(page: Page) -> None:
    """Заполнить диалог заказа: первый вендор по поиску + первая запчасть."""
    page.locator("#order_vendor_search").first.click()
    page.locator("#order_vendor_search").first.type("Fleet", delay=40)
    page.wait_for_timeout(900)
    items = page.locator("#orderVendorDropdown").locator("a, button, li, .dropdown-item")
    if items.count():
        items.first.click()
    page.wait_for_timeout(400)
    # Поиск по триграммам search_terms — нужно минимум 3 символа.
    page.locator("#partSearch").first.click()
    page.locator("#partSearch").first.type("bra", delay=40)
    page.wait_for_timeout(1200)
    parts = page.locator("#partSearchDropdown").locator("a, button, li, .parts-dd-item")
    if parts.count():
        parts.first.click()
        page.wait_for_timeout(300)
        page.locator("#partSearch").first.fill("")
        page.locator("#partSearch").first.type("fil", delay=40)
        page.wait_for_timeout(1000)
        parts2 = page.locator("#partSearchDropdown").locator("a, button, li, .parts-dd-item")
        if parts2.count():
            parts2.first.click()
    page.wait_for_timeout(400)
    qty = page.locator("#orderItemsBody input").first
    if qty.count():
        try:
            qty.fill("4")
        except Exception:
            pass
    page.wait_for_timeout(300)


def create_order(page: Page) -> None:
    fill_order_modal(page)
    page.locator("#createOrderBtn").first.click()
    page.wait_for_timeout(1500)


def create_stocktake(page: Page) -> None:
    page.locator("#stocktakeCreateModal input[type=text]").first.fill("Shelf count — Education demo")
    page.locator("#stocktakeCreateModal button[type=submit], #stocktakeCreateModal .btn-primary").first.click()
    page.wait_for_load_state("load")
    page.wait_for_timeout(800)
    m = re.search(r"/parts/stocktakes/([0-9a-f]{24})", page.url)
    if m:
        state["stocktake"] = m.group(1)


def count_some_lines(page: Page) -> None:
    inputs = page.locator("#stItemsTable input[type=number]")
    n = min(inputs.count(), 3)
    for i in range(n):
        try:
            inputs.nth(i).fill(str(5 + i))
            page.wait_for_timeout(150)
            row_btn = inputs.nth(i).locator("xpath=ancestor::tr//button").first
            if row_btn.count():
                row_btn.click()
                page.wait_for_timeout(400)
        except Exception:
            pass


def write_small_csv() -> str:
    tmp = Path(tempfile.gettempdir()) / "roobico_edu_customers.csv"
    tmp.write_text(
        "Company Name,Contact First Name,Contact Last Name,Phone,Email,Address,Taxable\n"
        "Prairie Freight LLC,Dana,Miller,+1 555 010 7788,dana@prairie.example,\"120 W Lake St, Chicago, IL 60601\",yes\n"
        "Lakeside Towing,Ben,Ortiz,+1 555 010 4411,ben@lakeside.example,\"800 N Clark St, Chicago, IL 60610\",no\n",
        encoding="utf-8",
    )
    return str(tmp)


def upload_csv(page: Page) -> None:
    page.locator("#importFile").set_input_files(write_small_csv())
    page.locator("#importUploadBtn").click()
    page.wait_for_selector("#importStep2", state="visible", timeout=10000)
    page.wait_for_timeout(500)


def ask_assistant(page: Page) -> None:
    # Панель помнит состояние в sessionStorage: если уже открыта — не трогаем
    # кнопку (повторный клик закрыл бы её).
    if page.locator("#assistantPanel.d-none").count():
        page.locator("#assistantToggleBtn").click()
    page.wait_for_selector("#assistantPanel:not(.d-none)", timeout=10000)
    page.locator("#assistantInput").fill("How do I turn an estimate into a work order?")
    page.locator("#assistantSendBtn").click()
    # Ждём конца стрима (кнопка Send снова активна), максимум 45 с.
    for _ in range(90):
        page.wait_for_timeout(500)
        if not page.locator("#assistantSendBtn").is_disabled():
            break
    page.wait_for_timeout(800)


def mech_open_first_wo(page: Page) -> None:
    card = page.locator("#mechWoList a, #mechWoList .mech-card, #mechWoList [href*='/mechanic/work_orders/']").first
    card.click(timeout=10000)
    page.wait_for_load_state("load")
    page.wait_for_timeout(1200)


def mech_confirm_mileage(page: Page) -> None:
    modal_el = page.locator("#mechMileageModal.show")
    if modal_el.count():
        inp = page.locator("#mechMileageInput")
        if inp.count() and not (inp.input_value() or "").strip():
            inp.fill("184250")
        page.locator("#mechMileageConfirmBtn").click()
        # Тост «Mileage saved» живёт ~3.5 с — ждём, чтобы не попал в кадр.
        page.wait_for_timeout(4200)


def mech_start_timer(page: Page) -> None:
    btn = page.locator("#mechLaborsContainer button:has-text('Start')").first
    if btn.count():
        btn.click()
        page.wait_for_timeout(2500)


def mech_stop_timer(page: Page) -> None:
    btn = page.locator("#mechTimerBarStopBtn, #mechLaborsContainer button:has-text('Stop')").first
    if btn.count() and btn.is_visible():
        btn.click()
        page.wait_for_timeout(1000)


# ── Shot list ─────────────────────────────────────────────────────────

# Getting started / login (anonymous)
S("login-page", "The Roobico login page: the Log in panel on the left, the product tour and pricing on the right.",
  "/", who="anon")
S("login-panel", "Log in panel: email, password with the show/hide eye, the Forgot password link and the Register link for new shops.",
  "/", who="anon", element=".land-login")
S("login-contact-tab", "Contact us tab on the login page: the support phone number and a message form (name, email, phone, company, topic, message).",
  "/", who="anon", prep=click("#tabContact"), element=".land-login")
S("login-register-modal", "Create your account form: company name, phone and address, owner's name, email and password — free for 1 location + 1 user.",
  "/", who="anon", prep=seq(click("[data-open-register]"), wait(600)), element="#viewRegister")
S("login-forgot-password", "Forgot password page: enter your email to receive a reset link (valid 1 hour).",
  "/forgot-password", who="anon")
S("login-pricing", "Pricing on the login page: free tier, per extra location, per extra user and per mechanic, annual discount.",
  "/", who="anon", prep=seq(scroll_to("#pricing"), wait(400)), element="#pricing")

# Navigation (owner)
S("app-shell", "The app layout: sidebar navigation on the left, top bar with your name, organization, Active shop switcher and Logout, page content on the right.",
  "/dashboard", hl=[".app-sidebar-col", ".app-topbar"])
S("sidebar-expanded", "Expanded sidebar: global search, every section you have access to, the Roobico AI Helper button and the theme toggle at the bottom.",
  "/dashboard", element=".app-sidebar-col")
S("sidebar-collapsed", "Collapsed sidebar (hamburger button): icons only, more room for tables. The state is remembered in your browser.",
  "/dashboard", prep=seq(click("#sidebarToggleBtn"), wait(400)), element=".app-sidebar-col")
S("topbar-shop-switcher", "Top bar: the Active shop dropdown switches between your locations; every page shows only the active shop's data.",
  "/dashboard", element=".app-topbar", hl=[".topbar-shop-select"])
S("global-search", "Global search in the sidebar: type 2+ characters to find customers, units (by number or VIN), parts, work orders, vendors and parts orders.",
  "/dashboard", prep=seq(type_slow("#globalSearchInput", "test"), wait(1500)), hl=["#globalSearchResults"])
S("table-columns-menu", "Table settings (the small ⚙ above any table): hide or show columns and Reset table; drag header dividers to resize, click headers to sort.",
  "/customers", prep=seq(click(".ft-gear"), wait(400)), hl=[".ft-menu"])
S("theme-dark-dashboard", "The same dashboard in the dark theme — switch with the theme toggle at the bottom of the sidebar.",
  "/dashboard", who="owner-dark")

# Dashboard
S("dashboard-overview", "Dashboard: revenue and payments cards, paid vs unpaid donut, parts orders, monthly goals and the mechanic hours chart for the selected period.",
  "/dashboard", full_page=True)
S("dashboard-filters", "Dashboard period filter: presets from Today to All Time, or a custom date range; Reset returns to the default.",
  "/dashboard", hl=[".date-preset-select"])
S("dashboard-quick-actions", "Quick actions on the dashboard: Create WO, Create PO, New Customer and New Vendor open the right form in one click.",
  "/dashboard", hl=[".dashboard-quick-action-btn"])
S("dashboard-goals-modal", "Monthly Goals dialog (Edit Goals): targets for labor, parts sales and total revenue that drive the goal rings.",
  "/dashboard", prep=modal("dashGoalsModal", "#dashGoalsEditBtn"), element="#dashGoalsModal .modal-dialog")

# Customers
S("customers-list", "Customers list: search by any field, the Active / Inactive / All filter, Company, Name, Phone, Email, Current Balance and Open.",
  "/customers", hl=["button[data-bs-target='#createCustomerModal']"])
S("customers-search-filter", "Search box and the status filter above the customers table — the same filter exists on Vendors, Parts, Users and Units.",
  "/customers", element="form.row", hl=["select[name=status]"])
S("customer-add-modal", "Add Customer dialog: company name, Taxable switch, Pricing Scale, Override part selling price, address with autocomplete and the contacts block.",
  "/customers", prep=modal("createCustomerModal", "button[data-bs-target='#createCustomerModal']"), element="#createCustomerModal .modal-dialog")
S("customer-add-modal-filled", "Add Customer with the fields filled in: a company with its address and main contact — at least a company name or a named contact is required.",
  "/customers", prep=seq(modal("createCustomerModal", "button[data-bs-target='#createCustomerModal']"),
                         fill("#customerCompanyName", "Prairie Freight LLC"),
                         fill("#customerAddress", "120 W Lake St, Chicago, IL 60601"),
                         js("""() => { const f = document.querySelector('#customerCreateContactsForm');
                              const ins = f ? f.querySelectorAll('input') : [];
                              const vals = ['Dana', 'Miller', '+1 (555) 010-7788', 'dana@prairie.example'];
                              ins.forEach((i, idx) => { if (i.type !== 'checkbox' && i.type !== 'radio' && vals[idx] !== undefined) { i.value = vals[idx]; i.dispatchEvent(new Event('input', {bubbles:true})); } }); }"""),
                         wait(300)),
  element="#createCustomerModal .modal-dialog")
S("customer-page", "Customer page: summary card (balance, labor rate, pricing scale), header buttons Back / History / Portal Link / Create Work Order and the Work Orders tab.",
  lambda: f"/customers/{state['customer']}", hl=["#sendPortalLinkBtn"])
S("customer-page-units-tab", "Customer → Units tab: the fleet with unit number, VIN, year/make/model, mileage, Inspection buttons and Add Unit.",
  lambda: f"/customers/{state['customer']}?tab=units")
S("customer-page-payments-tab", "Customer → Payments tab: every payment across the customer's work orders.",
  lambda: f"/customers/{state['customer']}?tab=payments")
S("customer-page-estimates-tab", "Customer → Estimates tab: quotes saved as estimates for this customer.",
  lambda: f"/customers/{state['customer']}?tab=estimates")
S("customer-page-details-tab", "Customer → Details tab: the edit form — contacts with a main contact, address, Taxable, Default Labor Rate, Pricing Scale, Override part selling price, attachments and Deactivate Customer.",
  lambda: f"/customers/{state['customer']}?tab=details", full_page=True)
S("customer-portal-link-prompt", "Portal Link: enter one or more emails and the customer receives a read-only portal link (valid 30 days, renews while in use).",
  lambda: f"/customers/{state['customer']}", prep=seq(click("#sendPortalLinkBtn"), wait(700)), element=".swal2-popup, .modal.show .modal-dialog")
S("unit-add-modal", "Create New Unit dialog: Unit Number, VIN, Year, Make, Model, Type and Mileage — the same form from the customer page and from a work order.",
  lambda: f"/customers/{state['customer']}?tab=units", prep=modal("createUnitModal", "button:has-text('Add Unit')"), element="#createUnitModal .modal-dialog")
S("unit-add-modal-vin", "Typing a 17-character VIN fills Year, Make, Model and Type automatically from the national VIN decoder.",
  lambda: f"/customers/{state['customer']}?tab=units",
  prep=seq(modal("createUnitModal", "button:has-text('Add Unit')"), type_slow("#unitVinInput", "1FUJGLDR9CSBF4567", 30), wait(3500)),
  element="#createUnitModal .modal-dialog", hl=["#unitYearInput", "#unitMakeInput", "#unitModelInput"])
S("unit-page", "Unit page: header with unit number, year / make / model and VIN, buttons Files / Recalls / Annual Inspection / Create Work Order, the inspection card and the full service history.",
  lambda: f"/customers/{state['customer']}/units/{state['unit']}")
S("unit-page-labor-details", "Show labor details on a unit's history: each job with its hours badge, labor total and a parts table with quantities and prices.",
  lambda: f"/customers/{state['customer']}/units/{state['unit']}", prep=seq(wait(1200), jsclick(".js-expand-wo-labors"), wait(1200)), full_page=True, optional=True)
S("unit-page-details-tab", "Unit → Details tab: edit the unit, Deactivate / Activate Unit, attachments and Create Work Order.",
  lambda: f"/customers/{state['customer']}/units/{state['unit']}?tab=details")
S("unit-recalls-modal", "NHTSA Recalls for the unit: open safety recalls with a NEW badge for ones you have not seen; a nightly job emails customers automatically.",
  lambda: f"/customers/{state['customer']}/units/{state['unit']}", prep=seq(click(".js-open-recalls-modal"), wait_for("#unitRecallsModal.show"), wait(3000)),
  element="#unitRecallsModal .modal-dialog")
S("unit-inspection-modal", "Annual Vehicle Inspection from the unit page: VIN and carrier prefilled, vehicle type presets, the component checklist and live PDF preview.",
  lambda: f"/customers/{state['customer']}/units/{state['unit']}", prep=seq(click_text("Annual Inspection"), wait_for("#annualInspectionModal.show"), wait(1500)),
  element="#annualInspectionModal .modal-dialog")

# Work orders
S("wo-list", "Work Orders list (All Time): WO #, Customer, Date, Unit, Mileage, Labor, Parts, Tax, Total, Balance, status badge and the Pay / Edit / Delete actions; Create Work Order and Bulk Payment in the header.",
  "/work_orders?date_preset=all_time", hl=["a[href='/work_orders/details']", "#bulkPaymentBtn"])
S("wo-list-filters", "Work order filters: search by any field, paid status (All / Paid / Unpaid / In Progress) and date presets with a custom range.",
  "/work_orders?date_preset=all_time", element=".wo-filter-form, form.row", hl=[".date-preset-select"])
S("wo-list-totals", "Footer totals for the current filter: Labor, Parts, Tax, Total and Unpaid — estimates are excluded.",
  "/work_orders?date_preset=all_time", element=".wo-pagination-row", optional=True)
S("wo-list-payments-tab", "Work Orders → Payments tab: every payment recorded on any work order with its own search and date filter and a totals row.",
  "/work_orders?date_preset=all_time", prep=seq(click("#tab-payments"), wait(1500)))
S("wo-list-estimates-tab", "Work Orders → Estimates tab: all quotes; Edit opens the estimate page, totals are separate from real work orders.",
  "/work_orders?date_preset=all_time", prep=seq(click("#tab-estimates"), wait(1500)))
S("wo-bulk-payment-modal", "Bulk Payment: pick a customer, enter the amount received, click Auto-Distribute to spread it across the oldest unpaid invoices, adjust and record all at once.",
  "/work_orders?date_preset=all_time", prep=seq(wait(1500), click("#bulkPaymentBtn"), wait_for("#bulkPaymentModal.show"), wait(1500)), element="#bulkPaymentModal .modal-dialog")
S("wo-pay-modal-list", "Record Payment from the list (Pay button): invoice total, already paid, remaining balance, amount, method, date, notes and attachments.",
  "/work_orders?date_preset=all_time", prep=seq(wait(2500), jsclick(".js-mark-paid"), wait_for("#paymentModalList.show"), wait(800)), element="#paymentModalList .modal-dialog")
S("wo-create-page", "Create Work Order page: customer and unit pickers, Mileage (required), Work Order Date, the first labor block, parts table, totals and the Create Work Order button with its dropdown.",
  "/work_orders/details")
S("wo-create-with-customer", "Customer and unit selected: the Customer Info panel with Open profile, the unit row with Recalls and Parts orders buttons, mileage filled from the unit.",
  lambda: f"/work_orders/details?customer_id={state['customer']}&unit_id={state['unit']}", prep=wait(1200))
S("wo-create-dropdown", "The Create Work Order dropdown: Save In Progress, Save as Estimate and Create Annual Inspection.",
  "/work_orders/details", prep=seq(click("#createWorkOrderDropdownToggle"), wait(300)), hl=[".dropdown-menu.show"])
S("wo-details", "An existing work order: number and status, Transfer / History / Back, customer & unit section, labor blocks, parts, totals and the action buttons (Paid, Email, Download PDF, WO Cost, Delete, Edit).",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=wait(800))
S("wo-details-full", "The whole work order page from top to bottom.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=wait(800), full_page=True)
S("wo-labor-block", "A labor block (job): Labor Description, Hours, Labor Rate, Labor Total override, the Mechanics line with Assign, the ⋮ menu and labor attachments.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), edit_mode),
  element="xpath=//*[contains(@class,'labor') and .//input[contains(@class,'labor-description')]][1]", optional=True)
S("wo-labor-menu", "Labor block menu (⋮): Add Preset, Describe Issue and Send for Authorization for this job only.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}",
  prep=seq(wait(800), edit_mode, click("xpath=(//input[contains(@class,'labor-description')]/ancestor::*[contains(@class,'card')][1]//button[@data-bs-toggle='dropdown'])[1]"), wait(300)),
  hl=[".dropdown-menu.show"], optional=True)
S("wo-assign-mechanics-modal", "Assign Mechanics: tick one or several mechanics; with several, percentages split evenly and can be edited — this split drives the Mechanic Hours report.",
  lambda: f"/work_orders/details?work_order_id={state['wo_open']}",
  prep=seq(wait(1500), edit_mode, wait(600), jsclick(".laborAssignBtn"), wait_for("#assignMechanicsModal.show"), wait(800)),
  element="#assignMechanicsModal .modal-dialog")
S("wo-mechanic-hours-modal", "Mechanic hours (pencil next to the Mechanics line): the job's timer sessions — who, when, how long — edit a duration or delete a wrong session.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), modal("woTimeLogsModal"), wait(800)),
  element="#woTimeLogsModal .modal-dialog", optional=True)
S("wo-parts-table", "Parts on the work order: Part Number search, Description, Qty, Cost, Price, Core toggle and line totals; + Add Misc Charge below.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), edit_mode),
  element="xpath=//input[contains(@class,'part-number')]/ancestor::*[contains(@class,'card')][1]", optional=True)
S("wo-parts-search-dropdown", "Part search dropdown: type 3+ characters — catalog results with stock and avg cost, cross-reference alternates, and + One-time part for a manual line.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}",
  prep=seq(wait(1500), edit_mode, wait(400), scroll_to("xpath=(//input[contains(@class,'part-number')])[last()]"),
           type_slow("xpath=(//input[contains(@class,'part-number')])[last()]", "bra", 60), wait(1800)),
  hl=[".parts-dd"], optional=True)
S("wo-add-misc-charge-modal", "Add Misc Charge: description, quantity, price and the Taxable checkbox — for shop fees, disposal, freight and similar.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), edit_mode, click("#addMiscChargePartBtn"), wait_for("#addMiscChargeModal.show"), wait(400)),
  element="#addMiscChargeModal .modal-dialog")
S("wo-add-preset-modal", "Add Preset: choose a service template to insert into this labor block — its labor hours, rate and parts are added with live catalog prices.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}",
  prep=seq(wait(800), edit_mode, modal("addPresetModal"), wait(600)), element="#addPresetModal .modal-dialog")
S("wo-describe-issue-modal", "Describe Issue: type the mechanic's notes and AI rewrites them into clear, professional English for the customer; the text prints on the PDF as the reported issue.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), edit_mode, modal("describeIssueModal"), wait(400)), element="#describeIssueModal .modal-dialog")
S("wo-totals", "Totals box: Shop supply (editable with reset), Taxable toggle, labor total, parts total (parts + cores + misc), sales tax and the grand total.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=wait(1200),
  element="xpath=//*[contains(@class,'wo-totals-row')]/ancestor::div[contains(@class,'card')][1]", optional=True)
S("wo-cost-modal", "Work Order Cost: enter each mechanic's hourly cost (stored only in your browser) to see cost, revenue and profit per job and for the whole work order.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), click("#woCostBtn"), wait_for("#woCostModal.show"), wait(600)), element="#woCostModal .modal-dialog")
S("wo-transfer-modal", "Transfer work order: pick the right customer — the unit follows automatically and labor rates, part prices and the tax flag are recalculated for the new customer.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), click("#transferWoBtn"), wait_for("#transferWoModal.show"), wait(500)), element="#transferWoModal .modal-dialog")
S("wo-payment-modal", "Record Payment on the work order page (Paid button): the amount is pre-filled with the remaining balance; choose Cash, Check, Credit Card, ACH or Other, add a check photo, send a receipt.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), click("#paidWorkOrderBtn"), wait_for("#paymentModal.show"), wait(600)), element="#paymentModal .modal-dialog")
S("wo-email-modal", "Email Work Order: tick recipients from the customer's contacts, add a new address on the fly (New badge), edit or delete contacts with the pencil / trash icons; the PDF and a portal link are attached.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), click("#emailWorkOrderBtn"), wait_for("#emailContactsModal.show"), wait(800)), element="#emailContactsModal .modal-dialog")
S("wo-send-authorization-modal", "Send for Authorization: the customer gets the PDF by email with Approve / Decline buttons; the result shows as a green ✓ Authorized or red ✕ Declined badge on the work order.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(1500), jsclick("#sendWoAuthBtn"), wait(1800)), element=".swal2-popup || .modal.show .modal-dialog")
S("wo-parts-orders-block", "Parts orders block under the unit row: linked vendor orders with status and payment status, Receive and Pay buttons, the usage check chips and Create parts order.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), click("#woPartsOrdersToggle"), wait(900)), hl=["#woPartsOrdersBlock"])
S("wo-attachments", "Work Order Attachments: upload photos and documents to the work order itself; each labor block and each payment has its own attachments too.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=seq(wait(800), click("button[data-bs-target='#woAttCollapse']"), wait(700)), hl=["#woAttCollapse"])
S("wo-annual-inspection-modal", "Annual Vehicle Inspection (49 CFR 396) from the work order: carrier and inspector fields, report number, vehicle type presets, the OK / NR / NA checklist and the live PDF preview.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}",
  prep=seq(wait(800), edit_mode, dropdown_item("#createWorkOrderDropdownToggle, #saveWorkOrderDropdownToggle, .dropdown-toggle-split", "#saveAnnualInspectionMenuBtn, #createAnnualInspectionBtn"), wait_for("#annualInspectionModal.show"), wait(1500)),
  element="#annualInspectionModal .modal-dialog", optional=True)
S("wo-save-dropdown", "Save dropdown on an existing work order: Save In Progress, Save as Estimate (or Convert to Work Order on an estimate) and Create Annual Inspection.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}",
  prep=seq(wait(800), edit_mode, click("#createWorkOrderDropdownToggle, #saveWorkOrderDropdownToggle, .dropdown-toggle-split"), wait(300)), hl=[".dropdown-menu.show"], optional=True)
S("wo-paid-locked", "A paid work order is locked: the status badge reads Paid, Edit is replaced by Unpaid — reverting deletes its payment records.",
  lambda: f"/work_orders/details?work_order_id={state.get('wo_paid', state['wo'])}", prep=wait(800), optional=True)
S("wo-estimate-page", "An estimate: the Estimate badge next to the number; the Save dropdown offers Convert to Work Order, which deducts the parts from stock at that moment.",
  lambda: f"/work_orders/details?work_order_id={state.get('estimate', state['wo'])}", prep=wait(800), optional=True)
S("wo-history-button", "History (top-right of a work order or customer) opens the Activity Journal filtered to that record — who changed what and when.",
  lambda: f"/work_orders/details?work_order_id={state['wo']}", prep=wait(800), hl=["a[href*='/reports/audit?entity_type=work_order']"])

# Calendar
S("calendar-week", "Calendar week view: appointments and work-order blocks per day, mechanic markers with initials, Today and the ‹ › arrows, Week / By mechanic switches, mechanic filter and the Work orders toggle.",
  "/calendar", prep=wait(1200))
S("calendar-by-mechanic", "By mechanic view: one column per mechanic for a single day; drag a work order chip from the Not scheduled strip into a lane to plan it.",
  "/calendar", prep=seq(wait(800), click("#calViewMechanics"), wait(1200)))
S("calendar-controls", "Calendar controls: navigation, view switches, the mechanic filter and the Work orders switch that shows WOs on the days mechanics actually worked.",
  "/calendar", prep=wait(800), hl=["#calMechanicFilter", "#calShowWO", "#calViewMechanics"])
S("calendar-appointment-modal", "New Appointment: title, date, start and end, customer, unit, mechanic, presets, status — and Create Work Order to turn the plan into a WO with everything carried over.",
  "/calendar", prep=seq(wait(800), modal("appointmentModal"), wait(500)), element="#appointmentModal .modal-dialog")
S("calendar-settings-modal", "Calendar Settings: the list of appointment statuses with their colors; add your own.",
  "/calendar", prep=seq(wait(800), modal("calSettingsModal", "#calSettingsBtn"), wait(500)), element="#calSettingsModal .modal-dialog")
S("calendar-wo-event-modal", "Work order block details: customer, unit, mechanics and hours, state, Open work order, Add to schedule and Planned for this day.",
  "/calendar", prep=seq(wait(800), modal("woEventModal"), wait(400)), element="#woEventModal .modal-dialog", optional=True)

# Parts
S("parts-list", "Parts list: search by any field, Active / Inactive / All, columns Part #, Description, Reference, In stock, Avg cost, Last price, Vendor, Category, Location and the row actions; Order and Add Part in the header.",
  "/parts/", hl=["button[data-bs-target='#orderModal']", "button[data-bs-target='#createPartModal']"])
S("parts-list-full", "The full Parts tab including the inventory cost totals under the table.",
  "/parts/", full_page=True)
S("parts-row-actions", "Row actions on a part: Cross refs, History, Locations (tracked parts only), Edit and Deactivate.",
  "/parts/", hl=[".partCrossRefsBtn", ".partHistoryBtn", ".partLocationsBtn", ".editPartBtn"])
S("parts-add-modal", "Create new part: part number (required), reference, description, In stock, Average cost, Vendor, Category, Location and the toggles for selling price, do-not-track, core charge and misc charges.",
  "/parts/", prep=modal("createPartModal", "button[data-bs-target='#createPartModal']"), element="#createPartModal .modal-dialog")
S("parts-add-modal-toggles", "Part toggles switched on: This part has selling price, This part has Core charge and Add Misc charge reveal their fields.",
  "/parts/", prep=seq(modal("createPartModal", "button[data-bs-target='#createPartModal']"), check("#sellingPriceToggle"), check("#coreChargeToggle"), check("#miscChargeToggle"), wait(400)),
  element="#createPartModal .modal-dialog", hl=["#sellingPriceGroup", "#coreCostGroup", "#miscChargesGroup"])
S("parts-edit-modal", "Edit part: the same form with the current values; the In stock field is a manual override and needs the Edit on-hand quantity permission.",
  "/parts/", prep=seq(wait(1500), jsclick(".editPartBtn"), wait_for("#createPartModal.show"), wait(900)), element="#createPartModal .modal-dialog")
S("parts-cross-refs-modal", "Cross references: search a part by number and add it — linked parts are interchangeable and appear as alternates in every part search.",
  "/parts/", prep=seq(wait(1500), jsclick(".partCrossRefsBtn"), wait_for("#partCrossRefsModal.show"), wait(1200)), element="#partCrossRefsModal .modal-dialog")
S("parts-history-modal", "Part history: the orders and work orders that moved this part, with dates and quantities and a date filter.",
  "/parts/", prep=seq(wait(1500), jsclick(".partHistoryBtn"), wait_for("#partHistoryModal.show"), wait(1500)), element="#partHistoryModal .modal-dialog")
S("parts-locations-modal", "Stock by location: quantity per storage location with Set qty, and Transfer between locations (From / To / Qty) — transfers never change the total.",
  "/parts/", prep=seq(wait(1500), jsclick(".partLocationsBtn"), wait_for("#partLocationsModal.show"), wait(1500)), element="#partLocationsModal .modal-dialog")
S("parts-deactivate-confirm", "Deactivate a part: the confirmation reminds you that stock and history stay; the part disappears from pickers until a parts order brings it back.",
  "/parts/", prep=seq(wait(1500), jsclick("button:has-text('Deactivate')"), wait(900)), element=".swal2-popup", optional=True)
S("parts-orders-tab", "Parts Orders tab: order #, vendor, date, total, paid / unpaid, status (ordered / received), the Not confirmed badge for email orders and the row actions.",
  "/parts/?tab=orders&date_preset=all_time")
S("parts-order-modal", "Order parts (one vendor): vendor search, order date, Find part, lines with Qty / Price / Total, core charges, non-inventory amounts, the AI Order Reader and the running total.",
  "/parts/?tab=orders", prep=modal("orderModal", "button[data-bs-target='#orderModal']"), element="#orderModal .modal-dialog")
S("parts-order-modal-filled", "An order being filled in: vendor picked, two part lines with quantities — the price defaults to the Last price you paid for the part.",
  "/parts/?tab=orders", prep=seq(modal("orderModal", "button[data-bs-target='#orderModal']"), fill_order_modal), element="#orderModal .modal-dialog")
S("parts-order-created", "Right after Create order: the dialog shows the order number and offers Receive and Pay without leaving it.",
  "/parts/?tab=orders", prep=seq(modal("orderModal", "button[data-bs-target='#orderModal']"), create_order), element="#orderModal .modal-dialog", optional=True)
S("parts-orders-tab-with-order", "The Parts Orders tab with a fresh order: status Ordered, Unpaid, actions Open / Receive / Pay / Delete.",
  "/parts/?tab=orders&date_preset=all_time")
S("parts-order-receive", "Receive an order: confirm the vendor bill number and the storage location for each line — stock and average cost update at that moment.",
  "/parts/?tab=orders", prep=seq(modal("orderModal", "button[data-bs-target='#orderModal']"), create_order, jsclick("#receiveOrderModalBtn"), wait(1800)),
  element=".swal2-popup || .modal.show:not(#orderModal) .modal-dialog || #orderModal .modal-dialog", optional=True)
S("parts-order-pay-modal", "Record parts order payment: order total, already paid, remaining balance, amount, method, date, notes and receipt attachments.",
  "/parts/?tab=orders", prep=seq(modal("orderModal", "button[data-bs-target='#orderModal']"), create_order, jsclick("#payOrderModalBtn"), wait_for("#partsOrderPaymentModal.show"), wait(900)),
  element="#partsOrderPaymentModal .modal-dialog", optional=True)
S("parts-orders-payments-tab", "Parts Orders Payments tab: every vendor payment with search and date filter, plus the Vendor credits section (parts returns and core returns).",
  "/parts/?tab=payments&date_preset=all_time")
S("parts-cores-tab", "Cores tab: cores the shop keeps per part (customer did not pay the deposit), editable Quantity with Save and the Return button.",
  "/parts/?tab=cores")
S("parts-core-return-modal", "Return cores to vendor: quantity, live Credit amount (qty × core cost), optional vendor and notes such as the RMA number.",
  "/parts/?tab=cores", prep=seq(modal("coreReturnModal"), wait(400)), element="#coreReturnModal .modal-dialog")
S("parts-cores-returns-tab", "Cores Returns tab: the ledger of returned cores with credit totals.",
  "/parts/?tab=cores_returns&date_preset=all_time")
S("parts-stocktakes-tab", "Stocktakes tab: physical counts numbered ST-1, ST-2… with status and the New Stocktake button.",
  "/parts/?tab=stocktakes")
S("parts-stocktake-create-modal", "Start a stocktake: optional name, Location scope (default Whole warehouse, sub-locations included) and Category scope.",
  "/parts/?tab=stocktakes", prep=modal("stocktakeCreateModal", "button[data-bs-target='#stocktakeCreateModal']"), element="#stocktakeCreateModal .modal-dialog")
S("parts-stocktake-page", "Counting screen: search, chips All / Pending / Counted / Discrepancies, columns Location, Part #, Description, Expected, Counted (+ Save), Variance, Value, Status; Add found part; Complete & apply and Cancel.",
  "/parts/?tab=stocktakes", prep=seq(modal("stocktakeCreateModal", "button[data-bs-target='#stocktakeCreateModal']"), create_stocktake, count_some_lines, wait(600)), optional=True)
S("parts-stocktake-complete-confirm", "Complete & apply: the confirmation with Set uncounted lines to zero — tick it for a full inventory, leave it off for a cycle count.",
  lambda: f"/parts/stocktakes/{state.get('stocktake', '')}", prep=seq(wait(600), click("#stCompleteBtn"), wait(700)), element=".swal2-popup, .modal.show .modal-dialog", optional=True)

# Vendors
S("vendors-list", "Vendors list: Name, Phone, Email, Website, Primary contact, Address, Balance (unpaid orders) and Status; Add Vendor; click a name for Vendor Orders.",
  "/vendors/", hl=["button[data-bs-target='#createVendorModal']"])
S("vendors-add-modal", "Create new vendor: name (required), website, address with autocomplete, notes and the contacts block with a main contact.",
  "/vendors/", prep=modal("createVendorModal", "button[data-bs-target='#createVendorModal']"), element="#createVendorModal .modal-dialog")
S("vendors-orders-modal", "Vendor Orders popup: the vendor's orders with a date filter and totals — Orders, Total Amount, Paid, Unpaid, Received, Not Received.",
  "/vendors/", prep=seq(wait(1000), jsclick(".openVendorOrdersBtn"), wait_for("#vendorOrdersModal.show"), wait(1500)), element="#vendorOrdersModal .modal-dialog")

# Reports
S("reports-index", "Reports page: Sales & Revenue (Sales Summary, General Revenue, Payments Summary), Balances, Parts & Inventory, Labor (Mechanic Hours, Timecard / Salary) and Audit (Activity Journal).",
  "/reports", full_page=True)
S("report-sales-summary", "Sales Summary: labor, parts, tax and totals by period, filtered by customer and dates; Generate and Download PDF.",
  "/reports/standard/sales_summary?date_preset=all_time", prep=wait(1500), full_page=True)
S("report-general-revenue", "General Revenue: left block Revenue − Parts Cost (no payroll), right block Revenue − Parts Orders − Salaries, with the explanation table below.",
  "/reports/standard/general_revenue?date_preset=all_time", prep=wait(1500), full_page=True)
S("report-payments-summary", "Payments Summary: payments received by method and date.",
  "/reports/standard/payments_summary?date_preset=all_time", prep=wait(1500), full_page=True)
S("report-customer-balances", "Customer Balances: who owes you — all-time by design, the date filter does not apply.",
  "/reports/standard/customer_balances", prep=wait(1500), full_page=True)
S("report-vendor-balances", "Vendor Balances: whom you owe for unpaid parts orders.",
  "/reports/standard/vendor_balances", prep=wait(1500), full_page=True)
S("report-parts-orders-summary", "Parts Orders Summary: purchasing by vendor and period — ordered, received, paid.",
  "/reports/standard/parts_orders_summary?date_preset=all_time", prep=wait(1500), full_page=True)
S("report-mechanic-hours", "Mechanic Hours: Billed Hours (from work order labor, split by assignment %) vs Tracked Hours (job timers) per mechanic, plus the uAttend Hours column when connected.",
  "/reports/standard/mechanic_hours?date_preset=all_time", prep=wait(1500), full_page=True)
S("report-filters", "Report controls: the report tabs, date presets / custom range, the customer or vendor filter, Generate and Download PDF.",
  "/reports/standard/sales_summary?date_preset=all_time", prep=wait(1000), hl=[".ss-date-input", "#customerDropdownBtn"])
S("report-timecard-salary", "Timecard / Salary report: salary employees with their weekly amount and hourly employees with uAttend hours × rate for the period.",
  "/reports/timecard-salary", prep=wait(1200), full_page=True)
S("report-activity-journal", "Activity Journal: search (WO number, customer, part, user, value…), filters by user, action and entity, and every create / edit / delete with its time.",
  "/reports/audit", prep=wait(800))
S("report-activity-journal-row", "An expanded journal entry: the changed fields with their old and new values.",
  "/reports/audit", prep=seq(wait(800), click(".journal-row"), wait(700)), optional=True)

# Settings
S("settings-index", "Settings: cards grouped into Company (Organization, Subscription & Billing, Shops), People (Users & Roles, Roles & Permissions), Work Orders, Parts & Pricing, Communications and Integrations.",
  "/settings", full_page=True)
S("settings-organization", "Organization: company details, address, billing details and the primary contact; Save Changes.",
  "/settings/organization", full_page=True)
S("settings-billing", "Subscription & Billing: subscription status and dates, the monthly price breakdown (locations, full users, mechanics), Pay now, Switch to annual and the invoice list.",
  "/settings/billing", full_page=True)
S("settings-shops", "Shops: your locations with Edit and Inactive, + Add shop, and the timezone of the active shop.",
  "/settings/locations")
S("settings-shop-add-modal", "Add Shop: name, address, phone and email of a new location — it gets its own settings and data.",
  "/settings/locations", prep=modal("createShopModal", "button[data-bs-target='#createShopModal']"), element="#createShopModal .modal-dialog")
S("settings-users", "Users & Roles: every user with role, shops, pay type and status (Invited / Active / Inactive); Edit, Permissions and Deactivate per row; + New user and Manage roles.",
  "/settings/users", hl=["button[data-bs-target='#createUserModal']"])
S("settings-user-create-modal", "Create new user: first and last name, email (the invitation goes there), phone, Role, Active, Pay type with salary amount and Shop access checkboxes.",
  "/settings/users", prep=modal("createUserModal", "button[data-bs-target='#createUserModal']"), element="#createUserModal .modal-dialog")
S("settings-user-edit-modal", "Edit user: the same fields plus the Active user switch; passwords are never set here — users manage their own.",
  "/settings/users", prep=seq(wait(1200), jsclick(".edit-user-btn"), wait_for("#editUserModal.show"), wait(700)), element="#editUserModal .modal-dialog")
S("settings-user-permissions-modal", "Per-user permissions: what the role grants, plus Allow and Deny overrides for this person — Deny always wins.",
  "/settings/users", prep=seq(wait(1200), jsclick("xpath=(//button[contains(@class,'user-perms-btn')])[3]"), wait_for("#userPermsModal.show"), wait(1800)), element="#userPermsModal .modal-dialog")
S("settings-roles", "Roles & Permissions: the roles list on the left, New role / Clone / Delete / Save, and the permission tree for the selected role.",
  "/settings/roles", prep=wait(800))
S("settings-roles-tree", "Editing a role: permissions grouped by section (Dashboard, Calendar, Customers, Parts, Work Orders, Reports, Settings…), with a filter box and Select all / Clear all.",
  "/settings/roles", prep=seq(wait(800), click("#roles-list button:has-text('Manager'), #roles-list a:has-text('Manager'), #roles-list li:has-text('Manager')"), wait(800)), full_page=True, optional=True)
S("settings-wo-settings", "Work Order Settings: Shop Supply Amount (% of labor), Core Charge Rules (charge for cores by default), Labor Rates with Add / Edit / Delete, and Workflows.",
  "/settings/work_orders", full_page=True)
S("settings-labor-rate-modal", "Add Labor Rate: name, code and hourly rate — the rate is snapshotted on every work order, so changing it later never changes old jobs.",
  "/settings/work_orders", prep=modal("createLaborRateModal", "button[data-bs-target='#createLaborRateModal']"), element="#createLaborRateModal .modal-dialog")
S("settings-service-templates", "Service Templates: one card per preset (e.g. PM Service, Brake Job) with Edit and Delete; + Add Preset.",
  "/settings/wo_presets")
S("settings-preset-modal", "New Preset: name, description, labor hours and rate, parts with optional pinned prices, Allow customer discount on parts, and the live estimate of labor / parts / total.",
  "/settings/wo_presets", prep=seq(click("#addPresetBtn"), wait_for("#presetModal.show"), wait(500)), element="#presetModal .modal-dialog")
S("settings-preset-edit-modal", "Editing an existing template: labor, parts table with quantities and prices (hand-edited prices are pinned and highlighted).",
  "/settings/wo_presets", prep=seq(click(".edit-preset-btn"), wait_for("#presetModal.show"), wait(900)), element="#presetModal .modal-dialog")
S("settings-pdf-design", "PDF Design: colors & branding with logo, which customer and unit fields to print, services & parts options (hours, rate, parts detail, cores, misc, shop supply, balance due), footer text and the live preview.",
  "/settings/pdf-design", full_page=True)
S("settings-parts-settings", "Parts & Pricing: Parts Locations tree (up to 4 levels), Parts Categories, Margin / Markup Rules (pricing scales) and the Sales Tax Rate.",
  "/settings/parts-settings", full_page=True)
S("settings-pricing-rules", "Margin / Markup Rules: scale selector with New scale / Set as default / Delete, Margin vs Markup mode and the From / To / % ranges with Add range and Save.",
  "/settings/parts-settings", element="xpath=//*[self::h2 or self::h5 or self::h6 or self::div][contains(normalize-space(.),'Margin / Markup Rules')]/ancestor::div[contains(@class,'card')][1]", optional=True)
S("settings-sales-tax", "Sales Tax Rate: automatic by shop ZIP, or a Custom Rate (Override) with Reset to API.",
  "/settings/parts-settings", element="xpath=//*[self::h2 or self::h5 or self::h6 or self::div][contains(normalize-space(.),'Sales Tax Rate')]/ancestor::div[contains(@class,'card')][1]", optional=True)
S("settings-parts-locations", "Parts Locations: the storage tree (Warehouse › Rack › Shelf › Bin) with Add under a parent, and Parts Categories next to it.",
  "/settings/parts-settings", element="xpath=//*[self::h2 or self::h5 or self::h6 or self::div][contains(normalize-space(.),'Parts Locations')]/ancestor::div[contains(@class,'card')][1]", optional=True)
S("settings-notifications", "Notifications settings page (placeholder for upcoming email / SMS rules).",
  "/settings/notifications")
S("settings-integrations", "Integrations (per shop): uAttend time clock and Parts orders from email, each with its own Connect / Turn on button.",
  "/settings/integrations")
S("settings-uattend-modal", "uAttend integration: API key, enable switch, Test connection, the employee list to tick with an hourly rate per person.",
  "/settings/integrations", prep=modal("integration-modal-uattend", "button[data-bs-target='#integration-modal-uattend']"), element="#integration-modal-uattend .modal-dialog")
S("settings-email-orders-modal", "Parts orders from email: enable the location's inbox address, forwarding instructions for Gmail / Outlook / vendor portals, and the Inbox history.",
  "/settings/integrations", prep=modal("integration-modal-email-orders", "button[data-bs-target='#integration-modal-email-orders']"), element="#integration-modal-email-orders .modal-dialog")

# Perks / tools / AI
S("perks-index", "Perks: quick tools outside the work order flow — currently Annual Inspection.", "/perks")
S("perks-annual-inspection", "Perks → Annual Inspection: every saved inspection of the shop with Download PDF and Delete; New Inspection for any vehicle, even one not in the system.",
  "/perks/annual-inspection")
S("perks-inspection-modal", "New Inspection from Perks: type the VIN, pick the vehicle type, enter the customer name by hand — no unit or customer record needed.",
  "/perks/annual-inspection", prep=seq(click_text("New Inspection"), wait_for("#annualInspectionModal.show"), wait(1500)), element="#annualInspectionModal .modal-dialog")
S("import-export-page", "Import / Export: one tab per entity (Customers, Vendors, Parts, Units, Work Orders), Download CSV / Excel for exports, and the 3-step import (upload → map columns → import).",
  "/import-export/")
S("import-export-units", "Units import is per customer: pick the customer in Import units for customer, then upload that customer's unit list.",
  "/import-export/?tab=units", hl=["#importCustomerSearch"])
S("import-export-mapping", "Column mapping step: each header from your file is matched to a Roobico field; fix any mismatch, then Import. Duplicates are skipped and reported with reasons.",
  "/import-export/", prep=upload_csv, full_page=True, optional=True)
S("ai-helper-panel", "Roobico AI Helper (bottom of the sidebar): ask how to do something in plain words — it answers from the documentation, can show screenshots and link to lessons, and can look up your own shop's numbers.",
  "/dashboard", prep=seq(click("#assistantToggleBtn"), wait(600)), element="#assistantPanel")
S("ai-helper-answer", "An AI Helper answer: numbered steps with the exact button names, and a link to the matching Education Center lesson.",
  "/work_orders", prep=ask_assistant, element="#assistantPanel", optional=True)
S("education-home", "Education Center home: overall progress ring, Continue button, lesson search, module cards with progress bars and suggested learning paths.",
  "/education/", full_page=True)
S("education-module", "A module page: lessons in order with numbers or check marks, minutes and screenshot counts, and the module progress bar.",
  "/education/work-orders/")
S("education-lesson", "A lesson page: steps with screenshots (click to zoom), callouts, the Mark lesson as complete button, Previous / Next and the module table of contents.",
  "/education/getting-started/welcome")

# Mechanic mode (mobile viewport)
S("mech-wo-list", "Mechanic work order list on a phone: search, the In work now section with running timers, All work orders and the + button for a new work order — no prices anywhere.",
  "/mechanic/work_orders", who="mechanic", prep=wait(1500))
S("mech-mileage-modal", "Current mileage prompt: the mechanic confirms the odometer (prefilled with the last known value) or types the real one before the job opens.",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, wait(600)), optional=True)
S("mech-wo-details", "Mechanic work order editor: customer and unit, jobs with Start / Stop timers, parts, Add Labor / Add Preset, Save and Done — labor and part prices are hidden.",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, mech_confirm_mileage, wait(800)), full_page=True, optional=True)
S("mech-timer-running", "A running job timer: the green dot and elapsed time, and the timer bar at the bottom with Stop — the office sees the mechanic working live.",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, mech_confirm_mileage, mech_start_timer, wait(1500)), optional=True)
S("mech-wo-list-in-work", "Back on the list: the work order appears under In work now while the timer runs; the bottom bar keeps the timer visible on every page.",
  "/mechanic/work_orders", who="mechanic", prep=wait(1500), optional=True)
S("mech-timer-stopped", "After Stop: the session is saved to the job's tracked time; hours auto-fill for the office from these sessions.",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, mech_confirm_mileage, mech_stop_timer, wait(1000)), optional=True)
S("mech-part-modal", "Part search in mechanic mode: find a part by number or description, see stock — no costs or prices.",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, mech_confirm_mileage, modal("mechPartModal"), wait(500)), element="#mechPartModal .modal-dialog", optional=True)
S("mech-preset-modal", "Add Preset in mechanic mode: pick a service template; its jobs and parts are added without prices.",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, mech_confirm_mileage, modal("mechPresetModal"), wait(500)), element="#mechPresetModal .modal-dialog", optional=True)
S("mech-unit-history-modal", "Unit history for mechanics: previous work orders of the unit with jobs and dates (paid ones show as completed).",
  "/mechanic/work_orders", who="mechanic", prep=seq(wait(1200), mech_open_first_wo, mech_confirm_mileage, click("#mechUnitHistoryBtn"), wait(1200)), element="#mechUnitHistoryModal .modal-dialog", optional=True)
S("mech-new-wo", "New work order in mechanic mode: pick the customer and unit (or create a new unit), enter the mileage, add jobs and parts, Save.",
  "/mechanic/work_orders/new", who="mechanic", prep=wait(1200))
S("mech-wo-list-desktop", "Mechanic work order list in a desktop browser — the same no-price interface, convenient on a shop computer.",
  "/mechanic/work_orders", who="mechanic", prep=wait(1500), viewport=DESKTOP)


# ── Runner ────────────────────────────────────────────────────────────

def resolve_path(shot: Shot) -> str | None:
    p = shot.path
    return p() if callable(p) else p


def save_webp(png: bytes, path: Path) -> tuple[int, int]:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)), Image.LANCZOS)
    # Очень длинные full-page кадры режем снизу: урок всё равно показывает верх.
    if img.height > 3200:
        img = img.crop((0, 0, img.width, 3200))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "WEBP", quality=WEBP_QUALITY, method=6)
    return img.width, img.height


def login(page: Page, email: str, password: str) -> None:
    page.goto(f"{BASE}/", wait_until="load")
    page.fill("#login_email", email)
    page.fill("#login_password", password)
    page.click("#loginForm button[type=submit]")
    page.wait_for_load_state("load")
    page.wait_for_timeout(800)
    if "login" in page.url or page.locator("#loginForm").count():
        raise RuntimeError(f"login failed for {email}: {page.url}")


def init_script(theme: str, sidebar_collapsed: bool) -> str:
    return f"""
      try {{
        localStorage.setItem('roobico.theme', '{theme}');
        localStorage.setItem('roobico.sidebar.collapsed', '{'1' if sidebar_collapsed else '0'}');
      }} catch (e) {{}}
    """


def take(page: Page, shot: Shot) -> tuple[int, int]:
    path = resolve_path(shot)
    if path:
        page.goto(f"{BASE}{path}", wait_until="load", timeout=60000)
        try:
            # Таблицы/модалки подгружаются fetch'ем — ждём тишины в сети,
            # но live-polling страниц WO может её не дать, поэтому с лимитом.
            page.wait_for_load_state("networkidle", timeout=6000)
        except PWTimeout:
            pass
        page.wait_for_timeout(shot.wait)
    if shot.prep:
        shot.prep(page)
    page.add_style_tag(content=HL_CSS)
    for sel in shot.hl:
        try:
            page.eval_on_selector_all(sel, "els => els.forEach(e => e.classList.add('edu-hl'))")
        except Exception:
            pass
    page.wait_for_timeout(150)
    if shot.element:
        # "a || b" — первый селектор, у которого есть видимый элемент
        # (например, SweetAlert-попап поверх уже открытой модалки).
        candidates = [s.strip() for s in shot.element.split("||")]
        loc = None
        for sel in candidates:
            cand = page.locator(sel).first
            try:
                cand.wait_for(state="visible", timeout=4000 if len(candidates) > 1 else 10000)
                loc = cand
                break
            except PWTimeout:
                continue
        if loc is None:
            raise PWTimeout(f"no visible element for {shot.element}")
        png = loc.screenshot(type="png")
    else:
        png = page.screenshot(type="png", full_page=shot.full_page)
    return save_webp(png, OUT_DIR / f"{shot.id}.webp")


def write_catalog() -> None:
    lines = [
        '"""Скриншоты Education Center: id → подпись.',
        "",
        "ФАЙЛ ГЕНЕРИРУЕТСЯ скриптом scripts/education_screenshots.py (--write-catalog)",
        "из подписей в его списке SHOTS — править подписи там, не здесь.",
        "Картинки лежат в static/img/education/<id>.webp. Подпись — alt-текст и",
        "figcaption в уроке и описание для AI-помощника (по ней модель выбирает,",
        "какой скриншот показать). Тест test_education.py проверяет, что каждый",
        "скриншот из уроков описан здесь и лежит на диске, и наоборот.",
        '"""',
        "from __future__ import annotations",
        "",
        'SHOT_DIR = "img/education"',
        'SHOT_EXT = "webp"',
        "",
        "",
        "def shot_url(shot_id: str) -> str:",
        '    return f"/static/{SHOT_DIR}/{shot_id}.{SHOT_EXT}"',
        "",
        "",
        "SHOTS: dict[str, str] = {",
    ]
    for shot in SHOTS:
        caption = shot.caption.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    "{shot.id}": "{caption}",')
    lines.append("}")
    lines.append("")
    CATALOG_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"catalog written: {CATALOG_PATH} ({len(SHOTS)} shots)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="префикс id (или несколько через запятую)")
    ap.add_argument("--skip-existing", action="store_true", help="не переснимать уже существующие файлы")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--write-catalog", action="store_true", help="перегенерировать app/education/shots.py")
    args = ap.parse_args()

    if args.list:
        for s in SHOTS:
            print(f"{s.id:34} {s.who:10} {s.caption[:90]}")
        print(len(SHOTS), "shots")
        return 0
    if args.write_catalog:
        write_catalog()
        if not args.only and len(sys.argv) == 2:
            return 0

    if not OWNER_PASSWORD:
        print("EDU_PASSWORD is required", file=sys.stderr)
        return 2

    prefixes = [p.strip() for p in (args.only or "").split(",") if p.strip()]
    todo = [s for s in SHOTS if not prefixes or any(s.id.startswith(p) for p in prefixes)]
    if args.skip_existing:
        todo = [s for s in todo if not (OUT_DIR / f"{s.id}.webp").exists()]
    print(f"{len(todo)} shots to take → {OUT_DIR}")

    failures: list[tuple[str, str]] = []
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        contexts: dict[str, Page] = {}

        def page_for(who: str, viewport: dict | None) -> Page:
            key = who
            if key not in contexts:
                vp = MOBILE if who == "mechanic" else DESKTOP
                ctx = browser.new_context(viewport=vp, device_scale_factor=1, locale="en-US",
                                          timezone_id="America/Chicago")
                ctx.add_init_script(init_script("dark" if who == "owner-dark" else "light", sidebar_collapsed=False))
                pg = ctx.new_page()
                pg.set_default_timeout(20000)
                pg.on("dialog", lambda d: d.accept())
                if who in ("owner", "owner-dark"):
                    login(pg, OWNER_EMAIL, OWNER_PASSWORD)
                elif who == "mechanic":
                    login(pg, MECH_EMAIL, MECH_PASSWORD)
                contexts[key] = pg
            pg = contexts[key]
            pg.set_viewport_size(viewport or (MOBILE if who == "mechanic" else DESKTOP))
            return pg

        owner = page_for("owner", None)
        discover(owner)

        for i, shot in enumerate(todo, 1):
            t0 = time.time()
            try:
                page = page_for(shot.who, shot.viewport)
                w, h = take(page, shot)
                print(f"[{i}/{len(todo)}] OK   {shot.id:34} {w}x{h}  {time.time() - t0:4.1f}s")
            except (PWTimeout, Exception) as exc:  # noqa: BLE001
                msg = str(exc).splitlines()[0][:160]
                tag = "skip" if shot.optional else "FAIL"
                print(f"[{i}/{len(todo)}] {tag} {shot.id:34} {msg}")
                failures.append((shot.id, msg))
                # После падения состояние страницы непредсказуемо — переоткроем контекст.
                try:
                    contexts.pop(shot.who).context.close()
                except Exception:
                    pass
        browser.close()

    print(f"\ndone in {time.time() - started:.0f}s; {len(todo) - len(failures)} ok, {len(failures)} failed")
    for sid, msg in failures:
        print(f"  - {sid}: {msg}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
