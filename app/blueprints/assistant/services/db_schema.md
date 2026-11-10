<!-- Схема shop-базы для промпта AI-помощника (см. data_tools.py / chat.py).
     Обновлять при изменении структуры коллекций. Не user-facing. -->

Common: every document has `shop_id` (scoped automatically — never filter on it),
`is_active: bool` (soft delete — filter `{"is_active": true}` unless the user asks
about deleted records), `created_at`/`updated_at` (UTC datetime). References
(`*_id`) are ObjectIds, rendered/queried as 24-hex strings.

### work_orders
Work orders & estimates. `wo_number: int` (human number, starts 1000),
`customer_id` → customers, `unit_id` → units, `status` (active: "open",
"in_progress", "paid"; estimates: "estimate"/"estimated"/"quote"/"quoted"),
`work_order_date: datetime`, `authorizations: array` (customer approvals).
For users without `work_orders.view_costs` (mechanics) the tools return a
paid work order with status "completed" — "paid" is never shown to them and
a filter on "paid" finds nothing; describe such a work order as completed.
`labors: array` of blocks: `{labor_id, labor: {description, hours (string),
hours_source ("preset" = hours taken from the service preset, "tracked" =
from mechanic timers, "" = typed by hand), rate_code,
hourly_rate, labor_full_total, issue_description (the "Describe issue"
text; a WO created from a calendar appointment carries the appointment's
note here), assigned_mechanics: [{user_id, name, percent}]}, parts:
[{part_id, part_number, description, qty, cost, price, core_charge,
misc_charge, one_time_part, unit (unit-of-measure key copied from the part
when it was added, e.g. "gallon"; absent = "each")}]}`.
`totals: dict` — money rollup: `{labor_total, parts_total, core_total,
misc_total, cost_total, shop_supply_total, sales_tax_rate, sales_tax_total,
grand_total, is_taxable}`. Unpaid balance = grand_total minus sum of active
work_order_payments for the WO.
Mechanic progress: `mechanic_done: bool` (every mechanic who worked on the
WO pressed Done — the "Mechanic done" badge), `mechanic_done_marks:
[{user_id, name, at}]` (who already pressed Done), `manager_confirmed:
bool` (confirmed by a manager, locked for mechanics).
Internal bookkeeping (never needed for answers): `labors_rev: int` is an
edit counter for the `labors` array and the Done marks — every write that
changes them adds 1 (any save from the office page, the app or the
mechanic page, Describe Issue, a timer start, hours recalculated after a
timer stop or a time-session edit, Done / Save In Progress, Transfer,
delete); missing = 0. It only stops two people from overwriting each
other and says nothing about money or progress. A labor block may also
carry `client_key` (the key a page or the app gave a new job before it
had a `labor_id`, so a repeated save does not create the job twice) and
`minted: {by, sid, at, ck}` (marks a job the server created during a
combined save: user id, device session, UTC time, that client_key).

### customers
`company_name`, `contacts: [{first_name, last_name, phone, email, is_main}]`
(legacy mirror fields `first_name/last_name/phone/email` also exist), `address`,
`taxable: bool`, `default_labor_rate` → labor_rates, `pricing_rule_id` →
parts_pricing_rules, `override_part_selling_price: bool`.

### units
Customer vehicles/equipment. `customer_id` → customers, `vin`, `unit_number`,
`make`, `model`, `year: int`, `type`, `mileage`, `recalls_notified: {campaigns:
[str], checked_at}`.

### work_order_payments
`work_order_id` → work_orders, `amount: float`, `payment_method` ("cash", ...),
`payment_date: datetime`, `notes`. Deleted payments have `is_active: false`.

### parts
Catalog + inventory. `part_number`, `description`, `reference`, `vendor_id` →
vendors, `category_id` → parts_categories, `location_id` → parts_locations,
`do_not_track_inventory: bool`, `in_stock: int` (total on hand; absent when not
tracked), `average_cost`, `selling_price` (+`has_selling_price`), `core_cost`
(+`core_has_charge`), `misc_charges: [{description, price, taxable}]`,
`unit_of_measure` (catalog key, absent = "each": each, gallon, quart, pint,
fl_oz, liter, milliliter, pound, ounce, kilogram, gram, foot, inch, meter, box,
case, pack, pair, set, roll, can, bottle, tube, drum, pail, kit),
`min_stock_enabled: bool` ("This part requires minimum stock"; absent = false),
`min_stock: int` (the minimum; 0 when disabled). The low-stock state is NOT
stored — compute it: below = `in_stock < min_stock`; near = `min_stock <=
in_stock <= max(min_stock + 1, ceil(1.2 * min_stock))`; both only when
`min_stock_enabled` is true and `do_not_track_inventory` is not true. Parts
below minimum: `{"min_stock_enabled": true, "do_not_track_inventory": {"$ne":
true}, "$expr": {"$lt": [{"$ifNull": ["$in_stock", 0]}, {"$ifNull":
["$min_stock", 0]}]}}`.

### part_location_stock
Per-location stock rows: `part_id` → parts, `part_number`, `location_id` →
parts_locations (null = Unassigned), `qty: int`.

### inventory_movements
Append-only stock journal: `part_id`, `part_number`, `location_id`, `type`
("initial", "receive", "unreceive", "wo_deduct", "wo_restore", "wo_adjust",
"manual_edit", "transfer", "stocktake", "vendor_return", "tracking_off"),
`qty_delta: int`, `stock_after: int`, `ref: {kind, id, label}`, `created_at`.

### parts_orders
Purchase orders to vendors. `vendor_id` → vendors, `order_number: int`,
`vendor_bill`, `items: [{part_id, part_number, description, price, quantity,
core_charge}]`, `non_inventory_amounts: [{type, description, amount}]`,
`status` ("ordered", "received", "returned"), `payment_status` ("unpaid",
"partially_paid", "paid", "credit"), `order_date: datetime`, `paid_amount`,
`remaining_balance`. Vendor returns: `is_return: true`, `return_for_order_id`,
`credit_total`.

### parts_order_payments
`parts_order_id` → parts_orders, `amount`, `payment_method`, `payment_date`,
`notes`.

### vendors
`name`, `website`, `address`, `contacts: [{first_name, last_name, phone, email,
is_main}]`, `notes`. `website` is stored as a full link
("https://fleetpride.com"); older records may hold it as typed
("fleetpride.com"), so match a site with a case-insensitive regex on the
domain. Vendor portal logins and passwords (the vendor's website
account) are NOT available to you: those fields are removed from results and
any query that references them is rejected. If the user asks for a vendor's
login or password, tell them to open Vendors → **Portal logins** (needs the
"View vendor portal logins & passwords" permission).

### labor_rates
`code` ("standard", ...), `name`, `hourly_rate: float`.

### parts_categories
`name`, `slug`.

### parts_locations
Location tree: `name`, `parent_id` → parts_locations (null = root).

### stocktakes
Inventory counts: `number: int`, `name`, `scope: {location_id, location_path,
category_id, category_name}`, `status` ("open", "completed", "cancelled"),
`items_total: int`, `totals: {items_counted, items_uncounted, items_zeroed,
items_adjusted, variance_qty, shortage_value, overage_value}` (set on complete).

### stocktake_items
`stocktake_id` → stocktakes, `part_id`, `part_number`, `location_path`,
`expected_at_count`, `counted_qty`, `variance`, `average_cost`, `status`
("pending", "counted"), `auto_zeroed: bool`.

### cores
Core charges on hand: `part_id` → parts, `part_number`, `core_cost`,
`quantity: int`.

### core_returns
`core_id` → cores, `part_id`, `part_number`, `quantity`, `core_cost`,
`credit_total`, `vendor_id`, `vendor_name`, `returned_at`.

### parts_pricing_rules
Pricing scales: `name`, `mode` ("margin"/"markup"), `rules: [{from, to,
value_percent}]`, `is_default: bool`.

### calendar_events
Appointments — the only thing the calendar shows (work orders are not
drawn on it; a WO is on the calendar only through a linked appointment).
`title` (free text stored exactly as typed — dispatchers put the phone
number and the job list here; may be ""; the API's `note` = the title
when it differs from `customer_label`, else ""), `start_time`/`end_time:
datetime` (shop wall-clock, no timezone; both on the same calendar day),
`status` ("scheduled", "confirmed", "in_progress", "completed",
"cancelled" plus custom per-shop keys; a WO-linked appointment's status
follows the work one way only: WO in work → "in_progress", mechanic done
/ completed / paid → "completed"; nothing ever reverts it to "scheduled",
"cancelled" is never touched, and when the shop's statuses lack the
target key nothing changes), `customer_id` (null when there is no
customer; an old record may carry `customer_label` "New Customer" with
no id — treat that label as empty), `customer_label`, `unit_id`,
`unit_label` (labels are refreshed from the live customer/unit when
read; for a linked appointment customer and unit are copied from the WO
and follow transfers / unit changes), `presets: [{id, name}]` →
wo_presets, `work_order_id` → work_orders (linked plan; a WO a mechanic
starts from the appointment gets one labor per preset with
`hours_source: "preset"`, the crew in `assigned_mechanics` and the note
in `issue_description` — the office web/phone forms prefill the same
things on the client; the calendar itself never writes to work orders:
moving an appointment does not reassign WO mechanics and reassigning WO
mechanics does not move appointments), `wo_number`. Mechanics (one or a
crew): `mechanic_ids: [ObjectId]` (filter by a mechanic with
`{"mechanic_ids": <id>}`), `mechanics: [{id, name}]`; legacy
`mechanic_id`/`mechanic_name` = the first mechanic only — older records
may have only these. Overlapping appointments of one mechanic are
allowed. Per-shop settings live in `calendar_settings`: `{key:
"statuses", statuses: [{key, label, color}]}` (system keys scheduled,
in_progress, completed, cancelled cannot be removed) and `{key: "hours",
start_hour, end_hour}` (visible grid hours, default 6–20).

### wo_time_logs
Mechanic timers: `work_order_id` → work_orders, `wo_number: int`, `labor_id`,
`user_id`, `user_name`, `started_at`, `stopped_at` (null = running),
`seconds: int` (elapsed, set on stop). A log whose `labor_id` is no longer
on the WO is time tracked on a removed job — it still counts as the
mechanic's tracked time. To find one person's timers (or jobs via
`assigned_mechanics.user_id`), filter by `user_id`: names stored here are
copies and can be outdated for people renamed before name copies were
kept in sync.

### wo_edit_views
Internal — not readable by you. Technical snapshots used only to merge
simultaneous edits of a work order safely: what one screen (office page,
mechanic page, app) showed when its user started editing, sent with a
save, or got back from it. Fields:
`work_order_id`, `user_id`, `sid` (device session), `channel` ("office",
"mechanic_web", "mobile"), `kind` ("load", "sent", "merged"), `protocol`,
`labors` (copies of the job rows), `minted` (sent: ids of jobs that save
created), `labors_rev`, `created_at`, `derived_from` (set when the view was
re-derived after the same user's Describe Issue or time-session edit);
office views also have `wo_fields: {customer_id, unit_id,
work_order_date, work_order_date_input (the page's date field,
shop-local), is_taxable, mileage, shop_supply_pct}`. Documents expire
automatically after 14 days. This collection is not in your list of
readable collections and is never needed to answer a user — use
work_orders and wo_time_logs, and for "who changed what" point the user
to the work order's **History** (Reports → Activity Journal).

### recall_notifications
Sent NHTSA recall notices: `customer_id`, `unit_id`, `campaign_number`,
`status` ("sent", "skipped_no_email"), `to: [email]`, `created_at`.
