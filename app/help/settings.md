# Settings

Sidebar → **Settings**. Cards by group: Organization, Subscription & Billing,
Shops · Users & Roles, Roles & Permissions · Work Order Settings, Service
Templates, PDF Design · Parts & Pricing · Integrations. ("Notifications" and
"Workflows" are placeholders — not built yet.)

## Users

**Users & Roles** page → **"+ New user"**: First/Last name, Email (globally
unique), Phone, **Role** (Manager, Parts manager, Senior mechanic, Mechanic,
Viewer, General manager — Owner is not assignable), Active flag, **Pay
type** (Salary (fixed) + weekly "Salary amount", or Hourly — hourly rates
are set per employee inside the uAttend integration, not here), **Shop
access** checkboxes, Password (min 8).

- **Deactivate** blocks login but keeps history; you cannot deactivate
  yourself.
- **"Permissions"** per user — allow/deny overrides on top of the role:
  "the role provides a baseline. Allow grants extra permissions on top, and
  Deny removes them. **Deny always wins.**" Changes apply immediately.

## Roles & Permissions

Roles are tenant-wide. System roles: **Owner** (always all permissions,
cannot be edited), General manager (all except billing), Manager (all except
roles & billing), Parts manager, Senior mechanic, Mechanic, Viewer. You can
create/clone custom roles and tick permissions in a grouped tree (Dashboard,
Calendar, Customers, Vendors, Parts, Parts Orders, Work Orders, Attachments,
Reports, Import/Export, Settings). System roles cannot be deleted; a custom
role in use by active users cannot be deleted either.

Key permission to know: **"View part costs inside WO"**
(work_orders.view_costs). A user with work-order access but WITHOUT this
permission works in **mechanic mode** — no prices anywhere (see the mechanic
doc). The default Mechanic and Senior mechanic roles are like this.

## Work Order Settings

- **Shop Supply Amount** — "Shop supply percentage" (default 5) applied to
  labor on WOs.
- **Core Charge Rules** — "Charge core by default" toggle for WO part lines.
- **Labor Rates** — name + hourly rate (defaults seeded: Standard $100,
  After Hours $150). Rates picked on WO labor lines and as customers'
  Default Labor Rate. Deleting is soft; old WOs keep their snapshotted rate.

## Service Templates (presets)

Reusable jobs: name, description, labor hours + rate ("— Use customer
default —" possible), parts list, "Allow customer discount on parts" flag.
Estimates on cards are live — preset prices always recompute from the
current catalog. Inserted into WOs via the labor block menu.

## PDF Design

Per-shop invoice PDF settings with live preview: header/accent colors, show
logo (logo itself is uploaded on the Shops page), toggles for customer
email/phone, unit number/VIN/mileage, labor hours/rate code, parts detail,
core/misc charges, shop supply, paid & balance; thank-you message and
terms/notes footer.

## Parts & Pricing

Categories, the parts storage location tree (4 levels max), **Margin /
Markup pricing scales** (multiple named scales, one default, assignable per
customer) and the **sales tax rate** (auto by shop ZIP or a custom override
with "Reset to API"). Details in the parts documentation.

## Integrations (per shop)

**uAttend** time clock: enter the API key ("Test connection" available),
enable, then in "Employees in uAttend" tick the employees whose time counts
and set their **$/hr** rates. Punches feed the Timecard/Salary report and
payroll in General Revenue; employees are AI-matched to Roobico users so
nobody is double-counted. Keys are stored encrypted; each shop configures
its own integration.

## Shops (multi-shop)

Settings → Shops: **"+ Add shop"** (name, email, address, phone, billing
address, logo). A new shop gets its own database with seeded categories,
pricing scale, labor rates and rules; owners automatically get access.
Per-shop **Timezone** selector lives here too. Shops are deactivated, not
deleted. Users see only their allowed shops in the top-bar **Active shop**
switcher; all pages show the active shop's data.

What is shared vs per-shop: users, roles, organization profile and the
subscription are tenant-wide; labor rates, pricing scales, categories,
locations, tax, presets, PDF design, integrations, timezone and all
operational data (customers, units, parts, WOs) are per shop.

## Subscription & Billing (owner)

Usage-based pricing: $100/mo per active location + $50/mo per active full
user + $25/mo per active mechanic (mechanic & senior mechanic roles). 30-day
free trial. The owner's billing page shows the monthly price breakdown,
subscription status (Active / Renews soon / Payment failed / Expired), "Paid
until" date, saved card, invoice history and a **"Pay now"** button (Stripe).
Renewal is charged automatically ~3 days before the period ends; a failed
charge gets a 7-day grace. When the subscription expires, only the owner can
log in (straight to the billing page) — other users are blocked until it's
renewed.

## Import / Export

Sidebar → **Import / Export**. Currently **import only** (no export yet).
Entities: **Customers**, **Units**, **Vendors**, **Parts**. Upload a CSV or
Excel (.xlsx) with a header row → "Read Headers" → map columns to fields
(or "— Skip —") → Import. Result shows imported/skipped counts and row
errors.

- Customers: Company Name, First/Last Name, Phone, Email, Address, Pricing
  Scale Name. Requires at least one labor rate to exist in the shop.
- Units: Unit Number, VIN, Year, Make, Model, Type, Mileage — imported
  WITHOUT a customer link (attach units to customers manually afterwards).
- Vendors: Vendor Name (+contact, phone, email, website, address, notes).
- Parts: Part Number, Description, Reference, In Stock, Average Cost,
  Selling Price.
- Rows missing the identity field (name / part number) are skipped; there is
  no duplicate detection — importing twice creates duplicates.
