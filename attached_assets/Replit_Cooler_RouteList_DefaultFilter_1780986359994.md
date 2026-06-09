# Cooler Route List — Default to Recent / Active Routes Only

**Files:** `blueprints/cooler_picking.py` (route_list function, line ~358)
           `templates/cooler/route_list.html`

## The problem

The `route_list()` backend function fetches every cooler route ever created —
no date or status filter. At the start of the season this is 24 rows; by the
end it will be hundreds. The "Today" / "Needs Action" tabs are client-side
only — they hide rows in the browser but all records are still loaded from the
database on every page open.

The page should open showing only what the team is actively working on (recent
and undelivered), with a clear way to search for or load older records when
needed.

---

## Change 1 — Backend: add a default date window + search parameter

In `blueprints/cooler_picking.py`, update `route_list()` to accept two
optional query parameters:

- `days` — how many days back to show (default: **14**). Covers today plus
  two weeks of history, which is enough to find any route still in progress
  or recently dispatched.
- `q` — a free-text search string (route number, driver name, or route name).
  When supplied, ignore the `days` window and search across all records.

**Step 1a — add imports at the top of the function (already present, just
noting them):**
```python
from flask import request
import datetime as _dt
```

**Step 1b — read the parameters at the start of the function, right after the
`def route_list():` line:**
```python
# Default window: last 14 days. Extend via ?days=30 or search via ?q=term
_days_back  = int(request.args.get("days", 14))
_search_q   = (request.args.get("q", "") or "").strip().lower()
_show_all   = request.args.get("all") == "1"

if _search_q or _show_all:
    _date_filter_sql = ""          # no date restriction when searching
    _date_params     = {}
else:
    _cutoff = (_dt.date.today() - _dt.timedelta(days=_days_back)).strftime("%Y-%m-%d")
    _date_filter_sql = "AND s.delivery_date >= :cutoff"
    _date_params     = {"cutoff": _cutoff}
```

**Step 1c — add the date filter to the queue_rows query (Step 1 in the
existing function, around line ~370):**

Find:
```python
queue_rows = db.session.execute(text(
    "SELECT bpq.status, i.route_id, s.delivery_date::text "
    "FROM batch_pick_queue bpq "
    "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
    "LEFT JOIN shipments s ON s.id = i.route_id "
    "WHERE bpq.pick_zone_type = 'cooler'"
)).fetchall()
```
Replace with:
```python
queue_rows = db.session.execute(text(
    "SELECT bpq.status, i.route_id, s.delivery_date::text "
    "FROM batch_pick_queue bpq "
    "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
    "LEFT JOIN shipments s ON s.id = i.route_id "
    "WHERE bpq.pick_zone_type = 'cooler' "
    + _date_filter_sql
), _date_params).fetchall()
```

**Step 1d — add the same date filter to the box_route_rows query (Step 2,
around line ~390):**

Find:
```python
box_route_rows = db.session.execute(text(
    "SELECT route_id, delivery_date::text, "
    "       COUNT(*) FILTER (WHERE status != 'cancelled') AS total, "
    "       COUNT(*) FILTER (WHERE status = 'closed')     AS closed, "
    "       COUNT(*) FILTER (WHERE status = 'open')       AS open_count, "
    "       COUNT(*) FILTER (WHERE label_printed_at IS NOT NULL AND status != 'cancelled') AS labels_printed "
    "FROM cooler_boxes "
    "GROUP BY route_id, delivery_date"
)).fetchall()
```
Replace with:
```python
box_route_rows = db.session.execute(text(
    "SELECT cb.route_id, cb.delivery_date::text, "
    "       COUNT(*) FILTER (WHERE cb.status != 'cancelled') AS total, "
    "       COUNT(*) FILTER (WHERE cb.status = 'closed')     AS closed, "
    "       COUNT(*) FILTER (WHERE cb.status = 'open')       AS open_count, "
    "       COUNT(*) FILTER (WHERE cb.label_printed_at IS NOT NULL AND cb.status != 'cancelled') AS labels_printed "
    "FROM cooler_boxes cb "
    "JOIN shipments s ON s.id = cb.route_id "
    "WHERE 1=1 " + _date_filter_sql.replace("s.delivery_date", "cb.delivery_date")
    + " GROUP BY cb.route_id, cb.delivery_date"
), _date_params).fetchall()
```

**Step 1e — apply text search filter after building the routes list (Step 5,
after the `routes.append(...)` block):**

After the full `routes` list is built, add:
```python
# Apply text search across route id, driver name, route name
if _search_q:
    routes = [
        r for r in routes
        if _search_q in str(r["route_id"]).lower()
        or _search_q in (r["driver"] or "").lower()
        or _search_q in (r["route_name"] or "").lower()
    ]
```

**Step 1f — pass the current filter state to the template:**

In the `return render_template(...)` call at the end, add:
```python
days_back=_days_back,
search_q=_search_q,
show_all=_show_all,
```

---

## Change 2 — Frontend: search bar + "Show older" controls

In `templates/cooler/route_list.html`, replace the current subtitle paragraph
and add a search/filter bar immediately after the page heading.

Find (around line ~8-9):
```html
<p class="text-muted mb-3 small">All cooler packing work — active, in-progress and completed today.</p>
```
Replace with:
```html
<p class="text-muted mb-2 small">
  {% if show_all %}Showing all records.
  {% elif search_q %}Search results for "{{ search_q }}".
  {% else %}Showing routes from the last {{ days_back }} days.{% endif %}
</p>

{# ── Search / filter bar ─────────────────────────────────────────────────── #}
<form method="get" class="d-flex flex-wrap gap-2 align-items-center mb-3">
  <input type="text" name="q" value="{{ search_q }}"
         class="form-control form-control-sm" style="max-width:260px;"
         placeholder="Search route, driver, name…">
  <button type="submit" class="btn btn-sm btn-primary">
    <i class="fas fa-search me-1"></i>Search
  </button>
  {% if search_q or show_all or days_back != 14 %}
  <a href="{{ url_for('cooler.route_list') }}" class="btn btn-sm btn-outline-secondary">
    <i class="fas fa-times me-1"></i>Clear
  </a>
  {% endif %}
  {% if not show_all and not search_q %}
  <span class="text-muted small ms-2">
    Not finding a route?
    <a href="{{ url_for('cooler.route_list') }}?all=1" class="ms-1">Load all records</a>
    &nbsp;|&nbsp;
    <a href="{{ url_for('cooler.route_list') }}?days=30">Last 30 days</a>
  </span>
  {% endif %}
</form>
```

---

## What this achieves

| Action | Result |
|---|---|
| Normal page open | Last 14 days of routes only — fast load |
| Type a route number / driver name and click Search | Searches across ALL records regardless of date |
| Click "Last 30 days" | Extends the window, still fast |
| Click "Load all records" | Fetches everything (same as today's behaviour) |
| Click "Clear" | Returns to the default 14-day view |

The "Today", "Needs Action", and "Ready for Dispatch" filter tabs continue to
work exactly as before — they're client-side filters on top of whatever
records the server returned.

---

## Recommended default window

14 days was suggested above. If your cooler season runs routes daily, a 7-day
window is likely enough for the working view (today + last week). Adjust the
default in the `request.args.get("days", 14)` line to match your preference.

---

## Testing checklist

- [ ] Page opens by default showing only routes from the last 14 days (count
      should be much lower than the current 24 for an off-season test).
- [ ] Searching by route number (e.g. "433") returns that route even if it's
      outside the 14-day window.
- [ ] Searching by driver name returns matching routes across all history.
- [ ] Clicking "Last 30 days" returns more routes than the default view.
- [ ] Clicking "Load all records" returns every route (same count as today).
- [ ] Clicking "Clear" returns to the 14-day default view.
- [ ] All existing filter tabs (Today, Needs Action, Ready for Dispatch) still
      work correctly on the filtered result set.
- [ ] Page subtitle updates to reflect the current filter state ("Showing
      routes from the last 14 days" / "Search results for X" / "Showing all
      records").
