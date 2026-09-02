# Customers, Units and the Customer Portal

## Customers list

**Customers** in the sidebar. Button **"Add Customer"**. Search "Search
customers by any field..." (company, contact names, phone, email, address).
Columns: Company (address underneath), Name (main contact; "Inactive" badge
for deactivated), Phone, Email, **Current Balance**, Actions → "Open".
Rows are clickable.

## Customer record

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

- **Default Labor Rate** — pre-selected labor rate on this customer's WOs.
  Never empty: falls back to the shop default ("standard").
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

- Units are created from the **work order flow** ("+ Add Unit") — there is
  no add-unit button on the customer page itself.
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
There is no customer merge feature.

## "NEW Customer" (system customer)

If mechanics use VIN scanning in the mobile app, the shop gets a technical
customer named **"NEW Customer"**. When a mechanic scans a VIN that is not
in the system yet, the unit is created under it automatically so work can
start immediately. This customer **cannot be deactivated** — instead, move
its work orders to the real company with the **Transfer** button on the WO
page (the unit follows the work order automatically), and its stray units
can be edited/deactivated like any others once they are re-homed.
