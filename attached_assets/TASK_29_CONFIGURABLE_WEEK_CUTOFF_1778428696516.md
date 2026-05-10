# Task #29 — Configurable Forecast Week Cutoff

## What & Why

Currently the forecast considers the most recent **completed Monday-to-Sunday week**
as its latest data point. This means data from Monday of the current week is invisible
until the following Monday's run — a 7-day delay even though by Friday afternoon most
of the working week's sales have already been invoiced.

For a B2B wholesaler operating Mon–Fri (with negligible Sat/Sun activity), this is
unnecessarily conservative. After Friday morning, the in-progress week's data is already
representative — it captures the bulk of the week's commercial activity.

This task makes the "completed week" boundary configurable. Operators set a weekday and
time at which the current in-progress week becomes treated as "complete enough to use."
By default: **Friday at 10:00 AM** (Europe/Athens).

The change is surgically narrow — only the `get_completed_week_cutoff()` helper in
`services/forecast/week_utils.py` is modified. Three services that call this helper
(`base_forecast_service`, `oos_demand_service`, `seasonality_service`) automatically
inherit the new behaviour without any change to their code.

Week aggregation itself stays Monday-to-Sunday — this task does NOT change how weeks
are bucketed or how `fact_sales_weekly_item` is built.

---

## Done Looks Like

- Two new settings exist in the `settings` table:
  - `forecast_week_rollover_weekday` — integer, 0=Mon … 6=Sun, default `4` (Friday)
  - `forecast_week_rollover_time` — string `HH:MM` 24-hour, default `10:00`
- A new "Forecast Week Rollover" section appears in the existing Forecast Workbench
  admin settings page (`/forecast/admin/settings`), with a weekday dropdown and a
  time picker, both validated server-side.
- `get_completed_week_cutoff()` in `services/forecast/week_utils.py` reads these two
  settings and returns:
  - The Monday of the **current week** if the rollover moment has been reached
    (so the in-progress week is treated as the latest completed week)
  - The Monday of the **previous week** if the rollover moment has not yet been
    reached (current behaviour preserved)
- All three callers (`base_forecast_service.py`, `oos_demand_service.py`,
  `seasonality_service.py`) inherit the new logic without code changes.
- The function is fully timezone-aware — uses Europe/Athens for evaluating the
  rollover, regardless of server timezone.
- Backwards compatibility: if either setting is missing or invalid, the function
  falls back to the **legacy behaviour** (Monday of current week, no rollover) so
  no existing forecast can be silently broken.
- Forecast results visibly change starting the configured rollover moment each week,
  and the change is observable in the Forecast Workbench (the "data through" line on
  the suppliers page reflects the new latest week).
- Pre-existing automated tests still pass; new tests cover the rollover logic at
  several timestamps.

---

## Scope — The Implementation

### S.1 — New settings keys

Add to `services/settings_defaults.py` in the `PHASE1_DEFAULTS` dict (in the section
near other forecast settings, after `forecast_buffer_stock_days`):

```python
"forecast_week_rollover_weekday": "4",   # 0=Mon, 4=Fri, 6=Sun
"forecast_week_rollover_time": "10:00",  # HH:MM 24h, Europe/Athens
```

The seeder in `services/permissions_seed.py` (or wherever defaults are written into
the settings table) does NOT need changes — the existing `Setting.get(... default)`
pattern handles missing rows.

### S.2 — Modify `services/forecast/week_utils.py`

Replace the existing function with the new logic. **Keep `monday_of()` unchanged** — it
is used elsewhere as a generic helper. Only `get_completed_week_cutoff()` changes.

```python
"""Week helpers for forecasting.

The completed-week cutoff defines the boundary between "history we use for
forecasting" and "data that is too recent / too incomplete to trust."

Historically the cutoff was the start of the current calendar week (Monday).
This means Monday morning's sales were not visible to the forecast until the
following Monday — a 7-day blackout.

For B2B operations with weekday-only delivery cycles, this is too conservative.
Most of the week's sales have already been invoiced by Friday morning. The
configurable rollover lets operators advance the cutoff at a chosen weekday +
time, treating the in-progress week as "complete" once the bulk of its sales
have arrived.

Setting keys (in the `settings` table):
  forecast_week_rollover_weekday : int 0..6 (0=Mon, 4=Fri, 6=Sun). Default 4.
  forecast_week_rollover_time    : string "HH:MM" 24h. Default "10:00".

All time evaluation is in Europe/Athens regardless of server timezone, so the
cutoff is stable across server deployments and DST changes.
"""
from datetime import date, datetime, time, timedelta
import logging

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ATHENS_TZ = ZoneInfo("Europe/Athens")

DEFAULT_ROLLOVER_WEEKDAY = 4   # Friday
DEFAULT_ROLLOVER_TIME = time(10, 0)


def monday_of(d: date) -> date:
    """Get the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _read_rollover_settings():
    """Read the two settings; return (weekday_int, time_obj) or (None, None) on failure.

    A failure (DB unreachable, invalid value, missing keys) returns (None, None)
    so the caller can fall back to legacy behaviour. NEVER raises.
    """
    try:
        from app import db
        from models import Setting

        wd_str = Setting.get(db.session, "forecast_week_rollover_weekday", "4")
        tm_str = Setting.get(db.session, "forecast_week_rollover_time", "10:00")

        wd = int(wd_str)
        if not (0 <= wd <= 6):
            return None, None

        h, m = tm_str.split(":")
        tm = time(int(h), int(m))
        return wd, tm
    except Exception as e:
        logger.warning("week_utils: settings read failed (%s); using legacy cutoff", e)
        return None, None


def get_completed_week_cutoff(now: datetime = None) -> date:
    """
    Return the Monday at the start of the latest 'completed-enough' week.

    Logic:
      * Compute the Monday of the current week ("this_monday").
      * If now-in-Athens has reached the configured rollover moment within
        this week, return this_monday — meaning the current in-progress
        week is now treated as the latest data we use.
      * Otherwise return last_monday (this_monday - 7 days) — the legacy
        behaviour where only fully-completed Mon-Sun weeks are used.

    All forecasting SQL filters week_start < cutoff, so:
      Returns this_monday  → forecast includes the current week's rows
      Returns last_monday  → forecast stops at last Sunday

    The `now` parameter is for testability. Production calls leave it None
    and the function uses Europe/Athens current time.
    """
    if now is None:
        now = datetime.now(tz=ATHENS_TZ)
    elif now.tzinfo is None:
        # Treat naive as Athens local
        now = now.replace(tzinfo=ATHENS_TZ)
    else:
        now = now.astimezone(ATHENS_TZ)

    today = now.date()
    this_monday = monday_of(today)
    last_monday = this_monday - timedelta(days=7)

    rollover_wd, rollover_tm = _read_rollover_settings()
    if rollover_wd is None or rollover_tm is None:
        # Settings missing or invalid — preserve legacy behaviour exactly
        return this_monday

    # The rollover moment for the *current week* is:
    #   this_monday + rollover_wd days, at rollover_tm
    rollover_date = this_monday + timedelta(days=rollover_wd)
    rollover_dt = datetime.combine(rollover_date, rollover_tm, tzinfo=ATHENS_TZ)

    if now >= rollover_dt:
        # Rollover already happened this week → include this week
        return this_monday + timedelta(days=7)
    else:
        # Rollover hasn't happened yet → only completed weeks
        return this_monday
```

**IMPORTANT — note the off-by-one detail:** the SQL filter is
`week_start < completed_week_cutoff`. So:

- To make the **current week visible to the forecast**, the cutoff must be
  `this_monday + 7 days` (next Monday) — because the current week's `week_start`
  IS this_monday, and `this_monday < this_monday + 7d` is true.
- To make only completed weeks visible (current behaviour), the cutoff must be
  `this_monday` itself — because `this_monday < this_monday` is false (the row
  is excluded).

The code above implements this correctly. Verify carefully when reviewing.

### S.3 — Add the admin UI

In `templates/forecast_workbench/admin_settings.html`, add a new card **after** the
existing "Forecast & Ordering Parameters" card:

```html
{% if current_user.role == 'admin' %}
<div class="card mb-4">
  <div class="card-header bg-light">
    <strong>Forecast Week Rollover</strong>
  </div>
  <div class="card-body">
    <p class="text-muted small mb-3">
      Controls when the in-progress week is treated as "complete enough" to be
      included in the forecast. Until the rollover moment, the forecast uses
      only fully-completed Monday–Sunday weeks. After the rollover moment, the
      current in-progress week becomes the latest data point.
    </p>

    <div class="row">
      <div class="col-md-4">
        <label for="rollover_weekday">Rollover Weekday</label>
        <select name="forecast_week_rollover_weekday"
                id="rollover_weekday" class="form-control">
          <option value="0">Monday</option>
          <option value="1">Tuesday</option>
          <option value="2">Wednesday</option>
          <option value="3">Thursday</option>
          <option value="4" selected>Friday (default)</option>
          <option value="5">Saturday</option>
          <option value="6">Sunday</option>
        </select>
        <!-- selected attribute set in template logic to current value -->
      </div>
      <div class="col-md-4">
        <label for="rollover_time">Rollover Time (Athens)</label>
        <input type="time" name="forecast_week_rollover_time"
               id="rollover_time" class="form-control"
               value="10:00" step="900">
        <!-- step=900 = 15-minute increments; value set from current setting -->
      </div>
    </div>

    <p class="text-muted small mt-3 mb-0">
      <strong>Caveat:</strong> the in-progress week's quantity will be slightly
      lower than a full Mon–Sun week because Saturday and Sunday have not happened
      yet. For high-volume Mon–Fri operations this is negligible. The week
      aggregation itself remains Monday–Sunday — this only changes whether the
      current week is visible to the forecast.
    </p>
  </div>
</div>
{% endif %}
```

The form already POSTs to `admin_settings_save` in
`blueprints/forecast_workbench.py`. Update that handler to:

1. Read the two new fields from `request.form`
2. Validate: weekday integer 0..6, time matches `^\d{2}:\d{2}$` and is parseable
3. On invalid input, flash an error and redirect back without saving
4. On valid input, write via `save_setting()` (or whatever the existing pattern uses)
5. Add an `ActivityLog` row for the change (same pattern as Task #27 Feature Flags UI)

### S.4 — Add a "data through" line to the suppliers page

In `templates/forecast_workbench/suppliers.html`, add a small subtitle under the page
header that displays which week is currently the latest in the forecast:

```
Forecast considers sales through:  Sun May 10, 2026
                                   (rollover at Friday 10:00 — current week included)
```

Use `get_completed_week_cutoff()` to compute the date — the week shown is
`(cutoff - 1 day)` since cutoff is the exclusive boundary.

This is the operator's primary feedback that the rollover is working as expected.

### S.5 — Out of scope

- No change to `fact_sales_weekly_item` structure or aggregation. Weeks remain
  Monday-to-Sunday. PostgreSQL `date_trunc('week', ...)` is unchanged.
- No change to `seasonality_service.py` — it inherits the new cutoff via
  `get_completed_week_cutoff()` automatically.
- No change to the OOS detection logic — it inherits the new cutoff automatically.
- No change to the daily 5 AM forecast schedule.
- No new feature flag — this is a configuration setting, not a flag. Old behaviour
  is preserved purely by the default values matching the historical case (and via
  graceful fallback if settings are missing).
- No retroactive recomputation. The next forecast run after the setting change
  picks up the new cutoff.

---

## Behavioural Examples

Once deployed with default settings (Friday 10:00 AM):

| Run timestamp (Athens) | Cutoff returned | Latest week in forecast | Behaviour |
|------------------------|-----------------|-------------------------|-----------|
| Mon May 4 — 05:00 | Mon May 4 | Apr 27 → May 3 | Legacy — current week not yet rolled |
| Tue May 5 — 14:00 | Mon May 4 | Apr 27 → May 3 | Legacy |
| Fri May 8 — 09:59 | Mon May 4 | Apr 27 → May 3 | Just before rollover |
| **Fri May 8 — 10:00** | **Mon May 11** | **May 4 → May 10** | **Rollover crossed — current week included** |
| Fri May 8 — 16:00 | Mon May 11 | May 4 → May 10 | Same as above |
| Sat May 9 — 09:00 | Mon May 11 | May 4 → May 10 | Same |
| Sun May 10 — 22:00 | Mon May 11 | May 4 → May 10 | Same |
| Mon May 11 — 05:00 | Mon May 11 | May 4 → May 10 | Standard new week starts |

Note: the row "Mon May 11 — 05:00" produces the same cutoff as the Friday rollover
case — but for a different reason. After Sunday midnight `this_monday` itself
advances to May 11, so the formula `this_monday + 7d` would advance again — but the
rollover check sees `now < new_rollover_dt` (Friday May 15 hasn't happened yet) and
returns `this_monday = May 11`. Correct behaviour: current week (May 11–17) is now
the in-progress one, awaiting its rollover.

### Stability caveat to flag in code comments

After the rollover, sales for the current week continue to accumulate:
- A run at Fri 10:30 AM sees Monday–Friday morning sales
- A run at Fri 5:00 PM sees Monday–Friday late-day sales (more)
- A run at Sat AM sees the same plus any Friday-evening sales

Within a 5-day window (Fri 10 AM → Mon 5 AM), forecast results may vary slightly
across re-runs as more invoices for the in-progress week arrive. The daily 5 AM
schedule means in practice this is observed only on Sat-morning vs. Sun-morning runs,
both of which see near-zero new data — so re-run instability is negligible.

Document this in the function docstring so future maintainers understand it.

---

## Required Tests

Create `tests/test_week_cutoff_rollover.py`:

| # | Scenario | Expected `get_completed_week_cutoff(now=...)` |
|---|----------|----------------------------------------------|
| T1 | Mon at 05:00 (start of week, no rollover yet) | this_monday |
| T2 | Wed at 14:00 (mid-week, no rollover) | this_monday |
| T3 | Fri at 09:59 (one minute before rollover) | this_monday |
| T4 | Fri at 10:00 (rollover moment exactly) | this_monday + 7d |
| T5 | Fri at 10:01 (just after rollover) | this_monday + 7d |
| T6 | Sun at 23:59 (week's last second) | this_monday + 7d |
| T7 | Settings have `weekday=2, time=08:00` (Wed 8 AM) | Wed 08:01 → this_monday + 7d |
| T8 | Settings have `weekday=2, time=08:00`; called Wed 07:59 | this_monday |
| T9 | Settings missing entirely | Falls back to this_monday (legacy) |
| T10 | Settings have invalid weekday `99` | Falls back to this_monday (legacy) |
| T11 | Settings have invalid time `"25:00"` | Falls back to this_monday (legacy) |
| T12 | DB completely unavailable (mock raises) | Falls back to this_monday (legacy) |
| T13 | Setting updates from default to "Mon 06:00" | Mon 06:00 → this_monday + 7d |
| T14 | Time zone correctness: 09:30 UTC in summer = 12:30 Athens (DST), should be after a 10:00 Athens cutoff | this_monday + 7d |

Also add to `tests/test_admin_settings.py` (or create new file `test_forecast_admin_rollover.py`):

| # | Scenario | Expected |
|---|----------|----------|
| F1 | POST valid weekday + time | Settings saved; flash success; activity_log entry written |
| F2 | POST invalid weekday `7` | Setting NOT saved; flash error; redirect |
| F3 | POST invalid time `"99:99"` | Setting NOT saved; flash error |
| F4 | GET admin settings as warehouse_manager | Section NOT visible |
| F5 | GET admin settings as admin | Section visible with current values pre-filled |

Existing regression tests must continue to pass:

```
pytest -q tests/test_override_ordering_pipeline.py
pytest -q tests/test_classification.py
pytest -q tests/test_seasonality.py
pytest -q tests/test_oos_demand.py
```

---

## Closeout

When complete, provide:

1. Confirmation that all T1–T14 + F1–F5 tests pass
2. Confirmation that all existing forecast tests still pass (no regressions)
3. Manual verification: change the rollover setting in development (e.g. set
   weekday to 2 and time to 09:00), wait until the moment, then trigger a
   forecast run and confirm the suppliers page shows the new "data through"
   week
4. Screenshot of the new Forecast Week Rollover admin section
5. Append assumption entries to `ASSUMPTIONS_LOG.md` for autonomous decisions
   (UI placement, time picker step interval, etc.)

---

## Critical Constraints

- The `monday_of()` helper must NOT be modified — it's used elsewhere as a
  generic helper.
- Default values must match the new setting (weekday=4, time=10:00) — but if
  settings are missing or invalid, the function MUST fall back to legacy behaviour
  (`monday_of(today)`) so existing forecasts don't silently break.
- Time evaluation must use Europe/Athens timezone explicitly. Server timezone
  must NOT influence the result. Use `zoneinfo.ZoneInfo("Europe/Athens")`.
- Week aggregation in `fact_sales_weekly_item` must NOT change. Weeks remain
  Monday-to-Sunday.
- Production setting values must NOT be changed as part of this development task.
  Build, test, and verify in development only.
- The settings UI must be visible only to `admin` role (gated with
  `{% if current_user.role == 'admin' %}`).
- All settings writes go through `save_setting()` and produce an `ActivityLog`
  row — same pattern as Task #27.
- The ActivityLog `details` JSON for these changes should include the old and
  new values, so audit trail shows the full transition.
