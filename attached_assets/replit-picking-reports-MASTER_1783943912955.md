# Fix the picking reports — master implementation guide

Single source of truth. Replaces `replit-fix-picking-reports.md` and `replit-reports-ui-change.md`. Verified against the live app (`ep-picking-bro.replit.app`) and live database.

## What's wrong (verified on screen + in data)
On **Reports → Time Reports** (`/shift/reports`):
1. **Items Picked / Items per Hour / Performance = 0 for everyone**, although the data exists (Arslan picked 233 items on 10 Jul). The page isn't joined to `item_time_tracking`.
2. **Hours & Idle are inflated.** Shifts auto-close at a fixed time (15:00) long after the picker's last pick — e.g. Arslan's last pick 10 Jul was 12:41 but the shift ran to 15:00, adding ~2h20 of phantom time. And "Idle" counts all non-picking work (packing, loading, waiting, breaks) as idle, so 519 min "idle" on a 233-pick day is meaningless.
3. **Breaks column** is always empty; **picker filter** is full of test accounts.
4. **Reports → Time Analysis** (`/admin/time_analysis`) hangs 45s+ and only duplicates this page.

Fix = wire the real data, fix the shift-close logic, define idle honestly, remove the dead extras.

---

## Part 1 — Database views (run in Replit Database pane)

**1a. Mark dedicated pickers** (idle shown only for these)
```sql
INSERT INTO settings (key, value)
VALUES ('dedicated_pickers', '["Arslan","picker1"]')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

**1b. Clean per-pick view**
```sql
CREATE OR REPLACE VIEW vw_pick_detail AS
SELECT
  item_started::date                             AS pick_date,
  picker_username                                AS picker,
  invoice_no,
  corridor,
  substring(location from '\d{2}-\d{2}-([A-Z])') AS level,
  unit_type,
  quantity_picked                                AS units,
  round(walking_time::numeric,1)                 AS walking_seconds,
  round(picking_time::numeric,1)                 AS picking_seconds,
  round(total_item_time::numeric,1)              AS total_seconds,
  round(expected_time::numeric,1)                AS expected_seconds,
  (total_item_time <= expected_time)             AS met_target,
  (walking_time > 60)                            AS long_gap
FROM item_time_tracking
WHERE picker_username <> 'administrator'
  AND was_skipped = false
  AND total_item_time > 0
  AND expected_time  > 0;
```

**1c. Per-picker per-day summary (feeds the report)**
```sql
CREATE OR REPLACE VIEW vw_picker_daily AS
SELECT
  pick_date,
  picker,
  count(*)                                        AS items_picked,
  sum(units)                                      AS units_picked,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_seconds)::numeric,1) AS median_seconds_per_pick,
  round(100.0 * avg(met_target::int),0)           AS pct_meeting_target,
  round(sum(total_seconds)/3600.0,2)              AS active_pick_hours,
  round(100.0 * sum(walking_seconds)/nullif(sum(total_seconds),0),0) AS walking_share_pct,
  sum(long_gap::int)                              AS long_gaps_to_watch
FROM vw_pick_detail
GROUP BY pick_date, picker
ORDER BY pick_date DESC, items_picked DESC;
```

**1d. Backfill shelf level on old rows (run once)**
```sql
UPDATE item_time_tracking
SET level = substring(location from '\d{2}-\d{2}-([A-Z])')
WHERE level IS NULL AND location ~ '\d{2}-\d{2}-[A-Z]';
```

Check: `SELECT * FROM vw_picker_daily WHERE pick_date > current_date - 14;` should now show non-zero items.

---

## Part 2 — Fix the "Time Reports" page (`/shift/reports`)

**Fix 2a — wire Items & Performance to real data.**
For each shift row, get the picker's picks for that date from `item_time_tracking` (or `vw_picker_daily` joined on picker + date):
- **Items Picked** = `items_picked`
- **Items per Hour** = `items_picked / active_pick_hours` (use active picking hours, not padded shift hours)
- **Performance** = `pct_meeting_target` (% of picks at or under the estimate) — replaces the broken 0%.

**Fix 2b — stop the auto-close padding.**
The auto-close job currently sets check-out to a fixed clock time. Change it to the picker's **last real activity** that day:
```
check_out_time = MAX(item_completed) for that picker/date   (fallback: last scan/action time)
total_duration_minutes = check_out_time − check_in_time
```
So a picker who stops at 12:41 gets a shift ending ~12:41, not 15:00. No more phantom hours.

**Fix 2c — define idle honestly.**
- Compute idle only **within the worked window** (check-in → last activity), never into the padded tail.
- Show idle **only for dedicated pickers** (from `dedicated_pickers`). For others, show "—" or "mixed role", because their gaps are other jobs, not idle.
- Rename the column **"Gaps between picks"** (or split: short gaps vs long gaps to watch). Don't call non-picking work "idle".

**Fix 2d — remove clutter.**
- Delete the **Breaks** column (never populated).
- Filter the **Picker dropdown** to real pickers only (drop administrator, admin, test_shipp, picker2, and non-picking staff).
- Fix the "Performance Insights" box — once items are wired it stops saying "0.0 items/hour" and "Total items: 0".

---

## Part 3 — Remove "Time Analysis"
It redirects to `/shift/reports`, hangs on load, and its old engine (`time_tracking_alerts`) is dead (`time_alerts_enabled = false`). Remove the **Reports → Time Analysis** menu item and the `/admin/time_analysis` route. Leave the `time_tracking_alerts` table in the DB. (Re-enabling alerts is a separate future task, not a report.)

---

## Part 4 — One code change (keep new data correct)
Where a confirmed pick is written into `item_time_tracking`, also set `level` from the location: regex `\d{2}-\d{2}-([A-Z])`. It's blank today, which is why height/ladder cost can't be measured.

---

## Paste this to the Replit Agent
> Our Time Reports page (`/shift/reports`) shows Items Picked and Performance as 0 for all pickers even though `item_time_tracking` has the data, and its Idle/Hours are inflated because shifts auto-close at a fixed time and all non-picking work is counted as idle. Fix it without deleting existing tables:
> 1. Run the SQL in Part 1 (creates `vw_pick_detail`, `vw_picker_daily`, `dedicated_pickers` setting, backfills `level`).
> 2. On `/shift/reports`: wire Items Picked, Items/Hour and Performance from `vw_picker_daily` (Performance = % meeting target); change the shift auto-close to end at the picker's last actual pick time instead of a fixed clock time; compute Idle only within the worked window and only for dedicated pickers; remove the empty Breaks column; filter the picker dropdown to real pickers.
> 3. Remove the `Reports → Time Analysis` menu item and `/admin/time_analysis` route (it only redirects here and hangs).
> 4. Where picks are written to `item_time_tracking`, also save `level` parsed from location (`\d{2}-\d{2}-([A-Z])`).
> Then show me `/shift/reports` with real non-zero numbers and confirm order-status/delivery screens still work.

---

## After it's done — confirm
- Time Reports shows **real** Items Picked and a sensible % meeting target (not 0).
- A picker who left at 12:41 shows a shift ending ~12:41, not 15:00.
- Idle appears only for Arslan / picker1, and looks reasonable (not 500+ min).
- No Breaks column; picker dropdown is clean; no Time Analysis menu item.
- Order status / delivery screens still load (their tables were untouched).
