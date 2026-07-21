# Roobico — Overview

Roobico is shop-management software for auto repair shops and fleets: work
orders, parts and inventory, vendor purchasing, customers and their units
(vehicles), payments, payroll-aware revenue reports and a customer portal.

## Navigation

Left sidebar sections: **Dashboard, Calendar, Parts, Vendors, Customers,
Work Orders, Settings, Reports, Import / Export**. The sidebar collapses
with the hamburger button; the theme toggle (light/dark) is at its bottom.

- **Global search** — the "Search..." box at the top of the sidebar finds
  customers, units, parts and work orders from anywhere (2+ characters).
- **Active shop** — organizations can have several shops; switch with the
  "Active shop" dropdown in the top bar. Every page shows only the active
  shop's data. Users only see shops they were granted access to.

## Users, roles, permissions

Access is role-based (Owner, General manager, Manager, Parts manager,
Senior mechanic, Mechanic, Viewer + custom roles), with per-user allow/deny
overrides ("Deny always wins"). The Owner always has full access. If a
section is missing from your sidebar, your role doesn't include it — ask
the account owner.

Users without the "View part costs inside WO" permission (mechanics) work in
a special **mechanic mode** without any prices — see the mechanic
documentation.

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

## Subscription

Roobico is billed per active location/user/mechanic with a 30-day trial;
the account owner manages it in Settings → Subscription & Billing. When a
subscription expires, only the owner can log in (to pay) until renewal.
