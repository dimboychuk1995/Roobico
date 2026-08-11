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
on the WO — manual prices are never auto-overwritten; (3) the scale isn't
the one assigned to that customer.

**Customer paid one check for several invoices.** Work Orders →
**Bulk Payment**: pick customer, enter the amount, "Auto-Distribute"
(spreads across oldest invoices), adjust, Record Payments.

**Edit a paid work order.** Paid WOs are locked. Click "Unpaid" to unlock —
**warning: this deletes its payment records**; re-record the payment after
editing.

**Overpayment / deposit.** Recording more than the balance is blocked, on
work orders and vendor orders alike. There is no deposit/credit feature.

**Estimates.** The Estimates tabs list work orders in an estimate/quote
status, but the current UI has no button to create one — treat an unpaid WO
as your quote, or ask support about the estimates workflow.

## Parts & inventory

**Stock number is wrong.** Small fix: part → Locations → "Set qty" per
location. Bigger audit: Stocktakes tab → New Stocktake (by location or
category), count, Complete & apply. To see why it drifted: part → History
(orders and WOs with quantities).

**Move parts to another shelf.** Part → Locations → "Transfer between
locations" (From / To / Qty). Totals don't change.

**Negative stock?** Work orders deliberately save even without enough stock
(you get a warning) — fix by receiving the pending order or adjusting.

**Enter a vendor invoice without typing.** Parts → Order → **AI Order
Reader**: upload the invoice PDF/photo; review matched parts, create missing
ones ("Create & Add"), then Create order.

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
create/edit/delete with user, time and endpoint.

**Revenue looks too high?** Total Revenue includes collected sales tax, and
Sales reports date WOs by their Work Order Date (falling back to creation
date). WOs without a customer are excluded from per-customer reports.

## Team & access

**Hire a mechanic who must not see prices.** Create the user with role
**Mechanic** (or Senior mechanic). They automatically get the no-price
mechanic mode with job timers; their saves always go to "In Progress" for a
manager to finish. No prices appear for them anywhere, web or mobile.

**Give one user an extra right (or take one away).** Users → Permissions →
Allow/Deny overrides. Deny always wins. For many users — create/clone a
role in Roles & Permissions.

**Employee left.** Users → Deactivate (login blocked, history kept). You
cannot deactivate yourself.

**Track employee hours.** Settings → Integrations → uAttend: API key,
enable, tick employees, set $/hr. Hours appear in Timecard / Salary, in
General Revenue payroll, on the Dashboard hours chart and in the Mechanic
Hours report (uAttend Hours column).

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

## Organization

**Open a second location.** Settings → Shops → "+ Add shop". It gets its
own settings (rates, pricing, tax, presets, integrations, timezone) and
data; grant users access via their Shop access checkboxes; switch shops in
the top bar.

**Move data from the old system.** Import / Export → import Customers,
Vendors, Parts, Units and historical Work Orders from CSV/Excel with column
mapping. Recommended order: Customers → Units (map the Customer Name column
to link them) → Vendors → Parts → Work Orders (matched to customers by name
and to units by Unit Number/VIN; paid WOs also record the payment).
Duplicates (same name / part number / VIN-per-customer as an existing
record, or a repeat inside the file) are skipped and reported with reasons,
so re-running a file will not create copies. Customers need at least one
labor rate to exist.

**Back up or move data out.** Import / Export → any tab → "Download CSV" /
"Download Excel" exports all records of that entity. The columns match the
import fields, so the file can be imported into another shop as-is.

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
