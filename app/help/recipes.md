# How-to recipes and tricky questions

Users phrase these many ways — match the intent, not the words.

## Pricing & money

**Special parts margin for ONE customer (e.g. 20%).**
1) Settings → Parts & Pricing → Margin / Markup Rules → "New scale": mode
Margin, one rule (From 0, To empty, 20). 2) Customer page → Details →
Pricing Scale → pick it. 3) If parts with a fixed selling price must also
follow it, enable "Override part selling price". Everyone else stays on the
default scale. (A "discount for a fleet" = the same recipe with a lower %.)

**Change parts pricing for the whole shop.** Edit the default scale's rules
(or create a new scale and "Set as default").

**Cheaper labor for one customer.** Create the rate in Settings → Work Order
Settings → Labor Rates, then set it as the customer's Default Labor Rate.
On any single WO you can also just pick a different rate on the labor line.

**Make a customer tax exempt.** Customer → Details → switch **Taxable** to
No. Their new WOs default to non-taxable (the per-WO Taxable toggle can
still override). Labor is never taxed for anyone; tax applies to parts +
taxable misc only.

**Part price didn't change after I changed the scale — why?**
In order: (1) the part has a fixed Selling price — it wins unless the
customer has "Override part selling price"; (2) the price was edited by hand
on the WO — manual prices are never auto-overwritten; (3) the price came
from a service template where it was pinned by hand — pinned template prices
don't follow the scale; (4) the scale isn't the one assigned to that
customer.

**Special price for one service (template).** Settings → Service Templates →
edit the template → type your price in the part's Price field. A hand-edited
price is *pinned* (highlighted in the editor): it saves with the template
and is used on WOs as typed, beating the part's selling price and the
pricing scale. Untouched rows keep following the catalog automatically.
Customers with "Override part selling price" still get their own scale.
To go back to automatic pricing, clear the Price field and click away.

**Customer paid one check for several invoices.** Work Orders →
**Bulk Payment**: pick customer, enter the amount, "Auto-Distribute"
(spreads across oldest invoices), adjust, Record Payments.

**Edit a paid work order.** Paid WOs are locked. Click "Unpaid" to unlock —
**warning: this deletes its payment records**; re-record the payment after
editing.

**Overpayment / deposit.** Recording more than the balance is blocked, on
work orders and vendor orders alike. There is no deposit/credit feature.

**Estimates.** Create a quote via Create Work Order dropdown → "Save as
Estimate" — it lives on the Estimates tab, takes no parts from stock and
cannot receive payments. Send it for customer approval like a WO (same
email + PDF, titled Estimate). When the job is approved, open it and use
Save dropdown → "Convert to Work Order" (parts are deducted at that
moment). The reverse works too: on an unpaid WO, Save dropdown → "Save as
Estimate" returns its parts to inventory and turns it back into a quote;
WOs with recorded payments must have the payments deleted first.

## Parts & inventory

**Stock number is wrong.** Small fix: part → Locations → "Set qty" per
location. Bigger audit: Stocktakes tab → New Stocktake (by location or
category), count, Complete & apply. To see why it drifted: part → History
(orders and WOs with quantities).

**Move parts to another shelf.** Part → Locations → "Transfer between
locations" (From / To / Qty). Totals don't change.

**Negative stock?** Work orders deliberately save even without enough stock
(you get a warning) — fix by receiving the pending order or adjusting.

**Which parts need reordering? (low stock / reorder point / minimum).**
Give the parts a minimum first: Parts → **Edit** → tick **"This part
requires minimum stock"** → type the minimum → Save (per part — there is
no bulk edit; for a brand-new catalog put the numbers in the **Min Stock**
column of the import file). From then on the Parts list marks them by
itself: red **Below min** = fewer on hand than the minimum, amber **Near
min** = at the minimum or within 20 % above it, at least 1 unit (minimum
5 → 5–6 is near, 4 is below; minimum 10 → 10–12 is near). To see only
those, pick **Below minimum** or **Below or near minimum** in the **Stock
level** dropdown next to the search box, or click the numbers in the
amber "3 below minimum · 2 near minimum" bar above the table. Then order:
Parts → **Order** → add the parts (the Last price is pre-filled) → Create
order; the marks disappear by themselves once the order is **received**
and the stock is above the near window. Nobody is emailed or pushed about
low stock — check the list, or on the phone the Parts tab → **Below min**
/ **Near min** chips (they also list parts with 0 on hand).

**"Near min" although stock equals the minimum — why?** The near window
is inclusive on purpose: a part sitting exactly at its minimum is one job
away from being below it. Set the minimum one lower if the mark should
appear only under that quantity.

**Count oil in gallons, not pieces (unit of measure).** Parts → **Edit**
(or Add Part) → **Unit of measure** → Gallon (Quart, Pint, Liter, Pound,
Foot, Box, Case, Pack, Pair, Set, Roll, Can, Bottle, Tube, Drum, Pail, Kit
and a few more are available) → Save. Stock and the minimum now read
"12 gal" / "min 5 gal" in the Parts list and the app; on a work order the
part search shows "Stock: 12 gal", the line gets a small "gal" under
Count, and the PDF, the emailed work order, the authorization page and
the customer portal print "2 gal" in the Qty column. Prices stay per unit
(per gallon) and quantities stay whole numbers — to sell half gallons make
the part a Quart. Work orders saved earlier keep the unit they were
written with. In the import file the column is **Unit** ("gal", "Gallon"
or "gallons" all work; empty = Each).

**Enter a vendor invoice without typing.** Parts → Order → **AI Order
Reader**: upload the invoice PDF/photo; review matched parts, create missing
ones ("Create & Add"), then Create order.

**AI put the whole line amount into the price.** This is now corrected
automatically: the reader extracts the unit price and the printed line
amount separately and, when they disagree, uses line amount ÷ qty (hover
the price to see the tooltip when that happened). If a price still looks
wrong, fix it in the review table before "Add" — the **Total** column shows
Qty × Price so a mismatch with the invoice is obvious.

**Average cost looks wrong / did not change after receiving.** Average cost
is the weighted average of what is in stock, so a small batch barely moves
it when a lot is in stock (10 at $100 + 1 at $200 → $109). It jumps to the
received price when stock was zero/negative or the average was 0. Check the
part's **History** for the receipts; if an order was received twice by
mistake, **Unreceive** it — that also removes the batch from the average.
To just set it, type the value in the part form.

**What did I pay last time, and where?** Parts list → **Last price** column
(hover shows the order #) and the **Vendor** column — the vendor is always
the one from the latest parts order with that part, it switches
automatically when you buy from someone else. The last price is also
pre-filled when you order the part again.

**Stop using a part (make it inactive).** Parts tab → row → **Deactivate**.
It disappears from WO part search, the mobile app, stocktakes and inventory
totals; its stock and history stay. To see it later, switch the filter next
to the search box to **Inactive parts** / **All parts**.

**Bring an inactive part back.** There is no Restore button — order it:
Parts → Order → find the part (inactive parts show an **Inactive** badge) →
add → **Create order**. The part is active again immediately. Receiving an
old order that contains it, saving such an order, confirming an email order
with it, or scanning an invoice with the AI Order Reader and adding the
line does the same.

**Get vendor orders into Roobico automatically from email.** Settings →
Integrations → **Email orders inbox** → enable → Save → copy the location's
address (`orders-…@roobico.com`). Then either set auto-forwarding in the
mailbox where vendors write you (Gmail: Settings → Forwarding; Outlook:
Settings → Mail → Forwarding; instructions for every provider are in that
window) or add the address as an extra notification email in the vendors'
portals. Forwarding everything is fine — non-orders are ignored. Each
confirmation/invoice becomes a parts order with the red **Not confirmed**
badge; open it (Confirm button), add unmatched lines with "Create & Add",
check prices, press **Confirm order**. Not an order? **Reject**. Gmail asks
for a confirmation code first — it is the subject of the email in
**Inbox history** in the same window.

**AI made an order that is wrong / missed one.** Wrong order → **Reject**
on the order (Parts Orders tab) — it disappears, the email stays in the
history as Rejected. Missed one → Settings → Integrations → Email orders
inbox → Inbox history → find the email (status Ignored) → **Create order**.
Vendor got created twice? Open the order, pick the right vendor from the
list and save (or Confirm): the auto-created duplicate is deleted and the
sender is remembered for the vendor you chose, so the next email from it
matches straight away.

**Why can't I receive / pay an order?** It still has the **Not confirmed**
badge (came from email). Confirm it first; unconfirmed orders are also
excluded from vendor balances and dashboard purchases on purpose.

**Order parts for a specific job.** Open the work order (or the create
screen — even before saving) → **Parts orders** under the unit → Create
parts order. It's the same dialog as Parts → Order, AI Order Reader
included, but the order links to that WO: the WO row gets a **PO #** badge,
the order gets a **WO #** badge, and once the WO is accepted the block
flags anything ordered for the job but not used on it.

**Where does Avg cost come from?** Weighted average recalculated at every
receiving: (old avg × old qty + received price × received qty) ÷ total.

**Core deposits.** Charging a core: the Core toggle on the WO part line adds
the deposit. NOT charging it: the shop keeps the customer's old core — it
appears on the Cores tab; send cores back via "Return" and track credit on
Cores Returns.

**Wrong parts came / defective.** Parts order (received or paid) →
**Return** → quantities + RMA note. Creates a credit document R-…, stock is
deducted; deleting the return puts stock back.

## Reports & numbers

**How much did I earn?** Reports → General Revenue: left block = Revenue −
Parts Cost (no payroll), right block = Revenue − Parts Orders − Salaries.
The table below explains every number.

**Why is payroll $0 in General Revenue?** Either the uAttend integration
and pay types/rates aren't set up, or a customer filter is applied (payroll
can't be split per customer — the banner explains). Salary users need Pay
type = Salary with a weekly amount; hourly users must be ticked in
Settings → Integrations with an $/hr rate.

**Customer/Vendor Balances ignore my dates.** By design — balances are
always all-time. Same for the Dashboard's Outstanding Balance widget.

**Is a mechanic efficient?** Reports → Mechanic Hours: Billed Hours (from
WO labor, split by assignment %) vs Tracked Hours (their real job timers).
With uAttend connected there is also a uAttend Hours column — attendance
from the time clock, so mechanics who don't use the job timer still show
their hours at work for any past period.

**Who changed/deleted this?** Reports → Activity Journal — every
create/edit/delete with the user, time and a Before/After of the fields.
Fastest path: open the work order or customer and press **History**; or
type the WO number / part number / customer into the journal search.

**Who did what today / this week?** Calendar → switch **Work orders** on:
every WO shows on the days mechanics actually worked on it (from their job
timers), with names and hours; click a block for details. **By mechanic**
view gives one column per mechanic for a single day; the dropdown filters a
single mechanic in the week view.

**The mechanic sees a "Current mileage" window and cannot continue.** That
is intended: confirm the odometer reading (prefilled with the last known
value) or type the real one and press Confirm — only then the job opens.
If Save says "Enter the unit's current mileage", fill the Unit mileage
field. A number lower than the unit's current odometer is accepted on the
work order but does not change the unit; fix the unit's mileage on its
page if it was wrong.

**Which jobs still have nobody on them?** Work Orders list → rows with a
red **Unassigned labor** badge next to the status have a labor line with
no mechanic. Open the WO, pick the mechanic in that labor block (and the
split percentage if several share it), Save.

**Plan a job for a mechanic.** Calendar → click the slot inside the
mechanic's lane (Week) or column (By mechanic) → customer, unit (the unit
box searches by number, make/model and VIN), presets, more mechanics if
it is a crew job, a note for the mechanic → Save. To turn the plan into a
WO press **Create Work Order**: customer, unit, presets, the whole crew
and the note are carried over and the appointment is linked to the new
WO, so its status updates automatically. Or leave it to the mechanic: on
the phone the appointment has **Start work**, which builds the WO from
the presets (hours, parts, crew) and shows the note under the job. An
existing WO can be scheduled too: drag its chip from the "Not scheduled"
strip into a mechanic's lane, or open it → **Add to schedule** and pick
the time.

**Wrong customer or unit on a linked appointment.** While an appointment
is linked to a work order its customer and unit are read-only in the
dialog — fix them on the work order (Transfer for the customer, the unit
field for the unit) and the appointment follows; or press **Unlink** in
the appointment if the link itself was the mistake. The same goes for the
status: a linked appointment shows In Progress / Completed from the WO —
to "reopen" it, reopen the job (mark the WO unpaid / open): if nobody has
tracked time on it, no payment was taken and Done was not pressed, the
appointment goes back to Scheduled; otherwise the work is a fact and it
stays as it is. Cancelled can always be set by hand.

**Move a planned job to another mechanic.** Drag the appointment into the
other mechanic's lane (Week) or column (By mechanic) — the mechanic
inside the appointment changes, and if it is linked to a WO the WO's
mechanic assignments change with it (until someone has tracked time on
the job; needs the right to edit work orders). Or do it from the WO: pick the new mechanic in the labor block
and Save — the future appointments of the mechanic you replaced move to
the new one (one that would collide with the new mechanic's other job
stays with the old one; move it by hand). Plans of the other crew members
are left alone, so a job split as "Mike on Monday, Sam on Tuesday" stays
split; starting a timer never moves anyone's plan either.

**Check the schedule from the phone (mechanic: "what's my day?").** Open
the **Calendar** tab in the app — every role with "View calendar" has it,
mechanics included. Tap the person icon for **Only mine**: just your
appointments and your own timer blocks remain; the choice sticks. Tap
the view icon (top-left) for Schedule / Day / 3 Day / Week / Month; tap
the month name to jump to a date. Tap any WO block → **Open work order**
to go straight to the job. Office users can create an appointment from
the **+** button or an empty slot and turn it into a WO with **Create
work order** — customer and unit are carried over and the appointment is
linked automatically.

**An old appointment is still hanging around after the job was done.**
The calendar hides a plan once the planned mechanic actually worked on
that WO at that time (the timer block replaces it), but the appointment
still exists. Click the WO block → **Planned for this day** lists every
appointment for that WO that day, hidden ones marked "replaced by actual
work" → **Delete** (or **Edit** to move it to a real follow-up slot).

**"Mechanic is already booked" when the slot looks empty?** The slot is
held by another appointment of that mechanic (cancelled ones don't count)
or by their timer: a running timer blocks only the time up to now, a
finished session blocks its real time. The message names the work order
and time; open that WO's block → Planned for this day to fix or delete
the plan.

**Revenue looks too high?** Total Revenue includes collected sales tax, and
Sales reports date WOs by their Work Order Date (falling back to creation
date). WOs without a customer are excluded from per-customer reports.

## Team & access

**Hire a mechanic who must not see prices.** Create the user with role
**Mechanic** (or Senior mechanic). They automatically get the no-price
mechanic mode with job timers; their saves always go to "In Progress" for a
manager to finish. No prices appear for them anywhere, web or mobile.

**Second mechanic joins a truck that is already being worked on.** Nothing
to set up: when the second mechanic scans the VIN or picks the customer and
unit, the app notices the unit already has a work order In Progress and
opens that WO (toast: "Work order #123 is already in progress for this
unit (Ivan working on it right now). Opening it…"). No duplicate WO is
created — both mechanics add their jobs and run their own timers on the
same work order.

**Two mechanics on one job — when is it "done"?** Each mechanic presses
**Done** in their own app. The WO leaves the **In Work** group, gets the
"Mechanic done" badge and sends the "finished" push only after the last
mechanic who worked on it (timer or assignment) has pressed Done; until
then the WO list shows "✓ Name" for those already finished. Sent it back
to the floor? Open the WO and click **Save In Progress** — all Done marks
are cleared and the WO is back in In Work.

**Give one user an extra right (or take one away).** Users → Permissions →
Allow/Deny overrides. Deny always wins. For many users — create/clone a
role in Roles & Permissions.

**Senior mechanic should see customers, units and the parts catalog, but no
money.** That is the default **Senior mechanic** role: it has "View
customers", "View parts", "View parts orders" and "View reports" but
neither "View part costs inside WO" nor "View part costs" — so the
customers list has no balance column, the parts list has no Avg cost /
Last price, the dashboard shows only the hours chart and the Reports page
shows only Mechanic Hours.

**Office person who can look at everything, including numbers, but change
nothing.** Role **Viewer**: read-only access to all sections with prices,
balances, financial reports and the Activity Journal — but no create /
edit / delete / payment keys, so pages render without action buttons and
work orders open read-only. Payroll stays hidden unless you tick "View
Timecard / Salary report".

**Bookkeeper needs payroll.** Roles & Permissions → clone Viewer (or create
a role) and tick "View Timecard / Salary report" (reports.view_payroll) plus
"View financial reports". If they also prepare pay changes in the user
form, add "Edit employees' pay type & salary" — note that even with that
key nobody can change their own pay.

**HR person who manages accounts but must not see salaries.** Give them
"Manage users" without "Edit employees' pay type & salary": the user form
has no pay fields for them and the server ignores any pay values they
send. They can only assign roles that are no wider than their own.

**Employee left.** Users → Deactivate (login blocked, history kept). You
cannot deactivate yourself.

**Track employee hours.** Settings → Integrations → uAttend: API key,
enable, tick employees, set $/hr. Hours appear in Timecard / Salary, in
General Revenue payroll, on the Dashboard hours chart and in the Mechanic
Hours report (uAttend Hours column).

**Mechanic created a WO on the wrong customer (with a new unit).** Open the
work order → **Transfer** (top-right) → pick the right customer → confirm.
The unit follows automatically: if the right customer already has a unit
with that VIN, the WO is re-linked to it; otherwise the unit is deactivated
for the wrong customer and recreated (with the same mileage) for the right
one. Money is re-priced for the right customer: every job gets that
customer's default labor rate (jobs from a preset keep the preset rate) and
its amount is recalculated from the hours, catalog parts get the price from
that customer's pricing matrix, and the sales-tax flag follows the customer's
"Taxable" setting. Hours, quantities, manually entered parts and the WO's
locked tax rate don't change. The mechanic can keep working during the
transfer — their next save picks up the new rates and prices. Paid WOs
can't be transferred — "Unpaid" first.

**Mechanic forgot to stop the timer / hours look wrong.** On the work order,
next to the "Mechanics: Xh Ym" line on the job, click the pencil (needs the
"Edit mechanics' actual tracked hours" permission — owner and managers have
it). Fix the session's duration or delete an accidental session. Tracked
hours, the mechanics' split and payroll reports recalculate instantly.

**See what mechanics are doing right now.** Open the work order — the
details page is live: a red "● working" marker with the mechanic's name
appears on a job within seconds of them starting a timer, tracked time
grows on screen, and when a mechanic saves or marks the WO Done the page
refreshes itself. Keep the WO open on a screen in the office and you have a
live status board for that job — no manual refreshing.

## Customer-facing

**Send the invoice.** WO → "Email Work Order" (PDF attached; you can add a
new contact on the fly). Look of the PDF: Settings → PDF Design.

**Get approval before doing the work.** WO → "Send for Authorization"
(whole WO or a single job). The customer approves/declines by email link;
the result badge and comment show on the WO.

**Give a customer online access.** Customer page → "Portal Link" → emails.
Read-only portal: their WOs, invoices PDF, payments, vehicles, recalls,
quarterly maintenance files. Valid 30 days, auto-renews with use.

**Recall notices.** Unit → Recalls checks NHTSA now; a nightly job emails
customers automatically when new recalls appear for their vehicles.

**DOT annual inspection (AVIR).** WO editor → "Create Annual Inspection",
or the "Annual Inspection" button on the unit page, or "Inspection" next to
a unit in the customer's Units tab: fill the carrier/inspector fields, pick
the vehicle type (its OK/NA preset applies automatically), adjust the
checklist (NR = needs repair with a date, NA = not applicable), save — the
49 CFR 396 PDF downloads with the marks. The unit page keeps the full
history and shows when the current inspection expires (12 months; yellow =
within 30 days, red = expired).

**Inspection for a vehicle that's not in the system.** Perks → Annual
Inspection → "New Inspection": type the VIN, pick the type, enter the
customer name by hand — no unit or customer record needed. All saved
inspections (quick ones and those from work orders/units) are listed on
that page with PDF download and delete.

## Learning & support

**Where do I learn how X works / is there a manual?** Sidebar → **Education
Center**: type the topic into the search box (e.g. "estimate", "core",
"payroll", "VIN") and open the lesson — numbered steps with screenshots.
Each lesson has a **Mark lesson as complete** button; the home page shows
how much you have covered.

**Onboard a new employee.** Invite them (Settings → Users), then tell them
to open **Education Center** and follow the learning path for their role
(owner / manager, mechanic, parts manager) on the home page. Progress is
per user, so ask them to tell you when they have finished.

**Show me a screenshot of where the button is.** Ask the AI Helper — it
can include the matching Education Center screenshot in the answer and
link to the lesson.

**I can't log in / I need to talk to a person.** Call **(773) 331-2795**
or use the **Contact us** tab on the login page (roobico.com → Contact us,
or roobico.com/#contact): name, email, topic, message — no login needed;
the reply comes by email.

## Organization

**Open a second location.** Settings → Shops → "+ Add shop". It gets its
own settings (rates, pricing, tax, presets, integrations, timezone) and
data; grant users access via their Shop access checkboxes; switch shops in
the top bar.

**Move data from the old system.** Import / Export → import Customers,
Vendors, Parts, Units and historical Work Orders from CSV/Excel with column
mapping. Recommended order: Customers → Units (choose the customer, then
upload that customer's unit list — one file per customer) → Vendors →
Parts → Work Orders (matched to customers by name
and to units by Unit Number/VIN; paid WOs also record the payment).
Duplicates (same name / part number / VIN-per-customer as an existing
record, or a repeat inside the file) are skipped and reported with reasons,
so re-running a file will not create copies. Customers need at least one
labor rate to exist.

**Add a unit to a customer.** Customers → open the customer → **Units**
tab → **Add Unit** → type the VIN (17 characters auto-fill Year, Make,
Model and Type) or at least a Unit Number, add the mileage → **Create
Unit**. A VIN the customer already has is refused as a duplicate. From the
work order page the same form is behind "+ Add Unit".

**Import a fleet's unit list for one customer.** Import / Export → Units
tab → type the customer's name in "Import units for customer" and pick it
→ upload the CSV/Excel with Unit Number / VIN (Year, Make, Model, Type,
Mileage optional) → "Read Headers" → map the columns → Import. Every row
lands on that customer; duplicates of a VIN the customer already has are
skipped and listed with the reason.

**Back up or move data out.** Import / Export → any tab → "Download CSV" /
"Download Excel" exports all records of that entity. The columns match the
import fields, so the file can be imported into another shop as-is — except
units, which are imported per customer: split a whole-shop units export by
the Customer Name column and import one file per customer (rows of another
customer are skipped with a reason).

**Customize any table.** Every table in Roobico is adjustable per user:
- **Hide / show columns** — hover the table and click the small ⚙ button in
  its top-right corner, then tick the columns you need. At least one column
  always stays visible.
- **Resize columns** — drag the thin divider on the right edge of a column
  header.
- **Sort** — click a column header (arrows show the direction). Paginated
  lists sort on the server across all pages; smaller tables sort in place.
- Your layout (hidden columns, widths, sort) is saved automatically to your
  account and applies on any device you log in from. It never affects other
  users. To go back to defaults, open ⚙ → **Reset table**.

**Why can't I create a part / customer / vendor?** Roobico blocks exact
duplicates: a part number, customer name or vendor name that already
exists in the shop (case-insensitive), or a unit VIN that already exists
for the same customer. If the message says the existing record is
deactivated, open the list with inactive records shown and reactivate it —
its history comes back with it — instead of creating a copy.
