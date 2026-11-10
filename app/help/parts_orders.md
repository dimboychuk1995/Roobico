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
  Search box searches every field including contacts. The website is a short
  clickable link (e.g. `fleetpride.com/login`) that opens in a new tab.
- **Add Vendor** button → form: Name (required), Website, Address
  (autocomplete), Notes, and a **Contacts** block — several contacts, one is
  always marked **"Main contact"** and used by default.
- **Website — no need to type https://.** Type it the way you say it:
  `fleetpride.com`, `www.napaonline.com/login`, `portal.vendor.com:8443`.
  Roobico stores a working link (`https://fleetpride.com`) and shows it
  without the `https://`. Only real web addresses are accepted — text like
  "N/A", an address with spaces or a `javascript:` / `ftp:` link is refused
  with "Enter a valid website, e.g. fleetpride.com". Leaving it empty is
  fine. Websites saved before this rule (without https) already work as
  links. The same rule applies when a vendor is created from the AI Order
  Reader, from an email order, from the mobile app or by import.
- **Delete = deactivate**, never a hard delete: the vendor is hidden from
  pickers but history stays; **Restore** brings it back. Inactive vendors
  cannot be selected on new orders or parts.
- Clicking a vendor name opens **Vendor Orders** — their orders with a date
  filter and totals: Orders, Total Amount, Paid, Unpaid, Received,
  Not Received. There is no separate vendor page — this popup is it.

### Vendor portal logins & passwords

Most parts vendors have a website where the shop orders parts, checks
prices or downloads invoices. Roobico keeps the **login and password for
that website** on the vendor, so the whole team can find them in one place
instead of a sticky note.

- **Who can see them:** only users whose role (or personal permissions)
  has **"View vendor portal logins & passwords"** (Settings → Roles →
  Vendors). By default: Owner, General manager, Manager and Parts manager.
  Senior mechanics, mechanics and viewers do NOT see them unless the owner
  ticks the permission for their role or for one person. Without the
  permission the Portal login section of the vendor form and the Portal
  logins tab are simply not there, and neither the login nor anything about
  the password is sent to that user's browser or phone. The Activity
  Journal (which a Viewer can open) never shows them either — only that a
  login or password was set, changed or removed.
- **Saving:** Add Vendor or Edit → **Portal login** section → Login and
  Password. **Show** next to the password field shows what you just typed.
  Editing needs "Edit vendors" (creating — "Create vendors") plus the
  portal-logins permission.
  - The saved password is never put back into the form. The field says
    "saved — leave blank to keep": leave it empty to keep the password,
    type a new one to replace it.
  - **Remove saved password** (checkbox, only when one is saved) deletes it.
    Clearing the Login field and saving removes the login.
  - Up to 256 characters each.
- **Encrypted:** the password is stored encrypted (the same server-side
  encryption Roobico uses for integration keys). It is never written to logs,
  exports, the Activity Journal or the AI assistant (the assistant can't
  read portal logins at all and points you to the Portal logins tab). If the server's
  encryption is not set up, saving or showing a password fails with
  "Password encryption is not configured on the server — contact support"
  (everything else about vendors keeps working).
- **Portal logins view (the quick look-up):** Vendors page → tab **Portal
  logins** (next to "Vendors"). A compact table of active vendors: Vendor,
  Website (link), Login with a **copy** button, Password as `••••••••` with
  **Show** and **copy** buttons. Search finds a vendor by name, website or
  login. Rows without a saved login/password show "—". Users who can see
  logins but cannot create or edit vendors land on this view right away;
  editors get an **Edit** button in each row.
  - **Show** reveals the password in the row; it **hides again by itself
    after 30 seconds** (or click Hide). **Copy** puts it on the clipboard
    without showing it ("Password copied").
  - **Every time a password is fetched from the server it is recorded** in
    Reports → Activity Journal as "Viewed portal password of <vendor>"
    (Action **Viewed**; who, when, from where — never the password itself):
    each **Show**, and each **Copy** while the password is hidden. Copying a
    password that is already shown on screen adds no new entry, and copying
    the login is not recorded. Saving shows up there too: "Portal login" and
    "Portal password" as set / changed / removed — never the values.
  - Too many password requests in a short time (60 in 10 minutes per user)
    are blocked for a few minutes as a safety measure.
- **Mobile app:** vendor screen → **Portal login** card (same permission):
  Website (tap to open), Login (long-press to copy), Password hidden —
  **Show** reveals it (long-press to copy), it hides again after 30 seconds
  or when you leave the screen. Each Show is recorded in the Activity Journal
  the same way as on the web. Vendor edit on the phone has Portal login and
  Portal password fields, a "Show password" switch, a "Remove saved password"
  switch and the same "leave blank to keep" rule; a new vendor gets the same
  fields when you can also edit vendors (the login is saved right after the
  vendor is created). (Needs the app update that ships with this feature.)
- Import / Export never carries portal logins or passwords: the export file
  has no such columns, and import ignores them.

## Creating a parts order

Button **"Order"** on the Parts page. One order = one vendor.

1. Pick the **Vendor** (searchable; only active vendors).
2. **Order Date** defaults to today.
3. **Find part** — type 2+ characters of part number or description, click a
   result to add a line. Lines have Part #, Description, In Stock, **Qty**,
   **Price** and **Total** (Qty × Price, plus the core charge when its toggle
   is on; recalculated as you type). The price defaults to the part's
   **Last price** — what you paid on the latest parts order with it (the
   search results show "Last $X · Avg $Y"); only a part that has never been
   ordered defaults to its average cost. Adding the same part again
   increases Qty.
   The search here also finds **inactive** parts (grey **Inactive** badge in
   the results and on the line): adding one and creating the order makes
   the part active again — this is the only way to reactivate a part.
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
  a **Total** column = Qty × Price, "Add" / "Add All Matched"). An inactive
  part is matched too (badge **Inactive**) and becomes active when the order
  is created;
- **New parts (not in database)** — lines with a **Create & Add** button.
  It opens the same full **Create new part** form as **Add Part** on the
  Parts page — every field is there: reference, unit of measure, minimum
  stock, selling price, "do not track inventory", core charge, misc
  charges, vendor / category / location. The form is prefilled with the
  line's part number, description and price (as the average cost) and with
  the order's vendor; after **Create** the part is added to the order with
  the line's quantity and price. **Create & Add All** walks through the new
  lines one by one in the same form (Cancel skips a line and moves on).

**Price = unit price, never the line amount.** The reader extracts two
numbers per line — the net unit price and the printed line amount (Ext /
Amount / Total column) — and checks that price × qty equals the line amount.
If the AI put the line amount into the price (qty 3, amount $150 → "price"
$150), the price is corrected to the amount ÷ qty ($50) before you see it;
such a price shows a tooltip on hover ("derived from the printed line
amount") so you know to glance at it. It uses the net (discounted) unit
price and the Shipped quantity, and ignores tax/freight/signature lines.
Always review the lines; the order is created only when you click **Create
order**.

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
   one. Only the first six pages of a PDF are read. The file type is
   recognised by the file itself, not by how the sender labelled it — a
   `.PDF` that a dealer's system sends as a generic binary attachment is
   still read as a PDF. What is **not** read: other file types (Excel, Word,
   ZIP…) and files over 6 MB. Such files are listed in the inbox row and in
   the email details as **"Not read: <file> (reason)"**, so an "ignored — no
   attachments" verdict always shows what arrived and why it was skipped.
2. **Then the text.** Only when there is no decisive attachment does AI look
   at the email itself: is it an order document with lines? If yes, vendor,
   vendor order/invoice number, date and the lines (part number,
   description, qty, unit price) are read from the text. Quotes,
   shipping/tracking notices without lines, statements, promos, receipts for
   fuel or software and anything else are marked **Ignored**. They are not
   deleted: the whole history is visible in the integration window, and an
   ignored email can be turned into an order with one click if AI was wrong.
3. **Vendor** is matched by name first — exact, or one name inside the
   other ("Hawk Ford" in the list vs "Hawk Ford of St. Charles Pro Elite
   Commercial Vehicle Center" on the invoice count as the same vendor) —
   then by the sender address learned from earlier orders (only when that
   address belongs to a single vendor: dealer portals like RepairLink send
   for many dealers from one address), and finally AI compares the invoice
   name against the whole vendor list. If nobody matches, the vendor is
   **created automatically** with the contacts from the email — the order
   dialog says so. Pick the right vendor in the dialog and save: the
   auto-created one is deleted and the sender is remembered for the vendor
   you chose, so the next email from it matches straight away.
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
  **Create & Add**. It opens the same full **Create new part** form as
  **Add Part** on the Parts page (reference, unit of measure, minimum
  stock, selling price, do-not-track, core charge, misc charges, vendor /
  category / location), prefilled with the line's part number, description
  and price and with the order's vendor; after **Create** the part is put
  on the order with the line's quantity and price. Lines you do not add
  (freight, tax, mistakes) are dropped on confirm.

Check quantities and prices in the main items table, then press
**Confirm order**. Changes are saved and the flag is removed — from now on it
is a normal order: receive it, pay it, return items.

**Reject** (in the dialog or in the row) removes the order when the email was
not an order for this shop. The email is kept in the inbox history as
*Rejected*; a vendor that was auto-created only for this email is
deleted again.

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
- check the **Received** column. Every line of the order is listed — also
  parts that are not tracked and parts that are currently inactive (an
  **Inactive** badge; receiving makes them active again) — with the
  ordered quantity pre-filled. If the vendor shipped fewer (or more) than
  ordered, type what actually arrived (**partial receipt**): the order's
  quantities are updated to match what was received, the original number
  stays visible on the line as "ordered N", and **0 removes the line**
  from the order. The order total therefore always equals the vendor bill,
  and stock gets exactly what came. If a payment already recorded on the
  order is larger than the new total, receiving is blocked ("Paid amount
  is greater than the received order total") — delete or correct that
  payment on the **Parts Orders Payments** tab first. The mobile app has
  only the Vendor Bill field: it receives every line as ordered, partial
  receipts are web-only.
- if the shop uses locations, choose **where to put** each received part
  (the **Location** column); default is the part's location, otherwise
  "Unassigned". A location chosen here becomes the part's default if it
  had none. Untracked parts have no location (shown as "—").
- Changed something in the order dialog first (quantities, prices, added
  or removed lines)? Those edits are **saved automatically** when you
  press **Receive Order**, and the receiving dialog opens with the saved
  quantities. If the save is refused (for example a payment exceeds the
  new total), the error shows in the dialog and nothing is received.
- Stock increases by the received quantities; the part's **average cost** is
  recalculated as a weighted average of old stock and the received price.
- Parts marked "do not track inventory" get no stock movement — only their
  average cost is set to the received price.

**Unreceive Order** rolls the received quantities back out of the same
locations and takes that batch back out of the average cost. It **always
works** — even when the parts were already used on work orders: stock
then goes **below zero** (a warning lists those parts, e.g. "BRK-100
(−3)"), and it corrects itself when the order is received again or the
stock is adjusted. Not possible while the order has active returns. A
partially received order keeps its received quantities after Unreceive
(the "ordered N" note is remembered for the next receiving); edit the
order if the numbers should change.

Received orders cannot be edited — Unreceive first. **Deleting** a
received order rolls its stock back the same way as Unreceive (stock can
go below zero, with the same warning) and deactivates its payments; it
never refuses because of missing stock. Like Unreceive, it is blocked
only while the order has active returns — delete the returns first.

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

Parts orders have their own keys in Settings → Roles: **View parts orders**
(the Orders / Payments tabs, order prices, vendor balances and a vendor's
orders list), **Create** (the Order button, AI invoice parser), **Edit**
(Edit, Confirm / Reject of e-mailed orders), **Delete**, **Receive**
(receive, un-receive, returns to vendor) and **Manage payments** (Pay,
deleting payments). Buttons that need a key you don't have are not
rendered — an order you may only view opens with a "View" button instead
of "Edit". Vendors have their own view / create / edit / deactivate
permissions; the vendor Balance column needs "View parts orders".

**"View vendor portal logins & passwords"** (Vendors group) controls the
vendors' website logins: the Portal logins tab, the Portal login section of
the vendor form, Show / Copy of passwords and the Portal login card in the
mobile app. It is on by default for Owner, General manager, Manager and
Parts manager only. Saving a login still needs Create / Edit vendors on top
of it. Without this permission, login / password fields in a save request
are ignored (the rest of the vendor is saved as usual). A user who
has this permission but cannot create or edit vendors (for example a
counter person you only want to look passwords up) opens Vendors straight on
the Portal logins tab.
