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
access** checkboxes.

**Invitations — the only way to create a user.** The new user gets an email
with an **Accept Invitation** link (valid 7 days) where they create their own
password. Until then the user shows an orange **Invited** badge in the list,
cannot log in, and has a **Resend invite** button in case the email got lost.
**Passwords are strictly personal**: neither the owner nor managers can set
or change another user's password anywhere in the app — the account and its
password belong to the user. If someone forgets their password, they reset it
themselves via **"Forgot password"** on the login page. Re-hiring a
deactivated user also goes through a fresh invitation (the old password is
cleared).

- **Deactivate** blocks login but keeps history; you cannot deactivate
  yourself.
- **Re-hiring**: creating a user with the email of a *deactivated* user of
  your company reactivates that user with the data you enter in the form (a
  fresh profile — name, role, shops, password are overwritten). If the email
  belongs to an active user (or a user of another company) you get "User
  with this email already exists."
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

**"Edit / delete customer contacts in email dialogs"**
(work_orders.manage_email_contacts) controls the pencil/trash icons next to
contacts in the WO email dialogs (send work order, payment receipt,
authorization). It is ON for every role by default; untick it for a role to
make the recipients list read-only there (adding a new recipient on the fly
still works).

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

Usage-based pricing: the **$59/mo base plan includes your first active
location and first active full user**. On top of that: $100/mo per extra
active location + $50/mo per extra active full user + $25/mo per active
mechanic (mechanic & senior mechanic roles). Example: 1 location with an
owner and 2 mechanics = $59 + 2 × $25 = $109/mo; 2 locations with 3 office
users and 4 mechanics = $59 + $100 + 2 × $50 + 4 × $25 = $359/mo. Only
active locations and users count — deactivated staff is free. 30-day
free trial.

**Annual billing — save 20%.** On the billing page you can switch the
billing period from monthly to annual ("Switch to annual" in the Monthly
price card). An annual subscription is billed once a year at 12 × the
monthly price minus 20% (e.g. $109/mo → $1,046.40/yr instead of $1,308),
and each payment extends the subscription by a full year. The price is
recalculated from the currently active locations/users at each renewal.
You can switch back to monthly at any time — the change applies to the
next invoice; already-issued invoices are not recalculated.

The owner's billing page shows the monthly price breakdown,
subscription status (Active / Renews soon / Payment failed / Expired), "Paid
until" date, saved card, invoice history and a **"Pay now"** button (Stripe).
Renewal is charged automatically ~3 days before the period ends; a failed
charge gets a 7-day grace. When the subscription expires, only the owner can
log in (straight to the billing page) — other users are blocked until it's
renewed.

## Import / Export

Sidebar → **Import / Export**. Entities: **Customers**, **Units**,
**Vendors**, **Parts**, **Work Orders**. Access is controlled by the
Import / Export permissions (view / import / export) in role settings.

**Export.** Each tab has "Download CSV" and "Download Excel" buttons — they
download ALL records of that entity for the current shop. Export columns
match the import fields, so a file exported from one shop can be imported
into another with automatic column mapping. Work Orders export additionally
includes Grand Total, Paid Amount and Balance.

**Import.** Upload a CSV or Excel (.xlsx; legacy .xls is not supported —
re-save as .xlsx) with a header row → "Read Headers" → map columns to
fields (or "— Skip —") → Import. The result shows imported/skipped counts,
and every skipped row is listed with the reason.

- Customers: Company Name, First/Last Name, Phone, Email, Address, Pricing
  Scale Name. Requires at least one labor rate to exist in the shop.
- Units: **Customer Name** (links the unit to an existing customer — import
  customers first), Unit Number, VIN, Year, Make, Model, Type, Mileage.
  A row needs at least a Unit Number or a VIN; a VIN that already exists
  for that customer is skipped as a duplicate.
- Vendors: Vendor Name (+contact, phone, email, website, address, notes).
- Parts: Part Number, Description, Reference, In Stock, Average Cost,
  Selling Price. Starting stock is recorded properly: the part gets a
  location row and an "initial" stock movement, same as manual creation.
- Work Orders (historical records from a previous system): **Date** and
  **Customer Name** are required; the unit is matched by Unit Number or VIN
  within that customer; Status is open / in_progress / completed / paid
  (default completed). Labor Total (or Hours), Parts Total and Sales Tax
  build the totals with the same math the app uses; a **paid** row also
  records a payment for the full amount (or the Paid Amount column), so
  Outstanding Balance stays correct. A WO Number that already exists is
  skipped; leave WO Number empty to auto-number.
- Rows missing the identity field (name / part number / unit identity) are
  skipped with a reason.
- Duplicates are rejected: a customer, vendor or part that already exists in
  the shop (same name / part number, case-insensitive) is skipped and listed
  in the import errors — including repeats inside the file itself. If the
  existing record is deactivated, the error says so: reactivate it instead
  of importing a copy.
