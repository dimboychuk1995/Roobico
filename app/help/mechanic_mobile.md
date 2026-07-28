# Mechanic mode and the Mobile app

## Mechanic mode (web)

Mechanic mode is **not a switch** — it turns on automatically for any user
whose role lacks the "View part costs inside WO" permission (the default
**Mechanic** and **Senior mechanic** roles). Such users are redirected from
the normal Work Orders pages to a simplified no-price interface at
/mechanic/work_orders.

What a mechanic sees and can do:

- **Job list**: search, chips All / In Progress / Open, section **"In work
  now"** with everyone's running timers, "My timer running" marker, "+"
  button for a new WO.
- **No money anywhere**: no prices, costs, totals, balances — stripped
  server-side, even for managers who open the mechanic pages.
- **Create/edit WOs**: pick customer and unit (can create a new unit:
  Unit number, VIN, Year, Make, Model, Type, Mileage), add jobs
  ("What was done..."), add parts by search (quantities only; a non-catalog
  entry becomes "one-time part — manager fills the price"), apply presets
  ("From preset").
- Mechanics' saves are always **In Progress** — a mechanic can never mark a
  WO complete, open or paid, and paid WOs are locked entirely. On mobile
  there is no separate "view" screen or Save button for mechanics: opening a
  WO always lands in the **editable view**, and every change (description,
  parts, mileage…) **autosaves as In Progress** ~1 second after the last
  edit ("Changes are saved automatically"). The only explicit button is
  **"Done — ready for review"**: it does NOT change the WO status — it shows
  managers a green **"Mechanic done"** badge (WO list web + mobile, WO
  details) meaning the mechanic finished and the job awaits review. Any new
  change or a restarted job timer clears the Done badge.
- The mechanic form has a **Unit mileage** field — saving (incl. autosave)
  records the mileage on the unit itself and on the WO.
- Prices/hours/rates that the office already entered are preserved when a
  mechanic edits; mechanics cannot delete existing jobs (manager-only).

### Job timers

Every saved job shows **"Tracked: H:MM:SS"** and a **"Start job"** /
**"Stop job"** button; a sticky bottom bar shows the running timer with a
Stop button. Rules:

- One running timer per person — starting another job auto-stops the
  previous one. There is no pause; stop and start again (time accumulates).
- Several mechanics can run timers on the same job simultaneously (the row
  shows "<names> working").
- Starting a timer immediately sets the WO to In Progress (no save needed)
  and clears the "Mechanic done" badge if it was set. Tracked time appears
  for the office in the WO timeline and in Reports → Mechanic Hours
  ("Tracked Hours" vs billed hours).
- Managers see who is working right now on the Work Orders list: a green
  "● <names>" line under the status badge (web and mobile).

### Mechanic extras in the mobile WO editable view

- **Job photos**: every job card has its own attachments block (camera or
  gallery) — files are tied to that specific job; the WO-level Attachments
  block is separate. Mechanics can view and upload, deleting needs the
  "Delete attachments" permission.
- **Send for approval**: a mechanic can email the customer an
  Approve/Decline request for a single job ("Send job for approval" on the
  job card) or for the whole WO ("Send work order for approval" at the
  bottom). Same customer-authorization flow managers use from the web.
- Managers reviewing a WO see **"Tracked"** per job — the summed clocked
  time of ALL mechanics who worked on that job (with a per-person breakdown
  when there was more than one) — on the mobile WO details and on the web
  WO page.

## Mobile app

Same login as the web (email + password). Bottom tabs: **Dashboard**,
**Work Orders**, **Customers**, **Parts**, **More** — tabs and rows hide
according to the user's permissions (a mechanic sees only Work Orders +
More).

- **Dashboard**: outstanding balance, revenue/paid/unpaid, WO count, labor,
  parts, parts-orders stats per period (Today/Week/Month/Year).
- **Work Orders**: list + Payments + Estimates segments (money segments
  hidden for mechanics), create/edit WOs, record payments, send for
  authorization, email PDF, delete (managers). Mechanic view mirrors the
  web mechanic mode incl. job timers, plus per-job photos and per-job /
  whole-WO "Send for approval". **AI scan** (managers only — hidden for
  mechanics) — photograph a paper work order and AI fills jobs/parts;
  "AI edit" polishes the issue description.
- A floating **hide-keyboard button** appears above the keyboard on every
  screen while typing.
- **Customers**: list/search, customer and unit pages, forms.
- **Parts**: Stock (with cross references ⇆), Orders (parts orders incl. AI
  invoice scan), Counts (stocktakes).
- **More**: global Search, Vendors, Calendar, Reports (all standard reports
  with period chips), Settings — **Active shop** switching and account info.

Web-only for now: Import/Export, PDF design, roles editing, integrations,
billing. Mobile-first: camera flows (AI scan of work orders and vendor
invoices, photo attachments).
