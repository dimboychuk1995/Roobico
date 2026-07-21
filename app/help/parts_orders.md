# Parts Orders (buying from vendors) and Vendors

Parts orders live on the **Parts** page under the **"Parts Orders"** tab.
Related tabs on the same page: "Parts Orders Payments", "Cores",
"Cores Returns", "Stocktakes". Vendors have their own **Vendors** section in
the sidebar.

## Vendors

- Vendors list: columns Name, Phone, Email, Website, Primary contact,
  Address, **Balance** (unpaid total across the vendor's orders), Status.
  Search box searches every field including contacts.
- **Add Vendor** button → form: Name (required), Website, Address
  (autocomplete), Notes, and a **Contacts** block — several contacts, one is
  always marked **"Main contact"** and used by default.
- **Delete = deactivate**, never a hard delete: the vendor is hidden from
  pickers but history stays; **Restore** brings it back. Inactive vendors
  cannot be selected on new orders or parts.
- Clicking a vendor name opens **Vendor Orders** — their orders with a date
  filter and totals: Orders, Total Amount, Paid, Unpaid, Received,
  Not Received. There is no separate vendor page — this popup is it.

## Creating a parts order

Button **"Order"** on the Parts page. One order = one vendor.

1. Pick the **Vendor** (searchable; only active vendors).
2. **Order Date** defaults to today.
3. **Find part** — type 2+ characters of part number or description, click a
   result to add a line. Lines have Part #, Description, In Stock, **Qty**,
   **Price** (defaults to the part's average cost). Adding the same part again
   increases Qty.
4. **Core charge**: if the part has a core charge, a toggle **"+ Core $X"**
   appears (on by default) — it adds qty × core charge to the total.
5. **Non inventory amount** section — spending that is not parts and does not
   touch stock: types **shop supply / tools / utilities / payment to another
   service**, plus description and amount. An order may consist of
   non-inventory lines only.
6. **Create order**. The order gets an internal sequential **Order #**
   (starts at 1000) and status **ordered**, payment status **Unpaid**.

The vendor's own invoice number is NOT entered here — it is entered as
**"Vendor Bill"** when receiving the order.

## AI Order Reader (scan a vendor invoice)

Button **"AI Order Reader"** in the order form. Upload the vendor's invoice —
PDF or photo (jpg/png/gif/webp/bmp/tiff, up to 16 MB). AI reads it and shows a
review panel:

- vendor matched by name, or a **Create Vendor** button prefilled from the
  invoice;
- **Matched parts** — lines recognized as existing parts (editable Qty/Price,
  "Add" / "Add All Matched");
- **New parts (not in database)** — lines with a **Create & Add** button that
  creates the part first.

It uses the net (discounted) unit price and the Shipped quantity, and ignores
tax/freight/signature lines. Always review the lines; the order is created
only when you click **Create order**.

## Receiving

**Receive Order** (or click the yellow "ordered" status in the list):

- enter **"Vendor Bill"** — the vendor's invoice number (optional);
- if the shop uses locations, choose **where to put** each received part
  ("Put received parts into:"); default is the part's location, otherwise
  "Unassigned". A location chosen here becomes the part's default if it had
  none.
- Stock increases by the received quantities; the part's **average cost** is
  recalculated as a weighted average of old stock and the received price.
- Parts marked "do not track inventory" get no stock movement — only their
  average cost is set to the received price.

**Unreceive Order** rolls the received quantities back out of the same
locations (average cost is NOT reverted). Not possible while the order has
active returns.

Received orders are frozen: they cannot be edited; delete requires rolling
back first.

## Paying vendors

**Pay** button on the order → Record parts order payment: Amount, **Method**
(Cash / Card / Bank transfer / Check / Other), Payment Date, Notes,
attachments. Partial payments are fine — the order becomes **Partially
Paid**; overpaying above the order total is blocked. On received orders the
payment date is always "today".

All payments are listed on the **"Parts Orders Payments"** tab (with totals);
a payment can be deleted there, the order balance re-syncs automatically.

Who the shop owes: **Reports → Vendor Balances** (per vendor: Orders, Total,
Paid, Outstanding). Spending breakdown by type: **Reports → Parts Orders
Summary** (Parts / Cores / Shop Supply / Tools / Utilities / Pmt to Svc).

## Returns to vendor

**Return** button on an order (available only when the order is received or
fully paid). Enter quantities per line (capped by what's still returnable) and
an optional note (reason, RMA number).

- A separate return document **"R-<number>"** is created with a **Credit**
  badge — it is a vendor credit, not a debt; payments cannot be applied to it.
- If the source order was received, the returned parts are deducted from
  stock (from the same locations); deleting the return puts them back.
- Returns subtract from purchase totals in the orders tab and reports.

## Access

Working with parts orders requires the parts permissions (view/edit parts);
vendors have their own view/edit/deactivate permissions.
