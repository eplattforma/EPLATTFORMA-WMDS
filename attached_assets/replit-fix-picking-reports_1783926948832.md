# Replit instructions — fix the picking reports

Goal: make picking reports read from the accurate per-pick data (`item_time_tracking`), use medians instead of wall-clock averages, and speak plain language. Two new views + one data fix. **Nothing here deletes or changes your existing tables.**

---

## Option A — paste this to the Replit Agent

> In our Postgres database, our picking reports are wrong because `pbi_fact_picking` measures a pick as `picking_complete_time − status_updated_at` (order wall-clock, which includes breaks and interruptions) and never uses our accurate per-pick table `item_time_tracking`.
>
> Please do the following, without altering existing tables or the existing `pbi_fact_picking` view:
> 1. Create a view `vw_pick_detail` — one clean row per pick from `item_time_tracking`, excluding test user `administrator`, skipped picks, and zero/blank times. Parse the shelf `level` letter from the `location` string. Add two flags: `met_target` (total time ≤ expected time) and `long_gap` (walking time > 60s).
> 2. Create a view `vw_picker_daily` — per picker per day: item count, units, **median** seconds per pick, % of picks meeting target, walking share %, and a count of long gaps to watch.
> 3. Backfill the `level` column on `item_time_tracking` for existing rows from `location`, and make sure new rows also save `level` going forward.
> 4. Point our picking dashboard / Power BI at `vw_picker_daily` (and `vw_pick_detail` for drill-down).
>
> Use the exact SQL I provide below.

Then paste the SQL from Option B.

---

## Option B — the SQL (run in the Replit Database pane or as a migration)

**1. Clean per-pick view (the foundation)**
```sql
CREATE OR REPLACE VIEW vw_pick_detail AS
SELECT
  item_started::date                                  AS pick_date,
  picker_username                                     AS picker,
  invoice_no,
  corridor,
  substring(location from '\d{2}-\d{2}-([A-Z])')      AS level,
  unit_type,
  quantity_picked                                     AS units,
  round(walking_time::numeric, 1)                     AS walking_seconds,
  round(picking_time::numeric, 1)                     AS picking_seconds,
  round(total_item_time::numeric, 1)                  AS total_seconds,
  round(expected_time::numeric, 1)                    AS expected_seconds,
  (total_item_time <= expected_time)                  AS met_target,
  (walking_time > 60)                                 AS long_gap
FROM item_time_tracking
WHERE picker_username <> 'administrator'
  AND was_skipped = false
  AND total_item_time > 0
  AND expected_time  > 0;
```

**2. Per-picker daily summary (what the report shows)**
```sql
CREATE OR REPLACE VIEW vw_picker_daily AS
SELECT
  pick_date,
  picker,
  count(*)                                            AS items_picked,
  sum(units)                                          AS units_picked,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_seconds)::numeric, 1)
                                                      AS median_seconds_per_pick,
  round(100.0 * avg(met_target::int), 0)              AS pct_meeting_target,
  round(100.0 * sum(walking_seconds) / nullif(sum(total_seconds), 0), 0)
                                                      AS walking_share_pct,
  sum(long_gap::int)                                  AS long_gaps_to_watch
FROM vw_pick_detail
GROUP BY pick_date, picker
ORDER BY pick_date DESC, items_picked DESC;
```

**3. Backfill the shelf level on old rows (run once)**
```sql
UPDATE item_time_tracking
SET level = substring(location from '\d{2}-\d{2}-([A-Z])')
WHERE level IS NULL
  AND location ~ '\d{2}-\d{2}-[A-Z]';
```

**Check it worked**
```sql
SELECT * FROM vw_picker_daily WHERE pick_date > current_date - 14;
```

---

## The one code change (so new data stays correct)

The screen/endpoint that writes a row into `item_time_tracking` when a pick is confirmed should also fill the `level` field. Right now it's left blank. In that write path, set:

```
level = <corridor-bay-LEVEL parsed from the location>   e.g. regex  \d{2}-\d{2}-([A-Z])
```

That's the only application code touched. Everything else is database views.

---

## Reporting rules to lock in (so it can't drift back)

- **Show median, not average.** `median_seconds_per_pick` and `pct_meeting_target` are the honest numbers. Averages get wrecked by a few interrupted picks.
- **`long_gaps_to_watch` is a monitor number, not a score.** Just watch it — no picker has to explain anything, no reason entry.
- **Idle = dedicated pickers only.** Add a setting listing them (e.g. key `dedicated_pickers`, value `["Arslan","picker1"]`) and show idle only for those. Others do mixed jobs, so their gaps aren't idle.
- **Leave `pbi_fact_picking` as-is** for order status / shipping / delivery — just stop using it for *picker speed*. Picker speed now comes from `vw_picker_daily`.

---

## Plain-language columns the report should display
Picker · Items picked · Units · **Median time per pick** · **% meeting target** · Walking share · Long gaps (to watch) — with a date filter. No `status_updated_at`, no ratios to decode.
