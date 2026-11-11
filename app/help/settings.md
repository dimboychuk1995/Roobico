# Settings

Sidebar → **Settings**. Cards by group: Organization, Subscription & Billing,
Shops · Users & Roles, Roles & Permissions · Work Order Settings, Service
Templates, PDF Design · Parts & Pricing · Integrations. ("Notifications" and
"Workflows" are placeholders — not built yet.)

## Users

**Users & Roles** page → **"+ New user"**: First/Last name, Email (globally
unique), Phone, **Role**, Active flag, **Pay type** (Salary (fixed) + weekly
"Salary amount", or Hourly — hourly rates are set per employee inside the
uAttend integration, not here), **Shop access** checkboxes.

**What the Role list offers.** First the system roles from the top of the
hierarchy down — General manager, Manager, Parts manager, Senior mechanic,
Mechanic, Viewer (plus **Owner** at the top, which only an owner sees and
can assign) — then, under "Custom roles", every custom role created in
**Roles & Permissions**, A→Z, so custom roles are assigned right here. A
new user has no role preselected ("Select a role…"): you always pick one
deliberately, nobody becomes an owner or a manager by accident. If you
renamed a role in Roles & Permissions, the list and the whole page show the
new name.

**The list is grouped by role.** Each role is its own section with a header
row such as **"Mechanic · 4 · 1 invited"**: the role name, how many people
have it, and how many of them have not accepted their invitation yet. The
sections always come in the same order:

1. the system roles, top of the hierarchy first — Owner, General manager,
   Manager, Parts manager, Senior mechanic, Mechanic, Viewer;
2. your custom roles, A→Z (the header says "custom role");
3. sections marked **Role not found** (see below);
4. **Inactive** — only when the status filter includes deactivated users.

A role nobody has gets no section. Inside a section people are sorted by
name (people without a name go last, by email). Clicking the **Name** or
**Email** column header sorts by that column inside every section — the
sections themselves stay in place. The **Role** column shows the role's
name, the **Status** column Invited / Active / Inactive.

- **Search** covers name, email, phone, ID and the role — by key or by
  name, so "mechanic", "parts manager" or the name of a custom role all
  work. The section counts then show only the matching people.
- **Inactive users are not split by role**: with the status filter on
  **All users** they are collected in one **Inactive** section at the very
  bottom; with **Inactive users** it is the only section. Their Role column
  still shows the role they had — that is the role they get back on
  **Activate**.
- **Role not found** (orange badge on a section header): the role these
  people have no longer exists in Roles & Permissions — typically a custom
  role that was deleted while the person was deactivated, and the person
  was activated again later. They get **no permissions from a role** (only
  their per-user Allow overrides) until you click **Edit** and pick an
  existing role — the Role field shows "Select a role…" for them.
- **Your mechanics** are the Senior mechanic and Mechanic sections: exactly
  these people can be assigned to jobs, count in the mechanic-hours numbers
  and are billed at the per-mechanic price. Everyone in the other active
  sections (invited people too) counts as a "full user" for billing — the
  first one is free; deactivated users are free.

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
  yourself. Deactivated users disappear from the default list — switch the
  status filter next to the search box to **Inactive users** / **All
  users** to see them (they are in the **Inactive** section at the bottom);
  each inactive row has an **Activate** button (same as the "Active user"
  checkbox in the edit form) that lets the person log in again with their
  existing password.
- **Re-hiring**: creating a user with the email of a *deactivated* user of
  your company reactivates that user with the data you enter in the form (a
  fresh profile — name, role, shops, password are overwritten). If the email
  belongs to an active user (or a user of another company) you get "User
  with this email already exists."
- **"Permissions"** per user — allow/deny overrides on top of the role:
  "the role provides a baseline. Allow grants extra permissions on top, and
  Deny removes them. **Deny always wins.**" Changes apply immediately.
- **Pay type / Salary amount** in the user form are visible and saved only
  with the permission **"Edit employees' pay type & salary"**
  (settings.manage_pay — Owner, General manager and Manager by default).
  Without it the form has no pay fields and the server keeps the existing
  values. **Nobody can change their own pay**, whatever their role — an
  owner edits the owner's pay from another owner account, or not at all.
- **Which roles you can assign.** You can give a user only a role whose
  permissions you have yourself (the Owner can assign any role). A Manager
  without "Manage roles & permissions" cannot promote a colleague to
  General manager, because that role includes managing roles. Editing the
  profile of someone whose role is already wider than yours (name, phone,
  shops) still works — only the role change is blocked.
- **Shop access applies immediately.** When you change a user's shops, the
  change takes effect on their very next request — no re-login needed; if
  their current shop was removed they are switched to the first allowed one.
- **Renaming a user** (Edit → First / Last name → Save) shows the new name
  everywhere at once, on old records too — nothing has to be re-saved:
  the mechanics of the jobs on old work orders (the "Mechanics:" line of
  a job and the work order cost breakdown), the "working now" names and
  the **Edit mechanic hours** list of timer sessions, the running-timer
  cards and the "already in progress for this unit" message on the
  mechanic page and in the mobile app, the tracked time of a job in the
  app, the Work Orders list, the Calendar, the Dashboard and Reports. For a
  rename made with this feature in place, the copies of the name saved
  inside old work orders, timer sessions and appointments are rewritten in
  the same step, so the AI assistant answers with the new name as well.
  Renames made before this feature existed are cleaned up by a one-time
  run of the Roobico administrator; until it has run, the AI assistant
  (and older app versions) may still show the old name on records saved
  before the rename — the screens listed above already show the new one.
  On purpose, the **Activity Journal** keeps the name the person had when
  each event happened, and AVIR inspection reports keep the inspector name
  printed on them. A page or app screen
  that was already open shows the new name after its next refresh. More
  detail: "People's names after a rename" in the Reports help.
  *How to fix a misspelled mechanic name*: Settings → Users & Roles → find
  the person (mechanics are in the Mechanic and Senior mechanic sections)
  → **Edit** → correct First / Last name → **Save**. Their old work
  orders, timers and reports follow.

## Roles & Permissions

Roles are tenant-wide. System roles: **Owner** (always all permissions,
cannot be edited), General manager (all except billing), Manager (all except
roles & billing), Parts manager, Senior mechanic, Mechanic, Viewer. You can
create/clone custom roles and tick permissions in a grouped tree (Dashboard,
Calendar, Customers, Vendors, Parts, Parts Orders, Work Orders, Attachments,
Reports, Import/Export, Settings). System roles cannot be deleted; a custom
role in use by active users cannot be deleted either.

**Every checkbox is enforced by the server.** The permission tree matches
the real checks on each action, so unticking a key really blocks it:

- *Customers*: view / create / edit / deactivate / manage units / manage
  portal links (the "Send portal link" button on a customer profile).
- *Vendors*: view / create / edit / deactivate / **view portal logins &
  passwords** (the vendors' website logins: the Portal logins tab, Show /
  Copy of passwords — every reveal is in the Activity Journal; by default
  only Owner, General manager, Manager and Parts manager).
- *Parts*: view / create / edit / delete (deactivate) / adjust stock
  (location transfers, stocktakes) / **edit on-hand quantity directly**
  (the "In stock" field when editing a part and "Set" by location; by
  default only Owner, General manager and Manager — a Parts manager or a
  custom role needs it ticked explicitly) / view costs.
- *Parts Orders*: view / create (including the AI invoice parser) / edit
  (incl. confirm/reject of e-mailed orders) / delete / receive (receive,
  un-receive, returns) / manage payments.
- *Work Orders*: view / create / edit (saving an existing WO) / change
  status (Paid ↔ Open ↔ In progress) / delete / manage payments (record,
  delete, bulk payments) / send to customer (WO e-mail, receipts,
  authorizations) / export PDF / view costs / edit tracked hours.
- *Reports*: **view** (the Reports page and the money-free **Mechanic
  Hours** report), **view financial reports** (Sales Summary, General
  Revenue, Payments Summary, Customer Balances, Vendor Balances, Parts
  Orders Summary — cards are hidden and the API answers 403 without it),
  **view Timecard / Salary** (employee pay — a separate key, off by default
  for everyone except Owner / General manager / Manager), export (report
  PDF), view audit (Activity Journal).
- *Settings*: each page has its own key — organization, locations, users,
  **pay** (pay type / salary fields in the user form), roles, parts
  settings & pricing, WO presets, WO settings (labor rates, supply, cores,
  PDF design has its own key), workflows, notifications, integrations,
  billing.
- *Dashboard*: view / edit goals. The money blocks (Paid vs Unpaid, Parts
  Orders spend, Goals, Outstanding Balance) additionally need "View part
  costs inside WO"; without it the dashboard shows only the labor-hours
  chart.
- *Attachments*: view / upload / delete — the Upload button and the ×
  on a thumbnail appear only with the matching key.

Global search and the parts/WO pickers only return sections and prices
the user may see; a user without "View part costs inside WO" never gets
amounts, even in search results.

**Buttons follow permissions everywhere.** Every list and detail page
renders only the actions the role is allowed to perform: no "Add
Customer" without *Create customers*, no Pay / Delete / PDF buttons on the
work-orders list without the matching keys, no Receive / Pay / Return /
Edit / Delete on parts orders, no Save on the customer or unit form, no
New Stocktake / Complete / count fields without *Adjust stock*, and so on.
A user with only **"View work orders"** can open any work order read-only
(no Edit, Save, Paid, Delete...). The server checks each action
independently of what the page shows, so a hand-crafted request gets 403.

**Who may change roles and users.** Only an **Owner** can edit, deactivate
or re-assign the role of another owner account, and only an owner can
assign the Owner role. Nobody can change their own role or their own
per-user overrides. A user managing roles or overrides can only grant
permissions they have themselves (a General manager, who has no billing
access, cannot add "Manage subscription & billing" to any role or user);
removing permissions is always allowed.

**After an upgrade** that refines which key a page checks, run
`python -m app.scripts.sync_default_roles` once: system roles catch up
with the defaults, and custom roles receive the precise keys their broad
keys used to imply (e.g. a role with "Edit parts" gets the Parts Orders
keys; "View reports" also grants *view financial reports* and *view
Timecard / Salary*; "Manage users" also grants *pay*), so custom roles
don't lose access. System roles follow the defaults instead: after the
reports split a **Senior mechanic** no longer opens financial reports or
payroll (they never see prices elsewhere either), a **Viewer** and a
**Parts manager** keep financial reports but not payroll. Then tidy the
roles in this screen.

Key permission to know: **"View part costs inside WO"**
(work_orders.view_costs). A user with work-order access but WITHOUT this
permission works in **mechanic mode** — no prices anywhere (see the mechanic
doc). The default Mechanic and Senior mechanic roles are like this. The same
key is the "may see money" switch outside work orders too: customer
balances (list, profile, Current Balance), the Payments and Estimates tabs
of a customer, totals and amounts in the customer's / unit's work-order
history, the dashboard money blocks and the WO Cost button. A Senior
mechanic therefore sees customers, units and the parts catalog, but never a
dollar amount.

**"View part costs (avg cost / cost columns)"** (parts.view_costs) governs
the purchase side of the parts catalog: the Avg cost / Last price columns
and inventory totals on the Parts page, Core Cost / Credit columns in Cores
and Cores Returns, the Average cost field in the part form, costs in part
search results, part history and cross references. Without it the server
strips those fields from every response and keeps the stored average cost
untouched when such a user edits a part. Prices on parts orders are part of
the order itself and follow *View parts orders*; vendor balances and a
vendor's orders list also require *View parts orders*, because a balance
is the sum of unpaid orders.

**"Edit mechanics' actual tracked hours"** (work_orders.edit_time_logs)
unlocks the pencil next to the tracked-time summary on a work order's job —
correcting or deleting mechanics' timer sessions (forgotten timers,
accidental starts). Owner always has it; for tenants created after this
feature the Manager and General manager roles include it by default. For
existing custom or manager roles tick it manually in Roles & Permissions.

**"Edit / delete customer contacts in email dialogs"**
(work_orders.manage_email_contacts) controls the pencil/trash icons next to
contacts in the WO email dialogs (send work order, payment receipt,
authorization). It is ON for every role by default; untick it for a role to
make the recipients list read-only there (adding a new recipient on the fly
still works).

## Work Order Settings

- **Shop Supply Amount** — "Shop supply percentage" (default 5) applied to
  labor on WOs. On the work-order page the amount is part of the Labor
  total; on invoices/PDFs, emails, the customer portal and authorization
  pages the customer sees it once, as a separate **Shop Supplies** line
  before the Total, while the Labor line is labor only.
- **Core Charge Rules** — "Charge core by default" toggle for WO part lines.
- **Labor Rates** — name + hourly rate (defaults seeded: Standard $100,
  After Hours $150). Rates picked on WO labor lines and as customers'
  Default Labor Rate. Deleting is soft; old WOs keep their snapshotted rate.

## Service Templates (presets)

Reusable jobs: name, description, labor hours + rate ("— Use customer
default —" possible), parts list, "Allow customer discount on parts" flag.
Inserted into WOs via the labor block menu.

**Part prices in a template.** By default prices are live: each time a
template is shown or applied, the price recomputes from the part's current
selling price or the pricing matrix, so templates follow the catalog
automatically. The Service Templates page calculates every price with the
same code a work order uses, so a card shows what a work order gets
**today** for a customer on the standard pricing (no own scale, no
discount) — never the price that was there when the template was created:

1. a price **you typed into the template** (pinned) — used as is;
2. else the part's own fixed **Selling price** from the catalog (the
   "This part has selling price" box in Parts) — it does **not** follow the
   average cost;
3. else the **default pricing scale** applied to the part's **current
   average cost** — receive the part at a new cost and the template price
   changes by itself the next time the page is opened.

So that you can see at a glance why a line has the price it has, the card
(and the Price column in the editor) marks everything that is not plain
"scale on average cost":

- **custom price** (yellow) — pinned in this template by hand;
- **fixed price** (grey) — the part's own Selling price from the catalog.
  If such a line does not follow the cost, that is why: open Parts → edit
  the part and change the Selling price, or untick "This part has selling
  price" so the scale takes over (in templates and in work orders alike);
- **below cost** (red) — the price is lower than the part's current
  average cost, so every sale loses money (hover to see the cost). Typical
  case: a fixed Selling price of $6.00 set when the part cost $4.28, and the
  cost has since risen to $7.10. On a card with more than five parts the
  "… and N more" line shows how many hidden lines are below cost;
- **no price** (yellow) — the part has no average cost (never received) and
  no Selling price, so nothing can be calculated and a work order gets $0:
  receive the part, set its Selling price or type a price in the template;
- **no markup** (yellow) — no tier of the default scale covers this cost
  (the cost is outside every tier: below the first tier's From, above the
  last tier's To, in a gap between tiers wider than a cent, or the scale
  has no tiers). The card shows the cost itself: that is what a work order
  created by a mechanic or in the mobile app charges, while the office
  work order editor leaves the Price field empty for you to fill in. Add a
  tier for this cost in Settings → Parts & Pricing;
- **not in catalog** (grey) — the line is not a catalog part: a free-text
  line, or a part that was deleted or deactivated. The card shows the
  price saved with the template; the office work order editor uses it,
  while a template added by a mechanic (mechanic page, mobile app, Start
  work on an appointment) adds such a line without a price for the office
  to fill in.

The "(Estimated)" total at the bottom of a card is the sum of those prices
plus labor at the template's rate. A customer with their own pricing scale
("Override part selling price") or a parts discount gets their own prices
when the template lands on their work order.

If you **edit a price by hand**, it becomes a *pinned price*:
it is highlighted in the editor, saved with the template, and used in work
orders exactly as you typed it — it wins over the part's selling price and
the pricing matrix. Typing the same number the field already shows does not
pin anything, and an empty Price field is never saved as "$0" — to make a
line free on purpose, type `0`: it is pinned and shown as "@ $0.00 custom
price". Two things still beat a pinned price:

- a customer whose pricing policy has "override part selling price" — such
  customers always get prices computed from their own scale;
- editing the price directly on the WO line after applying the template.

This works the same way when a **mechanic** adds the template from the
mobile app or the mechanic page: mechanics never see prices, so the server
prices the lines itself and uses the pinned template price for them (unless
the customer has "override part selling price").

To un-pin a price, clear the price field in the template editor and click
away — the automatic price comes back and the row follows the catalog again.
Costs always stay live regardless of pinning.

## PDF Design

Per-shop invoice PDF settings with live preview: header/accent colors, show
logo (logo itself is uploaded on the Shops page), toggles for customer
email/phone, unit number/VIN/mileage, labor hours/rate code, parts detail,
core/misc charges, shop supply, paid & balance; thank-you message and
terms/notes footer.

On the PDF the **Labor** total line is labor only, **Parts** is parts only,
and Core Charges, **Shop Supplies** and Misc are their own lines right
before the Total (the per-job Subtotal is labor + parts without shop
supply). If you untick "Show shop supply", that line disappears and its
amount is folded back into the Labor line, the Labor item rows and the
per-job Subtotals; unticking "Show core charges" / "Show misc charges" folds
those amounts into the Parts line the same way — so the PDF still adds up
to the same Total, the customer just does not see the charge itemized. The
work-order email, the customer portal and the authorization pages always
list shop supply, cores and misc as their own lines regardless of these
toggles.

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

**Parts orders from email** — one switch: *Create parts orders from vendor
emails*. Turning it on (it saves immediately, no Save button) shows the
location's private address (`orders-<code>@roobico.com`) that only the AI
reads. Forward the mailbox where vendors write you to that address, or ask
vendors to CC it — **How to set up forwarding** under the address has
step-by-step instructions for Gmail / Google Workspace, Outlook.com /
Microsoft 365, Yahoo, iCloud, Zoho, GoDaddy / cPanel mailboxes and vendors'
portals. Forwarding the whole mailbox is fine: everything that is not an
order is ignored. Providers that send a confirmation code / link to the
new address (Gmail, Yahoo, iCloud, Zoho) — open that email with **View**
under *Recent emails*. Vendor order confirmations / invoices become parts
orders marked **Not confirmed** (Parts Orders tab).

- **Recent emails** (collapsed link) — every email that reached the
  address with its result (*Order created* → link, *Linked to order*,
  *Ignored* + reason, *Error*, *Rejected*) and actions: **View** (text and
  the lines AI read), **Create order** (force an order from an
  ignored/failed email), **Retry**, **Ignore**.
- **Issue a new address** (inside the forwarding instructions) — if the
  address leaked to spammers; the old one stops working at once, update
  the forwarding rule.
- Turning the switch off pauses reading; the address is kept.

Requires the "Manage 3rd-party integrations" permission. Confirming and
rejecting the orders themselves needs "edit parts". Details of matching,
duplicates and the confirm flow: Parts Orders help → "Orders from email".

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

Usage-based pricing with a **free tier: your first active location and
first active full user cost nothing — forever**. A solo shop (1 location,
1 user, no mechanics) pays $0/mo and is never blocked. Beyond the free
tier: $100/mo per extra active location + $50/mo per extra active full
user + $25/mo per active mechanic (mechanic & senior mechanic roles).
Example: 1 location with an owner and 2 mechanics = 2 × $25 = $50/mo;
2 locations with 3 office users and 4 mechanics = $100 + 2 × $50 + 4 × $25
= $300/mo. Only active locations and users count — deactivated staff is
free. The 30-day free trial has no limits at all: any number of locations,
users and mechanics is free until the trial ends.

**Annual billing — save 20%.** On the billing page you can switch the
billing period from monthly to annual ("Switch to annual" in the Monthly
price card). An annual subscription is billed once a year at 12 × the
monthly price minus 20% (e.g. $50/mo → $480/yr instead of $600),
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
- Units: pick the customer first in the **"Import units for customer"**
  search box on the Units tab — every row in the file is linked to that
  customer (one file per customer; a Customer Name column in the file is
  ignored). Columns: Unit Number, VIN, Year, Make, Model, Type, Mileage.
  A row needs at least a Unit Number or a VIN; a VIN that already exists
  for that customer is skipped as a duplicate. A units export of the whole
  shop lists every customer: split it by the Customer Name column and
  import one file per customer — rows whose Customer Name differs from the
  chosen customer are skipped and reported, never silently re-assigned.
- Vendors: Vendor Name (+contact, phone, email, website, address, notes).
  Websites are stored as working links (`fleetpride.com` →
  `https://fleetpride.com`); a value that is not a web address is dropped
  with a warning and the vendor is imported without it. Vendor portal
  logins / passwords are never exported and cannot be imported.
- Parts: Part Number, Description, Reference, In Stock, Average Cost,
  Selling Price, Min Stock, Unit. Starting stock is recorded properly: the
  part gets a location row and an "initial" stock movement, same as manual
  creation. A number in **Min Stock** switches "This part requires minimum
  stock" on with that value (empty = off); **Unit** accepts the unit's key,
  name or short form in any case (gallon / Gallon / gal / gallons) and an
  unknown unit is imported as Each. The export writes the same two columns
  (Min Stock empty when the part has no minimum, Unit as the short form).
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
