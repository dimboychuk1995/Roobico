# Reports, Dashboard and Calendar

## Reports index

**Reports** in the sidebar — cards grouped by topic:

- Sales & Revenue: **Sales Summary**, **General Revenue**, **Payments
  Summary**.
- Balances: **Customer Balances**, **Vendor Balances**.
- Parts & Inventory: **Parts Orders Summary**.
- Labor: **Mechanic Hours**, **Timecard / Salary Report**.
- Audit: **Activity Journal**.

The first seven open the shared Standard Reports page as tabs. Pick filters
and click **"Generate"**; **"Download PDF"** exports the same report.

## Common filters

- **Date Preset**: All Time, Today, Yesterday, This Week, Last Week, This
  Month (default), Last Month, This Quarter, Last Quarter, This Year, Last
  Year, Custom (+ Date From / Date To). Weeks start Monday; dates use the
  shop timezone.
- **Chart Group By**: Month or Week (on Sales, Payments, Parts Orders,
  Mechanic Hours, General Revenue).
- **Customers** multi-select (Sales, Payments, Customer Balances, General
  Revenue) and **Vendors** multi-select (Parts Orders Summary) with search
  and Select All / Deselect All.
- **Customer Balances and Vendor Balances are always all-time** — they have
  no date filter by design.

## What each tab shows

- **Sales Summary** — billed totals per customer: Orders, Labor, Parts,
  Parts Cost, Tax, Hours, Revenue. WOs without a customer are excluded.
  Summary adds avg ticket and invoiced hours.
- **Payments Summary** — money received per customer: payment count, amount,
  average payment. Dated by payment date.
- **Customer Balances** — Billed / Paid / Outstanding per customer
  (outstanding floored at 0 per WO).
- **Vendor Balances** — Orders / Total / Paid / Outstanding per vendor for
  parts orders.
- **Parts Orders Summary** — vendor spending split: Parts, Cores, Shop
  Supply, Tools, Utilities, Pmt to Svc, totals, paid and balance.
- **Mechanic Hours** — per mechanic: **Billed Hours** (labor hours from WOs
  allocated by the assignment percentages) vs **Tracked Hours** (real job
  timer time), plus WO and labor-entry counts.
- **General Revenue** — see below.

## General Revenue report

Two headline blocks at the top:

- **"Revenue − Parts Cost"** — all income minus the shop's cost of parts
  used on Work Orders (payroll NOT subtracted).
- **"Revenue − Parts Orders − Salaries"** — all income minus real vendor
  spending minus payroll.

Under them the same formulas are printed with the actual numbers, then an
Activity strip (WO count, PO count, hours billed, mechanic hours, weeks) and
a detail table where every row has a "What is this" explanation, grouped
into: Money In (labor, parts, cores, misc, tax → Total Revenue), Money Out —
Parts Orders, Money Out — Payroll (salaries × weeks + uAttend hourly, per
employee), two Bottom Line calculations (Cash view and Job view) and
Mechanic Hours.

Notes: Total Revenue includes collected sales tax. If a customer filter is
applied, payroll and parts orders are counted as $0 (they can't be tied to
specific customers) — a banner explains this. If payroll isn't configured,
salaries count as $0.

## Timecard / Salary Report

Payroll per employee for a period (This week / Last week / This month / Last
month / This year / Custom):

- **Salary** employees: weekly `salary_amount` × weeks in the period
  (days ÷ 7; a shorter range prorates fractionally).
- **Hourly** employees: uAttend punches in the period × their hourly rate
  (set in Settings → Integrations; only employees ticked there appear).
- uAttend people are auto-matched to Roobico users (AI) so nobody is counted
  twice — a matched employee shows inside their salary row with a
  "↔ uAttend" badge.
- Banners explain when the uAttend integration is missing/disabled/erroring.
  The same payroll numbers feed the General Revenue report.

## Activity Journal

Audit log of every create/edit/delete action in the system (reads are not
logged): when, method, endpoint, path, status, user, shop, error. Filters by
method and endpoint. Passwords/tokens are masked.

## Dashboard

Sidebar → **Dashboard**. Date presets like reports (default This Month).
Widgets:

- **Work Orders Paid vs Unpaid (Money)** — donut with paid %, WO count,
  Labor/Parts/Total/Unpaid totals.
- **Parts Orders: Received vs Ordered** — received % (count) and paid %
  (amount), Items / Non-inventory / Spend.
- **Goals Progress** — three rings (Labor, Parts Sales, Total) against
  monthly goals set via **"Edit Goals"**; other periods prorate the monthly
  target by days.
- **Outstanding Balance (All Time)** — total customers owe; ignores the date
  filter on purpose.
- **Labor Hours By Mechanic** for the period.
- Quick actions: Create WO, Create PO, New Customer, New Vendor.

## Calendar

Sidebar → **Calendar** — weekly appointment scheduler (Mon–Sun, 06:00–21:00,
15-minute steps). Click an empty slot to create, click an event to edit,
drag & drop to move. Appointment fields: Customer, Unit, Service Presets,
Start/End, Assign to Mechanic, Status, Title/Note. When customer + unit are
set, a **"Create Work Order"** button opens a prefilled WO. Statuses
(Scheduled, Confirmed, In Progress, Completed, Cancelled) and their colors
are editable per shop via the gear → "Appointment Statuses".
