# Account-Manager Cockpit — User Manual (Tickets 1–3)

A short, non-technical guide to everything that has shipped in the cockpit
so far. The cockpit is a per-customer "single pane of glass" for account
managers: targets, performance, opportunities, risk, and AI-generated
Greek-language advice, all on one page.

> **Note on naming:** Internally we call them "Tickets" 1, 2, 3.
> A fourth phase is planned but not yet shipped — see *What's next* at
> the end.

---

## 0. Turning the cockpit on

Everything below is hidden until **Claudio** flips one master switch:

1. Sign in as an admin.
2. Open **Admin → Settings**.
3. Find the setting named **`cockpit_enabled`** and set it to **`true`**.
4. Save.

While `cockpit_enabled` is `false` (the default) every cockpit URL returns
**404** and no menu entry is shown — nothing else in the app changes.

### Permissions

The cockpit is also controlled per user. Open
**Admin → User Permissions** and grant any combination of:

| Permission key                          | What it lets the user do                          |
|-----------------------------------------|---------------------------------------------------|
| `customers.view_cockpit`                | See the cockpit page for any customer             |
| `customers.propose_target`              | Propose a new annual target (account managers)    |
| `customers.approve_target`              | Approve / reject / set / clear targets (managers) |
| `customers.bulk_set_targets`            | Use the bulk-set tool on the admin targets page   |
| `customers.ask_claude` *(Ticket 3)*     | See and use the Greek Claude advice surfaces      |

All keys are **unassigned by default** — grant them as you would any
other permission in the editor.

---

## 1. Ticket 1 — Customer search, targets, and admin tools

### 1.1 Find a customer (the picker landing page)

URL: **`/cockpit/`**

This is the entry point. Type any of:

- a customer code (e.g. `77700903`) — pressing **Enter** on an exact
  match jumps straight into that cockpit.
- part of a customer name (e.g. `cyfield`) — choose from the live
  search results.

You can also bookmark **`/cockpit/<customer_code>`** to skip the picker.

### 1.2 The Spend Target workflow

Each customer has one **active annual target**. The workflow is:

1. **Account manager proposes** an annual figure with optional notes.
2. **Manager approves** (target becomes active), **rejects** (with a
   reason — kept on the history), or **edits/sets** a value directly.
3. Setting `monthly`, `quarterly`, or `weekly_ambition` explicitly is
   optional — if you only enter `annual`, the cockpit auto-derives the
   other cadences (annual ÷ 12, ÷ 4, ÷ 52).
4. Manager-edits to an *existing* target **require notes** — initial
   creation does not.

Every change writes:
- a row to the **history table** (with previous values for diffing) and
- a row to the **cockpit audit log**, with the actor's display name.

### 1.3 The admin targets page

URL: **`/cockpit/admin/targets`** — requires `customers.approve_target`.

Lets a manager:

- See a paginated list of every customer's current target.
- Edit the annual cell inline (changes are notes-required, audited).
- **Bulk-set** a single annual figure across many customers at once
  (requires `customers.bulk_set_targets`). The bulk operation is
  **atomic**: if even one customer code in the batch is unknown, no
  rows are written.

---

## 2. Ticket 2 — The cockpit page itself

URL: **`/cockpit/<customer_code>`** — requires `customers.view_cockpit`.

A single dense page with these sections, top to bottom:

### 2.1 Header & controls

- Customer name, code, segment, ABC class, peer / reporting group.
- A **period** selector (last 90 d / 6 m / 12 m) and a **comparison
  basis** selector. These reload the page server-side.

### 2.2 KPI tiles (with sparklines)

Live metrics with one mini-chart each:

- Sales, GP, GM%, invoice count, average order value
- **Engagement score** (composite of recency + frequency + login
  activity)
- **Live cart** tile — current Magento abandoned-cart amount + item
  count + sync age. Click it to jump into the customer's Magento admin
  page (when `MAGENTO_BASE_URL` and `magento_customer_id` are
  available); falls back to the in-app abandoned-carts list otherwise.

### 2.3 Spend Target panel

Real-time achievement against the active target for **MTD, QTD, YTD**:

- `actual` vs `target`
- gap (€) and % completion
- run-rate projection (linear extrapolation to period-end)
- on-pace flag

Pending proposals show in a yellow banner with **Approve / Reject**
buttons (manager-only).

### 2.4 Trend chart

Monthly sales line for the selected period — drawn with Chart.js,
loaded from CDN. Hover tooltips show exact figures.

### 2.5 PVM (Price–Volume–Mix) bridge

A three-bar mini-chart breaking the change vs the prior period into
**price**, **volume**, and **mix** components.

### 2.6 Top Items (toggle: by GP / by Revenue)

Top SKUs the customer buys, ranked by gross profit or by revenue.
Switch view with the **By GP / By Revenue** buttons.

### 2.7 Active Offers + Offer Opportunities

- **Active Offers** — every live promotional price the customer
  currently has, with a header badge showing utilisation
  (`used / mixed / low_usage / unused / none`) and a count of live
  lines.
- **Offer Opportunities** — SKUs the customer buys regularly but has
  **no active offer for**. Sourced from a Postgres view shipped in
  Ticket 1; falls back to an empty list on SQLite.

### 2.8 White Space & Lapsed Items

- **White Space** — SKUs ≥ 30 % of the customer's peer group buys but
  this customer never has, restricted to categories the customer
  already shops in.
- **Lapsed Items** — SKUs the customer used to buy in the prior 90 d
  window but has dropped in the recent 90 d window.

### 2.9 Cross-Sell

Peer-popular SKUs in **categories the customer already buys from**,
excluding anything they've already bought in the period. Same peer
convention as White Space, just narrower.

### 2.10 Price Index Outliers

Lines where the customer's average price diverges materially from the
peer-group average for the same SKU — useful for renegotiation
conversations.

### 2.11 Churn Risk by Category

Categories where the customer's spend in the recent 90 d is
substantially below the prior 90 d. Flags the start of a drift
before it becomes a lost customer.

### 2.12 Activity Timeline

A merged feed of the last 14 days (capped at 20 events), drawn from:

- Magento login (most recent only)
- Invoices
- SMS sent to the customer
- Live-cart sync events
- Target changes

Each section degrades to "no data" rather than 500-ing the page if its
source table is missing or migrating.

### 2.13 Footer — data freshness

Shows when each upstream feed was last synced, so an AM can tell at a
glance whether the numbers above are stale.

---

## 3. Ticket 3 — Greek Claude advice + Recommended Actions

This is the AI layer. Hidden entirely unless a user has the
**`customers.ask_claude`** permission and the **`ANTHROPIC_API_KEY`**
secret is set in Replit Secrets.

> If `ANTHROPIC_API_KEY` is unset everything still loads cleanly — the
> panel just shows the Greek message
> *"Συμβουλές μη διαθέσιμες — επικοινώνησε με admin."*

### 3.1 Recommended Actions panel (auto-loaded)

Sits near the top of the cockpit page. On page load it asks Claude:

> *"Given this customer's full snapshot and gap-to-target, what are the
> top 4 actions for the AM today?"*

Claude responds **in Greek** (with English trade terms — SKU, GM%, GP,
target, gap, run-rate, ABC, HORECA, peer group, etc. — kept verbatim),
and the panel renders:

- A 2–3-sentence executive summary tied to the gap.
- A numbered list of the top actions with a priority badge
  (`P0 / P1 / P2`) and an optional "what to say to the customer" hint.

Results are cached for **12 hours** per identical snapshot, so revisits
are instant and don't re-bill the API.

### 3.2 ✦ Ask Claude buttons (per section)

Five focused buttons appear at section headers, each opening a modal
with section-scoped advice:

| Button location          | Section keyword sent to Claude |
|--------------------------|--------------------------------|
| Page header (top right)  | `all` (full snapshot)          |
| Top Items header         | `pricing`                      |
| Active Offers header     | `offers`                       |
| Offer Opportunities header | `opportunities`              |
| Price Index Outliers header | `pricing`                   |
| Churn Risk header        | `risk`                         |

Each modal returns the same shape (summary, peer context, key
findings, opportunities with confidence %, risks, next actions).

### 3.3 Failure modes (so support knows what they mean)

| HTTP | Greek message shown to user                                        | What it actually means |
|------|--------------------------------------------------------------------|------------------------|
| 503  | *Συμβουλές μη διαθέσιμες — επικοινώνησε με admin.*                 | `ANTHROPIC_API_KEY` is not set — set it in Replit Secrets. |
| 500  | *Σφάλμα κατά τη δημιουργία συμβουλής. Δοκίμασε ξανά.*              | Anthropic API call failed (rate limit, network, bad key). Full detail is in the server log only — never shown in the browser. |
| 404  | (none — page itself returns 404)                                   | Either `cockpit_enabled` is off or the customer code doesn't exist. |

### 3.4 Setting up the Anthropic key

1. Open **Replit Secrets**.
2. Add a secret named **`ANTHROPIC_API_KEY`** with your `sk-ant-…` key.
3. *(Optional)* add **`CLAUDE_MODEL`** to override the default
   `claude-sonnet-4-5`.
4. Restart the workflow (or wait for the next deploy).

No app code or database change is needed.

---

## 4. Where things live (cheat sheet)

| What you want                  | URL                                      | Permission                |
|--------------------------------|------------------------------------------|---------------------------|
| Find any customer              | `/cockpit/`                              | `customers.view_cockpit`  |
| Open one customer's cockpit    | `/cockpit/<customer_code>`               | `customers.view_cockpit`  |
| Manage all targets in one view | `/cockpit/admin/targets`                 | `customers.approve_target`|
| Bulk-set targets               | (button on the admin targets page)       | `customers.bulk_set_targets` |
| Get AI advice                  | (✦ buttons on the cockpit page)          | `customers.ask_claude`    |

---

## 5. Rollback / emergency disable

In one click — set **`cockpit_enabled = false`** in admin settings.
Every `/cockpit/...` URL returns 404, the menu entry disappears, and
no other part of the app is affected. No data is deleted; flipping the
switch back to `true` restores everything exactly as it was.

---

## 6. What's next (proposed but not shipped)

These are queued as project tasks #27 and #28:

- **Ticket 4 — AM notes & follow-up reminders.** Let AMs leave dated
  notes on a customer ("called Maria, retry next Tuesday") that show
  up in the activity timeline and on the home dashboard.
- **Ticket 5 — Manager roll-up dashboard.** A top-down view of all
  customers in a manager's portfolio with target attainment heatmap
  and which AM owns the gap.

Both will land behind the same `cockpit_enabled` master switch, so
nothing changes in production until they're explicitly turned on.
