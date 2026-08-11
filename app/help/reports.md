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
  timer time), plus WO and labor-entry counts. If the uAttend integration is
  connected, a **uAttend Hours** column is added — attendance hours from the
  time clock for the same period. uAttend employees are matched to app users
  automatically (same matching as the Dashboard hours chart): hours of an
  employee matched to a mechanic land in that mechanic's row; employees
  matched to non-mechanics (managers, owner) are excluded; unmatched
  employees appear as their own rows. This means a mechanic who never
  presses the job timer still shows attendance hours here — use it to see
  who was at work even when Tracked Hours is empty.
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
  twice — an employee matched to a **salary** user is folded into that salary
  row with a "↔ uAttend" badge. An employee matched to an **hourly** user
  (e.g. a mechanic) keeps their own hourly row — with the internal user's
  role and a "↔ Internal" badge — whether or not that user ever logs in.
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
  target by days. Below the rings, the same card shows **Labor Hours —
  Cumulative** — a line chart with up to three lines so you can see how they
  diverge:
  - **Actual** — time mechanics really spent, from job timers (start/stop on
    a WO labor line; covers both jobs assigned by the office and jobs
    mechanics picked up themselves). Only users with a mechanic /
    senior mechanic role count — a manager starting a timer is ignored;
  - **Invoiced** — labor hours billed on work orders (by WO date);
  - **uAttend** — hours from the uAttend time clock; the line appears only
    when the uAttend integration is connected in Settings → Integrations.
    Mechanics only here too: a uAttend employee matched to an internal user
    without a mechanic role (a manager, an owner) is excluded; unmatched
    uAttend employees still count — they are shop-floor mechanics without
    a system account.
  The lines are running totals: each day adds that day's hours, so every
  line starts at zero and only goes up — the gap between lines is the
  accumulated difference. Points are daily; All Time shows the last
  12 months. Hover a day to see the totals so far and that day's hours
  (the "+x.xx" value).
  The summary below gives totals, **Invoiced ÷ Actual** (billed hours per
  hour of tracked work — above 100% you bill more than the time spent) and
  **Actual ÷ uAttend** (share of the clocked shift spent on WO jobs), plus a
  per-mechanic table: Actual / Invoiced / uAttend hours side by side ("—"
  means no data of that kind). Hours with no assigned mechanic show as the
  "Unassigned labor" row, so the table always adds up to the chart totals.
  uAttend employees are merged into a mechanic's row using the same
  AI matching as the Timecard / Salary report. Matching refreshes itself
  whenever the roster changes on either side (a user or uAttend employee is
  added, renamed, deactivated or (de)selected for sync) — no manual step
  needed. An employee that stays on a separate row simply could not be
  matched confidently: give them the same name or email in both systems
  and the rows merge on the next dashboard load.
- **Outstanding Balance (All Time)** — total customers owe; ignores the date
  filter on purpose.
- Quick actions: Create WO, Create PO, New Customer, New Vendor.

## Calendar

Sidebar → **Calendar** — weekly appointment scheduler (Mon–Sun, 06:00–21:00,
15-minute steps). Click an empty slot to create, click an event to edit,
drag & drop to move. Appointment fields: Customer, Unit, Service Presets,
Start/End, Assign to Mechanic, Status, Title/Note. When customer + unit are
set, a **"Create Work Order"** button opens a prefilled WO. Statuses
(Scheduled, Confirmed, In Progress, Completed, Cancelled) and their colors
are editable per shop via the gear → "Appointment Statuses".
