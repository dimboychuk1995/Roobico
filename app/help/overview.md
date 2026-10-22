# Roobico — Overview

Roobico is shop-management software for auto repair shops and fleets: work
orders, parts and inventory, vendor purchasing, customers and their units
(vehicles), payments, payroll-aware revenue reports and a customer portal.

## Navigation

Left sidebar sections: **Dashboard, Calendar, Parts, Vendors, Customers,
Work Orders, Settings, Reports, Perks, Import / Export**. The sidebar
collapses with the hamburger button; the theme toggle (light/dark) is at
its bottom.

**Perks** — quick tools outside the normal work order flow. Currently:
**Annual Inspection** — create a DOT annual inspection for any vehicle
without adding it to the system (type the VIN, pick the vehicle type,
enter the customer name by hand); the page also lists every saved
inspection of the shop with PDF download and delete.

- **Global search** — the "Search..." box at the top of the sidebar finds
  customers, units, parts and work orders from anywhere (2+ characters).
- **Active shop** — organizations can have several shops; switch with the
  "Active shop" dropdown in the top bar. Every page shows only the active
  shop's data. Users only see shops they were granted access to.
- **Every table is personal** — use the small **Columns** button above any
  table to hide/show columns, drag the dividers between column headers to
  resize, click a header to sort. Your layout is saved to your account and
  follows you to any device; "Reset table" in the Columns menu restores
  defaults. Other users are not affected.

## Education Center

Sidebar → **Education Center** (available to every role, no permission
needed). An in-app course with step-by-step lessons and real screenshots
for every section of the app: 11 modules (Getting Started, Dashboard,
Customers & Units, Work Orders, Calendar, Parts & Inventory, Vendors &
Parts Orders, Reports, Settings, Mechanic Mode & Mobile App, Perks /
Import / Export / AI) with about 80 lessons.

- **Home page** — overall progress ring, a **Continue** button (the last
  lesson you opened but did not finish, or the next unfinished one), a
  search box that matches lesson titles and descriptions, module cards with
  progress bars, and three suggested learning paths (owner / manager,
  mechanic, parts manager).
- **Lesson page** — "In this lesson" summary, numbered steps with
  screenshots (click a screenshot to zoom), tips and warnings, the
  permission keys each action needs, links to related lessons, and the
  module table of contents on the right. **Mark lesson as complete** at
  the bottom saves your progress; click again to undo. ← / → arrow keys
  move between lessons.
- **Progress is personal**: saved to your account, follows you to any
  device, not visible to other users. **Reset progress** on the home page
  clears it.
- Mechanics reach it from the graduation-cap button in the header of the
  mechanic work order list.
- The AI Helper knows every lesson and screenshot: ask "how do I …" and it
  can show the relevant screenshot in its answer and link to the lesson.

How to use it: "Learn how to do X" → Education Center → type X in the
search box → open the lesson. New employee onboarding → send them the
Education Center and ask them to follow the path for their role; the
owner can see nothing of their progress, so ask them to say when they are
done.

## Contact & support

- **Phone**: **(773) 331-2795**.
- **Contact us form**: on the login page (roobico.com) there is a second
  tab, **Contact us**, next to **Log in** — name, email, optional phone and
  company, a topic (general question, demo, pricing, help with my account,
  billing) and a message. It works without logging in, so it is the right
  channel when you cannot get into your account. Direct link:
  roobico.com/#contact. We reply by email, usually within one business day.
- Inside the app, the **Roobico AI Helper** (sidebar) answers "how do I"
  questions instantly; the **Education Center** has the lessons.

## Users, roles, permissions

Access is role-based (Owner, General manager, Manager, Parts manager,
Senior mechanic, Mechanic, Viewer + custom roles), with per-user allow/deny
overrides ("Deny always wins"). The Owner always has full access. If a
section is missing from your sidebar, your role doesn't include it — ask
the account owner.

Users without the "View part costs inside WO" permission (mechanics) work in
a special **mechanic mode** without any prices — see the mechanic
documentation. The same key hides money outside work orders: customer
balances, dashboard revenue blocks, the WO Cost button. Purchase costs in
the parts catalog have their own key ("View part costs"), financial reports
("View financial reports") and the Timecard / Salary report ("View
Timecard / Salary report") have theirs, and editing employees' pay in the
user form needs "Edit employees' pay type & salary". Every page shows only
the buttons the role may use, and the server re-checks each action, so what
you see is exactly what you can do.

## Money flow in one paragraph

Money comes in through **Work Orders** (labor + parts + tax billed to
customers, payments recorded on the WO). Money goes out through **Parts
Orders** (buying from vendors) and **payroll** (salaries + uAttend hourly).
The **General Revenue** report puts these together; **Customer Balances**
shows who owes you, **Vendor Balances** — whom you owe.

## Mobile app

The Roobico mobile app uses the same login. It covers work orders (including
mechanic mode with job timers), customers, parts, vendors, reports, calendar
and shop switching, plus camera AI flows (scan a paper work order or a
vendor invoice). See the mechanic & mobile documentation.

## Email confirmation

After signing up, the account owner gets a confirmation email. Until the
link in it is clicked, a yellow bar at the top of the app reminds about it
with a **Resend email** button (the link is valid 7 days; resend is limited
to a few times per hour). Confirmation does not block any features — it just
verifies we can reach you at that address. Team members invited through
Settings → Users confirm their email automatically when they accept the
invitation and set a password.

## Subscription

Roobico is **free forever for 1 location + 1 full user** — a solo shop pays
nothing. Extra locations ($100/mo), extra full users ($50/mo) and mechanics
($25/mo each) are billed per unit. The 30-day trial removes all limits —
any number of locations, users and mechanics is free during the trial.
Annual billing is available at a 20% discount (12 × monthly − 20%, billed
once a year). The account owner manages it in Settings → Subscription &
Billing. When a paid subscription expires, only the owner can log in (to
pay) until renewal; a shop that fits the free tier is never blocked.
