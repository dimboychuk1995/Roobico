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
    created per shop on first scan and **cannot be deactivated**. Nobody has
    to tidy it up either: units left under "NEW Customer" **without a work
    order** (and without an appointment, annual inspection or attached
    file) are **deleted automatically every night**, after a 6-hour grace
    period so a VIN you scanned a moment ago is never touched. A unit that
    has a work order stays until the office transfers that WO. After the
    Transfer the original unit left behind under "NEW Customer" (deactivated
    when the Transfer created a copy for the real company, or simply no
    longer linked when the company already had that VIN) is deleted the
    next night — unless it still has attached files, an annual inspection
    or an appointment of its own, which Transfer does not move; move or
    delete those first.
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
  change or a restarted job timer clears the Done badge. Things that change
  nothing are not counted: tapping **Add labor** and leaving the new job
  empty, typing something and undoing it before the autosave, or
  confirming the odometer reading the work order already has keeps your
  Done.
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
  correct it later. When the app sends you into a work order that is
  already in progress for this unit ("Work order #… is already in progress
  for this unit. Opening it…"), the odometer you just confirmed is saved to
  that work order automatically — no other change needed.
- **The work order date follows the mechanic's last real save.** Every
  save a mechanic makes that changes something — the Save button in web
  mechanic mode, and in the app every autosave that changed jobs, parts or
  mileage, or a press of Done — stamps the WO with that day, and that date
  is the invoice date on the PDF and in the lists. A job created on Monday and
  finished on Wednesday is dated Wednesday. Opening a WO without changing
  anything does not move the date; office (manager) saves keep the date as
  it was set.
- Prices/hours/rates that the office already entered are preserved when a
  mechanic edits. On the web mechanic page a mechanic can remove only a job
  that has not been saved yet; in the app the trash icon on a job removes it
  for everyone — except a job somebody is working on right now (see
  "Working on one work order together" below).
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
  ("Tracked Hours" vs billed hours). It does not feed pay: Timecard /
  Salary uses salaries and uAttend punches.
- **No timers on estimates.** Start job on an estimate answers *"This work
  order is an estimate. Time can be tracked after the office converts it
  to a work order."* — nothing starts, and a timer you have running on
  another work order keeps running. Ask the office to convert the estimate
  first. (A manager or owner may time work on an estimate; it stays an
  estimate.)
- **When Start job says no**, the message tells you why:
  - *"This job is no longer on the work order. Refresh the work order and
    try again."* — somebody removed the job at that very moment. If your
    timer on another job was running, the message ends with *"Your timer
    on WO #… was stopped."* — that time is saved.
  - *"This work order was confirmed by a manager and is locked."* — ask the
    manager to cancel the confirmation.
  - *"This work order is locked. Time can't be tracked on it."* — the
    office has closed this work order; nothing starts. Ask the office if
    you still need to log time on it.
  - *"This work order is being updated by someone else right now. Please
    try again."* — press Start job again in a moment.
  - Stop with nothing running says *"No timer is running."*
- Managers see who is working right now on the Work Orders list: a green
  "● <names>" line under the status badge (web and mobile).

### Working on one work order together

Several mechanics, the office and managers on the phone can work on the
same work order at the same time — everybody's work is kept. Each save is
merged on the server with what is already saved, so a colleague's new job,
part or quantity is never wiped out by your save, even if your screen was
not refreshed yet.

- **The screen refreshes itself.** In the app the work order checks for
  changes every 20 seconds, whenever you come back to the screen, when you
  return to the app and right after you press Start job / Stop job; the
  web mechanic page checks every 20 seconds while its tab is on screen,
  and Start job / Stop job refresh it at once when you have no unsaved
  changes. Jobs and parts added by colleagues or the office simply appear
  (the app says "Updated with changes from other people."). While the
  **Add part** picker or a job's **Send for approval** window is open, the
  app does not refresh the jobs — it catches up as soon as you close it,
  so the list never moves under your finger. If the web page cannot
  refresh (for example the connection dropped), one small notice appears
  and the page keeps retrying by itself, less often the longer it fails
  (at most every 5 minutes) — no pop-ups every 20 seconds. A mileage you
  are typing in the web page's header is never overwritten by the
  refresh.
- **What you are typing is never overwritten.** The app first saves your
  own changes and then shows the combined result. The web mechanic page
  keeps your unsaved changes and shows *"Someone else just updated this
  work order. When you save, their changes will be merged with yours."* —
  just press Save. Stop in the bottom timer bar does not throw away
  unsaved changes either. Setting a value back after the app showed
  somebody else's change (they made it 3, you set your 2 again) saves your
  value.
- **Your part, text or approval goes to the job you picked.** Even when
  colleagues' jobs shift the list, a part you add, the polished text from
  **AI edit** and a job's **Send for approval** always go to the job you
  tapped. If somebody removed that job in the meantime, nothing goes to
  another job and the app tells you: *"That job was removed by someone
  else — the part was not added."*, *"That job was removed by someone else
  — the polished text was not applied."* or *"This job is no longer on the
  work order — the approval request was not sent."* One exception for
  **Send for approval**: if a manager confirmed the work order while you
  had it open, the app can no longer read the job list. If the work order
  has not changed since your screen last updated, the request goes out as
  usual. If it has (jobs added, removed or changed), the app does not send
  it, so the customer never gets a request for the wrong job, and says
  *"A manager locked this work order, and it changed since you opened it —
  the approval request was not sent. Ask a manager to send it."* Tap
  Cancel to close the window.
- **Merge notes.** When your save was combined with somebody else's
  changes, the app shows a **"Merged with changes from others"** box above
  the jobs (the web page shows the list right after Save), for example
  *Kept job "Brakes" added by another user.* Close it with ×; there is
  nothing else to do.
- **Removing a job.** In the app the trash icon removes the job for
  everyone — unless a colleague's timer is running on it or somebody
  changed it after you opened the work order; then the job stays and the
  note says why. Ask the colleague to press Stop job first if the job
  really has to go.
- **Bad connection.** The app saves your changes one after another, in
  order. If a save fails — or the server does not answer within 30
  seconds — the line under the jobs says *"Autosave failed — … It will
  retry automatically"* (after a timeout: *"Autosave failed — The server
  did not answer in time. Check the connection and try again. It will
  retry automatically."*) and the app tries again by itself; going back to
  the list or switching to another app saves what you typed first. A new
  job or part is never created twice, even when a save is repeated. After
  a failed or unanswered save, your next change is always sent — even one
  that only puts a value back (for example fixing a mistyped mileage) —
  because the failed save may still have reached the server. That save
  counts as a change, so it clears your Done mark. If a mistyped higher
  odometer reading did reach the server, the work order gets your
  correction, but the unit's odometer keeps the higher number (it never
  moves back): ask the office to fix the unit's mileage.
- **Locked by the manager.** If the manager confirms the work order (or it
  becomes an estimate) while you have it open, the app shows the reason at
  the top and stops saving ("Changes can't be saved while the work order
  is locked."). A paid work order switches to view only. On the web
  mechanic page a yellow bar at the top gives the reason and **Save** is
  greyed out: *"A manager locked this work order — changes can't be saved
  right now."* (with unsaved changes: *"A manager locked this work order —
  your unsaved changes can't be saved."*); an estimate shows *"This work
  order is an estimate — only the office can change it."* Opening a work
  order that is already confirmed shows *"This work order was confirmed by
  a manager and is locked."* once, then the bar. The page keeps checking
  every 20 seconds: when the manager cancels the confirmation, the bar
  disappears — a page without unsaved changes refreshes by itself, and
  unsaved changes stay on the page with Save working again. Ask the
  manager to cancel the confirmation if you still need to change
  something.
- **Part quantities stay as they are.** A part line with quantity 0 (for
  example a placeholder the office added) stays 0 on the web mechanic page
  until you change it yourself.
- **Keep the app updated.** Live refresh, merge notes and automatic retries
  need the latest app version. Older versions still save safely — the
  server removes a job only if that phone had the job on screen, so their
  saves never delete jobs a colleague added, and an edit screen left open
  under a newer one cannot remove jobs added after it was opened either
  (unless you went back to the work order screen more than once — pull to
  refresh, another notification — before saving the older screen; on an
  older version keep only one edit screen of a work order open) — but they
  do not refresh by themselves: go back and open the work order again to
  see what others changed. Because the server is extra careful with them,
  a job you delete in an older version can come back after the save
  (typically a job added on another device after you opened the work
  order); delete it once more, or better, update the app. If the office
  removes a job you just added while you are still typing in an older
  version, your next saves keep it removed; to add it again, go back, open
  the work order again, then add it. Older versions also show no merge
  notes.

### Mechanic extras in the mobile WO editable view

- **Job photos**: every job card has its own attachments block (camera or
  gallery) — files are tied to that specific job; the WO-level Attachments
  block is separate. Mechanics can view and upload, deleting needs the
  "Delete attachments" permission.
- **Send for approval**: a mechanic can email the customer an
  Approve/Decline request for a single job ("Send for approval" on the job
  card opens the "Send job for approval" window) or for the whole WO
  ("Send work order for approval" at the bottom). Same
  customer-authorization flow managers use from the web. The request is
  always for the job you tapped, even if colleagues' jobs moved it in the
  list meanwhile.
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
- **Editing a work order on the phone (managers)** is merged with what the
  mechanics do meanwhile, like on the web: their new jobs, parts, mechanic
  assignments and timer hours stay when you press Save. While nothing is
  changed in your form it refreshes itself when the work order changes;
  once you have started editing it shows *"Someone else just updated this
  work order. When you save, their changes will be merged with yours."*
  and keeps your changes. If the save was combined with somebody else's
  changes, a "Work order saved" message lists what happened. With an older
  app version the server is extra careful: a job you delete there can come
  back after Save (typically one added on another device after you opened
  the work order) — delete it once more, or update the app.
- A floating **hide-keyboard button** appears above the keyboard on every
  screen while typing.
- **Calendar**: the shop schedule in a Google-Calendar-style layout — see
  "Calendar on the phone" below.
- **Customers**: list/search, customer and unit pages, forms.
- **Parts**: Stock (with cross references ⇆, minimum-stock badges and the
  All / Below min / Near min chips — see "Parts on the phone" below),
  Orders (parts orders incl. AI invoice scan; **Receive** here asks only
  for the vendor bill and receives every line as ordered — a partial
  receipt with the real quantities is done on the web), Counts
  (stocktakes).
- **More**: global Search, Vendors, Reports (all standard reports with
  period chips), Settings — **Active shop** switching and account info.

### Parts on the phone

- **Stock** lists active parts that have something on hand; each card shows
  the quantity in the part's unit — "×12" for pieces, "12 gal" / "3 qt" for
  other units — and, for parts with a minimum stock, "Min 5" (with the
  unit) under the name and a red **Below min** or amber **Near min** badge
  next to the quantity (the quantity itself turns red / amber). The rule is
  the one from Parts & Inventory → "Minimum stock": below = fewer than the
  minimum, near = at the minimum or up to 20 % above it (at least 1).
- Chips under the search box: **All**, **Below min**, **Near min**. A chip
  shows how many parts it lists ("Below min · 2") — and **Near min counts
  the below ones too** (it is "below or near", everything that would be
  marked), so its number is never smaller than Below min's. With Below min
  or Near min selected the list also includes parts with **0 on hand**,
  which the All list hides; search and pull-to-refresh work inside the
  filter, and the chip you picked is remembered on the phone. Empty results
  say "No parts below minimum" / "No parts near minimum".
- The part screen shows the stock the same way plus two rows: **Unit of
  measure** (Each, Gallon, Quart…) and **Minimum stock** (the minimum with
  its unit and the badge; "—" when the part does not require one or its
  inventory is not tracked). The minimum and the unit are set on the web in
  the part form; the phone only shows them.

### Calendar on the phone

The **Calendar** tab shows the same appointments as the web calendar
(needs "View calendar", which every default role has, mechanics included)
and works like Google Calendar on a phone:

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
- **Pinch to zoom** (Day / 3 Day / Week) — pinch up/down with two fingers
  to stretch or squeeze the hours (the spot under your fingers stays
  put); pinch left/right in 3 Day or Week to make the days wider — zoom
  a portrait phone week in until the mechanic lanes appear, then the grid
  scrolls sideways. The zoom level is remembered; the view menu
  (top-left icon) gets a **Reset zoom** item while it is changed.
- **Jump to a date** — tap the month name in the header to drop down a
  month picker (dots mark days with events); the square "today" icon
  returns to today.
- **What is on the grid** — the shop's **appointments**, the same ones
  the web calendar shows: colour = appointment status (the statuses and
  colours set on the web), the title (the office's note — phone number,
  job list), customer and unit. An appointment linked to a work order
  also shows the WO number and the live work-order state (Not started /
  In progress / Mechanic done / Completed / Paid). Work orders themselves
  are not drawn on the calendar — a job is on the schedule only when
  somebody planned it as an appointment. Mechanics see "Paid" as
  "Completed" and never see money here.
- **Mechanic lanes** — in Day, 3 Day and Week every day is split into one
  column per mechanic of the shop (each mechanic has a colour, as on the
  web), empty future days included; an "Unassigned" lane (grey "–")
  appears only when an appointment has no mechanic. The marker row under
  the dates names the lanes. **Tap an empty slot inside a lane and the
  new appointment is already assigned to that mechanic.** In Week the
  whole week always stays on the screen, so lanes appear there only when
  they have room — turn the phone sideways or use an iPad; in a portrait
  phone week each day is one column and the blocks carry the mechanic's
  initials badge instead. In Day / 3 Day the lanes keep a readable width
  and the grid scrolls sideways if a shop has many mechanics (the time
  column stays; ‹ › next to the dates move between days). The lanes can
  be switched off in the view menu (top-left icon → "Mechanic lanes");
  "Only mine" hides them automatically.
- **Only mine** — the person icon in the header keeps only the
  appointments you are assigned to (alone or as part of a crew). Handy
  for a mechanic's "what's my day"; the choice is remembered.
- **Open an appointment** — tap a block for the details: time, customer,
  unit, mechanic(s), presets and the note. A linked appointment shows
  "Linked to WO #…", the work-order state and **Open work order**, which
  opens the WO screen (mechanics get their mechanic view with timers).
- **Create an appointment** (needs "Create appointments") — tap an empty
  slot (30-minute steps) or the **+** button. Fields: optional title,
  customer (search), unit chips, start/end (tap the time, pick an hour
  from the strip and :00 / :15 / :30 / :45), **mechanics** — tap one or
  several chips; the plan then shows in every one of their lanes. The
  date is the day you tapped (the form only shows it). Two appointments
  of one mechanic may overlap — nothing refuses the slot; the web
  calendar draws such blocks side by side.
- **Edit / status / delete** — in the event details: status chips switch
  the appointment status in one tap ("Edit appointments"), **Edit** opens
  the full form, **Delete** removes it ("Delete appointments"). **Create
  work order** (office roles with "Create work orders") opens the new-WO
  form with the customer, unit, one job per preset and the note
  pre-filled (a toast says how many presets were applied; the note sits
  in the first job's *Describe issue*, or — without presets — becomes the
  job line when it is short, up to 200 characters, and the issue text
  otherwise; an office user still needs at least one job line to
  save); on save the server assigns the appointment's crew to the jobs
  and links the appointment to the WO — on the web its block then
  carries the "WO #" badge.
- **Mechanic: Start work from the calendar** — a mechanic who taps an
  appointment sees **Start work** (or **Open work order** if it is
  already linked). Start work opens the new-WO flow with the customer and
  unit pre-filled (mileage prompt as usual) and the server builds the
  work order from the appointment: **one job per service preset** with
  the preset's planned hours and parts (priced by the shop's rules, as on
  the web), the **whole crew** of the appointment assigned to every job,
  and the appointment's **note** in the first job's *Describe issue*
  field — it opens expanded under the job, so the mechanic sees (and can
  add to) what the office wrote ("customer hears grinding front left").
  An appointment without presets but with a note gives one job whose
  description is the note itself. The WO screen also shows a
  **Scheduled** card at the top with the planned time, crew and note.
  Fine print: presets, crew and note are copied only when the appointment
  is for the same unit as the WO (an appointment without a unit counts as
  a match; one for another unit is linked but not copied), and a crew
  member who is no longer an active mechanic is skipped. If that unit
  already has a work order in progress, the app opens that WO instead
  and the appointment is linked to it. **The appointment's colour then
  follows the work**: it turns In Progress the moment the WO is in work
  (a timer is started or the mechanic saves), and Completed when the
  mechanic presses Done (or the office completes / pays the WO). The
  status never goes back by itself — if the office reopens the job, the
  appointment keeps its colour until someone changes it by hand. This is
  automatic on the web calendar too; a Cancelled appointment is never
  touched.
- **Linked appointments are driven by the work order** — in a linked
  appointment the customer and unit cannot be edited (change them on the
  WO; Transfer moves the appointment along), and the In progress /
  Completed status chips follow the WO while it is in work — only
  Cancelled can be set by hand. Moving the appointment to another time or
  mechanic never changes the work order. The web calendar picks such
  changes up within half a minute (it refreshes every 30 seconds); the
  phone reloads whenever you return to the tab or pull down.
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
