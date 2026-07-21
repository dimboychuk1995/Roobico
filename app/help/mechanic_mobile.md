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
  ("From preset"). Save hint: **"Saved work orders go to the manager for
  review."**
- Mechanics' saves are always **In Progress** — a mechanic can never mark a
  WO complete, open or paid, and paid WOs are locked entirely.
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
- Starting a timer sets the WO to In Progress. Tracked time appears for the
  office in the WO timeline and in Reports → Mechanic Hours ("Tracked
  Hours" vs billed hours).

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
  web mechanic mode incl. job timers. **AI scan** — photograph a paper work
  order and AI fills jobs/parts; "AI edit" polishes the issue description.
- **Customers**: list/search, customer and unit pages, forms.
- **Parts**: Stock (with cross references ⇆), Orders (parts orders incl. AI
  invoice scan), Counts (stocktakes).
- **More**: global Search, Vendors, Calendar, Reports (all standard reports
  with period chips), Settings — **Active shop** switching and account info.

Web-only for now: Import/Export, PDF design, roles editing, integrations,
billing. Mobile-first: camera flows (AI scan of work orders and vendor
invoices, photo attachments).
