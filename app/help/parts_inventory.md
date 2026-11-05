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
  interchangeable part numbers. In stock shows the quantity in the part's
  unit of measure ("12 gal", "3 qt"; pieces show just the number) and, for
  parts with a minimum stock, a muted "min 5" line plus a **Below min** /
  **Near min** badge — see "Minimum stock" below. Location shows a
  per-location breakdown when stock is split ("Shelf A × 3, Shelf B × 1");
  stock without a location shows "Unassigned".
- Row actions: **Cross refs**, **History**, **Locations** (tracked parts
  only), **Edit** (needs "Edit parts"), **Deactivate** (needs "Delete
  parts"). "Add Part" needs "Create parts", "Order" needs "Create parts
  orders", "New Stocktake" and the transfer form in Locations need "Adjust
  stock".
- The **Avg cost** and **Last price** columns, the inventory-cost totals
  under the table and the Core Cost / Credit columns in Cores are shown only
  with the permission **"View part costs (avg cost / cost columns)"**;
  without it the server also strips costs from part search, history and
  cross references, and the part form has no Average cost field. Minimum
  stock badges and the Stock level filter do not need it.
- Status filter next to the search box: **Active parts** (default),
  **Inactive parts**, **All parts**. Inactive parts carry a grey
  **Inactive** badge and have only the **History** button.
- **Stock level** filter (the second dropdown): **All** (default), **Below
  minimum**, **Below or near minimum**. It keeps only parts that require a
  minimum stock and are under it (or under / close to it); the status
  filter and the search still apply, and the choice survives paging.
  **Reset** clears search, status and stock level together.
- Footer: **"Total inventory cost"** (Σ stock × avg cost) and **"Total core
  cost"** — they count the parts shown by the current filter (inactive parts
  are not in the default totals).

### Minimum stock (reorder points): "Below min" and "Near min"

- A part can carry a **minimum stock**: tick **"This part requires minimum
  stock"** in the part form and type the quantity (see "Part fields"). The
  list then compares the total on hand (all locations together) with that
  minimum **every time the page is opened** — nothing is stored and no
  email or push is sent; the list, its filter and the mobile app are where
  you see it.
- **Below min** (red badge; the row is tinted red with a red bar on the
  left): on hand is **less than** the minimum — time to order.
- **Near min** (amber badge; amber-tinted row): on hand is **at the minimum
  or up to 20 % above it** — rounded up, and always at least 1 unit above.
  Example: minimum 5 → 4 or less is Below min, 5–6 is Near min, 7 and more
  is fine. Minimum 10 → Near min is 10–12; minimum 25 → 25–30; minimum 1 →
  1–2. A minimum of 0 is allowed: such a part is never "below" (unless its
  stock went negative) and shows Near min at 0–1 on hand.
- Every part with a minimum shows a muted **"min 5"** line under its
  quantity (with the unit: "min 5 gal"), so the threshold is visible even
  while stock is fine. Hover the badge or that line for the exact numbers:
  "In stock 3 · minimum 5 (near = up to 6)".
- **Summary line.** When the current selection (status filter + search)
  contains such parts, an amber bar appears above the table: "**3 below
  minimum** · **2 near minimum**". The second number counts only the parts
  inside the near window, not the ones already below. Both numbers are
  links: the first opens the **Below minimum** filter, the second **Below
  or near minimum** — which lists both groups, so it shows 3 + 2 = 5 rows.
  No bar means nothing is below or near its minimum.
- **Filtering.** Stock level **Below minimum** shows only parts under their
  minimum; **Below or near minimum** shows everything that is highlighted.
  Parts with **0 on hand** are included: the web list shows zero-stock
  parts anyway, and the mobile app's Stock list — which normally hides
  parts with nothing on hand — shows them as soon as a Below min / Near min
  chip is selected. An empty result says "No parts below minimum" / "No
  parts below or near minimum".
- Parts with **"Do not track inventory"** are never flagged (the minimum is
  switched off for them), nor are parts without a minimum. Inactive parts
  keep their minimum and unit; with the status filter on **Inactive parts**
  / **All parts** they are marked the same way.
- Badges, highlighting and the filter need only **"View parts"** — users
  without "View part costs" see them too. Setting or changing the minimum
  is part of the part form ("Create parts" / "Edit parts") and is recorded
  in the Activity Journal as "Requires minimum stock" / "Minimum stock".
- On the phone the Parts tab has the same badges plus **All / Below min /
  Near min** chips — see Mechanic Mode & Mobile App → "Parts on the phone".

### Inactive parts (and how a part becomes active again)

- **Deactivate** hides the part everywhere it could be picked: work order
  part search, the mobile app, stocktakes and inventory totals. Nothing is
  deleted — history, stock quantity and locations stay on the part. If it
  still has stock, the confirmation says so; the quantity simply stays
  hidden until the part is active again.
- There is **no Restore button**. A part becomes active again **only through
  a parts order**: in the order dialog the part search also finds inactive
  parts (marked **Inactive**); add the part to the order and click
  **Create order** — the part is active again at that moment. The same
  happens when an inactive part is in an order you **Receive**, in an order
  you save (Update), or in an email order you **Confirm**. The AI Order
  Reader matches inactive parts too, so scanning an invoice with that part
  and adding the line reactivates it.
- Creating a new part with the number of an inactive one is blocked with
  "already exists but is inactive. Add it to a parts order…" — order it
  instead of creating a copy, so the stock history continues.
- The part's edit data keeps **reactivated_at / reactivated_by** and the
  order number that brought it back (visible to admins in the database;
  not shown in the UI).
- Deactivating does not clear the part's minimum stock or unit of measure
  — both are back as they were when the part is reactivated.

## Part fields

The same form opens when you create a part from an order — **Create & Add**
on an unmatched line of the AI Order Reader or of an order created from
email, on the Parts page and inside a work order alike: every field below is
available there too, prefilled with the line's part number, description and
price and with the order's vendor.

- **Part number** (the only required field), **Reference** (alternative/OEM
  number), **Description**. Part numbers are unique within the shop
  (case-insensitive): creating or renaming a part to an existing number is
  blocked with "already exists". If the message says the existing part is
  inactive, add that part to a parts order instead of creating a copy — the
  order makes it active again and its stock history continues (see
  "Inactive parts" above).
- **In stock** — starting quantity; afterwards stock changes only through
  orders, work orders, transfers, stocktakes and manual adjustments.
  Editing this number on an existing part is a manual override and needs
  the permission **"Edit on-hand quantity directly"** (by default only
  Owner, General manager, Manager). Without it the field is locked in the
  edit form and the server rejects a changed quantity — receive a parts
  order or run a stocktake instead.
- **Average cost** — the weighted average cost of what is in stock,
  recalculated on every receiving:
  (old avg × old qty + received price × received qty) / total qty.
  Example: 2 in stock at $100, you receive 2 at $200 → 4 in stock at $150.
  Two rules keep it sane: if stock is zero or negative (a work order used
  parts before they were received) or the average is 0 (cost unknown), the
  received price simply becomes the average; and **Unreceive** / deleting a
  received order takes that batch back out of the average, so re-receiving
  does not count it twice. Work orders never change the average. You can
  still type it by hand in the part form.
- **Last price** — the price on the **latest parts order** with this part
  (by order date; returns and unconfirmed email orders don't count). Set
  automatically when an order is created, saved, deleted or an email order
  is confirmed; shown in the Parts list ("Last price" column, hover for the
  order #) and read-only in the part form. It is the **default price when
  you add the part to a new parts order** (average cost is used only while
  the part has never been ordered). "-" means no orders yet.
- **Unit of measure** — how the part is counted: **Each** (default),
  Gallon, Quart, Pint, Fluid ounce, Liter, Milliliter, Pound, Ounce,
  Kilogram, Gram, Foot, Inch, Meter, Box, Case, Pack, Pair, Set, Roll, Can,
  Bottle, Tube, Drum, Pail, Kit. It changes labels only: quantities stay
  whole numbers (half a gallon cannot be entered — make such a part a
  Quart), prices and the average cost are per unit (per gallon, per quart)
  and the pricing scale works exactly as for pieces. Where it shows: the
  Parts list ("12 gal", "min 5 gal"), the mobile Parts tab and part screen
  ("Unit of measure" row), the part search on a work order ("Stock: 12
  gal"), a small "gal" / "qt" under the Count box of the WO part line, and
  the Qty column of the printed documents — the WO / estimate PDF, the
  emailed work order, the authorization email and page and the customer
  portal print "2 gal" / "3 qt" (pieces print the bare number, no "ea").
  The unit is copied onto the WO line when the part is picked (non-catalog
  "one-time" parts are Each), so changing it on the part later does not
  rewrite old work orders. Import / Export column **Unit**: the export
  writes the short form (gal, qt, ea); the import accepts the name, the
  short form or a plural in any case ("Gallon", "gal", "gallons"), empty
  means Each and an unknown value is imported as Each.
- **"This part requires minimum stock"** → **Minimum stock** — a whole
  number, 0 or more, required while the box is ticked ("Minimum stock is
  required when the part requires minimum stock" / "Minimum stock must be
  a whole number (0 or more)"). It is the reorder point described in
  "Minimum stock" above: below it the part is red, within 20 % above it
  (at least 1) amber. The controls disappear while **"Do not track
  inventory"** is on — a part without stock tracking cannot have a
  minimum, and turning tracking off switches an existing minimum off (set
  it again after you turn tracking back on). Unticking the box clears the
  number. Import / Export column **Min Stock**: a number means the part
  requires that minimum, empty means no minimum; the export writes the
  number only for parts that have one.
- **"This part has selling price"** → fixed **Selling price** that overrides
  the pricing scale (unless the customer has "Override part selling price").
- **"Do not track inventory for this part"** — part stays usable on WOs and
  orders, but stock is never adjusted (turning this on zeroes stock,
  disables core charge and switches the minimum stock off).
- **"This part has Core charge"** → **Core cost** (refundable deposit per
  unit).
- **"Add Misc charge for this part"** → a list of misc charges (description,
  price, taxable) automatically added when the part is put on a work order.
  The description is required: a row with a price but no description is
  refused ("Misc charge description is required."), a row where nothing
  was entered is simply ignored. The same rule applies when creating the
  part from a parts order (Create & Add) and when editing it.
- **Vendor** — follows the purchases: every time a parts order with this
  part is created, saved, confirmed (email order) or the latest one is
  deleted, the part's vendor becomes the vendor of its **latest** parts
  order, together with Last price. There is no separate "last vendor" field
  — the Vendor you see is where you bought it last. You can still pick a
  vendor by hand for a part that has never been ordered (or until the next
  order overrides it).
- Category, **Location** (the part's primary/default storage location).
  Attachments available in edit mode.

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
  adjustment — needs "Edit on-hand quantity directly", otherwise the Set
  control is hidden) and a **"Transfer between locations"** section
  (From / To / Qty) — transfers don't change the total and need only
  "Adjust part stock".
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
