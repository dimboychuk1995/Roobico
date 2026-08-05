# Parts and Inventory

The **Parts** page has six tabs: **"Parts"**, **"Parts Orders"**, **"Parts
Orders Payments"**, **"Cores"**, **"Cores Returns"**, **"Stocktakes"**.
Header buttons: **"Order"** (new parts order) and **"Add Part"**.
Categories, storage locations and pricing scales are configured on
**Settings → Parts & Pricing**.

## Parts list

- Search "Search parts by any field..." — part number, description,
  reference, stock, cost, and also vendor/category/location names.
- Columns: Part #, Description, Reference, In stock, Avg cost, Vendor,
  Category, Location, Actions. The Part # cell shows a ⇆ hint with
  interchangeable part numbers. Location shows a per-location breakdown when
  stock is split ("Shelf A × 3, Shelf B × 1"); stock without a location shows
  "Unassigned".
- Row actions: **Cross refs**, **History**, **Locations** (tracked parts
  only), **Edit**, **Deactivate** (soft delete; **Restore** brings it back).
- Footer: **"Total inventory cost"** (Σ stock × avg cost) and **"Total core
  cost"**.
- There is no low-stock/min-quantity feature — no reorder points exist.

## Part fields

- **Part number** (the only required field), **Reference** (alternative/OEM
  number), **Description**. Part numbers are unique within the shop
  (case-insensitive): creating or renaming a part to an existing number is
  blocked with "already exists". If the message says the existing part is
  deactivated, reactivate it (its stock history comes back with it) instead
  of creating a copy.
- **In stock** — starting quantity; afterwards stock changes only through
  orders, work orders, transfers, stocktakes and manual adjustments.
- **Average cost** — weighted average, recalculated on every receiving:
  (old avg × old qty + received price × received qty) / total qty.
- **"This part has selling price"** → fixed **Selling price** that overrides
  the pricing scale (unless the customer has "Override part selling price").
- **"Do not track inventory for this part"** — part stays usable on WOs and
  orders, but stock is never adjusted (turning this on zeroes stock and
  disables core charge).
- **"This part has Core charge"** → **Core cost** (refundable deposit per
  unit).
- **"Add Misc charge for this part"** → a list of misc charges (description,
  price, taxable) automatically added when the part is put on a work order.
- Vendor, Category, **Location** (the part's primary/default storage
  location). Attachments available in edit mode.

## Cross references (interchangeable parts)

Button **"Cross refs"** → modal "Cross references — <part>": search a part by
number and add it. Linked parts are mutually interchangeable (linking is
transitive: A–B and B–C makes A–C interchangeable). They appear as
alternative rows in the part search on work orders and parts orders, and in
the mobile app. Removing a reference unlinks just that part.

## Storage locations & stock by location

- The location tree (up to **4 levels**: e.g. Warehouse › Rack › Shelf › Bin)
  is built on Settings → Parts & Pricing, card "Parts Locations".
- Each part has one primary location, but stock can sit in several locations
  at once. The **"Locations"** button on a part opens **"Stock by
  location"**: per-location quantities with **"Set qty"** (manual
  adjustment) and a **"Transfer between locations"** section (From / To /
  Qty) — transfers don't change the total.
- A location can't be deleted while it has sub-locations, is a primary
  location for parts, or still holds stock (transfer it out first).
- **"History"** on a part shows its Orders and Work Orders with dates and
  quantities.

## How stock moves

Stock increases when a parts order is **received** and when a WO is deleted
(parts return). It decreases when parts go on a **work order**, on **vendor
returns**, and via manual adjustments/stocktakes. On WOs stock **may go
negative** (the WO still saves; you get a warning). Every change is journaled
internally.

## Stocktakes (physical counts)

Tab **"Stocktakes"** → **"New Stocktake"**: optional name, **Location
scope** (default "Whole warehouse", sub-locations included) and **Category
scope**. The warehouse keeps working while you count: each entered quantity
is compared to the system stock at the moment of counting, and adjustments
apply as deltas on completion.

- Count screen: filter, chips All / Pending / Counted / Discrepancies,
  **"Add found part"** for parts found on a shelf but missing from the list.
  Columns: Location, Part #, Description, Expected, Counted (+Save),
  Variance, Value, Status. If stock moves after a line was counted it's
  flagged **"Recount"**.
- **"Complete & apply"** applies variances. For a full inventory tick
  **"Set uncounted lines to zero"** in the confirmation — everything you did
  not count in the scope is treated as not found and zeroed; for a cycle
  count leave the box unticked and uncounted lines stay untouched.
  **"Cancel"** discards. Stocktakes are numbered ST-1, ST-2, ...
- **"What changed"** — a completed stocktake shows a was → became table of
  every adjusted line, split into three groups: **Quantity changed**,
  **Went to zero** (counted as 0 or auto-zeroed as uncounted) and
  **Found during count** (system had 0, counting found stock — including
  parts added via "Add found part"). Each row shows Was, Became, the ±
  difference and its value at average cost; the header badges give the
  shortage/overage totals. Lines whose count matched the system are not
  listed — the full line-by-line record stays in the items table below.

## Cores and Cores Returns

- A part with a core charge either bills the deposit to the customer (Core
  toggle on the WO line) or — if not billed — the old core is kept by the
  shop and tracked on the **"Cores"** tab (quantity per part).
- **Adjusting the quantity by hand**: the Quantity column on the Cores tab
  is editable — type the real number of cores on hand and press **Save**
  (a core got damaged, lost, or found). This is a plain correction: it does
  NOT create a vendor credit or a Cores Returns entry. Setting it to 0
  removes the row from the list.
- **"Return"** on a core → "Return cores to vendor": quantity, live Credit
  amount (qty × core cost), optional vendor and notes (RMA #). The
  **"Cores Returns"** tab is the ledger of returned cores and credit totals.
- **Where the credit money shows up**: the **Parts Orders Payments** tab has
  a **Vendor credits** section listing every parts return and core return
  with its credit amount — the money vendors owe you back. It is
  informational: credits do not reduce the Payments Total and never push a
  vendor's balance negative (vendor balance only counts unpaid orders).

## Pricing scales (margin / markup)

Settings → Parts & Pricing, card **"Margin / Markup Rules"**:

- Mode **Margin** (price = cost / (1 − %); % must be < 100) or **Markup**
  (price = cost × (1 + %)).
- Rules by cost ranges: From / To / % ("Leave To empty to represent ∞").
  Example one-rule scale: from 0, To empty, 20 → 20% on everything.
- Several **named scales** can exist; one is the shop **default**
  ("Set as default"). The default scale cannot be deleted, nor the last one.
- A specific scale can be assigned to a customer (**Pricing Scale** on the
  customer) — that's how one customer gets special parts pricing. The
  customer's **"Override part selling price"** switch makes the scale apply
  even to parts with a fixed selling price.
- The seeded "Default" scale is markup-mode with tiers from 500% (parts
  under $1) down to 15% (over $5000).

Sales tax also lives on this settings page: automatic by shop ZIP, or a
**Custom Rate (Override)** with a "Reset to API" option.
