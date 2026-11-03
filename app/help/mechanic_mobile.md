# Mechanic mode and the Mobile app

## Mechanic mode (web)

Mechanic mode is **not a switch** — it turns on automatically for any user
whose role lacks the "View part costs inside WO" permission (the default
**Mechanic** and **Senior mechanic** roles). Such users are redirected from
the normal Work Orders pages to a simplified no-price interface at
/mechanic/work_orders.

What a mechanic sees and can do:

- **Job list**: search, section **"In work now"** with everyone's running
  timers, "My timer running" marker, "+" button for a new WO. The list shows
  **only In Progress work orders** — active work, including WOs marked
  "Done" that await manager review. Completed (open), paid and estimate WOs
  never appear in a mechanic's list (server-side, web and mobile alike), so
  the list is exactly "what is on the shop floor right now". Past work on a
  vehicle is still available through the unit's history.
- **No money anywhere**: no prices, costs, totals, balances — stripped
  server-side, even for managers who open the mechanic pages.
- **Create/edit WOs**: pick customer and unit (can create a new unit:
  Unit number, VIN, Year, Make, Model, Type, Mileage), add jobs
  ("What was done..."), add parts by search (quantities only; a non-catalog
  entry becomes "one-time part — manager fills the price"), apply presets
  ("From preset").
- **One truck in the shop = one work order.** The moment a mechanic picks a
  unit (by hand, from the unit search, or by scanning its VIN) the app
  checks whether that unit already has a work order **In Progress** — a
  colleague is working on it, or it is simply open on the floor. If so, the
  mechanic is taken **into that work order** instead of creating a new one;
  the toast says which WO it is and who is working on it right now ("Work
  order #123 is already in progress for this unit (Ivan working on it
  right now). Opening it…"). Both mechanics then work on the same WO —
  each with their own job timers and their own Done. The server enforces
  the same rule: a mechanic's attempt to create a second WO for a busy
  unit is refused and the existing WO is returned, so older app builds
  cannot create a duplicate either. The rule ignores work orders a manager
  has already **confirmed** (locked for mechanics), completed/paid ones
  and estimates — those leave the unit free for a new WO. Managers are not
  redirected: they create work orders deliberately.
- **Scan VIN** (new WO screen, before picking anything): the **"Scan VIN"**
  button opens the camera. Just point it at the VIN **barcode** (door-jamb
  sticker, dashboard plate — Code 39/128, DataMatrix, QR) — the VIN is
  picked up automatically, no buttons to press (there is a flashlight
  toggle for dark cabs). Other barcodes in the frame are ignored — only a
  valid 17-character VIN triggers. What happens next:
  - the VIN belongs to an existing unit → the customer AND unit are filled
    in automatically and the work order starts right away — unless that
    unit already has a work order In Progress, in which case **that** WO
    opens (see "One truck in the shop = one work order" above);
  - the same VIN is active on **several companies** → the app asks which
    company this job is for;
  - the VIN is **not in the system** → a new unit is created automatically
    under the technical customer **"NEW Customer"** (make/model/year are
    decoded from the VIN via NHTSA) and the WO starts on it. The office
    later moves the WO to the real company with the **Transfer** button on
    the WO page — the unit follows automatically and the office's prices
    (labor rates, part prices, tax) are re-applied for the real company;
    you can keep working on the WO while this happens. "NEW Customer" is
    created per shop on first scan and **cannot be deactivated**.
  The same scanner is available on the **New unit** form (barcode icon next
  to the VIN field) — it fills the VIN and decodes Year/Make/Model/Type.
  If the vehicle has no scannable barcode, type the VIN manually on the
  New unit form as before.
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
- **Several mechanics on one WO — Done is per mechanic.** Everyone who
  worked on the WO (started a job timer on it, or is assigned to one of its
  jobs) has to press Done themselves. Until the last one does, the WO stays
  in the manager's **"In Work"** group, shows no "Mechanic done" badge and
  sends no "finished" push; the WO list shows who has already finished as a
  green **"✓ Name"** next to the working (●) and idle names. In your own
  app the WO shows **"Done"** as soon as *you* pressed it — that is your
  mark, not the whole job. Pressing Done, changing anything, or restarting
  a timer affects only your own mark; a colleague's mark stays. When a
  manager saves the WO as **In Progress**, all marks are cleared — every
  mechanic has to press Done again.
- **Mileage first.** When a mechanic opens a work order (app or web
  mechanic page) — and when picking the unit for a new one — a "Current
  mileage" window appears, prefilled with the last recorded odometer. The
  mechanic must confirm or correct it before anything else can be done:
  the window cannot be dismissed, the only ways out are Confirm or going
  back to the list, and the server refuses any mechanic save without a
  mileage ("Enter the unit's current mileage before saving"). The confirmed
  value is written to the work order (as the mileage at the time of the
  job) and moves the unit's odometer forward; a smaller number is kept on
  the WO only and never rolls the unit back. The **Unit mileage** field on
  the form (and the mileage box in the web header) stays editable to
  correct it later.
- **The work order date follows the mechanic's last real save.** Every
  save a mechanic makes that changes something — the Save button in web
  mechanic mode, and in the app every autosave that changed jobs, parts or
  mileage, or a press of Done — stamps the WO with that day, and that date
  is the invoice date on the PDF and in the lists. A job created on Monday and
  finished on Wednesday is dated Wednesday. Opening a WO without changing
  anything does not move the date; office (manager) saves keep the date as
  it was set.
- Prices/hours/rates that the office already entered are preserved when a
  mechanic edits; mechanics cannot delete existing jobs (manager-only).
- Jobs a mechanic adds without a preset automatically get the shop's
  **"standard" labor rate** (or the first active rate) on the server — so
  the office never sees a $0 labor line; the office can change the rate
  later as usual.
- Once a manager **confirms** the WO (mobile: "Confirm work order" after
  the "Mechanic done" badge), the WO is closed for mechanics — it cannot
  be opened or edited, and a job timer cannot be started on it — until the
  manager cancels the confirmation.
- **Unit history right inside the WO** (web): the **History** button next to
  the unit (on an open WO, and next to the unit picker when creating one)
  opens the unit's past work orders — WO #, date, status, jobs and parts
  with quantities. Money-free like everything else here, and a paid WO
  simply shows as **Completed**. The current WO is not listed. Up to 30
  most recent WOs are shown.

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

Same login as the web (email + password). You stay signed in
permanently — closing or restarting the app does not log you out, and the
session renews itself on every use, so there is no periodic re-login. You
only leave the account by tapping Log out yourself. (Access still ends
immediately when a user is deactivated — session lifetime never overrides
that.) Bottom tabs: **Dashboard**, **Work Orders**, **Calendar**,
**Customers**, **Parts**, **More** — tabs and rows hide according to the
user's permissions (a mechanic sees only Work Orders + Calendar; the gear
in the header opens Settings). Inside the screens
the action buttons follow the same keys as the web: the pencil on a
customer / unit / vendor needs the edit (or "manage units") key, "+ Add
unit", "+ New order", "+ New count", Receive / Pay / Return on a parts
order, Complete / Cancel / counting on a stocktake, Confirm / Email /
Delete / Mark unpaid on a work order and the calendar's "+" all appear only
with the matching permission; the Reports screen lists financial reports
only with "View financial reports". Whatever the app shows, the server
checks each request itself and answers 403 if the key is missing.

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
- **Calendar**: the shop schedule in a Google-Calendar-style layout — see
  "Calendar on the phone" below.
- **Customers**: list/search, customer and unit pages, forms.
- **Parts**: Stock (with cross references ⇆), Orders (parts orders incl. AI
  invoice scan), Counts (stocktakes).
- **More**: global Search, Vendors, Reports (all standard reports with
  period chips), Settings — **Active shop** switching and account info.

### Calendar on the phone

The **Calendar** tab shows the same schedule as the web calendar (needs
"View calendar", which every default role has, mechanics included) and
works like Google Calendar on a phone:

- **Views** — tap the icon at the top-left to switch: **Schedule** (a
  running list of days with events, empty days skipped, "Show earlier /
  Show more" extend the list), **Day** (hourly timeline with a week strip
  above it), **3 Day**, **Week** (the whole week always fits the screen)
  and **Month** (a full-screen grid with coloured event chips; tap a day
  to open it in Day view). The app remembers the last view. Swipe
  left/right in any grid to move to the next/previous day, 3 days, week
  or month. **The calendar is the only screen of the app that rotates**:
  turn the phone sideways for a wider Week / 3 Day grid (the rest of the
  app stays portrait).
- **Jump to a date** — tap the month name in the header to drop down a
  month picker (dots mark days with events); the square "today" icon
  returns to today.
- **What is on the grid** — both kinds of blocks from the web calendar:
  **appointments** (colour = appointment status) and **work orders**
  (colour = WO state: Not started / In progress / Mechanic done /
  Completed / Paid; a ▶ before the title means a timer is running right
  now). A WO with tracked time appears exactly when the mechanic's timer
  was running; a WO nobody has started yet sits in the **WO strip** under
  the day header (and as an outlined chip in Month/Schedule) with "Not
  scheduled" — the phone never invents a time either. Mechanics see "Paid"
  as "Completed" and never see money here.
- **Mechanic lanes** — in Day, 3 Day and Week every day is split into one
  column per mechanic of the shop (same colours and initials as the web
  calendar), empty future days included; an "Unassigned" lane (grey "–")
  appears only when something has no mechanic. The marker row under the
  dates names the lanes. **Tap an empty slot inside a lane and the new
  appointment is already assigned to that mechanic.** In Week the whole
  week always stays on the screen, so lanes appear there only when they
  have room — turn the phone sideways or use an iPad; in a portrait phone
  week each day is one column and the blocks carry the mechanic's
  initials badge instead. In Day / 3 Day the lanes keep a readable width
  and the grid scrolls sideways if a shop has many mechanics (the time
  column stays; ‹ › next to the dates move between days). The lanes can
  be switched off in the view menu (top-left icon → "Mechanic lanes");
  "Only mine" hides them automatically.
- **Only mine** — the person icon in the header filters the calendar to
  events assigned to you (appointments where you are the mechanic and WO
  blocks of your own timers). Handy for a mechanic's "what's my day"; the
  choice is remembered.
- **Open an event** — tap a block for the details: time, customer, unit,
  mechanic(s), presets, jobs and planned hours, time worked per mechanic.
  A WO block (or an appointment linked to a WO) has **Open work order**,
  which opens the WO screen (mechanics get their mechanic view with
  timers).
- **Create an appointment** (needs "Create appointments") — tap an empty
  slot (30-minute steps) or the **+** button. Fields: optional title,
  customer (search), unit chips, start/end (tap the time, pick an hour
  from the strip and :00 / :15 / :30 / :45), **mechanics** — tap one or
  several chips; the plan then shows in every one of their lanes and the
  slot is checked for each of them. The date is the day you tapped (the
  form only shows it). The server refuses a slot where a chosen mechanic
  already has a job (same rule as the web) and the app shows the reason.
- **Edit / status / delete** — in the event details: status chips switch
  the appointment status in one tap ("Edit appointments"), **Edit** opens
  the full form, **Delete** removes it ("Delete appointments"). **Create
  work order** (office roles with "Create work orders") opens the new-WO
  form with the customer and unit pre-filled; the server links the
  appointment to the WO once it is saved — the appointment then shows as
  scheduled on the web, no duplicate "Not scheduled" chip.
- **Mechanic: Start work from the calendar** — a mechanic who taps an
  appointment sees **Start work** (or **Open work order** if it is
  already linked). Start work opens the new-WO flow with the customer and
  unit pre-filled (mileage prompt as usual); if that unit already has a
  work order in progress, the app opens that WO instead and the
  appointment is linked to it. **The appointment's colour then follows
  the work**: it turns In Progress the moment the WO is in work (a timer
  is started or the mechanic saves), and Completed when the mechanic
  presses Done (or the office completes / pays the WO). This is automatic
  on the web calendar too; a Cancelled appointment is never touched.
- Pull down in Day/3 Day/Week/Schedule to refresh; the calendar also
  reloads every time you return to the tab.

### Push notifications (managers)

Office users (any non-mechanic role) get a push notification on their phone
when a mechanic **takes a work order into work** and when a mechanic
**finishes** one — WO-level events only, individual job timers don't spam:

- **"WO #… in work"** — the WO just went in progress by a mechanic action:
  they started their first job timer on it, or saved it from mechanic mode,
  or picked a finished WO back up after Done.
- **"WO #… finished"** — the mechanic pressed **Done**: the WO is waiting
  for a manager's review/confirmation. With several mechanics on the WO
  this arrives once, when the **last** of them presses Done (the push
  names that mechanic).

Tapping the notification opens that work order in the app (switching the
active shop first if the WO belongs to another shop). Notifications are per
device: they start after the first login on the phone (allow notifications
when asked) and stop on logout. Mechanics don't receive these alerts, and
actions by office users don't trigger them — only mechanics' own take/finish
do. Requires the installed app (TestFlight build); pushes don't work inside
the Expo Go development client.

Web-only for now: Import/Export, PDF design, roles editing, integrations,
billing. Mobile-first: camera flows (AI scan of work orders and vendor
invoices, photo attachments), push notifications.
