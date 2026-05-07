# Task #28 — Login Behaviour Analytics in Cockpit

## What & Why

Account managers currently see a single "days since last login" datapoint per customer.
The cockpit has access to richer behavioural data — `magento_customer_login_log` already
captures every login event for the last 90+ days, refreshed every 30 minutes from the
Magento FTP. This data is unused except for the engagement score formula.

This task surfaces login behaviour as actionable marketing intelligence — telling
account managers *when* a customer typically logs in, *whether their pattern is
changing*, and *when to time outreach* to land before their next ordering session.

The work is purely additive — new functions in `services/cockpit_data.py`, a new panel
in the existing cockpit template, and a new fleet-level page. No migrations, no new
tables, no changes to flag-reading code or any existing route.

---

## Done Looks Like

### Per-customer panel (Phase A)

- A new **"Login Behaviour"** panel appears on the per-customer cockpit page
  (`/cockpit/<customer_code>`) in the data dictionary returned by `get_cockpit_data()`.
- The panel renders a 7-column × 3-row heatmap (days × time-of-day buckets) showing
  login frequency over the last 90 days.
- A summary strip below the heatmap shows: most active day, peak login window,
  average session duration, login count over last 30 days, login count over the
  previous 30 days for trend, last login timestamp, and a trend arrow (↑/↓/→).
- A "Marketing insight" text block translates the data into plain language for the
  AM (e.g. "This customer typically logs in Tuesday 2–4 PM. Schedule outreach for
  Tuesday morning to catch them before they order.").
- The panel is wrapped in `{% if data.login_behaviour %}` so it gracefully disappears
  when login data is unavailable.
- The login behaviour data is added to the Claude advice payload so "Ask Claude" can
  reference login patterns in its narrative.

### Fleet-level report (Phase B)

- A new route `/cockpit/login-insights` shows aggregated login intelligence across
  all customers — gated by `customers.use_cockpit` permission and the
  `cockpit_enabled` feature flag (same as the rest of cockpit).
- The page contains four sections:
  1. **Top 20 most engaged** — customers with the highest login frequency in last 30 days
  2. **At-risk customers** — login frequency dropped 30%+ vs previous 30-day window
  3. **Day-of-week heatmap** — aggregated across all customers (when does the platform get used?)
  4. **Dormant but recently bought** — customers who haven't logged in for 14+ days
     but have invoiced sales in the last 60 days
- Each section is sortable and exportable to CSV.

### Quality gates

- No regressions on existing cockpit tests.
- Performance: the per-customer panel must add < 200ms to cockpit page load.
- The fleet-level page must complete in < 5 seconds for typical customer counts
  (limit fleet-level queries to top 500 by activity).

---

## Scope — What To Build

### Phase A — Per-Customer Login Behaviour Panel

#### A.1 — New function in `services/cockpit_data.py`

Add this near the other panel-fetcher functions (after `_fetch_open_orders`, before
`_compute_engagement_score`):

```python
def _fetch_login_behaviour(customer_code: str, lookback_days: int = 90) -> dict:
    """
    Returns login pattern analysis for one customer.

    Reads from magento_customer_login_log (full history table).

    Returns dict with keys:
      heatmap                 — list of {day_of_week, time_bucket, count}
                                where day_of_week is 0=Mon..6=Sun
                                and time_bucket is "morning"/"afternoon"/"evening"
      peak_day_name           — string like "Tuesday" or None
      peak_hour_range         — string like "14:00 – 16:00" or None
      avg_session_minutes     — float (capped at 120) or None
      session_count_with_logout — int
      logins_last_30d         — int
      logins_prev_30d         — int
      trend_direction         — "up" / "down" / "stable" / None
      trend_pct               — float (-100..+inf) or None
      last_login_at           — datetime or None
      total_logins_in_window  — int
      best_contact_window     — human-readable text (Greek + English fallback)
      data_quality            — "good" / "limited" / "insufficient"
    """
```

Implementation rules:

**Heatmap buckets:**
- Time-of-day buckets: `morning` (06:00–12:00), `afternoon` (12:00–18:00),
  `evening` (18:00–24:00). Logins between 00:00–06:00 go to evening (treat as
  "late night" — same row).
- Use Postgres `EXTRACT(DOW FROM last_login_at)` for day of week.
- Returns an array — one row per (day, time_bucket) with non-zero count. The
  template fills in zero cells visually; no need to return all 21 cells from SQL.

**Peak day / peak hour:**
- Most frequent day of week (mode). If two days tie, return the most recent one.
- Peak hour range = the 2-hour window with the most logins. Compute by counting
  logins per hour, then find the contiguous 2-hour pair with the highest sum.
- If the top day has fewer than 5 logins in the window, set both fields to None
  and `data_quality = "limited"`.

**Session duration:**
- Computed only from rows where both `last_login_at` and `last_logout_at` are set.
- Cap each individual session at 120 minutes (anything longer is treated as
  "user closed tab without logging out" and excluded from the average).
- If fewer than 3 valid sessions, return `None` for `avg_session_minutes`.

**Trend:**
- Compare `logins_last_30d` vs `logins_prev_30d`.
- Direction: "up" if delta > +20%, "down" if delta < -20%, "stable" otherwise.
- Returns None if `logins_prev_30d == 0` (no baseline to compare to).

**Best contact window text:**
- Greek-first (most AMs are Greek), with English fallback.
- Greek: "Συνδέεται συνήθως {day} {time_range}. Στείλτε επικοινωνία {one_step_earlier}
  για μέγιστη αποδοχή."
- English fallback (if Greek strings cause issues): "Most often logs in {day}
  {time_range}. Schedule outreach for {one_step_earlier} for highest reach."
- If `data_quality != "good"`, return text like "Insufficient login pattern data —
  contact this customer through their preferred channel."

**Data quality flag:**
- `"good"` — at least 10 logins in window
- `"limited"` — between 3 and 10 logins
- `"insufficient"` — fewer than 3 logins (heatmap will be sparse, peak useless)

#### A.2 — Wire into `get_cockpit_data()`

In `services/cockpit_data.py:1163` area (where other `_safe(...)` calls are),
add:

```python
login_behaviour = _safe(
    lambda: _fetch_login_behaviour(customer_code, lookback_days=90),
    None,  # default if it fails
    "login_behaviour"
)
```

Then in the `payload` dict at line 1211:

```python
"login_behaviour": login_behaviour,
```

The `_safe` wrapper handles errors — if the login query fails for any reason, the
panel disappears rather than breaking the whole cockpit (existing pattern).

#### A.3 — Render the panel in `templates/cockpit/cockpit.html`

Add a new card section after the Engagement Score panel and before the Activity
Timeline. Wrap in `{% if data.login_behaviour %}`.

Heatmap markup:

```html
<table class="login-heatmap">
  <thead>
    <tr><th></th><th>Mon</th><th>Tue</th>...<th>Sun</th></tr>
  </thead>
  <tbody>
    <tr><th>Morning</th>{% for day in 0..6 %}<td class="hb-{{count_for(day, 'morning')}}">{{count_for(day, 'morning')}}</td>{% endfor %}</tr>
    <tr><th>Afternoon</th>...</tr>
    <tr><th>Evening</th>...</tr>
  </tbody>
</table>
```

Apply CSS classes `hb-0` through `hb-5+` for cell colour intensity. Use the existing
cockpit panel/card design — reuse `.cockpit-panel` and `.kpi-strip` classes that
already exist in the template.

Below the heatmap, render the summary strip and the marketing insight box.

#### A.4 — Feed login data to Claude advice

In `services/claude_advice_service.py`, the existing `_clip_payload()` function
already passes the cockpit snapshot through. Verify it preserves the new
`login_behaviour` key. If `_clip_payload` filters keys explicitly, add
`login_behaviour` to the allowed list.

Update the Greek system prompt (`GREEK_SYSTEM_PROMPT` constant in the same file) to
mention login behaviour as a signal Claude can use:

> "Έχεις πρόσβαση σε δεδομένα συνδέσεων του πελάτη: peak_day, peak_hour_range,
> trend_direction, last_login_at. Χρησιμοποίησέ τα όταν είναι σχετικά για να
> προτείνεις πότε ο AM να επικοινωνήσει με τον πελάτη."

(Translation hint: "You have access to customer login data: peak_day,
peak_hour_range, trend_direction, last_login_at. Use them when relevant to
suggest when the AM should contact the customer.")

#### A.5 — Cache invalidation

The existing Claude advice cache uses `_hash_payload()` which hashes the entire
snapshot. Adding `login_behaviour` to the payload automatically busts the cache
when login data changes — no extra work needed.

---

### Phase B — Fleet-Level Login Insights Report

#### B.1 — New route in `blueprints/cockpit.py`

Add at the end of the cockpit blueprint, before the API routes:

```python
@cockpit_bp.route("/login-insights")
@login_required
@require_permission_hard("customers.use_cockpit")
def login_insights():
    """Fleet-level login behaviour intelligence for marketing planning."""
    from services.cockpit_data import get_login_insights_fleet
    data = get_login_insights_fleet(top_n=500)
    return render_template("cockpit/login_insights.html", data=data)
```

#### B.2 — New function in `services/cockpit_data.py`

```python
def get_login_insights_fleet(top_n: int = 500) -> dict:
    """
    Fleet-level login analytics. Returns dict with:
      most_engaged       — list of top N by logins_last_30d
      at_risk            — list of customers whose login freq dropped 30%+
      dow_heatmap        — array of {day_of_week, count} aggregated all customers
      dormant_with_sales — customers with no login 14+d but invoiced last 60d
      generated_at       — timestamp
    """
```

Implementation:

- `most_engaged`: query `magento_customer_login_log` joined with `ps_customers`,
  group by `customer_code_365`, count logins in last 30 days, order DESC, limit 20.
  Include customer name, last_login_at, login count, and an "engagement trend"
  arrow per customer.

- `at_risk`: subquery counting logins in `last_30d` and `prev_30d`. WHERE clause:
  `prev_30d > 5 AND last_30d < prev_30d * 0.7`. Order by absolute drop size DESC.
  Limit 20. Include customer name, drop %, last login, and recent invoice value.

- `dow_heatmap`: simple GROUP BY day_of_week and time_of_day buckets. Aggregated
  across all customers in the last 90 days. Returns 21 cells (7 days × 3 buckets).

- `dormant_with_sales`: customers in `magento_customer_last_login_current` with
  `last_login_at < NOW() - INTERVAL '14 days'`, joined to `invoice_headers` to
  filter for those with invoices in last 60 days. Order by invoice value DESC.
  Limit 30.

#### B.3 — New template `templates/cockpit/login_insights.html`

Four sections in vertical order, each in a `.cockpit-panel` card:

1. **At-Risk Customers** (most actionable — show first)
2. **Most Engaged Customers** (positive list, balances the page)
3. **Platform Usage Heatmap** (the macro view)
4. **Dormant But Recently Bought** (re-engagement targets)

Each table sortable client-side using the existing `data-sortable` pattern.
Each table has an "Export CSV" button using the existing `/cockpit/api/export-csv`
endpoint pattern (or a new one if simpler).

Customer names link to their per-customer cockpit `/cockpit/<customer_code>` page.

#### B.4 — Add to navigation

Add a link to `/cockpit/login-insights` in the cockpit header navigation
(currently shows just "All Customers" / search). Insert "Login Insights" as a
sibling link, gated by `{% if has_permission('customers.use_cockpit') %}`.

---

## Out of Scope

- No new tables or migrations.
- No changes to `services/ftp_login_sync.py` or its scheduling.
- No changes to engagement score formula in `_compute_engagement_score`.
- No notifications, alerts, or scheduled reports based on login data.
- No tracking of failed login attempts (data not available in source).
- No tracking of pages viewed within sessions (data not available).
- No new permissions — reuses existing `customers.use_cockpit` and
  `customers.ask_claude`.
- No mobile-specific layouts — desktop only for Phase B fleet view; per-customer
  panel inherits cockpit's existing responsive behaviour.

---

## Implementation Notes

### Data quality caveats to handle

- **Sessions with no logout time**: many B2B users close browser without logging
  out. `last_logout_at` is NULL for these. Treat NULL logout as "session
  duration unknown" and exclude from the average — do NOT assume duration is the
  time until next login.
- **Sessions over 2 hours**: cap at 120 minutes before averaging. Real B2B sessions
  rarely exceed 30 minutes; longer values are almost always abandoned tabs.
- **Customers with < 3 logins in 90 days**: panel still renders but with
  `data_quality = "insufficient"`. Heatmap shows but is sparse; peak fields are
  None; marketing insight text reflects the limitation.
- **Time zone**: all timestamps in `magento_customer_login_log` are stored as
  `TIMESTAMPTZ`. Heatmap should bucket by **local time of the customer**
  (Europe/Athens) since that's when they're actually shopping. Use `AT TIME ZONE
  'Europe/Athens'` in the SQL extraction.
- **Empty result handling**: every panel function must return `None` or an empty
  dict cleanly when the customer has no login history at all — never raise an
  exception that breaks the cockpit. The `_safe()` wrapper provides backstop but
  prefer clean returns where possible.

### Performance considerations

The login log table will grow continuously. Add this index if it doesn't already
exist (additive — safe to run anytime):

```sql
CREATE INDEX IF NOT EXISTS idx_login_log_customer_time
  ON magento_customer_login_log (customer_code_365, last_login_at DESC);
```

The fleet-level queries may scan large amounts of data. Use LIMIT generously and
prefer materialised aggregates in temp tables over multiple separate queries.

### Logging

Each new function should log key counts at INFO level:

```python
logger.info(f"login_behaviour({customer_code}): {total_logins} logins, "
            f"peak_day={peak_day_name}, quality={data_quality}")
```

This makes it easy to spot data issues in the live system.

---

## Required Tests

Add to `tests/test_cockpit_login_behaviour.py`:

| # | Scenario | Expected |
|---|----------|----------|
| T1 | Customer with 50+ logins over 90 days | Returns full heatmap, peak fields, trend arrow, `data_quality = "good"` |
| T2 | Customer with 5 logins | Returns sparse heatmap, peak day/hour set, `data_quality = "limited"` |
| T3 | Customer with 2 logins | Returns minimal data, peak fields = None, `data_quality = "insufficient"` |
| T4 | Customer with NO login history | Returns None (panel hides) |
| T5 | All sessions have NULL logout | `avg_session_minutes = None` |
| T6 | Session with logout 5 hours after login | Capped to 120 min before averaging |
| T7 | Trend up (last_30d 2x prev_30d) | `trend_direction = "up"` |
| T8 | Trend down (last_30d < 70% of prev_30d) | `trend_direction = "down"` |
| T9 | No baseline (prev_30d = 0) | `trend_direction = None` |
| T10 | Heatmap respects Europe/Athens timezone | A login at 23:00 UTC in summer = 02:00 local = "evening" bucket |
| T11 | Fleet-level `most_engaged` query | Returns at most 20, ordered by login count DESC |
| T12 | Fleet-level `at_risk` excludes customers with low baseline | Customer with 3 prev → 0 last is NOT included; need prev > 5 |
| T13 | `dormant_with_sales` requires both criteria | Customer with no login but no invoices is NOT included |
| T14 | Login insights page renders for admin | HTTP 200, contains all 4 section headers |
| T15 | Login insights page denies user without permission | HTTP 403 |

---

## Closeout

When complete, provide:

1. Confirmation that the per-customer panel renders for at least 3 real customers
   in production with real login data. Screenshot or description.
2. Confirmation that the fleet-level page loads in under 5 seconds with the live
   data set.
3. All T1–T15 tests passing: `pytest -q tests/test_cockpit_login_behaviour.py`
4. Confirm no regression: `pytest -q tests/test_cockpit_ticket1.py
   tests/test_cockpit_ticket3.py` (existing cockpit tests must still pass).
5. Verify that "Ask Claude" advice for a customer with strong login pattern actually
   references the login data in the response narrative.
6. Add an entry to `ASSUMPTIONS_LOG.md` for any autonomous decisions (heatmap colour
   thresholds, exact Greek phrasing of marketing insights, etc.).

---

## Critical Constraints

- Do NOT modify the FTP login sync or change the schedule.
- Do NOT change the engagement score formula in `_compute_engagement_score()`.
- The per-customer panel must NOT break the cockpit if `_fetch_login_behaviour`
  fails — the existing `_safe()` wrapper pattern is mandatory.
- The `cockpit_enabled` feature flag must continue to gate the entire cockpit URL
  space — including the new `/cockpit/login-insights` page.
- The `customers.use_cockpit` permission must be required for both new endpoints.
- All timestamps must respect Europe/Athens timezone for bucketing — UTC bucketing
  would put 11 PM Greek logins into Wednesday instead of Tuesday.
- The fleet-level page query must use a LIMIT (max 500 customers per section) to
  prevent slow queries as data grows.
- Production login data must NOT be modified, exported, or sent to external APIs
  except as part of the existing Claude advice flow (which already has user opt-in
  via the `customers.ask_claude` permission).
