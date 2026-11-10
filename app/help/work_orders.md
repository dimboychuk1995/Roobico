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
  Each action needs its key: Pay and Bulk Payment — "Manage payments",
  Download PDF — "Export PDF", Delete — "Delete work orders", Create Work
  Order — "Create work orders". Without "Edit work orders" the button reads
  **Open** and the work order opens read-only: no Edit / Save / Paid /
  Delete, and the parts-order, email and authorization buttons appear only
  with their own keys ("Send to customer", "Create parts orders"...).
  Moving a **paid** work order back to Open or In Progress (which removes
  its payment records) always needs "Change work order status", even for
  users who may otherwise save work orders.
- Under the status badge the list also shows mechanic activity: a green
  "● <names>" line — mechanics with a running job timer on this WO right
  now — and a green **"Mechanic done"** badge when the mechanic saved the
  WO as "Done, ready for review" (the WO stays In Progress until you
  complete it; the badge clears if a mechanic starts a timer again).
  With **several mechanics** on one WO the badge appears only when **all**
  of them pressed Done; until then the WO stays In Work and the names line
  shows who already finished as a green **"✓ Name"** (● green — working
  right now, ● grey — assigned but idle).
- A red **"Unassigned labor"** badge next to the status means at least one
  labor line of the WO (one with a description, hours or parts) has no
  mechanic yet — nobody is assigned to it and no mechanic's timer has run
  on it (a line with tracked time belongs to whoever tracked it, exactly as
  the WO page shows). Open the WO and assign someone in that labor block.
  The badge disappears once every labor line has a mechanic, and is never
  shown on paid WOs.
- **"In Work" group** — WOs that mechanics have taken but not marked done
  yet are grouped at the top of the table under an "In Work · taken by
  mechanics, not marked done yet" header, highlighted with the in-progress
  color, so the shop floor status is visible at a glance. They are not
  duplicated below — each WO appears once. The group **ignores the date
  filter**: whatever range is selected, everything currently in work is
  always shown, even a WO dated weeks ago (search and the paid-status
  dropdown still apply). Note the footer totals still follow the selected
  date range, so an old in-work WO shown on top may not be counted there.
  **"Save In Progress"** on the WO page (or setting the status to In
  Progress) puts a WO back into this group even if every mechanic had
  already pressed Done: their Done marks are cleared and each mechanic has
  to press Done again when they really finish.
- **The list is live** — no manual refresh needed. Every few seconds the
  page checks the server and reloads itself when anything changes: a
  mechanic starts or stops a timer (the green "● working" names and the
  "In Work" group update), saves a WO or marks it Done, someone creates,
  edits, pays or deletes a work order from another computer. Your filters,
  page, active tab and scroll position are preserved. The reload politely
  waits if you are typing in a filter field or have a modal (e.g. payment)
  open, and resumes the moment you finish. Works out of the box.
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
3. **Mileage** is required to create. It is stored on the work order as a
   snapshot: opening an old WO (and its PDF) shows the mileage as it was
   at that time, not the unit's current odometer. Editing the mileage on a
   WO updates the unit's odometer only when the new value is higher than
   the unit's current one, so an old WO can be corrected without rolling
   the unit back. **Work Order Date** defaults to today
   and can be changed. It is also the invoice date on the PDF and the date
   the lists and sales reports use. **A mechanic's save moves this date to
   the day of that save** — the Save button in web mechanic mode, and in the
   app every autosave that actually changed something (jobs, parts,
   mileage) or the first press of Done — so a job started on Monday and
   finished on Wednesday is invoiced for Wednesday. Merely opening a WO in
   the app does not move it, and office saves leave the date as set.
4. Buttons: **"Create Work Order"**, or from its dropdown: **"Save In
   Progress"**, **"Create Annual Inspection"**. There is also
   **"📷 Recognize WO"** — upload a photo/PDF of a handwritten work order and
   AI fills the jobs and parts (review before applying).
5. **From the Calendar** — an appointment's "Create Work Order" button opens
   this page with the customer, unit, one labor block per preset, the
   whole crew of the appointment assigned to every block (even split:
   50/50 for two mechanics) and the note already in place — a short note
   becomes the first block's description, a long one (or one next to a
   preset that already named the job) goes into the block's **Describe
   Issue** text, which the mechanic sees under the job. Saving links the
   appointment to the WO: the page then shows a **Scheduled** card at the
   top (date, time, mechanics, presets and note of every live appointment
   of this WO, with an Open calendar link), the appointment's block on the
   calendar carries the WO number and live state, and its own status
   follows the work (In Progress once a mechanic starts, Completed on
   Done / completed / paid; it never goes back by itself and a Cancelled
   appointment is never touched). Transfer and a unit change move the
   appointment to the new customer/unit — change customer and unit on
   the WO, not in the appointment (its dialog keeps them read-only while
   linked). Everything else is one-way: the calendar never changes a work
   order — moving an appointment to another mechanic or time leaves the
   labor assignments alone, and reassigning a mechanic here does not move
   the appointments. The other direction, from a saved WO to the
   calendar: the **Schedule** button (next to History / Back on an
   existing work order, not an estimate) opens the calendar with a new
   linked appointment prefilled from the WO — customer, unit, the
   mechanics of the labor blocks, the first jobs as the title, the
   planned hours as the duration; set the date and time and Save. On the
   phone a mechanic does the same with **Start work** in the appointment:
   the server builds the jobs from the presets (hours, rate and pinned
   part prices of each preset, crew on every job) and puts the note into
   the first job's issue description — or makes the note the only job
   line when the appointment has no presets. Work orders are not shown
   on the Calendar by themselves — only their appointments are; see
   Reports → Calendar in the help.

## Transfer to another customer

The **Transfer** button (top-right of an existing work order's page, for
users who can edit WOs) moves the work order to a different customer — the
typical case is a mechanic who created the WO on a brand-new customer with a
new unit, when it really belongs to an existing customer. Pick the new
customer in the dialog and confirm. The unit follows the work order
automatically:

- If the new customer **already has a unit with the same VIN**, the work
  order is simply re-linked to that unit — nothing else changes (a
  deactivated duplicate gets reactivated with the fresh mileage).
- If not, the unit **moves**: it is deactivated for the old customer and an
  identical unit (same VIN, unit number, make/model/year, mileage) is
  created for the new customer.

Jobs, parts, totals, payments history and tracked mechanic time are not
touched — only the customer (and the unit link) changes. Paid work orders
cannot be transferred; unpay first.

## Statuses

- **Open (Unpaid)** — default after creation.
- **In Progress** — set by "Save In Progress", by mechanics, or automatically
  when a mechanic starts a job timer (never on an estimate — see below).
- **Estimate** — a quote: created via the Create dropdown → **"Save as
  Estimate"**. Estimates live on the **Estimates** tab of the WO list (they
  are hidden from the main Work Orders tab and its totals), take **no parts
  from inventory**, cannot receive payments, and are edited only in the
  office web interface (locked for mechanics and the mobile editor). An
  open estimate shows an **Estimate** badge on its page; a plain Save keeps
  it an estimate. **Convert to Work Order** (Save dropdown) turns it into a
  normal open WO — at that moment all its parts are deducted from stock and
  core tracking starts. The reverse also works: on a normal (unpaid) work
  order the Save dropdown has **Save as Estimate** — the WO becomes an
  estimate, all its parts are returned to inventory and pending cores are
  removed. A work order with recorded payments cannot become an estimate —
  delete its payments first. You can convert back and forth as many times
  as needed; stock moves accordingly each time.
- **Job timers never convert an estimate.** A mechanic cannot start a job
  timer on an estimate: Start job answers *"This work order is an
  estimate. Time can be tracked after the office converts it to a work
  order."*, nothing starts, and a timer the mechanic has running on another
  work order keeps running. Convert the estimate first (Save dropdown →
  **Convert to Work Order**), then the mechanic can start. A manager or
  owner may time work on an estimate (for example a diagnosis): the time is
  recorded, but the estimate stays an estimate — no status change, no parts
  taken from stock.
- **Estimates can be sent for customer approval** exactly like a WO: the
  same "Send for approval" flow, the same email with the PDF, and the same
  green **✓ Authorized** badge when the customer approves — approval does
  not change the status or touch inventory, it is the customer's sign-off
  on the quote.
- **Paid** — set automatically when the remaining balance reaches $0.
- **Paid WOs are locked** — they cannot be edited until made unpaid. After
  **Unpaid** (or after deleting the last payment of a paid WO) you can
  click Edit right away — no reload needed: jobs and parts you remove are
  removed when you Save. Once in a while the page reloads by itself right
  after Unpaid / the payment delete and then shows "Marked as unpaid." or
  "Payment deleted." — that is normal, just continue.
- **Careful:** switching a WO back to Unpaid/In Progress **deletes all its
  payment records**. The delete-WO confirmation also warns about this.
- Deleting a WO returns its parts to inventory and removes payments.
  Deleting an **estimate** changes neither stock nor cores — an estimate
  never took any parts from inventory.

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
auto-fill for that job. Simply saving the work order does **not** count as a
manual edit: only actually typing in the Hours or Labor Total field does. So
if a mechanic tracked more time while you had the page open, saving your
other edits keeps their latest tracked hours instead of the stale number on
your screen.

- **Assign** button → "Assign Mechanics" modal: pick one or several
  mechanics. One mechanic = 100%; several = percentages auto-split evenly and
  can be edited. This split drives the Mechanic Hours report.
- **Edit mechanic hours (✎ on the "Mechanics:" line).** Users with the
  "Edit mechanics' actual tracked hours" permission (owner and managers by
  default) see a small pencil next to the tracked-time summary. It opens a
  list of the job's timer sessions — who, when started, how long. Change a
  session's duration (hours/minutes) and click Save, or delete a wrong
  session entirely (e.g. the mechanic forgot to stop the timer overnight,
  or started it by accident). Everything derived from time recalculates
  automatically: the job's Hours (when they follow the timers — not preset
  or typed hours), the mechanics' percentage split, the work order totals,
  Reports → Mechanic Hours ("Tracked") and the Dashboard's Actual hours.
  Pay does not change: Timecard / Salary and the payroll in General
  Revenue come from salaries and uAttend punches, never from job timers.
  Sessions edited by hand get an "edited" badge; a running session must be
  stopped before it can be edited. Paid work orders are locked — unpay
  first.
  *When the page shows the new numbers:* outside Edit mode the page
  reloads by itself — right after Save in that window, or, after deleting
  a session, when you close the window. In **Edit** mode the page does not
  reload, so your unsaved edits stay: Save in that window closes it with
  *"Hours updated. Save the work order to see the recalculated labor
  hours."* (after deleting a session you simply close it), the form keeps
  showing the old Hours for now, and the recalculated hours appear once
  you click Save on the work order. The fix counts as your own change, not
  someone else's: removing that job afterwards and saving works normally.
  Saving after you correct a timer session keeps the recalculated hours;
  hours, a rate or a preset another user set on that job in the meantime
  are kept, and a job another user removed stays removed. Hours you type
  yourself still win.
- **Shop supply** is a percentage of labor (default 5%, configurable), shown
  as an editable "Shop supply: $" field with a reset (↺) button. On the
  work-order page it is part of the **Labor total** (labor + shop supply).
  The customer never sees it hidden inside labor: on the invoice/PDF, in
  emails, in the customer portal and on authorization pages the **Labor**
  line is labor only and **Shop Supplies** is listed once as its own line
  right before the Total (details in "Tax and totals" below).
- Block menu (⋮): **"Add Preset"**, **"Describe Issue"** (AI polishes/
  translates the issue text into professional English for the customer),
  **"Send for Authorization"** (this labor only). Saving a Describe Issue
  text on a job that is already saved stores it at once ("Issue
  description saved.") — also in Edit mode, without touching your other
  unsaved edits; on a job that is not saved yet it goes with the next
  Save ("Issue description updated. It will be saved with the work
  order."). If somebody removed that job meanwhile, you get *"This job is
  no longer on the work order. Reload the page."*

## One unit — one work order (mechanic mode)

When a mechanic selects a unit for a new work order (web mechanic page or
mobile app: by hand, from the unit search, or by VIN scan) and that unit
already has a work order **In Progress**, the mechanic is sent into the
existing work order instead of creating another one; the message names
the WO and the mechanic working on it right now. Several mechanics then
share one WO with their own job timers. The server refuses a mechanic's
duplicate create for a busy unit (the existing WO is returned), while
managers may still create additional work orders for the same unit from
the office pages. Confirmed, completed, paid work orders and estimates do
not block a new one.

## Live updates from mechanics

The work order details page updates itself while it is open — no manual
refresh needed. Every few seconds it checks the server and:

- **Running timers show up live.** When a mechanic starts a job timer (from
  the mobile app or mechanic mode), a red "● working" indicator with the
  mechanic's name appears on that job's "Mechanics:" line within seconds,
  and tracked time keeps accumulating on screen.
- **Changes reload the page automatically.** When a mechanic saves the work
  order, marks it Done, or a timer changes the status/hours, the page
  reloads by itself to show the fresh jobs, parts and totals (your scroll
  position is preserved). The same happens if another manager updates or
  pays the work order from a different computer. While a window is open
  (payment, email, mechanic hours…) or a message box is on screen (the
  list of notes after a save, a confirmation question) the page waits and
  reloads after you close it, so nothing you are typing or reading there
  is lost.
- **You are protected while editing.** If you are in Edit (or Confirm) mode
  when a mechanic saves changes, the page does NOT reload under you. A
  notice says *"Mechanics just updated this work order. When you save,
  their changes will be merged with yours."* Keep working and click Save
  as usual — the mechanic's new jobs, parts and quantities are kept (see
  "Several people editing one work order" below). There is no need to
  refresh first.

The automatic reload ignores your own changes: your saves, Describe Issue
texts and mechanic-hours fixes are never taken for somebody else's update.
A few of your own actions do reload the page on purpose: a mechanic-hours
fix made outside Edit mode (see **Edit mechanic hours** above), **Save as
Estimate** / **Convert to Work Order**, and a save that was combined with
other people's changes (see "After you save" below). Polling pauses while
the browser tab is in the background (it catches up the moment you switch
back). This works out of the box — there is nothing to configure, and
mechanics do not need to do anything special in the app.

## Several people editing one work order

Mechanics (app or web mechanic page), the office and managers on the phone
can all work on the same work order at the same time — everybody's work is
kept. Every save is **merged** with what is already saved: the server
compares three versions — what your screen showed when you started, what
is in the work order right now, and what you are saving — and keeps the
changes of every person. Nothing needs to be set up.

**What wins when two people change the work order**

- **Different things** (you fix a price, a mechanic adds a part): both
  changes are kept.
- **The same field** (both of you rewrote the same job description or
  changed the same part's quantity): the person who saves **last** wins
  for that one field. A field you did **not** touch never overwrites
  somebody else's change — a page you opened in the morning will not undo
  the mechanic's afternoon work when you finally click Save.
- **Jobs and parts someone else added** while your page was open are kept.
  After your save they appear at the end of the job list (or of that job's
  parts).
- **Jobs and parts you removed** are removed — unless someone else changed
  that job or part after you opened the page; then it stays, and the note
  tells you why.
- **A job somebody is working on is never removed.** If a mechanic's timer
  is running on the job, your save keeps it (*Kept job "…": someone is
  working on it.*) — this also holds when the mechanic presses Start job
  at the very moment your save arrives. If its tracked hours changed since
  you opened the page, it is kept as well (*Kept job "…": it was changed by
  another user.*). When your removal was saved first, the timer simply
  does not start: the mechanic sees *"This job is no longer on the work
  order. Refresh the work order and try again."* Ask the mechanic to stop
  the timer first if the job really has to go.
- **A job another person removed** while you still had it on screen stays
  removed ("Job … was removed by another user") — unless you changed that
  job yourself; then it comes back with your changes.
- **Tracked hours win.** Hours that come from the mechanics' timers keep
  following the timers even if your page still shows an older number —
  unless you type Hours (or a Labor Total) yourself. Changing only the
  labor rate re-prices the latest tracked hours.
- **Customer, unit, date, Taxable and mileage** change only if *you* changed
  them on your page. A **Transfer** to another customer done by someone
  else, or a date moved by a mechanic's save, is not undone by your older
  page.
- **Mechanic assignments and misc charges**: assignments you did not touch
  stay as they are now; misc charges added by a mechanic (from a catalog
  part) and those added in the office are both kept.
- **Your Describe Issue texts and mechanic-hours fixes count as yours.**
  They are stored at once while your form stays open, and the page
  remembers them as your own changes: removing that job afterwards and
  saving works normally, and if a mechanic rewrites the same issue text
  later, your next Save of other changes keeps the mechanic's newer text.

**While saving.** The Save button shows "Saving…" and the form does not
accept typing until the answer arrives (usually under a second). If
something on the page still changed during the save (for example a part
price filled in automatically), you get *"You changed the work order while
it was being saved…"* — see the messages below.

**After you save.** If the save was combined with other people's changes,
the page reloads and shows a short list of what happened, for example
*Kept job "Brakes" added by another user.* or *Part "Oil filter" was
removed by another user.* (when there is nothing to list, just the toast
"Saved. Changes other people made in the meantime were merged in.").
Notes can also come without a reload, for example *"The unit was also
changed by another user; your choice was kept."*, and warnings such as
*"Saved, but stock levels could not be updated…"* are listed in the same
window. The list stays on screen until you click OK: the page does not
reload under it, and if you refresh the page before closing it, the list
appears again. Nothing to do — it is for your information. Without notes
you just see "Saved." **Save as Estimate** and **Convert to Work Order**
always reload the page; their result ("Estimate saved.", "Saved." or the
list) appears after the reload.

**Messages you may see**

- *"This work order is being updated by someone else right now. Please try
  again."* — several saves arrived in the same moment and yours could not
  be stored. Nothing of yours was lost on the page: click Save again.
- *"Couldn't confirm what you started from, so nothing was removed. Please
  review the work order."* — the page could not tell Roobico what it
  showed when you started editing — usually because it was left open for
  more than two weeks. Your changes were saved, but the jobs and parts you
  deleted were kept to be safe (each with a note such as *Kept job "…": it
  was not in your copy of the work order.*). Reload the page, check the
  work order and delete them again.
- *"This page was opened before Roobico was updated. Reload the page and
  make your change again."* — the page was opened before an update of
  Roobico, and the work order now has a job or part that this page does
  not show (you removed it, or somebody else added it). Nothing was saved
  and inventory did not change. Reload the page (F5), make the change again
  and click Save.
- *"You changed the work order while it was being saved. Those changes are
  still on the page but are not saved yet — save the work order again."* —
  something on the page changed while the save was on its way (for example
  a price filled in automatically). The first save went through; the page
  stays in Edit mode with the newer changes on it. Click Save again.
- *"The selected unit belongs to a different customer. Select a unit of
  this customer."* — you changed the customer, but the Unit field still
  holds a unit of the previous customer. Pick one of the new customer's
  units (or add one) and Save.
- *"This work order was moved to another customer by another user. Reload
  the page to see the change."* — while your page was open, somebody
  transferred the work order to another customer, and you picked a unit of
  the previous customer. Nothing was saved: reload, then make your change
  again.
- *"Saved, but stock levels could not be updated. Please check this work
  order's parts in inventory."* (similar: *"Saved, but core charges could
  not be synced…"*, *"Saved, but the unit mileage could not be updated."*,
  *"Saved, but notifications or the calendar could not be updated."*) —
  the work order itself was saved, only a follow-up step failed. Check
  that part's stock (or the cores, the unit's odometer, the appointment)
  by hand.
- *"Network error — your changes are still on the page…"* — the connection
  dropped. Click Save again; a new job is recognized, so a repeated Save
  never creates the same job twice.

**Time on removed jobs is not lost.** When a job that has tracked time is
removed, the time stays with the mechanic: it still counts in Reports →
Mechanic Hours ("Tracked") and in the Dashboard's Actual hours, and the
work order shows **"Tracked on removed labor lines: Xh Ym"** under the
jobs. Pay is a separate thing: Timecard / Salary (and the payroll in
General Revenue) is calculated from salaries and uAttend punches, never
from job timers, so removing a job does not change anybody's pay. To bill
the time, add the job again and type its hours.

**How to…**

- *Edit a work order while mechanics are working on it* — click Edit, make
  your changes, click Save. If the notice "Mechanics just updated this work
  order…" appears, just keep going: your Save merges their changes.
- *Remove a job a mechanic is still timing* — ask the mechanic to press
  Stop job, then remove the job and click Save.
- *Make sure my hours stick* — type them into the job's Hours (or Labor
  Total) field. A typed value always wins over timer hours.
- *Remove a job right after fixing its issue text or its mechanic hours* —
  just remove it and click Save: those fixes count as your own changes, so
  the job goes.
- *Edit right after Unpaid* — click Edit, change, Save; no reload needed.
  If the page reloads by itself once and shows "Marked as unpaid.", just
  continue.
- *My save was refused with "This page was opened before Roobico was
  updated"* — press F5 to reload, make the change again, Save.
- *See who changed or removed something* — the work order's **History**
  (Reports → Activity Journal): every save shows the job list before and
  after it; a save that was merged with other people's changes also shows
  its **Merge notes**.

## Parts on a work order

Type in the Part Number cell (3+ characters) to search the catalog. The
dropdown always offers **"+ One-time part"** — a manual line with no
inventory link (editable cost, never deducted from stock). Catalog results
show stock (in the part's unit of measure, e.g. "Stock: 12 gal"), avg cost
and cross-reference alternates ("⇄ Cross ref for ...") so you can pick an
interchangeable part.

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
- **Unit of measure**: a part counted in gallons, quarts, pounds etc. (set
  in the parts catalog) shows its unit under the Count field ("gal") and
  prints the quantity with it on the PDF, in emails and in the customer
  portal ("2 gal"); parts counted in pieces show just the number.

## Tax and totals

- **Taxable** toggle on the WO defaults from the customer's Taxable flag.
- Tax applies to parts + taxable misc charges only — **labor is never
  taxed**. Rate comes from the shop ZIP (or the custom rate in Settings →
  Parts Settings); the rate is locked into the WO at save time.
- Grand total = Labor total (labor + shop supply) + Parts total (parts +
  cores + misc) + sales tax — this is how the work-order page groups the
  amounts.
- **What the customer sees.** The work-order page is the internal view:
  there "Labor total" includes shop supply and "Parts total" includes cores
  and misc charges. On everything sent to the customer — the invoice/PDF,
  the work-order email, the customer portal page and the authorization
  page/email — every amount is listed exactly once: the **Labor** line is
  labor only, the **Parts** line is parts only, and **Core Charges**, **Shop
  Supplies**, **Misc** and **Sales Tax** are each their own line (shown only
  when non-zero), so Labor + Parts + Core Charges + Shop Supplies + Misc +
  Sales Tax = Total. The per-job **Subtotal** on the PDF, and the job
  amounts in the portal and on authorization pages, are labor + parts
  (including that job's cores and misc) without shop supply, so job
  subtotals + Shop Supplies + Sales Tax on taxable work orders add up to the
  Total. The mobile app's work-order screen shows the same split (Labor,
  Parts, Shop supply, Misc, Cores, Tax).
- **Hidden Shop Supplies line.** If Settings → PDF Design has "Show shop
  supply" unticked, the PDF prints no Shop Supplies line; its amount is
  folded back into the Labor line, into the Labor item rows and into the
  per-job Subtotals, so the PDF still adds up to the same Total — the
  customer just does not see shop supply itemized. "Show core charges" and
  "Show misc charges" work the same way: a hidden Core Charges / Misc line
  is folded into the Parts line. The email, portal and authorization pages
  always show each of these as its own line.
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

Amounts on the authorization page and email follow the customer-facing rule
(see "Tax and totals"): for one labor the customer sees **Labor total**
(labor only), **Parts**, **Shop supply** (that job's share, as its own line)
and **Block total** — the full amount of that job including shop supply,
which is what they approve. For a whole work order each job card shows labor
+ parts without shop supply, and **Shop supply** (plus **Sales tax** on
taxable work orders) is listed once right above the **Grand total**.

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
  (logo etc.) is configured in Settings → PDF design. For an estimate the
  same document is titled **Estimate** (file Estimate-<number>.pdf) and
  switches back to Work Order automatically after conversion. If a labor
  has a **Described Issue** filled in, the PDF prints it under the labor
  description as "Reported issue: …" — on both work orders and estimates.
- **Email Work Order** — sends the PDF to chosen customer contacts (you can
  add a new contact on the fly and save it to the customer). The email also
  includes a customer portal link.
- **Managing contacts in the email dialog** — each saved customer contact in
  the recipients list has a pencil (edit name / email / phone) and a trash
  icon (delete the contact from the customer — after an "are you sure"
  prompt). Changes are saved to the customer card immediately, exactly as if
  you edited them on the Customers page, and apply to all email dialogs (work
  order, payment receipt, authorization). Deleting the main contact promotes
  the next one to main. Recipients added on the fly but not yet saved show a
  green "New" badge and an ✕ to remove them from the list. The pencil/trash
  icons are controlled by the **"Edit / delete customer contacts in email
  dialogs"** permission (Settings → Roles) — it is enabled for all roles by
  default; switch it off for a role to make the list read-only.
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
