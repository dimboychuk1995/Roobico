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

**Who sees which card.** "View reports" opens the page and the money-free
**Mechanic Hours** report. The Sales & Revenue, Balances and Parts &
Inventory cards need **"View financial reports"**, **Timecard / Salary**
needs **"View Timecard / Salary report"**, the Activity Journal needs
"View audit journal", and the PDF button needs "Export reports". Cards you
are not allowed to open are not shown; opening a report by URL without the
right returns to the Reports page (the API answers 403). Default roles:
Owner / General manager / Manager see everything, Parts manager and Viewer
see financial reports but not payroll, Senior mechanic and Mechanic see
only Mechanic Hours (a mechanic has no Reports section at all).

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

Requires the permission **"View Timecard / Salary report (employee pay)"**
— by default only Owner, General manager and Manager; tick it for a
bookkeeper role in Settings → Roles. Payroll per employee for a period
(This week / Last week / This month / Last month / This year / Custom):

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

Reports → **Activity Journal** (permission "View audit journal") — the
complete history of who did what in the shop. Every change made through
the web app, the mobile app, the customer portal or the e-mail inbox lands
here; reads/views are not logged.

Each row shows **When** (shop time), **Who** (user name and role, or
"Customer" for portal actions and "System" for automatic ones), **Action**
(Created, Updated, Deleted, Status changed, Payment, Sent, Received,
Returned, Activated/Deactivated, Signed in/out, Timer, Marked done,
Confirmed, Transferred, Imported, Uploaded, Permissions changed, Settings
changed, Access denied, Failed request), **Object** (the work order, unit,
customer, part, parts order, vendor, user, role, appointment, … — a link
opens it) and **What happened** — a plain sentence such as
"WO #1042: status open → paid" or "Payment $250.00 (card) recorded for
WO #1042 — balance $0.00". Click a row to expand the details:

- a **Before / After** table of every field that changed (customer name,
  address, part cost, unit mileage, role permissions, WO jobs and hours,
  appointment time, …);
- extra details (amounts, quantities, recipients, files);
- technical trace: HTTP method and path, status code, IP, user id.

Business events are recorded for work orders (create, save with job/part
changes, status, mechanic Done, manager confirmation, payments and their
removal, bulk payments, e-mails, authorizations and the customer's
approve/decline, transfer, delete, timers start/stop, tracked-hours edits,
annual inspections), customers and units (create, update, activate /
deactivate, both web and mobile), vendors, parts (create, update with
before/after of cost and price, stock adjustments and transfers,
deactivate, cross references), parts orders (create, update, receive,
un-receive, returns, payments, e-mail order confirm/reject, delete), cores,
stocktakes, appointments, attachments, users (invite, edit, activate /
deactivate, per-user permissions), roles (create, edit with the list of
added/removed permissions, clone, delete), locations, presets, labor
rates, data imports, and sign-ins / sign-outs (web and mobile).

Anything else that changes data (settings pages, integrations, billing,
AI tools) is still journaled as a **technical request** with a readable
name ("PDF design saved", "Integration settings saved"); failed requests
and access denials are journaled as well, so attempts to do something
without permission are visible.

**Filters**: free-text search (WO number, customer, part number, user
name, any changed value), period presets or a custom range, user, object
type, action, source (web / mobile / portal / system), location (for
multi-location tenants) and "Show": everything, business events only or
technical requests only. The **History** button on a work order or
customer page opens the journal filtered to that one object. Passwords and
tokens are masked everywhere.

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

Sidebar → **Calendar** — the shop's schedule and its activity log in one
grid (Mon–Sun, 06:00–21:00, 15-minute steps). It shows two kinds of blocks:

- **Appointments** — what you planned by hand. Click an empty slot to
  create one, click a block to edit, drag & drop to move. Fields: Customer,
  Unit, Service Presets, Date, Start/End, Assign to Mechanic, Status,
  Title/Note. Appointment statuses (Scheduled, Confirmed, In Progress,
  Completed, Cancelled) and their colors are editable per shop via the
  gear → "Appointment Statuses".
- **Work orders** — every active WO appears on the calendar automatically,
  even if nobody ever scheduled it, so you can see who did what, when, and
  what is still waiting. A WO block has a left edge in the mechanic's colour
  (plus their initials in the corner) and a status chip; the legend under
  the toolbar explains the colors:
  - **Not started** — WO exists, no mechanic has started it;
  - **In progress** — a mechanic is working on it (a pulsing chip means a
    timer is running right now);
  - **Mechanic done** — all mechanics pressed Done, the manager has not
    reviewed it yet;
  - **Completed** — the work is finished (manager saved/confirmed it or
    hours were tracked), payment still pending;
  - **Paid** — invoice paid.
  Estimates are not shown (they are quotes, not work). In mechanic mode
  "Paid" is shown as "Completed" — mechanics never see money.

**Where a work order lands on the grid.** If mechanics tracked time on it,
the WO shows as one block per mechanic per work session, exactly when that
mechanic's timer was running (short stop/start pauses under 20 minutes are
merged). So a mechanic's blocks never overlap: if Sergey worked on WO A
8:00–9:00, then on WO B 9:10–11:00 and came back to WO A after lunch, you
see three separate blocks, not two day-long bars. Two mechanics on the same
job show as two blocks with their names. A WO with no tracked time and no
appointment is **not placed on the grid at all** — it sits as a chip in the
**"Not scheduled"** strip under the day (or mechanic) header on its Work
Order Date, so the calendar never invents a time. A WO with an
appointment is "scheduled" and has no chip. Click a WO block or chip for
the summary — customer, unit, mechanic hours, jobs, planned hours, its
appointments — and **Open Work Order**.

**Views and filters** (toolbar, right side):

- **Week** — classic 7-day grid. Inside every day column each mechanic
  who has blocks that day gets their own narrow lane (same order as the
  "By mechanic" view), so one mechanic's blocks never mix with another's.
  Click a day header to expand that day: the wide column splits into **one
  lane per mechanic of the shop** (three mechanics = three lanes), each
  with the mechanic's marker and name in the header above its lane; an
  "Unassigned" lane appears only when something has no mechanic. Dropping
  a chip or a plan into a lane assigns it to that mechanic. Click the
  header a second time to make the day even wider (the other six days
  shrink to narrow strips), and a third time to go back to the normal
  week.
- **Who is who**: every mechanic has a colour. The row of markers on top
  (colour, initials, name) is the key; each block carries the same colour
  on its left edge and the mechanic's initials in its corner, so you can
  tell at a glance whose block it is. Click a marker to show only that
  mechanic, click it again to show everyone.
- **By mechanic** — one day, a column per mechanic plus "Unassigned". Each
  header shows how many different jobs (work orders / appointments) the
  mechanic has that day — several timer sessions on one WO count once. A WO with several
  mechanics appears in each of their columns. Use ‹ › to move by day. In
  this view, dragging a block into another column reassigns the mechanic
  of that appointment.
- **All mechanics** dropdown — show only one mechanic (or only unassigned
  blocks) in either view.
- **Work orders** switch — hide the automatic WO blocks and keep only your
  appointments. The choice is remembered in this browser.

**Scheduling a work order.** Drag a chip from the "Not scheduled" strip
onto the grid (into a mechanic's lane or column to pick who), or click a
chip / a timer block and press **Add to schedule** — this opens a new
appointment linked to the WO, prefilled with the customer, unit, planned
hours and the mechanic (for a timer block: the next quarter-hour after
that session, so you plan the follow-up, not the past); save it to put
the plan on the grid. Timer blocks themselves are facts and cannot be
moved. **Facts beat plans**: timer blocks are always
shown; once the planned mechanic actually works on that WO during the
planned time, the plan disappears from the grid (it is still listed in the
WO window under "Planned for this day", where you can edit or delete it),
so the calendar shows what really happened, not what was planned. A plan
for a *different* mechanic on the same WO stays on the grid — that
mechanic still has their part to do. **One mechanic, one job at a time**: an appointment cannot be saved
or moved onto a slot where the same mechanic already has another
appointment (cancelled ones don't count) or where the mechanic's timer
shows they were actually working on a different work order — the
calendar refuses with the conflicting time and work order, so pick another
time or mechanic. An older appointment that overlaps such actual work is
marked with a red **!** badge and outline ("planned here, but the mechanic
was actually working on WO #…"); fix or delete it. Linked appointments have a dashed border, show the live WO
status, number and jobs, and keep an **Open WO** button and an **Unlink**
button in the edit dialog. As soon as a WO has at least one linked
appointment, its "Not scheduled" chip disappears (it is in the plan now);
actual timer blocks are never hidden. Deleting the WO keeps the
appointment and only removes the link.

**Creating a work order from the calendar.** Fill customer and unit (and,
optionally, presets, mechanic and a note), then press **Create Work
Order**. The appointment is saved first, then the WO page opens in a new
tab with everything prefilled: customer, unit, one labor block per preset,
the mechanic assigned 100% to each block, and the note as the labor
description (or as the issue description when a preset already named the
job). Once you save the WO, the appointment is linked to it and starts
showing the WO status. Mechanics (mechanic mode) do not get this button.

