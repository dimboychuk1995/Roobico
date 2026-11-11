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

**A service template shows an old price / does not follow the average
cost.** Settings → Service Templates and look at the badge next to the
line: (1) **fixed price** — the part itself has a fixed Selling price in
the catalog, so neither the template nor a work order recalculates it from
cost; open Parts → edit the part → change the Selling price or untick
"This part has selling price" and the line starts following the average
cost through the pricing scale; (2) **custom price** — somebody typed a
price into the template; edit the template and clear the Price field to go
back to automatic pricing; (3) **no price** — the part has no average cost
yet; (4) no badge — the price already is the default scale on the current
average cost. A red **below cost** badge means the line sells for less than
the part costs now. Work orders of customers with their own scale
("Override part selling price") are priced by that scale, so their price
can differ from the card.

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
editing. No reload is needed after Unpaid: click Edit, make the changes,
Save — jobs and parts you delete are removed. (Once in a while the page
reloads by itself right after Unpaid and shows "Marked as unpaid." — that
is normal, just continue.)

**Overpayment / deposit.** Recording more than the balance is blocked, on
work orders and vendor orders alike. There is no deposit/credit feature.

**Estimates.** Create a quote via Create Work Order dropdown → "Save as
Estimate" — it lives on the Estimates tab, takes no parts from stock and
cannot receive payments. Send it for customer approval like a WO (same
email + PDF, titled Estimate). When the job is approved, open it and use
Save dropdown → "Convert to Work Order" (parts are deducted at that
moment). The reverse works too: on an unpaid WO, Save dropdown → "Save as
Estimate" returns its parts to inventory and turns it back into a quote;
WOs with recorded payments must have the payments deleted first. Deleting
an estimate never touches stock or cores. Mechanics cannot start job
timers on an estimate — convert it first; a manager may time work on an
estimate and it stays an estimate.

## Parts & inventory

**Stock number is wrong.** Small fix: part → Locations → "Set qty" per
location. Bigger audit: Stocktakes tab → New Stocktake (by location or
category), count, Complete & apply. To see why it drifted: part → History
(orders and WOs with quantities).

**Move parts to another shelf.** Part → Locations → "Transfer between
locations" (From / To / Qty). Totals don't change.

**Negative stock?** Work orders deliberately save even without enough stock
(you get a warning), and **Unreceive** / deleting a received order whose
parts were already used also leaves a minus (the warning lists the parts)
— fix by receiving the pending order or adjusting.

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
ones ("Create & Add" — it opens the same full "Create new part" form as Add
Part, prefilled with the line's number, description, price and the order's
vendor; set the unit, minimum stock, selling price, core / misc charges if
needed → Create, and the line lands on the order), then Create order.

**AI put the whole line amount into the price.** This is now corrected
automatically: the reader extracts the unit price and the printed line
amount separately and, when they disagree, uses line amount ÷ qty (hover
the price to see the tooltip when that happened). If a price still looks
wrong, fix it in the review table before "Add" — the **Total** column shows
Qty × Price so a mismatch with the invoice is obvious.

**The vendor shipped fewer (or more) parts than I ordered.** Receive the
order as usual: Parts → Orders → open the order → **Receive Order** (or
click the yellow "ordered" status in the list). In the dialog every line
shows the ordered quantity and a **Received** field pre-filled with it —
type what actually arrived for each line (ordered 15, got 10 → type 10;
nothing came → 0, the line is removed), set the Vendor Bill and locations
as usual → Receive Order. The order is updated to the received quantities
(the original stays on the line as "ordered 15"), so the order total
equals the vendor bill and stock gets only what came; the toast says how
many lines were changed. Got more than ordered? Type the bigger number —
the order grows to match. Edited quantities or prices in the order dialog
before pressing Receive Order? They are saved automatically first. If a
payment already recorded is bigger than the new total, receiving stops —
delete or correct that payment on the Parts Orders Payments tab, then
receive again. The missing parts arrive later? Create a new order for the
remainder (a back-order); an order is received once. Received by mistake
or want to redo it? **Unreceive** — it always works, even when the parts
were already used on work orders (stock goes below zero, with a warning),
then receive again with the right numbers. On the phone the Receive
dialog has only the Vendor Bill field — partial receipts are done on the
web.

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
badge; open it (Confirm button), add unmatched lines with "Create & Add"
(the full part form, prefilled from the line and the order's vendor),
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

**Save a vendor's website login (portal login / password).** Vendors → the
vendor's **Edit** (or **Add Vendor** for a new one) → **Website**: type it
plainly, e.g. `fleetpride.com` — no `https://` needed → **Portal login**
section: Login and Password (**Show** checks what you typed) → Update
Vendor. The password is stored encrypted. Later: leave Password empty to keep
it, type a new one to replace it, tick **Remove saved password** to delete
it. You need "View vendor portal logins & passwords" (Owner, General
manager, Manager, Parts manager by default) plus Edit/Create vendors — if
the section is missing, ask the owner to tick that permission in Settings →
Roles. On the phone: vendor → edit (pencil) → Portal login / Portal
password.

**Quickly look up a vendor password.** Vendors → tab **Portal logins** →
search the vendor → **Show** (hides itself after 30 seconds) or the copy
button next to the password ("Password copied") / next to the login; the
website link opens the portal in a new tab. On the phone: vendor → Portal
login card → Show, long-press to copy. Each password Show — and Copy while
the password is hidden — is logged in Reports → Activity Journal ("Viewed
portal password of …", Action **Viewed**); copying the login is not.
**Who looked at a vendor's password?** Reports → Activity Journal → Object
type **Vendor**, Action **Viewed** (or click a "Viewed portal password of …"
row). No Portal logins tab? You
don't have the "View vendor portal logins & passwords" permission. Want
someone to look passwords up without editing vendors? Give their role
"View vendors" + that permission only — they open Vendors straight on
Portal logins.

## Reports & numbers

**How much did I earn?** Reports → General Revenue: left block = Revenue −
Parts Cost (no payroll), right block = Revenue − Parts Orders − Salaries.
The table below explains every number.

**Why is payroll $0 in General Revenue?** Check in this order: (1) the
period is **All Time** — payroll is counted per period, pick This Month or
a date range (a yellow "Payroll is incomplete" banner says so); (2) a
customer filter is applied — payroll can't be split per customer, the
banner explains; (3) the uAttend integration and pay types/rates aren't set
up: salary users need Pay type = Salary with a weekly amount; hourly users
must be ticked in Settings → Integrations with an $/hr rate; (4) uAttend
did not answer — the yellow banner quotes its error, salaries are still
counted but hourly pay is missing: fix the connection in Settings →
Integrations.

**Hourly pay is missing or too small in General Revenue / Timecard.**
Both reports show the same payroll, so check it in Reports → Timecard /
Salary, one person at a time:
- The person has **no row at all** — they are not ticked in Settings →
  Integrations → uAttend (only ticked employees are paid by punches).
- Their row says *"Hourly rate not set in Integrations"* — set the $/hr
  rate there. In General Revenue the same person shows as
  "Name — 12.00h, hourly rate not set" with $0.
- Their row says *"No punches in this period"* — they did not clock in on
  the uAttend clock in these dates (job timers in Roobico do not count for
  pay).
- They appear as **Salary** with a tiny total and the note *"uAttend rate
  $33.00/hr is not used — Pay type is Salary"* — an hourly rate was typed
  into "Salary amount", so they get $33 per week. Settings → Users → Edit →
  **Pay type = Hourly** → Save; the next report load pays punches × rate.

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

**Who did what today / this week?** That is tracked time, not the
calendar: Reports → **Mechanic Hours** (Tracked vs Billed hours per
mechanic for any period), the Work Orders list → **In Work** group for
what is running right now, or a work order's time logs for every
session. The calendar shows plans (appointments) only.

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

**Plan a job for a mechanic.** Calendar → **By mechanic** → click inside
the mechanic's column at the start time (in Week view click the day and
pick the mechanic in the dialog) → customer and unit if known (the unit
box searches by number, make/model and VIN), presets, more mechanics if
it is a crew job, the phone number and the job list in the title → Save.
To turn the plan into a WO press **Create Work Order** (needs customer
and unit): customer, unit, presets, the whole crew and the note are
carried over and the appointment is linked to the new WO, so its status
updates automatically. Or leave it to the mechanic: on the phone the
appointment has **Start work**, which builds the WO from the presets
(hours, parts, crew) and shows the note under the job. An existing WO
can be scheduled too: open it → **Schedule** → the calendar opens a
linked, prefilled appointment — set the date and time, Save.

**Wrong customer or unit on a linked appointment.** While an appointment
is linked to a work order its customer and unit are read-only in the
dialog — fix them on the work order (Transfer for the customer, the unit
field for the unit) and the appointment follows; or press **Unlink** in
the appointment and Save if the link itself was the mistake (the
appointment stays, only the link goes). The status is different: In
Progress / Completed arrive from the WO automatically, but nothing ever
moves them back — if the job was reopened, or the mechanic started the
wrong WO, click the appointment and set the status by hand. Cancelled
can always be set by hand and the sync never touches it.

**Move a planned job (other time, day or mechanic).** Drag the block: in
Week view to another time or day, in By mechanic into another column —
a one-mechanic plan gets that mechanic, for a crew the member whose
column you left is swapped for the new one, the Unassigned column
removes the mechanics. Drag the bottom edge to change the length; Esc
cancels a drag. The dialog does the same: Date, Start / End (changing
Start keeps the duration), the Mechanics tags. Moving an appointment
never changes the work order it is linked to — the mechanics on the WO's
labor blocks stay as they are; reassign them on the WO if the job really
changed hands.

**Check the schedule from the phone (mechanic: "what's my day?").** Open
the **Calendar** tab in the app — every role with "View calendar" has it,
mechanics included. Tap the person icon for **Only mine**: only the
appointments you are assigned to remain; the choice sticks. Tap the view
icon (top-left) for Schedule / Day / 3 Day / Week / Month; tap the month
name to jump to a date. Tap an appointment for its details: a linked one
has **Open work order** to go straight to the job, an unlinked one gives
a mechanic **Start work**, which builds the WO from it. Office users can
create an appointment from the **+** button or an empty slot and turn it
into a WO with **Create work order** — customer and unit are carried
over and the appointment is linked automatically.

**An old appointment is still hanging around after the job was done.**
Nothing leaves the calendar by itself — a linked appointment turns
Completed when the work order is done, an unlinked one keeps the status
you gave it. Click it and set **Completed** (or **Cancelled** if the
visit never happened — it stays visible but faded), or **Delete** it if
it was created by mistake. To tidy a whole day use By mechanic: the
header counts each mechanic's live appointments, cancelled ones are not
counted.

**Two jobs at the same time for one mechanic?** Allowed — the calendar
never refuses an overlap. The two blocks are drawn side by side in the
column so the clash is visible at a glance, and the dialog shows a small
overlap hint under the fields while you pick the time. Decide yourself:
move one of them (drag), shorten it (bottom edge) or give it to someone
else (By mechanic → drag into another column).

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

**Two mechanics on one work order — do they overwrite each other?** No.
Both can add jobs, parts and quantities to the same work order at the
same time — two phones, a phone and the web mechanic page, with the office
editing too. Every save is merged on the server: a colleague's new job or
part is never deleted by your save, even if your screen was not refreshed.
The app refreshes the work order by itself (every 20 seconds and whenever
you come back to it), so each mechanic sees the other's jobs. When a save
was combined with someone else's changes, a **"Merged with changes from
others"** box (app) or a short list after Save (web) says what happened —
just read it. To remove a colleague's job: make sure nobody's timer is
running on it (such a job is never removed), then delete it. Keep the app
updated — older versions still save safely but do not refresh by
themselves, and a job deleted in an older version can come back after the
save (delete it once more); on an older version keep only one edit screen
of a work order open. Details: Work Orders help → "Several people editing
one work order".

**A job disappeared — where did it go?** 1) Open the work order →
**History** (or Reports → **Activity Journal** and search the WO number).
Every save shows the **Jobs** list before and after it and who saved, so
you see exactly which save removed the job and who made it; a save that
was merged with other people's changes also shows its **Merge notes**.
2) The mechanic's time is not lost: under the jobs the work order shows
**"Tracked on removed labor lines: Xh Ym"**, and the hours still count in
Reports → Mechanic Hours ("Tracked") and the Dashboard's Actual hours.
Pay is not affected at all — Timecard / Salary comes from salaries and
uAttend punches, never from job timers. 3) To bring the job back, add it
again (a preset is quickest) and type its hours. Good to know: a save
removes a job only when the person saving had that job on screen and
deleted it — never a job somebody added after they opened the work order,
and never a job whose timer is running. If someone deleted the job while
you still had it open and you then changed it, your save brings it back.

**Save says "This page was opened before Roobico was updated".** The work
order page was opened before an update of Roobico, and the work order now
has a job or part that this page does not show (you removed it, or
somebody else added it). Nothing was saved and stock did not move. Press
F5 to reload, make the change again, click Save.

**Save says I changed the work order while it was being saved.** Something
on the page changed while the save was on its way (for example a price
filled in by itself). The first save went through, and the newer changes
are still on the page in Edit mode — just click Save again.

**Jobs I deleted came back with "Couldn't confirm what you started
from".** The page could not tell Roobico what it showed when you started
editing (usually it had been open for more than two weeks), so the save
kept everything to be safe. Reload the page, delete them again, Save.

**Start job says the job is no longer on the work order.** Somebody
removed that job at that same moment. Reopen the work order (reload the
page on the web) to see the current jobs; the work order's **History**
(Activity Journal) shows who removed it. If your timer on another job was
running, the message says it was stopped — that time is saved.

**Mechanic can't start a timer on an estimate.** By design: Start job says
"This work order is an estimate. Time can be tracked after the office
converts it to a work order." Open it in the office → Save dropdown →
**Convert to Work Order**; then the mechanic can start the timer.

**Save is greyed out on the mechanic web page.** Read the yellow bar at
the top: the work order was confirmed by a manager, is an estimate, or is
already paid. Ask the manager to cancel the confirmation (or convert the
estimate); the page notices within about 20 seconds, the bar disappears
and your unsaved changes are still there — then Save.

**The app says "That job was removed by someone else…" or "This job is no
longer on the work order…".** You added a part, applied an AI edit or sent
a job for approval while a colleague removed that job. Nothing was put on
another job. Check the work order and redo it on the right job.

**See who has which role / list my mechanics.** Settings → **Users &
Roles**: the list is grouped by role — Owner, General manager, Manager,
Parts manager, Senior mechanic, Mechanic, Viewer, then your custom roles
A→Z — and every section header shows how many people have that role and
how many of their invitations are still pending ("Mechanic · 4 · 1
invited"). Your mechanics are the **Senior mechanic** and **Mechanic**
sections: exactly these people can be assigned to jobs and are billed per
mechanic. To see only them, type "mechanic" in the search box (a role name
works too — "parts manager" or the name of a custom role). Former staff:
switch the status filter to **All users** — deactivated people are
collected in the **Inactive** section at the bottom, each with the role
they had. A section with an orange **Role not found** badge means those
people's role was deleted: click **Edit** and give them an existing role —
until then they have no permissions from a role.

**Give someone a custom role.** Create the role first in Settings → **Roles
& Permissions** (New role, or Clone a similar one and adjust the ticks).
Then Users & Roles → **Edit** on the person (or **+ New user**) → **Role**
→ pick it under "Custom roles" → Save. The person moves to that role's
section of the list. You can assign only a role whose permissions you have
yourself (the owner can assign any).

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
work orders open read-only. Who is paid how much (the Timecard / Salary
report and the per-employee lines in General Revenue) stays hidden unless
you tick "View Timecard / Salary report"; General Revenue still shows the
payroll totals.

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
cannot deactivate yourself. The person leaves the default list; with the
status filter on **All users** / **Inactive users** they are in the
**Inactive** section, where **Activate** brings them back.

**Renamed a mechanic but an old name still shows?** Reload the page:
reports, the dashboard, the Work Orders list and the calendar look the
person up by account and show the current name. A few places keep the old
name on purpose — the Activity Journal (it is a log of what happened),
printed annual inspection reports, and uAttend-only rows (the name comes
from uAttend). The AI assistant can still use an old name only for a
rename made before this feature existed, until the Roobico administrator's
one-time cleanup has run. The full list and fixes are in Reports help →
**"People's names after a rename"**.

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
it). Fix the session's duration (then Save in that window) or delete an
accidental session. The job's tracked hours, the mechanics' split, the
work order totals and Reports → Mechanic Hours recalculate at once; pay
(Timecard / Salary) is based on salaries and uAttend punches, so it does
not change. Outside Edit mode the page reloads to show the new hours; in
Edit mode they appear after you click Save on the work order (your other
unsaved edits stay).

**See what mechanics are doing right now.** Open the work order — the
details page is live: a red "● working" marker with the mechanic's name
appears on a job within seconds of them starting a timer, tracked time
grows on screen, and when a mechanic saves or marks the WO Done the page
refreshes itself. Keep the WO open on a screen in the office and you have a
live status board for that job — no manual refreshing.

## Customer-facing

**Send the invoice.** WO → "Email Work Order" (PDF attached; you can add a
new contact on the fly). Look of the PDF: Settings → PDF Design.

**Invoice shows a smaller Labor (or Parts) than the work-order page.** Not
a bug — the two views group the same money differently. The work-order page
is the internal view: its "Labor total" includes shop supply and its "Parts
total" includes core and misc charges. Every customer document (invoice/PDF,
work-order email, customer portal, authorization page/email) lists each
amount once: Labor = labor only, Parts = parts only, and Core Charges, Shop
Supplies, Misc and Sales Tax are their own lines — so the Total is exactly
the same, only the grouping differs. Quick check: Labor + Parts + Core
Charges + Shop Supplies + Misc + Sales Tax on the invoice = the Grand total
on the WO page. One exception by design: an authorization for a single job
shows "Block total" = that job's labor + parts + its share of shop supply,
because that is the amount the customer approves.

**Hide shop supply from the invoice.** Settings → PDF Design → untick "Show
shop supply" → Save PDF Design. The Shop Supplies line disappears from the
PDF and its amount folds into the Labor line, the Labor item rows and the
per-job Subtotals, so the invoice still adds up to the same Total ("Show
core charges" / "Show misc charges" fold into the Parts line the same way).
This changes only the PDF: the work-order email, the customer portal and
authorization pages always list shop supply as its own line. To stop
charging shop supply instead of hiding it, set "Shop supply: $" to 0 on the
work order (↺ restores the default) or change the shop supply percentage in
Settings → Work Order Settings.

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
