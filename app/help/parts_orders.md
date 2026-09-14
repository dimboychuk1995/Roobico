# Parts Orders (buying from vendors) and Vendors

Parts orders live on the **Parts** page under the **"Parts Orders"** tab.
Related tabs on the same page: "Parts Orders Payments", "Cores",
"Cores Returns", "Stocktakes". Vendors have their own **Vendors** section in
the sidebar.

Three ways an order appears: typed in by hand (**Order** button), scanned
from a vendor invoice (**AI Order Reader**), or created automatically by AI
from the location's **email inbox** (orders marked **Not confirmed** — see
"Orders from email" below).

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

## Orders from email (AI inbox, "Not confirmed" orders)

Every location can have its **own private mailbox** that only the AI reads.
Set it up in **Settings → Integrations → Email orders inbox** (see the
Settings help): the location gets an address like
`orders-a1b2c3d4e5f6@roobico.com`. Forward the vendor mail there — either
the whole mailbox (auto-forwarding) or just vendor senders (a filter), or
give the address to vendors as a CC / extra notification email.

What happens with every email that arrives:

1. **PDFs first.** Every PDF or image attached to the email is read by the
   same engine as the AI Order Reader — the body of such emails usually
   says only "see attached", so the attachment is what counts. If a PDF is
   an order document (order confirmation, invoice, packing slip) with
   itemized lines, the email IS an order and the lines are taken **from the
   PDF**, never from the text. If the PDF is a quote, a statement or a
   payment receipt, the email is NOT an order, even if the text sounds like
   one. Only the first six pages of a PDF are read.
2. **Then the text.** Only when there is no decisive attachment does AI look
   at the email itself: is it an order document with lines? If yes, vendor,
   vendor order/invoice number, date and the lines (part number,
   description, qty, unit price) are read from the text. Quotes,
   shipping/tracking notices without lines, statements, promos, receipts for
   fuel or software and anything else are marked **Ignored**. They are not
   deleted: the whole history is visible in the integration window, and an
   ignored email can be turned into an order with one click if AI was wrong.
3. **Vendor** is matched by the sender address (learned from previous
   confirmed orders), then by name. If nobody matches, the vendor is
   **created automatically** with the contacts from the email — check it
   when confirming (the order dialog says so).
4. **Parts** are matched by part number (separators like spaces/dashes are
   ignored). Lines that do not exist in the catalog are kept on the order as
   **unmatched lines** for you to resolve.
5. **Duplicates**: if the same vendor already has an order with this vendor
   order/invoice number (in "Vendor Bill"), or an order typed in during the
   last three weeks with the same parts and quantities, the email is
   **linked** to that order (its attachments are added there) instead of
   creating a second one.
6. The parts order is created with status **ordered**, the vendor number in
   **Vendor Bill**, the email files as attachments, and the flag
   **Not confirmed** (red badge in the Parts Orders list). Office users get
   a push notification.

**Until a person confirms the order it is "on hold"**: it cannot be
received, paid or returned, and it is not counted in vendor balances,
dashboard purchases or the Vendor Balances report. It is a proposal, not a
debt.

### Confirming an order from email

Filter the Parts Orders tab by **Not confirmed (from email)** or click
**Review** in the yellow banner, then click the red **Confirm** button in
the Status column (or **Edit**). The order dialog opens with a yellow
"Created from email" block on top:

- who sent it, the subject, when, the vendor order/invoice number, and a
  warning if the vendor was created automatically;
- **Lines not found in the parts catalog** — the same table as in the AI
  Order Reader: edit part number / description / qty / price and press
  **Create & Add** to create the part and put it on the order. Lines you do
  not add (freight, tax, mistakes) are dropped on confirm.

Check quantities and prices in the main items table, then press
**Confirm order**. Changes are saved and the flag is removed — from now on it
is a normal order: receive it, pay it, return items.

**Reject** (in the dialog or in the row) removes the order when the email was
not an order for this shop. The email is kept in the inbox history as
*Rejected*; a vendor that was auto-created only for this email is
deactivated again.

### Suggest-only mode

In the integration settings you can choose **Suggest only**: AI reads the
email but does not create anything; the inbox history shows **Needs review**
with the vendor and number of lines, and you press **Create order** there.
Use it while you are getting used to the feature, then switch to automatic.

### Where to look when something is off

- **Settings → Integrations → Email orders inbox → Inbox history** — every
  email with its result: *Order created* (link to the order), *Linked to
  order*, *Ignored* (with AI's reason), *Needs review*, *Error* (AI could not
  read it; **Retry** or **Create order** manually). **View** shows the text
  and the lines AI read — forwarding confirmation codes from Gmail / Yahoo /
  iCloud also show up here.
- The list only shows what reached the mailbox. If nothing appears within a
  couple of minutes, the forwarding rule on the mail side is not active
  (Gmail requires confirming the forwarding address first).

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
- **Finding returns**: the status filter on the Parts Orders tab has a
  **Returns** option — it shows only return documents, and the footer totals
  then sum just the returns (as negative amounts). Search and the date
  filter work as usual.
- **Return paperwork**: every return row has a **Files** button — attach the
  vendor's credit invoice / RMA paperwork there (images or PDF). These files
  belong to the return itself and are NOT shown on the original parts
  order — the original keeps only its own attachments.

## Orders linked to a work order

A parts order can be created straight from a work order page (the
**Parts orders** button in the Customer & Unit section) — it opens the very
same order dialog as the Parts page, including the AI Order Reader, core
charges and non-inventory amounts; the only difference is that the order is
linked to that work order. Such orders live in the normal Parts Orders list
like any other order and carry a **WO #** badge linking to the work order;
the work orders table shows matching **PO #** badges on WO rows that have
linked orders. Inside the work order the same orders are shown with
statuses, Open/Receive/Pay actions and — once the WO is accepted (not an
estimate) — a check of which ordered items are actually used on that WO.
See the Work Orders help for details.

## Access

Working with parts orders requires the parts permissions (view/edit parts);
vendors have their own view/edit/deactivate permissions.
