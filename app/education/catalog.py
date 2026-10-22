"""Каталог Education Center: модули и уроки.

Единственный источник структуры: порядок модулей/уроков, заголовки, краткие
описания и ориентировочное время. Тело урока — шаблон
`public/education/lessons/<module>/<lesson>.html`; скриншоты, которые он
использует, извлекаются из шаблона (макрос shot("id", ...)) и подписываются
через SHOTS в shots.py.

ID уроков стабильные: по ним хранится прогресс пользователей
(master.education_progress) — переименовывать нельзя, только добавлять.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .shots import SHOTS, shot_url

_LESSONS_DIR = Path(__file__).resolve().parents[1] / "templates" / "public" / "education" / "lessons"
_SHOT_RE = re.compile(r"""shot\(\s*['"]([a-z0-9][a-z0-9-]*)['"]""")


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    summary: str
    minutes: int = 4
    module_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.module_id}/{self.id}"

    @property
    def template(self) -> str:
        return f"public/education/lessons/{self.module_id}/{self.id}.html"

    @property
    def url(self) -> str:
        return f"/education/{self.module_id}/{self.id}"

    def shot_ids(self) -> list[str]:
        return lesson_shot_ids(self.module_id, self.id)


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    summary: str
    icon: str
    lessons: tuple[Lesson, ...] = field(default_factory=tuple)

    @property
    def url(self) -> str:
        return f"/education/{self.id}/"

    @property
    def minutes(self) -> int:
        return sum(l.minutes for l in self.lessons)


def _module(id: str, title: str, summary: str, icon: str, lessons: list[tuple]) -> Module:
    built = tuple(
        Lesson(id=lid, title=lt, summary=ls, minutes=lm, module_id=id)
        for (lid, lt, ls, lm) in lessons
    )
    return Module(id=id, title=title, summary=summary, icon=icon, lessons=built)


MODULES: tuple[Module, ...] = (
    _module(
        "getting-started", "Getting Started",
        "What Roobico is, how the app is laid out, and the order to set things up on day one.",
        "bi-rocket-takeoff",
        [
            ("welcome", "Welcome to Roobico",
             "The big picture: the building blocks of the app and how a repair job flows through them.", 4),
            ("login-account", "Logging in & your account",
             "The login page, Contact us, forgot password, email confirmation and logging out.", 4),
            ("navigation", "Navigating the app",
             "Sidebar, top bar, active shop switcher, global search, themes and personal table layouts.", 6),
            ("setup-checklist", "First-day setup checklist",
             "The recommended order to configure a new shop so everything downstream just works.", 6),
            ("help-and-ai", "Getting help: AI Helper & Education Center",
             "How to ask the built-in assistant, where the Education Center lives and how progress is tracked.", 3),
        ],
    ),
    _module(
        "dashboard", "Dashboard",
        "Your shop at a glance: revenue, paid vs unpaid, parts purchasing, monthly goals and mechanic hours.",
        "bi-grid-1x2",
        [
            ("dashboard-overview", "Reading the dashboard",
             "Every widget explained: what the numbers include, which filters apply and who can see money.", 6),
            ("dashboard-goals", "Setting monthly goals",
             "Labor, parts sales and total goals — how to set them and how progress is measured.", 3),
        ],
    ),
    _module(
        "customers", "Customers & Units",
        "Companies and private clients, their contacts, vehicles (units), balances and the read-only customer portal.",
        "bi-people",
        [
            ("customers-list", "The Customers list",
             "Search, the Active / Inactive / All filter, the Current Balance column and what each button needs.", 4),
            ("add-customer", "Adding a customer",
             "One record for companies and private clients: contacts, main contact, address and the Taxable switch.", 5),
            ("customer-page", "The customer page",
             "Summary card, Work Orders / Units / Payments / Estimates / Details tabs and the header buttons.", 6),
            ("customer-pricing", "Per-customer labor rate, pricing scale & tax",
             "Default Labor Rate, Pricing Scale and Override part selling price — special pricing for one client.", 5),
            ("add-unit", "Adding units (vehicles)",
             "From the Units tab or from a work order; VIN auto-fill, unit number, mileage, duplicates.", 5),
            ("unit-page", "The unit page: history, recalls, inspections",
             "Full service history with labor details, NHTSA recalls, DOT inspection card and the Details tab.", 6),
            ("customer-portal", "Customer portal",
             "Sending a Portal Link, what the customer sees, link validity and what the portal cannot do.", 5),
            ("deactivate", "Deactivating & reactivating",
             "Soft deactivation of customers and units, finding inactive records and bringing them back.", 3),
        ],
    ),
    _module(
        "work-orders", "Work Orders",
        "The heart of Roobico: from a blank work order to an invoiced, paid job — labor, parts, tax, payments and PDFs.",
        "bi-clipboard-check",
        [
            ("wo-list", "The Work Orders list",
             "Filters, tabs (Work Orders / Payments / Estimates), status badges, the In Work group and footer totals.", 7),
            ("create-wo", "Creating a work order",
             "Customer, unit, mileage, date and the Create dropdown — plus Recognize WO and the calendar shortcut.", 6),
            ("labor-blocks", "Labor blocks (jobs)",
             "Description, hours, labor rate, manual labor total, shop supply and the block menu.", 6),
            ("assign-mechanics", "Assigning mechanics & tracked hours",
             "The Assign Mechanics modal, percentage splits, auto-filled hours from timers and editing sessions.", 6),
            ("parts-on-wo", "Adding parts to a work order",
             "Catalog search, one-time parts, cross references, price auto-fill rules, cores and negative stock.", 7),
            ("charges-tax-totals", "Misc charges, shop supply, tax & totals",
             "Misc charges, the Taxable toggle, where the tax rate comes from and how the grand total is built.", 5),
            ("presets-on-wo", "Using service templates on a work order",
             "Insert a template into a labor block and understand how its prices follow the catalog.", 3),
            ("statuses", "Statuses: Open, In Progress, Paid",
             "What each status means, what locks a work order, and the warning about reverting a paid one.", 5),
            ("estimates", "Estimates (quotes)",
             "Save as Estimate, the Estimates tab, approval, Convert to Work Order and converting back.", 6),
            ("authorizations", "Customer authorization",
             "Send the whole work order or a single job for approval; the approve/decline page and badges.", 4),
            ("payments", "Recording payments",
             "The Paid button, methods, partial payments, attachments, receipts and the Payments tab.", 5),
            ("bulk-payment", "Bulk payment for one customer",
             "One check for many invoices: Auto-Distribute across the oldest unpaid work orders.", 3),
            ("pdf-email", "PDF invoice & emailing the customer",
             "Download PDF, Email Work Order, managing contacts in the email dialog and the portal link.", 5),
            ("attachments", "Attachments",
             "Photos and files on the work order, on a labor block and on a payment.", 3),
            ("parts-orders-for-wo", "Ordering parts for a job",
             "The Parts orders block on a work order: create, receive, pay, PO # badges and the usage check.", 6),
            ("transfer", "Transfer to another customer",
             "Move a work order (and its unit) to the right customer; what gets re-priced and what stays.", 4),
            ("live-updates", "Live updates & the In Work group",
             "The list and the details page refresh themselves as mechanics work; how you are protected while editing.", 4),
            ("wo-cost", "Work Order Cost (profit view)",
             "An internal profit view with mechanic cost rates stored only in your browser.", 3),
        ],
    ),
    _module(
        "calendar", "Calendar",
        "Plan appointments, see work orders on the days mechanics actually worked, and schedule by mechanic.",
        "bi-calendar3",
        [
            ("calendar-basics", "Calendar views",
             "Day / week / month, the Work orders switch, colors and what the blocks mean.", 4),
            ("appointments", "Appointments",
             "Create, edit, move and cancel appointments; statuses and presets from calendar settings.", 5),
            ("wo-on-calendar", "Work orders on the calendar",
             "Work orders placed by mechanic timers, Create Work Order from an appointment, linked status.", 5),
            ("by-mechanic", "By mechanic view & scheduling",
             "One column per mechanic, the Not scheduled strip, drag to schedule and the booked-slot rule.", 5),
        ],
    ),
    _module(
        "parts", "Parts & Inventory",
        "The parts catalog, stock by location, movements, physical counts, cores and inactive parts.",
        "bi-tools",
        [
            ("parts-list", "The Parts list",
             "Search, columns, Avg cost and Last price, footer totals and the row action buttons.", 5),
            ("add-part", "Adding a part",
             "Every field of the part form: stock, average cost, selling price, do-not-track, core charge, misc charges.", 7),
            ("cross-refs", "Cross references (interchangeable parts)",
             "Link interchangeable part numbers and see alternates in every part search.", 3),
            ("locations", "Storage locations & stock by location",
             "The location tree, Stock by location, Set qty and transfers between shelves.", 5),
            ("stock-movements", "How stock moves",
             "Orders in, work orders out, returns, adjustments, negative stock and the part History.", 4),
            ("stocktakes", "Stocktakes (physical counts)",
             "Scope by location or category, counting, Recount flags, Complete & apply and the What changed view.", 7),
            ("cores", "Cores & core returns",
             "Charging a core or keeping it, the Cores tab, manual corrections, returns and vendor credits.", 5),
            ("inactive-parts", "Inactive parts",
             "Deactivate a part, find it later, and how a parts order brings it back.", 3),
        ],
    ),
    _module(
        "purchasing", "Vendors & Parts Orders",
        "Buying from vendors: orders by hand, from a scanned invoice or straight from your email inbox; receiving, paying, returns.",
        "bi-shop",
        [
            ("vendors", "Vendors",
             "The Vendors list, adding a vendor with contacts, balances and the Vendor Orders popup.", 4),
            ("create-order", "Creating a parts order",
             "Vendor, date, part lines, last price, core charges, non-inventory amounts and the order total.", 6),
            ("ai-order-reader", "AI Order Reader",
             "Upload a vendor invoice and let AI pick the vendor and fill the lines; review before creating.", 5),
            ("receive", "Receiving an order",
             "Vendor bill, stock locations, average cost recalculation, Unreceive.", 5),
            ("pay-orders", "Paying vendors",
             "Record payments on orders, partial payments, the Parts Orders Payments tab and vendor credits.", 4),
            ("returns", "Returns to vendor",
             "Return wrong or defective parts, RMA notes, credit documents and stock effects.", 3),
            ("email-orders", "Orders from your email inbox",
             "Enable the location inbox, forward vendor emails, confirm or reject AI-created orders.", 7),
        ],
    ),
    _module(
        "reports", "Reports",
        "Nine reports covering sales, revenue, payments, balances, purchasing, mechanic hours, payroll and the audit journal.",
        "bi-graph-up",
        [
            ("reports-overview", "Reports overview",
             "The Reports page, date filters, export to Excel / PDF and which permission opens which report.", 4),
            ("general-revenue", "General Revenue",
             "Revenue minus parts cost vs revenue minus purchases and payroll — and why payroll can be $0.", 6),
            ("sales-reports", "Sales reports",
             "Sales Summary, Sales by Customer and Payments — what dates they use and what they include.", 5),
            ("balances", "Customer & vendor balances",
             "Who owes you and whom you owe; why these reports ignore the date filter.", 3),
            ("mechanic-hours", "Mechanic Hours",
             "Billed vs tracked hours per mechanic, assignment splits and the uAttend column.", 4),
            ("timecard-salary", "Timecard / Salary",
             "Payroll view: salary and hourly employees, uAttend hours and the permission it needs.", 4),
            ("activity-journal", "Activity Journal",
             "Who changed what and when, Before / After diffs, filters and the History button on records.", 4),
        ],
    ),
    _module(
        "settings", "Settings",
        "Organization, shops, people and permissions, work order defaults, templates, PDF design, parts pricing, integrations and billing.",
        "bi-gear",
        [
            ("organization", "Organization",
             "Company profile, legal and billing details, contacts and the timezone.", 3),
            ("shops", "Shops (locations)",
             "Adding a second location, per-shop settings, granting access and switching shops.", 4),
            ("users", "Users & invitations",
             "New user by invitation, roles, pay type, shop access, deactivate, re-hire and personal passwords.", 6),
            ("roles", "Roles & permissions",
             "System roles, cloning a role, the permission tree and per-user Allow / Deny overrides.", 7),
            ("wo-settings", "Labor rates, shop supply & cores",
             "Work Order Settings: labor rates, default rate, shop supply percentage and core charge rules.", 4),
            ("service-templates", "Service templates",
             "Reusable jobs with labor and parts, pinned prices and the customer discount flag.", 5),
            ("pdf-design", "PDF design",
             "Logo, colors, visible sections and footer text of your invoices and estimates.", 3),
            ("parts-settings", "Categories & storage locations",
             "Parts categories and the 4-level location tree.", 3),
            ("pricing-scales", "Pricing scales & sales tax",
             "Margin vs markup, tiered rules, named scales, the default scale and the sales tax override.", 6),
            ("integrations", "Integrations: uAttend & email orders",
             "Connect the uAttend time clock, match employees and set hourly rates; enable the orders inbox.", 6),
            ("billing", "Subscription & billing",
             "Free tier, per-unit pricing, annual billing, payment card, invoices and what happens on expiry.", 4),
        ],
    ),
    _module(
        "mechanic", "Mechanic Mode & Mobile App",
        "How mechanics work without prices on the web and in the mobile app: timers, VIN scan, Done and confirmation.",
        "bi-phone",
        [
            ("mechanic-mode", "Mechanic mode (no prices)",
             "Who gets mechanic mode, what is hidden, and why every save goes to In Progress.", 4),
            ("mechanic-web", "Mechanic web pages",
             "The mechanic work order list and editor in the browser, mileage prompt and one unit = one WO.", 5),
            ("timers", "Job timers",
             "Start / stop timers per job, what they drive (hours, calendar, payroll) and fixing sessions.", 5),
            ("mobile-app", "Mobile app basics",
             "Install, log in, pick the server, switch shops, themes and what office users see.", 4),
            ("mobile-wo", "Work orders in the app",
             "Open, create and edit work orders on the phone; autosave, photos and the date rule.", 5),
            ("vin-scan", "VIN scan & the NEW Customer",
             "Scan a VIN with the camera, how unknown units land under NEW Customer, and the Transfer fix.", 4),
            ("done-confirm", "Done, confirm & push notifications",
             "Marking a job Done, several mechanics on one WO, manager confirmation and phone notifications.", 5),
        ],
    ),
    _module(
        "tools", "Perks, Import / Export & AI",
        "DOT annual inspections for any vehicle, moving data in and out, and every AI feature in one place.",
        "bi-stars",
        [
            ("annual-inspection", "DOT annual inspection (AVIR)",
             "Create a 49 CFR 396 inspection from a work order, a unit or Perks; checklist, presets and history.", 6),
            ("import-data", "Importing data",
             "Customers, units per customer, vendors, parts and historical work orders from CSV / Excel.", 6),
            ("export-data", "Exporting data",
             "Download CSV / Excel of any entity for backups or moving to another shop.", 2),
            ("ai-features", "AI features overview",
             "Recognize WO, Describe Issue, AI Order Reader, email orders, uAttend matching and the AI Helper.", 5),
        ],
    ),
)


# ── Lookups ───────────────────────────────────────────────────────────

_MODULES_BY_ID = {m.id: m for m in MODULES}
_LESSONS_BY_KEY = {l.key: l for m in MODULES for l in m.lessons}
ALL_LESSONS: tuple[Lesson, ...] = tuple(l for m in MODULES for l in m.lessons)
TOTAL_LESSONS = len(ALL_LESSONS)


def get_module(module_id: str) -> Module | None:
    return _MODULES_BY_ID.get(module_id)


def get_lesson(module_id: str, lesson_id: str) -> Lesson | None:
    return _LESSONS_BY_KEY.get(f"{module_id}/{lesson_id}")


def get_lesson_by_key(key: str) -> Lesson | None:
    return _LESSONS_BY_KEY.get(key)


def neighbours(lesson: Lesson) -> tuple[Lesson | None, Lesson | None]:
    """Предыдущий и следующий урок в сквозном порядке (через границы модулей)."""
    idx = ALL_LESSONS.index(lesson)
    prev_l = ALL_LESSONS[idx - 1] if idx > 0 else None
    next_l = ALL_LESSONS[idx + 1] if idx + 1 < len(ALL_LESSONS) else None
    return prev_l, next_l


@lru_cache(maxsize=None)
def lesson_shot_ids(module_id: str, lesson_id: str) -> list[str]:
    """Скриншоты, которые использует шаблон урока (в порядке появления)."""
    path = _LESSONS_DIR / module_id / f"{lesson_id}.html"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: list[str] = []
    for sid in _SHOT_RE.findall(text):
        if sid not in seen:
            seen.append(sid)
    return seen


def lesson_template_exists(lesson: Lesson) -> bool:
    return (_LESSONS_DIR / lesson.module_id / f"{lesson.id}.html").exists()


# ── Для AI-помощника ──────────────────────────────────────────────────

@lru_cache(maxsize=1)
def assistant_catalog_text() -> str:
    """Компактное описание уроков и скриншотов для системного промпта.

    Формат строк:
      LESSON <url> — <Module> › <Title>: <summary>
      SHOT <url> — <caption>
    """
    lines: list[str] = []
    for m in MODULES:
        for l in m.lessons:
            lines.append(f"LESSON {l.url} — {m.title} › {l.title}: {l.summary}")
            for sid in l.shot_ids():
                caption = SHOTS.get(sid)
                if caption:
                    lines.append(f"  SHOT {shot_url(sid)} — {caption}")
    return "\n".join(lines)
