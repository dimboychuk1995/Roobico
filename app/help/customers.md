# Customers, Units and the Customer Portal

## Customers list

**Customers** in the sidebar. Button **"Add Customer"**. Search "Search
customers by any field..." (company, contact names, phone, email, address).
Next to the search box is the status filter **Active customers** (default) /
**Inactive customers** / **All customers** — the same filter exists on
Vendors, Parts, Users and the customer's Units tab. Columns: Company
(address underneath), Name (main contact; "Inactive" badge for
deactivated), Phone, Email, **Current Balance**, Actions → "Open". Rows are
clickable. The **Current Balance** column (and the balance on the customer
profile, the Payments and Estimates tabs, totals in the work-order history)
appear only for users with "View part costs inside WO"; a Senior mechanic
sees the customer without any amounts, and a paid work order is shown to
them as "completed". "Add Customer" needs "Create customers", the profile
Save button "Edit customers", "Portal Link" "Manage customer portal links".

## Customer record

- The **Add Customer** dialog has: Company Name, **Default Labor Rate**
  (the shop default, usually "Standard", is preselected), Taxable, Pricing
  Scale, Override part selling price, Address and the Contacts block. All
  of them can be changed later on the Details tab.
- One record type for both companies and private clients. Rule: **"You must
  enter either Company Name or at least one contact with a name."**
- **Contacts** — any number; each has First/Last Name, Phone, Email; exactly
  one is the **"Main contact"** (shown in tables, default email recipient).
  There are no separate customer-level phone/email fields.
- **Address** — required (autocomplete).
- **Taxable** switch — "No" = tax exempt; it presets the Taxable toggle on
  the customer's work orders.
- There is no customer notes field; use attachments on the customer page if
  needed.

## Per-customer settings (summary card + Details tab)

- **Default Labor Rate** — pre-selected labor rate on this customer's WOs
  (on every new labor line). Chosen already in the Add Customer dialog at
  creation (the shop default, usually "Standard", is preselected there) and
  changeable later on the Details tab. Never empty: falls back to the shop
  default ("standard").
- **Pricing Scale** — which margin/markup scale prices parts on this
  customer's WOs ("Choose which pricing scale is applied to parts in work
  orders for this customer."). This is how one customer gets special parts
  pricing.
- **Override part selling price** — "Always use the scale, ignore part's
  selling price". Shows an "Override" badge on the summary card.

## Customer page tabs

**Work Orders** (search, paid filter, date presets; footer totals Labor /
Parts / Total / Unpaid) · **Units** · **Payments** (payments across their
WOs; delete possible) · **Estimates** (WOs in estimate/quote status) ·
**Details** (the edit form + attachments).

**Current Balance** = Σ over active WOs of max(0, total − paid) — an
overpaid WO never offsets another one.

Header buttons: **"Back"**, **"Portal Link"**, **"Create Work Order"**.

## Units (vehicles)

- Units are created either from the customer page — **Units tab → "Add
  Unit"** (needs the "Create/edit customer units" permission; a deactivated
  customer cannot get new units) — or from the **work order flow** ("+ Add
  Unit" next to the unit picker). Both open the same form.
- Fields: **Unit Number**, **VIN** (typing a 17-character VIN auto-fills
  Year, Make, Model, Type via the national VIN decoder), Year, Make, Model,
  Type, Mileage. License plates are not stored.
- Unit page tabs: **Work Orders** (full service history; "Show labor
  details" expands a card per job — description, hours badge, labor total,
  and a parts table with qty, price, per-line totals and a parts subtotal)
  and **Details** (edit, **Deactivate Unit** / **Activate Unit**,
  attachments, **Create Work Order**).
- **Recalls** button — checks NHTSA safety recalls for the unit (needs
  Year+Make+Model or a valid VIN). New recalls show a "NEW" badge; a nightly
  job also emails customers a recall digest automatically.
- **Annual Inspection** card shows the unit's latest DOT annual inspection
  with its **Valid until** date — an
  inspection lasts 12 months, the card turns yellow within 30 days of
  expiry and red once expired. **History** expands all previous inspections
  of the unit; each row has its own Download PDF, and users with the
  "Delete work orders" permission can delete records. A new inspection can
  be created right here — the **Annual Inspection** button in the unit
  header (and the **Inspection** button on each row of the customer's
  Units tab) opens the same dialog as the work order editor, prefilled
  with the unit's VIN and the customer as the carrier.

## Customer portal

- Staff send access from the customer page: **"Portal Link"** → enter one or
  more emails → the customer gets a "Your Customer Portal" email. The link
  is valid **30 days** and auto-renews while in use; re-sending usually
  keeps the same link.
- The portal is **read-only** ("This is a read-only customer portal. To make
  changes, please contact the shop."). The customer sees tabs: **Work
  Orders** (with totals, paid, balance; each opens a detailed view with a
  Download PDF button), **Vehicles** (clicking one filters the other tabs;
  per-vehicle **Recalls** check), **Payments**, **Authorizations** (history
  of approvals/declines), **Maintenance Files** (quarterly maintenance PDF
  per vehicle: pick Quarter + Year → Download PDF).
- Approving/declining work happens through the separate **authorization
  email link** (see the Work Orders doc), not through the portal — the
  portal only shows the history.

## Deactivating

Customers and units are deactivated (soft), never deleted — history stays.
The single exception is the system customer "NEW Customer": its stray units
that carry no history at all are deleted by the nightly cleanup (see below).
There is no customer merge feature.

- **Finding an inactive one**: Customers list → status filter **Inactive
  customers** (or **All**); on a customer page → **Units** tab → **Inactive
  units**. Inactive rows carry a grey **Inactive** badge.
- **Activating back**: open the customer → **Activate Customer**; open the
  unit → **Details** → **Activate Unit**. Everything (history, units,
  balances) is where it was.
- Inactive customers and units are hidden from work-order pickers, the
  mobile app and the customer portal until activated.

## "NEW Customer" (system customer)

If mechanics use VIN scanning in the mobile app, the shop gets a technical
customer named **"NEW Customer"**. When a mechanic scans a VIN that is not
in the system yet, the unit is created under it automatically so work can
start immediately. This customer **cannot be deactivated** — instead, move
its work orders to the real company with the **Transfer** button on the WO
page (the unit follows the work order automatically, and labor rates, part
prices and the tax flag are recalculated for the real company).

Nobody has to tidy "NEW Customer" up by hand: **every night** the system
deletes the stray units under it — units that have **no work order (or
estimate), no calendar appointment, no annual inspection and no attached
file**. Anything scanned or edited in the last 6 hours is left alone, so a
VIN a mechanic has just scanned is never touched. A unit that has a work
order stays until the office transfers that WO. After the Transfer the
original unit left behind under "NEW Customer" (deactivated when the
Transfer created a copy for the real company, or simply no longer linked
when the company already had that VIN) is deleted the next night — unless
it still has attached files, an annual inspection or an appointment of its
own, which Transfer does not move; move or delete those first. Deleted
units are listed in Reports → Activity Journal (source "System", user
"Nightly cleanup") with their VIN, unit # and id. Only the system customer
is cleaned this way — an ordinary customer that merely happens to be named
"New Customer" and its units are never touched.
