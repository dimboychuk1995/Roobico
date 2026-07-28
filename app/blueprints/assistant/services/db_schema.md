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
`labors: array` of blocks: `{labor_id, labor: {description, hours (string),
rate_code, hourly_rate, labor_full_total, issue_description, assigned_mechanics:
[{user_id, name, percent}]}, parts: [{part_id, part_number, description, qty,
cost, price, core_charge, misc_charge, one_time_part}]}`.
`totals: dict` — money rollup: `{labor_total, parts_total, core_total,
misc_total, cost_total, shop_supply_total, sales_tax_rate, sales_tax_total,
grand_total, is_taxable}`. Unpaid balance = grand_total minus sum of active
work_order_payments for the WO.

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
(+`core_has_charge`), `misc_charges: [{description, price, taxable}]`.

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
is_main}]`, `notes`.

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
Appointments: `title`, `start_time`/`end_time: datetime`, `status`
("scheduled", ... — per-shop statuses), `customer_id`, `customer_label`,
`unit_id`, `unit_label`, `mechanic_id`, `mechanic_name`,
`presets: [{id, name}]`.

### wo_time_logs
Mechanic timers: `work_order_id` → work_orders, `wo_number: int`, `labor_id`,
`user_id`, `user_name`, `started_at`, `stopped_at` (null = running),
`seconds: int` (elapsed, set on stop).

### recall_notifications
Sent NHTSA recall notices: `customer_id`, `unit_id`, `campaign_number`,
`status` ("sent", "skipped_no_email"), `to: [email]`, `created_at`.
