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
- Under the status badge the list also shows mechanic activity: a green
  "● <names>" line — mechanics with a running job timer on this WO right
  now — and a green **"Mechanic done"** badge when the mechanic saved the
  WO as "Done, ready for review" (the WO stays In Progress until you
  complete it; the badge clears if a mechanic starts a timer again).
- **"In Work" group** — WOs that mechanics have taken but not marked done
  yet are grouped at the top of the table under an "In Work · taken by
  mechanics, not marked done yet" header, highlighted with the in-progress
  color, so the shop floor status is visible at a glance. They are not
  duplicated below — each WO appears once.
- **Push notifications**: office users with the mobile app installed also
  get a phone notification the moment a mechanic takes a WO into work and
  when they mark it Done — see the Mechanic mode & Mobile app help for
  details.
- **Confirming a mechanic's work**: when a WO shows "Mechanic done", a
  manager can confirm it from the mobile app WO page ("Confirm work
  order"). A confirmed WO gets a **Confirmed** badge and is **locked for
  mechanics** — they cannot open or edit it until the manager cancels the
  confirmation ("Cancel confirmation"). Confirming does not change the WO
  status — complete it and collect payment as usual.
- Footer totals for the current filter: Labor, Parts, Tax, Total, Unpaid.
  Estimates are excluded from the main tab and its totals — they live on
  their own tab.
- **Payments** tab — every payment recorded on any WO, with its own search
  and date filter; the totals row at the bottom sums exactly the filtered
  payments. **Estimates** tab — all quotes (WOs saved as estimates); Edit
  opens the estimate page. See "Estimate" in Statuses below.
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
- **Estimate** — a quote: created via the Create dropdown → **"Save as
  Estimate"**. Estimates live on the **Estimates** tab of the WO list (they
  are hidden from the main Work Orders tab and its totals), take **no parts
  from inventory**, cannot receive payments, and are edited only in the
  office web interface (locked for mechanics and the mobile editor). An
  open estimate shows an **Estimate** badge on its page; a plain Save keeps
  it an estimate. **Convert to Work Order** (Save dropdown) turns it into a
  normal open WO — at that moment all its parts are deducted from stock and
  core tracking starts. There is no reverse conversion.
- **Estimates can be sent for customer approval** exactly like a WO: the
  same "Send for approval" flow, the same email with the PDF, and the same
  green **✓ Authorized** badge when the customer approves — approval does
  not change the status or touch inventory, it is the customer's sign-off
  on the quote.
- **Paid** — set automatically when the remaining balance reaches $0.
- **Paid WOs are locked** — they cannot be edited until made unpaid.
- **Careful:** switching a WO back to Unpaid/In Progress **deletes all its
  payment records**. The delete-WO confirmation also warns about this.
- Deleting a WO returns its parts to inventory and removes payments.

## Parts orders for a work order

In the office interface (requires the "View part costs inside WO"
permission), the Customer & Unit section of a WO has a discreet
**Parts orders** button under the unit row — on existing WOs and on the
create screen alike. It expands a block where you can:

- **Create parts order** — opens the SAME order dialog as on the Parts page
  (Parts → Order Parts), with everything it can do there: vendor search,
  order date, part search with cross-references and stock counts, core
  charges, non-inventory amounts (shop supplies, tools…), the running order
  total, and the **AI Order Reader** — upload a vendor invoice (PDF/photo)
  and it picks the vendor and fills the items; unknown vendors and parts can
  be created right from the scan results without leaving the dialog. The
  only difference from the Parts page: the order is automatically linked to
  this work order. It ALSO appears on Parts → Parts Orders like any other
  order, marked with a **WO #** badge that links back to the work order.
- The work orders table shows the mirror of that badge: each WO row with
  linked orders carries **PO #** badges; clicking one opens that parts
  order on the Parts page.
- Orders can be created **before the WO is saved**: they attach to the page
  and link to the work order automatically the moment you press Create
  Work Order (the WO # badge appears then).
- See every linked order with its **status** (ordered / received) and
  **payment status** (unpaid / partial / paid), total and balance, and act
  right there: click the order number (or **Open**) to view/edit it in the
  full dialog, **Receive** (asks for the vendor bill and stock locations,
  then updates stock) and **Pay** (records a payment with attachments for
  the receipt).
- **Usage check** — shown once the WO is accepted (a real work order, not
  an estimate and not an unsaved page): each order item is shown as a
  chip — green when the WO actually uses that many of the part, yellow when
  only part of the ordered quantity is on the WO (e.g. "2/3"), red when the
  part is not on the WO at all. A warning box lists everything ordered for
  this WO but not used in it, so nothing bought for a job gets forgotten on
  a shelf. While the WO is still an estimate or hasn't been created yet,
  the chips stay neutral and no "not used" warnings appear — the job list
  isn't final yet, so the comparison would only add noise.

## Labor blocks (jobs)

Each block has: **Labor Description**, **Hours**, **Labor Rate** (dropdown of
the shop's rates, shown as "Name ($X/hr)"; preselected from the customer's
Default Labor Rate), and an editable **Labor Total** override. If you type a
manual Labor Total it wins; otherwise labor = hours × rate. The hourly rate is
snapshotted on save, so changing a rate later never changes old WOs.

For jobs that mechanics created themselves, **Hours auto-fill from the
mechanics' tracked timer time** and keep following it until someone edits
Hours manually; jobs applied from a preset keep the preset's hours instead.
You can always type your own Hours or Labor Total — a manual value stops the
auto-fill for that job.

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
- **Create Annual Inspection** — fills out a DOT Annual Vehicle Inspection
  report (49 CFR 396) for the unit. The dialog has the carrier/inspector
  fields, an optional **Report Number** (your own numbering; auto-generated
  when left empty), a live PDF preview and the full **component checklist**
  (Brake System, Coupling Devices, … Windshield Wipers): every item starts
  as **OK**; switch an item to **NR** (needs repair — a repaired-date field
  appears) or **NA** (does not apply). Picking a **vehicle type** applies
  its OK/NA preset automatically (e.g. Semi Trailer marks steering, exhaust,
  coupling etc. as N/A); adjust any item after that. "Mark all OK" /
  "Clear" set the whole list at once; "Clear" prints an empty form to be
  filled by hand. On the printed form OK items get a check mark in the OK
  column, needs-repair items get an X plus the repaired date, and N/A items
  are marked in the third column.
- Inspections are **kept as history** per unit (a new one does not erase the
  previous). Each inspection is valid for 12 months — the unit page shows
  the latest one with its "Valid until" date (yellow when it expires within
  30 days, red when expired), the full history with PDFs, and Delete
  buttons (requires the "Delete work orders" permission).

## Attachments

Files can be attached at three levels: the work order, each labor block, and
each payment. All are visible in the editor and deleted with the WO.
