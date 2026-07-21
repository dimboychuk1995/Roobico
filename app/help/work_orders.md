# Work Orders

A work order (WO) is the central document of a repair job: customer, unit
(vehicle), labor lines ("jobs"), parts, charges, tax, payments. The WO number
doubles as the invoice number (numbering starts at 1000, per shop).

## Work Orders list page

Three tabs: **"Work Orders"**, **"Payments"**, **"Estimates"**.

- Filters: search "Search work orders by any field...", paid-status dropdown
  (All / Paid / Unpaid / In Progress), date presets (Today ... All Time,
  Custom Range; default This Month) filtering by the work order date.
- Columns: WO #, Customer, Date, Unit, Mileage, Labor, Parts, Tax, Total,
  Balance, Paid (status badge), Actions (Pay / Download PDF / Edit / Delete).
- Footer totals for the current filter: Labor, Parts, Tax, Total, Unpaid.
- **Payments** tab — every payment recorded on any WO. **Estimates** tab —
  WOs whose status is an estimate/quote (there is no separate "estimate"
  object; it's a status filter).
- Header buttons: **"Create Work Order"** and **"Bulk Payment"**.

## Creating a work order

1. **Customer** — must already exist (create customers in the Customers
   section; there is no inline customer creation here). Selecting one shows a
   Customer Info panel with an "Open profile" link.
2. **Unit** — pick the customer's unit, or click **"+ Add Unit"** to create
   one inline (VIN auto-fills Make/Model/Year/Type via the VIN decoder).
3. **Mileage** is required to create. **Work Order Date** defaults to today
   and can be changed.
4. Buttons: **"Create Work Order"**, or from its dropdown: **"Save In
   Progress"**, **"Create Annual Inspection"**. There is also
   **"📷 Recognize WO"** — upload a photo/PDF of a handwritten work order and
   AI fills the jobs and parts (review before applying).

## Statuses

- **Open (Unpaid)** — default after creation.
- **In Progress** — set by "Save In Progress", by mechanics, or automatically
  when a mechanic starts a job timer.
- **Paid** — set automatically when the remaining balance reaches $0.
- **Paid WOs are locked** — they cannot be edited until made unpaid.
- **Careful:** switching a WO back to Unpaid/In Progress **deletes all its
  payment records**. The delete-WO confirmation also warns about this.
- Deleting a WO returns its parts to inventory and removes payments.

## Labor blocks (jobs)

Each block has: **Labor Description**, **Hours**, **Labor Rate** (dropdown of
the shop's rates, shown as "Name ($X/hr)"; preselected from the customer's
Default Labor Rate), and an editable **Labor Total** override. If you type a
manual Labor Total it wins; otherwise labor = hours × rate. The hourly rate is
snapshotted on save, so changing a rate later never changes old WOs.

- **Assign** button → "Assign Mechanics" modal: pick one or several
  mechanics. One mechanic = 100%; several = percentages auto-split evenly and
  can be edited. This split drives the Mechanic Hours report.
- **Shop supply** is a percentage of labor (default 5%, configurable), shown
  as an editable "Shop supply: $" field with a reset (↺) button. It is part
  of the Labor total.
- Block menu (⋮): **"Add Preset"**, **"Describe Issue"** (AI polishes/
  translates the issue text into professional English for the customer),
  **"Send for Authorization"** (this labor only).

## Parts on a work order

Type in the Part Number cell (3+ characters) to search the catalog. The
dropdown always offers **"+ One-time part"** — a manual line with no
inventory link (editable cost, never deducted from stock). Catalog results
show stock, avg cost and cross-reference alternates ("⇄ Cross ref for ...")
so you can pick an interchangeable part.

Price auto-fill precedence:
1. customer has **Override part selling price** → price is always computed
   from cost via the customer's pricing scale;
2. else the part's fixed **Selling price** (if set);
3. else computed from average cost via the pricing scale (margin/markup).
A price you edit by hand is never overwritten.

- **Core** toggle appears on parts with a core charge; line total =
  (price + core) × qty. If you do NOT charge the core, the quantity is
  automatically tracked in the shop's Cores list (to send back to vendors).
- Inventory is deducted on save (one-off and "do not track" parts excluded).
  **Stock may go negative** — the WO always saves; shortages appear as
  warnings.
- **"+ Add Misc Charge"**: description, quantity, price, Taxable checkbox.
  Parts can also carry their own automatic misc charges from the catalog.

## Tax and totals

- **Taxable** toggle on the WO defaults from the customer's Taxable flag.
- Tax applies to parts + taxable misc charges only — **labor is never
  taxed**. Rate comes from the shop ZIP (or the custom rate in Settings →
  Parts Settings); the rate is locked into the WO at save time.
- Grand total = Labor total (labor + shop supply) + Parts total (parts +
  cores + misc) + sales tax.
- **"Work Order Cost"** button — internal profit view: enter mechanics'
  hourly cost rates (stored only in your browser) and see per-job cost,
  revenue and profit.

## Presets (Service Templates)

Settings → **Service Templates**. A preset = name, labor hours + rate,
parts list, and an "Allow customer discount on parts" flag. Insert into a WO
via the labor block menu → "Add Preset". Preset prices self-heal: they always
pull live costs/prices from the catalog when applied.

## Authorizations (customer approval)

**"✉ Send for Authorization"** (whole WO or one labor). The customer gets an
email with the PDF and a link to a public page with **"✓ Approve"** /
**"✕ Decline"** buttons and an optional comment. The result shows as a green
"✓ Authorized" / red "✕ Declined" badge on the WO and labor (comment in the
tooltip), and in the customer portal history.

## Payments

**"Paid"** button → Record Payment: Invoice Total / Already Paid / Remaining
Balance are shown; amount pre-filled with the balance. Methods: **Cash,
Check, Credit Card, ACH, Other**. Partial payments are supported; overpaying
is blocked. Attachments (check photo etc.) can be added. A payment receipt
email can be sent.

**Bulk Payment** (list page): pick a customer, enter the amount received,
click **Auto-Distribute** — it spreads the money across the oldest unpaid
invoices; adjust per-WO amounts if needed and record all at once.

## PDF, email, inspections

- **Download PDF** — the invoice PDF (WorkOrder-<number>.pdf). Layout
  (logo etc.) is configured in Settings → PDF design.
- **Email Work Order** — sends the PDF to chosen customer contacts (you can
  add a new contact on the fly and save it to the customer). The email also
  includes a customer portal link.
- **Create Annual Inspection** — generates a DOT Annual Vehicle Inspection
  (49 CFR 396) PDF for the unit; only the latest inspection per unit is kept.

## Attachments

Files can be attached at three levels: the work order, each labor block, and
each payment. All are visible in the editor and deleted with the WO.
