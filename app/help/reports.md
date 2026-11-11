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
see financial reports but not payroll (in General Revenue they get the
payroll totals without the per-employee lines), Senior mechanic and
Mechanic see only Mechanic Hours (a mechanic has no Reports section at
all).

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
  timer time — including time on jobs that were later removed from a work
  order), plus WO and labor-entry counts. If the uAttend integration is
  connected, a **uAttend Hours** column is added — attendance hours from the
  time clock for the same period. uAttend employees are matched to app users
  automatically (same matching as the Dashboard hours chart): hours of an
  employee matched to a mechanic land in that mechanic's row; employees
  matched to non-mechanics (managers, owner) are excluded; unmatched
  employees appear as their own rows. This means a mechanic who never
  presses the job timer still shows attendance hours here — use it to see
  who was at work even when Tracked Hours is empty. Every row is one person
  under their **current** name from Settings → Users: hours on work orders
  saved before a rename count under the new name, a deactivated user keeps
  their row with "(inactive)" after the name, and two people who share a
  name get separate rows and separate chart lines (see "People's names
  after a rename" below).
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
salaries count as $0. The Mechanic Hours rows use each mechanic's current
name, like the Mechanic Hours tab ("(inactive)" for deactivated users).

**Payroll in General Revenue is the Timecard / Salary report.** The
"Money Out — Payroll" section is calculated by the same code as Reports →
Timecard / Salary, so for the same period "Salaries" equals the Timecard's
Salary subtotal, "Hourly (uAttend)" equals its Hourly subtotal and "Total
payroll" equals its grand total. A mechanic who has both a Roobico account
(Pay type = Hourly) and a uAttend profile is counted here by their uAttend
punches × rate, exactly like in Timecard.

Under the totals every paid person is listed: `Name — salary $1,000.00/wk`
or `Name — 38.50h x $30.00/hr`. **These per-employee lines are payroll
data**: they are shown only to users who have the permission "View Timecard
/ Salary report (employee pay)". Everyone else who can open General Revenue
(for example Parts manager and Viewer) sees the three totals — Salaries,
Hourly (uAttend), Total payroll — but no names, rates or hours; the section
description says so. Two helper lines can appear in that list:

- `Name — 12.00h, hourly rate not set` with $0 — the person clocked hours in
  uAttend but has no $/hr in Settings → Integrations. Set the rate and the
  pay appears.
- A salary line whose "What is this" says *"Also has a uAttend rate … but
  Pay type is Salary"* — the person is paid the weekly salary, their punches
  are not paid. If they should be paid by the hour, change **Pay type** to
  Hourly in Settings → Users (recipe: "Hourly pay is missing or too small
  in General Revenue / Timecard").

**When payroll is incomplete, the report says so** instead of quietly
showing $0. A yellow banner "Payroll is incomplete" (the same text is
printed in the PDF summary and shown as a "Payroll Warning" card in the
mobile app) appears when:

- **The period has no start or no end** — All Time, or a Custom range with
  only one of the two dates filled in. Payroll is counted per period —
  weekly salary × weeks and uAttend hours inside the dates — so without
  both dates it is $0. Pick This Month, Last Month or fill in both dates.
- **uAttend did not answer** (wrong or expired API key, uAttend blocking the
  server, too many requests, network). Salaries are still counted, hourly
  pay is missing; the banner quotes uAttend's error. Open Timecard / Salary
  to see the same error and fix the connection in Settings → Integrations.

General Revenue reads uAttend hours through a 15-minute cache (one entry
per date range), so opening the report, re-generating it and downloading
the PDF do not hammer uAttend. Timecard / Salary always asks uAttend for
fresh punches and refreshes the cache entry of the dates it is showing.
So after editing punches in uAttend: open Timecard for **the same From /
To dates** (use Custom for a period Timecard has no preset for) and General
Revenue shows the same hours right away; otherwise General Revenue catches
up by itself within 15 minutes.

## Timecard / Salary Report

Requires the permission **"View Timecard / Salary report (employee pay)"**
— by default only Owner, General manager and Manager; tick it for a
bookkeeper role in Settings → Roles. Payroll per employee for a period
(This week / Last week / This month / Last month / This year / Custom):

- **Salary** employees: weekly `salary_amount` × weeks in the period
  (days ÷ 7; a shorter range prorates fractionally).
- **Hourly** employees: uAttend punches in the period × their hourly rate
  (set in Settings → Integrations; only employees ticked there appear).
- **Job timers are not used for pay.** The hours mechanics track with Start
  job / Stop job (Tracked Hours in Mechanic Hours) never appear here and
  never change anybody's pay — editing or deleting a timer session, or
  removing a job that has tracked time, leaves this report as it was.
- uAttend people are auto-matched to Roobico users (AI) so nobody is counted
  twice — an employee matched to a **salary** user who has a weekly amount
  is folded into that salary row with a "↔ uAttend" badge (salary is what
  is paid; the punches are shown for reference). An employee matched to an
  **hourly** user (e.g. a mechanic) keeps their own hourly row — with the
  internal user's role and a "↔ Internal" badge — whether or not that user
  ever logs in. The badge shows the user's current name from Settings →
  Users.
- **A Salary user with no weekly amount does not hide hourly pay.** New
  users are created with Pay type = Salary; if nobody typed an amount, the
  person has a $0 salary row and their uAttend row stays as a normal hourly
  row (punches × rate), so the pay is not lost.
- **Salary and a uAttend rate at the same time.** If a user has Pay type =
  Salary with an amount *and* their uAttend profile has an $/hr rate, the
  salary is paid and the note under the total says *"uAttend rate $33.00/hr
  is not used — Pay type is Salary"*. This is the usual mistake of typing an
  hourly rate into "Salary amount": the person then gets $33 per **week**.
  Fix: Settings → Users → Edit → Pay type = Hourly.
- Banners explain when the uAttend integration is missing/disabled/erroring.
- **The same numbers feed the General Revenue report** — both reports use
  one calculation, so Salary subtotal, Hourly subtotal and the grand total
  here equal Salaries, Hourly (uAttend) and Total payroll there for the
  same From / To dates. (General Revenue may show uAttend hours that are up
  to 15 minutes old — see the cache note in the General Revenue section —
  and with no date range it shows no payroll at all, while this page then
  shows one week of salaries.)

## Activity Journal

Reports → **Activity Journal** (permission "View audit journal") — the
complete history of who did what in the shop. Every change made through
the web app, the mobile app, the customer portal or the e-mail inbox lands
here. Reads/views are not logged, with one exception: every reveal of a
vendor's portal password (Show, or Copy while the password is hidden, on
the Portal logins tab or in the mobile app) is recorded as Action
**Viewed** — "Viewed portal password of <vendor>" (never the password
itself). To see who looked at vendor passwords: Object type **Vendor** +
Action **Viewed**.

Each row shows **When** (shop time), **Who** (user name and role, or
"Customer" for portal actions and "System" for automatic ones), **Action**
(Created, Updated, Deleted, Status changed, Payment, Sent, Received,
Returned, Activated/Deactivated, Signed in/out, Timer, Marked done,
Confirmed, Transferred, Imported, Uploaded, Permissions changed, Settings
changed, Viewed, Access denied, Failed request), **Object** (the work order, unit,
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
customer page opens the journal filtered to that one object. Passwords,
tokens and vendor portal logins are masked everywhere: a change of a
vendor's portal login or password shows only as set / changed / removed,
so the journal is safe to open for a Viewer who may not see vendor logins.

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
  Each row is one person under their current name from Settings → Users —
  renaming a mechanic renames their row on the next load, hours on old
  work orders included. A deactivated mechanic's billed hours stay on a row
  marked "(inactive)"; their timer hours no longer count in Actual, which
  counts current mechanics only.
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

Sidebar → **Calendar** — the shop's appointment board: who is booked for
what, and when. It shows **appointments only** — the blocks you, a
colleague or someone in the mobile app created by hand. Work orders do
not appear on it by themselves; a work order is on the calendar only
when an appointment is **linked** to it (see "Appointments and work
orders" below). The page keeps itself fresh: every 30 seconds it
re-reads the visible days, so an appointment created on a phone or by a
colleague shows up without pressing F5 (the check pauses while a dialog
is open, while you are dragging a block and while the browser tab is
hidden).

**Two views.**

- **Week** — seven columns, Monday to Sunday, of the selected week.
  Today's number has a filled circle. Click a day header to open that
  day in the By mechanic view.
- **By mechanic** — one day, one column per mechanic of the shop (the
  same order as in work orders) plus a last **Unassigned** column for
  appointments nobody is assigned to yet. Each header shows the
  mechanic's colour dot, name and how many appointments (cancelled ones
  not counted) they have that day.

In both views the hours run down the left side: hour lines, lighter
quarter-hour lines and a red line at the current time in today's
column. The visible hours come from **Working hours** in the settings
(06:00–20:00 by default) and stretch automatically whenever an
appointment starts earlier or ends later — nothing is ever hidden. A
normal day fits on the screen; a very long one scrolls inside the grid.

**Toolbar.** ‹ **Today** › move by a week (Week) or by a day (By
mechanic); the date box jumps straight to a date; **Week / By mechanic**
switch the view; the **All mechanics** dropdown shows one mechanic (or
only unassigned appointments) in either view — the view and the filter
are remembered in this browser; the gear opens **Calendar settings**
(needs "Manage calendar settings"). Under the toolbar a legend shows one
dot per status. The address bar follows you
(`/calendar?date=2026-11-09&view=day`), so a bookmark or a link pasted
to a colleague opens the same day in the same view.

**What a block shows.** The colour is the appointment status. The first
line is the time (07:30–09:00), then the **title** in bold — the free
text where dispatchers put the phone number and the job list; when it
is empty the customer's name is shown instead — then the customer (when
it differs from the title) and the unit. In Week view every mechanic of
the appointment has a small chip with their initials in the top-right
corner, in that mechanic's colour (the same colour marks their column in
By mechanic). Mechanic names on blocks, chips, tooltips, in the dialog
and on the **Scheduled** card of a work order are always the person's
current name from Settings → Users, also on appointments booked before a
rename. A linked appointment carries a **WO #123** badge. A
**Cancelled** appointment stays on the grid, faded and with the title
struck through, so you can still see what fell out of the day. Hover a
block for the full text. Two appointments at the same time in one
column are drawn **side by side**, each narrower: overlaps are allowed
(one mechanic can be double-booked on purpose — a drop-off and a quick
check), the calendar never refuses them.

**Create an appointment** (needs "Create appointments"). Click an empty
spot on the grid: the dialog opens with the date of that column (Week)
or of the selected day (By mechanic), the start snapped to the nearest
15 minutes and an end one hour later; in By mechanic the mechanic of
that column is already chosen. Fields:

- **Customer** — type to search and pick from the list; optional (the ×
  clears it, typing something else does too). **Unit** becomes available
  once a customer is chosen and searches by unit number, make/model and
  VIN.
- **Service presets** — one or several, each becomes a tag. They turn
  into labor blocks when you create a work order from the appointment.
- **Date**, **Start**, **End** — 15-minute steps; End also offers 24:00
  (midnight). Changing Start keeps the duration. An appointment stays
  within one day and must end after it starts.
- **Mechanics** — one or several tags. Leave empty for "Unassigned".
- **Status** — one button per status; the chosen one is filled with the
  status colour. Always editable, also on a linked appointment.
- **Title / note** — up to 500 characters: "(773) 331-2795 — oil change,
  check brakes, DOT sticker". Shown in bold on the block and carried into
  the job when you create a work order from the appointment.

If a chosen mechanic already has another appointment at that time, a
small hint appears under the fields — information only, Save still
works. Press **Save**.

**Edit, move, resize, cancel, delete.** Click a block to open the same
dialog (without "Edit appointments" it opens read-only). Drag a block to
move it: it snaps to 15 minutes and can go to another day (Week) or into
another mechanic's column (By mechanic — a one-mechanic plan simply gets
that mechanic; for a crew the member whose column you dragged out of is
swapped for the new one and the rest stay; dropping on **Unassigned**
removes the mechanics). Esc while dragging cancels. Drag the **bottom
edge** of a block to make it longer or shorter (15-minute steps, at
least 15 minutes). Change the status with the buttons in the dialog —
**Cancelled** keeps the record visible but faded; **Delete** (needs
"Delete appointments") removes it for good.

**Calendar settings** (gear; needs "Manage calendar settings").
*Appointment statuses*: rename a status, pick its colour, add your own
("Waiting for parts", "Warning"), remove a custom one with the ✕ button
next to it. The four built-in statuses — Scheduled, In Progress,
Completed, Cancelled — show a lock instead of the ✕ and cannot be
removed, because the work-order link uses them (Confirmed is a normal
status you may rename or drop).
*Working hours*: the first and last hour of the grid. Save re-draws the
legend, the grid and the status buttons.

**Appointments and work orders.** The two are tied by one link, and the
rule is simple: **the work order is the source of truth; the calendar
never changes a work order.**

- *From an appointment to a work order* — fill customer and unit (and,
  if you like, presets, mechanics and the note), press **Create Work
  Order**. The appointment is saved first, then the work order page
  opens in a new tab with everything prefilled: customer, unit, one
  labor block per preset, all mechanics of the appointment assigned to
  every block (even split: one mechanic — 100 %, two — 50/50), and the
  note as the job description — or, when it is long or a preset already
  named the job, as the block's **Describe Issue** text that the
  mechanic sees under the job. Save the work order and the appointment
  is linked to it. Mechanics (mechanic mode) do not get this button: on
  the phone they press **Start work** in the appointment and the server
  builds the work order for them (see Mechanic app → Calendar on the
  phone).
- *From a work order to the calendar* — on the page of an existing work
  order (not an estimate) press **Schedule** next to History / Back: the
  calendar opens with a new appointment already linked to the WO and
  prefilled from it — customer, unit, the mechanics of its labor blocks,
  the first jobs as the title and the planned hours as the duration
  (today at 08:00; set the real date and time and Save). A work order
  may have several appointments, e.g. two visits. The **Scheduled** card
  at the top of the WO page lists every live (not cancelled) appointment
  — date, time, mechanics, presets, note — with an **Open calendar**
  link.
- *What a linked appointment shows* — the **WO #** badge on the block;
  in the dialog a panel "Linked to WO #…" with the live work-order state
  (Not started, In progress, Mechanic done, Completed, Paid — mechanics
  see Paid as Completed), the first jobs, **Open WO** and **Unlink**
  (press it, then Save).
- *What follows the work order automatically* — the **status**: when a
  mechanic starts a timer on the WO (or takes it into work from the
  phone) the appointment turns **In Progress**; when the mechanic
  presses Done, or the WO is completed or paid by any route (the
  office's Save, the status menu, Mark paid, a payment, a bulk payment),
  it turns **Completed**. The status never goes back by itself — if a
  job is reopened, set the appointment status by hand — and a
  **Cancelled** appointment is never touched. If your shop replaced the
  built-in statuses, the sync has nothing to set and leaves the
  appointment alone. The **customer and unit**: a Transfer or a unit
  change on the work order moves the appointment to the new
  customer/unit; in the dialog these two fields are read-only while
  linked ("Customer and unit come from WO #… — change them there or
  unlink").
- *What never happens* — moving an appointment to another mechanic or
  another time does **not** reassign anyone on the work order and
  changes nothing else there; reassigning a mechanic in the WO's labor
  blocks does **not** move its appointments (drag them yourself); the
  status buttons on the appointment do not change the WO status.
  **Unlink** clears the link and nothing else; deleting the work order
  does the same for all its appointments — they stay on the grid with
  the status they had.

**Mechanic mode.** Mechanics see the same calendar (every default role
has "View calendar") and can create or edit appointments if their role
allows it; the dialog has no Create Work Order button for them and the
work-order state shows Paid as Completed.

**On the phone.** The mobile app's Calendar tab shows the same
appointments (Schedule / Day / 3 Day / Week / Month, mechanic lanes,
"Only mine"), and anything created or moved there appears on the web
within half a minute — see Mechanic app → Calendar on the phone.

**How to…**

- *Plan a job for a mechanic* — By mechanic → click inside the
  mechanic's column at the start time → customer, unit, presets, note →
  Save.
- *Move a job to another day* — Week → drag the block into the other
  day's column; or open it and change **Date**.
- *Give a job to another mechanic* — By mechanic → drag the block into
  the other column; or open it and change the **Mechanics** tags.
- *Make a job longer or shorter* — drag the block's bottom edge.
- *See one mechanic's week* — Week → **All mechanics** → pick the name.
- *Find jobs nobody is assigned to* — By mechanic → the **Unassigned**
  column; or **All mechanics** → Unassigned in Week view.
- *Put an existing work order on the schedule* — WO page →
  **Schedule** → date and time → Save.
- *A customer calls to cancel* — open the appointment → **Cancelled** →
  Save (it stays visible, faded); Delete only if it was a mistake.
- *Who actually worked on what* — that is tracked time, not the
  calendar: Reports → **Mechanic Hours**, or the work order's time logs.

## People's names after a rename

Renaming a user (Settings → Users → Edit → First / Last name) changes the
name in these places right away, for old records too — they look the person
up by account, so there is no need to re-save old work orders or
appointments:

- Reports → **Mechanic Hours** (table, chart and PDF), the Mechanic Hours
  rows of **General Revenue** and the "↔ Internal" badge of
  **Timecard / Salary**;
- the Dashboard's per-mechanic hours table;
- the **Work Orders** list and its In Work group (the mechanics of a job,
  the green "working now" dot, the Done check mark) — on the web and in
  the mobile app;
- the **Calendar** (chips, tooltips, the appointment dialog, the
  **Scheduled** card on a work order and the crew prefilled by
  **Schedule**).

Reports and the dashboard are calculated each time they are opened, so the
new name shows on the next load. Deactivated users keep their history:
their rows stay in reports and on the dashboard with "(inactive)" after the
name. When a user account no longer exists at all, the name saved on the
work order at the time is shown, or "Former user".

**What keeps the old name on purpose**

- The **Activity Journal** is a log: the **Who** column and the event text
  show the name the person had when it happened. To see all of one
  person's actions, pick them in the **User** filter (it follows the
  account, not the name) instead of typing the new name into the search.
- Annual inspection (AVIR) reports keep the inspector name printed on them.
- A uAttend employee who is not matched to a user shows the name from
  uAttend — rename them in uAttend too, or give them the same name or
  e-mail in both systems so their hours merge into the user's row.

**How to…**

- *Fix a misspelled mechanic name* — Settings → Users → Edit → correct
  First / Last name → Save. Reports, the dashboard, the Work Orders list
  and the calendar show the new spelling the next time they load.
- *One person shows on two rows* — the hours were recorded under two
  different user accounts (for example an old login that was deactivated
  and replaced with a new one). The old account's row shows that
  account's own name with "(inactive)"; hours are not moved between
  accounts automatically.
- *A row still shows an old name* — reload the page. If that row has only
  uAttend hours, it is a uAttend employee who was not matched to the user,
  and the name comes from uAttend: see the uAttend point above.
