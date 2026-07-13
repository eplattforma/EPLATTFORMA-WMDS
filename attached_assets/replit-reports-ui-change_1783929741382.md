# Replit instructions — show only the new picking reports in the UI

Companion to `replit-fix-picking-reports.md` (run that SQL first). This step: in the **Reports** menu, remove every old picking / time-tracking / idle report and show only the new corrected ones. **No database view is deleted** — old views stay so order-status, shipping and delivery screens keep working. This is a menu + page change only.

---

## First, two more SQL bits (run in the Database pane)

**1. Mark who the dedicated pickers are** (idle is shown only for them)
```sql
INSERT INTO settings (key, value)
VALUES ('dedicated_pickers', '["Arslan","picker1"]')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
```

**2. Idle view for dedicated pickers only** (replaces the old idle report)
```sql
CREATE OR REPLACE VIEW vw_idle_dedicated AS
SELECT
  i.start_time::date                                                          AS idle_date,
  sh.picker_username                                                          AS picker,
  round(sum(CASE WHEN i.duration_minutes <= 60 THEN i.duration_minutes ELSE 0 END)::numeric, 0) AS working_idle_min,
  sum(CASE WHEN i.duration_minutes <= 60 THEN 1 ELSE 0 END)                   AS working_idle_gaps,
  round(sum(CASE WHEN i.duration_minutes > 60 THEN i.duration_minutes ELSE 0 END)::numeric, 0)  AS long_absence_min_to_watch,
  sum(CASE WHEN i.duration_minutes > 60 THEN 1 ELSE 0 END)                    AS long_absence_gaps
FROM idle_periods i
JOIN shifts sh ON sh.id = i.shift_id
WHERE sh.picker_username IN (
  SELECT jsonb_array_elements_text(value::jsonb) FROM settings WHERE key = 'dedicated_pickers'
)
GROUP BY 1, 2
ORDER BY idle_date DESC;
```

So the new Reports section is backed by exactly three objects: `vw_picker_daily`, `vw_pick_detail`, `vw_idle_dedicated`.

---

## Paste this to the Replit Agent

> In our Reports section I want to remove all the old picker-performance, time-tracking and idle reports and replace them with new ones. Do NOT delete any database view — only change the Reports menu and pages.
>
> 1. Find how the Reports menu / navigation is built (the nav config, route list, or menu component) and the report page components.
> 2. **Remove from the Reports menu** every item that shows picker speed, picking time, time-tracking or idle using the old logic — anything driven by `pbi_fact_picking` duration, `order_time_breakdown`, `time_tracking_alerts`, or raw `idle_periods`. Remove the menu entries and unlink the pages, but leave the database views/tables in place (order-status, shipping and delivery screens still use `pbi_fact_picking`).
> 3. **Add three new Reports pages**, backed by these views:
>    - **"Picker performance"** → `vw_picker_daily`. Columns: Date, Picker, Items picked, Units, Median time per pick (s), % meeting target, Walking share %, Long gaps (to watch). Add a date filter. Default sort: newest date, most items first.
>    - **"Pick detail"** (drill-down, optional link from the row) → `vw_pick_detail`, filtered by picker + date.
>    - **"Idle — dedicated pickers"** → `vw_idle_dedicated`. Columns: Date, Picker, Working idle (min), Long absence to watch (min).
> 4. If we use `permissions_menu_filtering_enabled` / menu config, hide the old items there too so they don't reappear for any role.
> 5. Keep labels plain — no `status_updated_at`, no efficiency ratios. Show "% meeting target" and "median time per pick", not averages.
>
> Then show me the updated Reports menu so I can confirm only the three new reports appear.

---

## What "old reports to remove" means (checklist for the Agent)
Remove any Reports-menu item whose data comes from:
- `pbi_fact_picking` **used as a speed/duration report** (it stays as a view for order status/delivery — just not in Reports as picker speed)
- `order_time_breakdown` (per-order pick/pack timing report)
- `time_tracking_alerts` (as a standalone report screen)
- raw `idle_periods` shown for all pickers

Keep in Reports (untouched): sales, invoices, routes, deliveries, discrepancies, stock.

---

## The two named reports (VERIFIED on the live app)

Confirmed by opening both screens while logged in as Polis:

- **"Time Reports"** = menu *Reports → Time Reports*, route **`/shift/reports`**, page title "Time & Productivity Reports".
- **"Time Analysis"** = menu *Reports → Time Analysis*, route **`/admin/time_analysis`** — this **redirects to `/shift/reports`**. It is a dead duplicate that shows nothing of its own.

### Fix 1 — remove the "Time Analysis" menu item
It just redirects to Time Reports. Delete the menu entry (and the dead `/admin/time_analysis` route). One time report, not two.

### Fix 2 — repair "Time Reports" (`/shift/reports`)
What's on the page now and what to do with each part:

| Section | State now | Action |
|---|---|---|
| Shift summary (Total Shifts, Hours, Avg duration) | Works | Keep |
| Recent Shifts table (check-in/out, duration, status) | Works | Keep |
| **Items Picked / Items per Hour** | **0 for everyone** — not wired to `item_time_tracking` | **Wire to real data** (per picker, per shift date) |
| **Performance %** | **0% for everyone** — broken | Replace with **% of picks meeting target** (median-based) |
| **Idle Time** column | Raw minutes (300–519) for all pickers, incl. multi-role | Show only for **dedicated pickers**; split working idle vs long absence, or hide for others |
| **Breaks** column | Always "—" (never tracked) | **Remove the column** |
| Picker filter list | Cluttered with test/non-pickers (administrator, admin, test_shipp, picker2, Polis…) | Show only real pickers |
| "Performance Insights" (Top performer 0.0/hr, Items processed: 0) | Artifacts of the broken wiring | Fixes itself once Items are wired |

The one real bug behind most of this: the report counts items from an empty source instead of `item_time_tracking`. Point "Items Picked" at `item_time_tracking` joined to each shift by `picker_username` and date, and add a "% meeting target" column from the same table.

**Agent instruction:** "On `/shift/reports`: (1) fix Items Picked / Items per Hour by counting from `item_time_tracking` per picker per shift date — they currently show 0; (2) replace the 0% Performance column with % of picks where `total_item_time <= expected_time`; (3) show Idle Time only for dedicated pickers (from the `dedicated_pickers` setting) and remove the empty Breaks column; (4) filter the picker dropdown to real pickers only. Then remove the `Reports → Time Analysis` menu item and its `/admin/time_analysis` route, which only redirects here."

### Reference views (optional, if you prefer views over inline queries)

**"Time report"** — a per-order view on the real data:

```sql
CREATE OR REPLACE VIEW vw_order_time AS
SELECT
  invoice_no,
  picker_username                                        AS picker,
  min(item_started)::date                                AS pick_date,
  count(*)                                               AS lines,
  sum(quantity_picked)                                   AS units,
  round(sum(total_item_time) / 60.0, 1)                  AS total_minutes,
  round(sum(walking_time)    / 60.0, 1)                  AS walking_minutes,
  round(sum(picking_time)    / 60.0, 1)                  AS picking_minutes,
  round(100.0 * sum(walking_time) / nullif(sum(total_item_time), 0), 0) AS walking_share_pct,
  round(100.0 * sum((total_item_time <= expected_time)::int) / count(*), 0) AS pct_lines_met_target,
  sum((walking_time > 60)::int)                          AS long_gaps
FROM item_time_tracking
WHERE picker_username <> 'administrator'
  AND was_skipped = false
  AND total_item_time > 0
  AND expected_time  > 0
GROUP BY invoice_no, picker_username;
```

- **Strip:** `total_packing_time` (packing isn't tracked — always 0), `average_time_per_item` (misleading average), `total_locations_visited` (not needed).
- **Swap in:** the `vw_order_time` columns above — per order: lines, units, total/walking/picking minutes, walking share, % of lines that met target, long gaps.

**"Time Analysis"** → not a real report (it redirects to `/shift/reports`). Remove the menu item and its route, as in Fix 1 above. The old alert engine behind it (`time_tracking_alerts`) is dead — 10 alerts on one day, May 2025, and `time_alerts_enabled = false`. Turning alerts back on is a *separate* task, not a report.

**`vw_picker_daily`** (from the earlier SQL file) is what feeds the corrected productivity section of Time Reports: picker × day — median time per pick, % meeting target, walking share, long gaps to watch.

---

## After it's done — confirm
- The Reports menu shows **only**: Picker performance, Pick detail, Idle — dedicated pickers (plus the untouched sales/route reports).
- Opening **Picker performance** shows recent days with median time and % meeting target.
- Order status / delivery screens still load normally (proves the old view is still alive).
- No page still shows average pick duration or all-picker idle.
