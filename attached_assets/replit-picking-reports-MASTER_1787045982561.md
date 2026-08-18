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

**1e. Correct idle view — time when NO order is open** (per your rule: an order ends at packing; anything until the next order starts is idle). Handles batch picking, where orders overlap, by merging open-order intervals and measuring the gaps. Bounded by the last packing of the day, so auto-close padding never enters it.
```sql
CREATE OR REPLACE VIEW vw_picker_idle_daily AS
WITH iv AS (
  SELECT t.picker_username, t.item_started::date AS d, t.invoice_no,
         min(t.item_started)                               AS s,
         coalesce(i.packing_complete_time, max(t.item_completed)) AS e
  FROM item_time_tracking t
  JOIN invoices i ON i.invoice_no = t.invoice_no
  WHERE t.picker_username <> 'administrator' AND t.was_skipped = false
  GROUP BY t.picker_username, t.item_started::date, t.invoice_no, i.packing_complete_time),
o AS (SELECT picker_username, d, s, e,
        max(e) OVER (PARTITION BY picker_username, d ORDER BY s ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) pm FROM iv),
g AS (SELECT picker_username, d, s, e,
        sum(CASE WHEN pm IS NULL OR s > pm THEN 1 ELSE 0 END) OVER (PARTITION BY picker_username, d ORDER BY s) grp FROM o),
isl AS (SELECT picker_username, d, min(s) a, max(e) b FROM g GROUP BY picker_username, d, grp)
SELECT picker_username AS picker, d AS work_date,
  round((EXTRACT(epoch FROM (max(b)-min(a)))/60.0)::numeric,0)                                              AS span_min,
  round((sum(EXTRACT(epoch FROM (b-a)))/60.0)::numeric,0)                                                   AS in_order_min,
  round(((EXTRACT(epoch FROM (max(b)-min(a))) - sum(EXTRACT(epoch FROM (b-a))))/60.0)::numeric,0)           AS idle_between_orders_min
FROM isl
GROUP BY picker_username, d;
```

Check: `SELECT * FROM vw_picker_daily WHERE pick_date > current_date - 14;` should now show non-zero items, and `SELECT * FROM vw_picker_idle_daily WHERE work_date > current_date - 14;` shows real idle (e.g. Arslan 9 Jul ≈ 209 min, vs the old system's 341 min).

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

> **Where idle is accounted (important).** Idle officially lives at the **shift** level in `idle_periods` (15+ min gaps, auto-detected, linked to the shift — never to an order). But the *same* gap also inflates the **walking time of the next pick line** (walking = previous confirm → next "arrived" tap), so it silently lands on that order too. That double-representation is why raw order actuals look 2–3× over estimate. The `vw_order_performance` view above strips it out (walking capped at 2 min/line) so the pace is fair, and shows the stripped time as "Interruptions" so it's still visible.

**Fix 2c — define idle correctly (order-boundary rule).**
An order ends when packing is complete; the time from there until the next order starts is idle. Use **`vw_picker_idle_daily`** (Part 1e) for the Idle column — it implements exactly this and correctly handles batch picking (overlapping orders) by measuring time when *no* order is open.
- Stop using `idle_periods` (arbitrary 15-min gaps) as the Idle source — it over-counts and mixes in packing/other work.
- Show idle **only for dedicated pickers** (from `dedicated_pickers`); for others show "—/mixed role".
- Because this idle is bounded by the last packing of the day, the auto-close padding (Fix 2b) can no longer inflate it.
- Optional: also surface `in_order_min` (time inside open orders) vs `idle_between_orders_min` so the split is visible.

**Fix 2d — remove clutter.**
- Delete the **Breaks** column (never populated).
- Filter the **Picker dropdown** to real pickers only (drop administrator, admin, test_shipp, picker2, and non-picking staff).
- Fix the "Performance Insights" box — once items are wired it stops saying "0.0 items/hour" and "Total items: 0".

**Fix 2e — add a per-order performance view (picker vs estimate per order).**
This is the "how does the picker perform against the estimate, order by order" view. It separates real work from interruptions so the numbers are conclusive.

```sql
CREATE OR REPLACE VIEW vw_order_performance AS
SELECT
  invoice_no,
  picker_username                                             AS picker,
  min(item_started)::date                                     AS pick_date,
  count(*)                                                    AS lines,
  sum(quantity_picked)                                        AS units,
  round((sum(expected_time)/60.0)::numeric,1)                 AS estimated_min,
  round((sum(picking_time + LEAST(walking_time,120))/60.0)::numeric,1) AS working_min,
  round((sum(GREATEST(walking_time-120,0))/60.0)::numeric,1)  AS interruption_min,
  round((sum(total_item_time)/60.0)::numeric,1)               AS elapsed_min,
  round((100.0*sum(expected_time)/nullif(sum(picking_time + LEAST(walking_time,120)),0))::numeric,0) AS pace_vs_estimate_pct
FROM item_time_tracking
WHERE picker_username <> 'administrator'
  AND was_skipped = false
  AND total_item_time > 0
  AND expected_time  > 0
GROUP BY invoice_no, picker_username;
```

Columns to display, per order: **Estimate (min) · Working time (min) · Interruptions (min) · Pace vs estimate %**.
- **Working time** = hands-on picking (walking capped at 2 min/line, so a single long gap doesn't wreck the order).
- **Interruptions** = the time lost to long gaps on that order (the "idle" that landed here — see idle note below).
- **Pace vs estimate %** = 100 means exactly on the corridor estimate; above 100 = beat it. This is the fair judgement of the picker.

**Sorting for readability (your point).** Make the report *grouped by picker, then sorted by `pick_date` descending* by default, and make the columns click-sortable — most usefully by **Pace vs estimate** (find who/what is genuinely slow) and by **Interruptions** (find where time is being lost). The current Recent-Shifts list is unsorted and mixes pickers, which is why it's hard to conclude; a picker + date grouping with a total row per picker fixes that.

---

## Part 3 — Remove "Time Analysis"
It redirects to `/shift/reports`, hangs on load, and its old engine (`time_tracking_alerts`) is dead (`time_alerts_enabled = false`). Remove the **Reports → Time Analysis** menu item and the `/admin/time_analysis` route. Leave the `time_tracking_alerts` table in the DB. (Re-enabling alerts is a separate future task, not a report.)

---

## Part 4 — One code change (keep new data correct)
Where a confirmed pick is written into `item_time_tracking`, also set `level` from the location: regex `\d{2}-\d{2}-([A-Z])`. It's blank today, which is why height/ladder cost can't be measured.

---

## Part 5 — Occupancy & idle reporting (the "are they kept busy" report)

*Enabled by the process change: pickers now tap **Packing complete only when packing is actually finished**, so `packing_complete_time` is a trustworthy end-of-work marker. Data check that prompted this: recorded packing time was a median of only 40s and a 58-line order was closed in 24s — packing was being done after the tap, i.e. in what looked like idle. Once the tap is accurate, the views below are correct.*

**Concept.** An order occupies the picker from its first pick to `packing_complete_time`. Orders overlap (batch picking), so "occupied" = time when **any** order is open, and **idle = time in the working span when no order is open.** Occupancy % = occupied ÷ span.

**5a. Occupancy per picker per day**
```sql
CREATE OR REPLACE VIEW vw_picker_occupancy_daily AS
WITH iv AS (
  SELECT t.picker_username, t.item_started::date AS d, t.invoice_no,
         min(t.item_started) AS s,
         coalesce(i.packing_complete_time, max(t.item_completed)) AS e
  FROM item_time_tracking t JOIN invoices i ON i.invoice_no = t.invoice_no
  WHERE t.picker_username <> 'administrator' AND t.was_skipped = false
  GROUP BY t.picker_username, t.item_started::date, t.invoice_no, i.packing_complete_time),
o AS (SELECT picker_username,d,s,e,
        max(e) OVER (PARTITION BY picker_username,d ORDER BY s ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) pm FROM iv),
g AS (SELECT picker_username,d,s,e,
        sum(CASE WHEN pm IS NULL OR s>pm THEN 1 ELSE 0 END) OVER (PARTITION BY picker_username,d ORDER BY s) grp FROM o),
isl AS (SELECT picker_username,d, min(s) a, max(e) b FROM g GROUP BY picker_username,d,grp),
gaps AS (SELECT picker_username,d,a,b,
        EXTRACT(epoch FROM (a - lag(b) OVER (PARTITION BY picker_username,d ORDER BY a)))/60.0 AS gap FROM isl)
SELECT picker_username AS picker, d AS work_date,
  min(a)::time AS first_order, max(b)::time AS last_order_end,
  round((EXTRACT(epoch FROM (max(b)-min(a)))/60.0)::numeric,0)                                     AS span_min,
  round((sum(EXTRACT(epoch FROM (b-a)))/60.0)::numeric,0)                                          AS occupied_min,
  round(((EXTRACT(epoch FROM (max(b)-min(a))) - sum(EXTRACT(epoch FROM (b-a))))/60.0)::numeric,0)  AS idle_min,
  round((100.0*sum(EXTRACT(epoch FROM (b-a)))/nullif(EXTRACT(epoch FROM (max(b)-min(a))),0))::numeric,0) AS occupancy_pct,
  count(*) FILTER (WHERE gap > 1)      AS idle_gaps,
  round(max(gap)::numeric,0)           AS longest_idle_min
FROM gaps GROUP BY picker_username, d;
```

**5b. Idle-gap detail (the "when and how long" — powers investigation and the timeline)**
```sql
CREATE OR REPLACE VIEW vw_idle_gaps AS
WITH iv AS (
  SELECT t.picker_username, t.item_started::date AS d, t.invoice_no,
         min(t.item_started) AS s,
         coalesce(i.packing_complete_time, max(t.item_completed)) AS e
  FROM item_time_tracking t JOIN invoices i ON i.invoice_no = t.invoice_no
  WHERE t.picker_username <> 'administrator' AND t.was_skipped = false
  GROUP BY t.picker_username, t.item_started::date, t.invoice_no, i.packing_complete_time),
o AS (SELECT picker_username,d,s,e,
        max(e) OVER (PARTITION BY picker_username,d ORDER BY s ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) pm FROM iv),
g AS (SELECT picker_username,d,s,e,
        sum(CASE WHEN pm IS NULL OR s>pm THEN 1 ELSE 0 END) OVER (PARTITION BY picker_username,d ORDER BY s) grp FROM o),
isl AS (SELECT picker_username,d, min(s) a, max(e) b FROM g GROUP BY picker_username,d,grp),
gg AS (SELECT picker_username,d,a AS gap_end,
        lag(b) OVER (PARTITION BY picker_username,d ORDER BY a) AS gap_start FROM isl)
SELECT picker_username AS picker, d AS work_date,
  gap_start::time AS idle_from, gap_end::time AS idle_to,
  round((EXTRACT(epoch FROM (gap_end - gap_start))/60.0)::numeric,1) AS idle_min,
  (EXTRACT(epoch FROM (gap_end - gap_start))/60.0 >= 20) AS long_block
FROM gg
WHERE gap_start IS NOT NULL AND gap_end > gap_start;
```

**5c. Packing now reported (and adoption monitored).** Update `vw_order_performance` to include packing:
```sql
CREATE OR REPLACE VIEW vw_order_performance AS
SELECT
  t.invoice_no,
  t.picker_username                                        AS picker,
  min(t.item_started)::date                                AS pick_date,
  count(*)                                                 AS lines,
  sum(t.quantity_picked)                                   AS units,
  round((sum(t.expected_time)/60.0)::numeric,1)            AS pick_estimate_min,
  round((sum(t.picking_time + LEAST(t.walking_time,120))/60.0)::numeric,1) AS pick_working_min,
  round((GREATEST(EXTRACT(epoch FROM (i.packing_complete_time - i.picking_complete_time)),0)/60.0)::numeric,1) AS packing_min,
  round(((45 + 3*count(*))/60.0)::numeric,1)               AS packing_estimate_min,
  (coalesce(EXTRACT(epoch FROM (i.packing_complete_time - i.picking_complete_time)),0) < 0.3*(45+3*count(*))) AS packing_suspiciously_fast
FROM item_time_tracking t
JOIN invoices i ON i.invoice_no = t.invoice_no
WHERE t.picker_username <> 'administrator' AND t.was_skipped = false
  AND t.total_item_time > 0 AND t.expected_time > 0
GROUP BY t.invoice_no, t.picker_username, i.packing_complete_time, i.picking_complete_time;
```
`packing_suspiciously_fast = true` flags orders closed far faster than the packing estimate — during the transition it tells you which pickers are still tapping "complete" before finishing. That flag should trend to near-zero once the habit sticks.

**What the report shows (per picker, per day), dedicated pickers only for idle:**
- **Occupancy %** — the headline "kept busy" number (occupied ÷ working span).
- Occupied min · Idle min · Idle gaps · Longest idle.
- An **idle-gap list** (from 5b) sorted longest-first, with blocks ≥20 min highlighted — so you see *when* the dead time was, not just a total.
- **Packing min vs estimate** per order, with the "closed too fast" flag.

**Best visual: a daily timeline strip** — one row per picker across the working hours, green segments = orders worked (5a islands), blank = idle (5b gaps), long blocks in red. It makes "who has empty stretches, and when" obvious at a glance and is the natural way to compare pickers day to day. (Real example, Arslan 9 Jul: busy 04:45–05:13, then small gaps, two big idle blocks of 58 min at 06:38 and 71 min at 10:21 — those two are ~62% of his idle.)

---

## Paste this to the Replit Agent
> Our Time Reports page (`/shift/reports`) shows Items Picked and Performance as 0 for all pickers even though `item_time_tracking` has the data, and its Idle/Hours are inflated because shifts auto-close at a fixed time and all non-picking work is counted as idle. Fix it without deleting existing tables:
> 1. Run the SQL in Part 1 (creates `vw_pick_detail`, `vw_picker_daily`, `dedicated_pickers` setting, backfills `level`).
> 2. On `/shift/reports`: wire Items Picked, Items/Hour and Performance from `vw_picker_daily` (Performance = % meeting target); change the shift auto-close to end at the picker's last actual pick time instead of a fixed clock time; source Idle from `vw_picker_idle_daily` (time when no order is open, per the order-boundary rule) and show it only for dedicated pickers; remove the empty Breaks column; filter the picker dropdown to real pickers. Also add a per-order performance report from `vw_order_performance` showing Estimate / Working time / Interruptions / Pace vs estimate %, grouped by picker and sortable.
> 3. Remove the `Reports → Time Analysis` menu item and `/admin/time_analysis` route (it only redirects here and hangs).
> 4. Where picks are written to `item_time_tracking`, also save `level` parsed from location (`\d{2}-\d{2}-([A-Z])`).
> 5. Add an Occupancy section from `vw_picker_occupancy_daily` (headline Occupancy %, plus Occupied/Idle/Idle gaps/Longest idle), an idle-gap list from `vw_idle_gaps` (longest first, blocks ≥20 min highlighted), and packing columns from the updated `vw_order_performance` (packing min vs estimate, "closed too fast" flag). Ideally render a per-picker daily timeline strip (green = orders worked, blank = idle) from `vw_picker_occupancy_daily` islands and `vw_idle_gaps`. Show Occupancy/idle for dedicated pickers only.
> Then show me `/shift/reports` with real non-zero numbers and confirm order-status/delivery screens still work.

---

## After it's done — confirm
- Time Reports shows **real** Items Picked and a sensible % meeting target (not 0).
- A picker who left at 12:41 shows a shift ending ~12:41, not 15:00.
- Idle appears only for Arslan / picker1, and looks reasonable (not 500+ min).
- No Breaks column; picker dropdown is clean; no Time Analysis menu item.
- Order status / delivery screens still load (their tables were untouched).
