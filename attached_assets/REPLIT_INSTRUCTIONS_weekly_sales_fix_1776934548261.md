# Replit Instructions: Fix Weekly Sales Builder Row-by-Row Upserts

## Objective
Replace the inefficient Python loop-based upsert pattern in the weekly sales builder with a single SQL `INSERT...SELECT...ON CONFLICT` statement. This will reduce forecast run time by 50% and eliminate thousands of database round-trips.

## Why This Matters
**Current behavior:** Fetch aggregated SQL results from invoice data → loop through Python → execute one INSERT/UPDATE per row (thousands of statements)

**Target behavior:** Single SQL statement does all aggregation and upsert in one operation (one statement total)

**Expected improvement:** 5–10 seconds saved per forecast run (~50% of weekly_sales stage time)

---

## Step 1: Locate and Review the Current Implementation

### File to examine
```
services/forecast/weekly_sales_builder.py
```

### What to look for
Find the function (likely `build_weekly_sales()` or similar) that:
1. Queries invoice data and aggregates by week + item_code
2. Loops through results in Python
3. Calls `db.session.add()` or `db.session.execute()` for each row individually

**Expected pattern you'll see:**
```python
# Current BAD pattern (pseudo-code)
for row in aggregated_results:
    stmt = insert(FactSalesWeeklyItem).values(
        week_start=row.week_start,
        item_code_365=row.item_code,
        gross_qty=row.qty,
        sales_ex_vat=row.revenue,
        updated_at=now()
    ).on_conflict_do_update(...)
    session.execute(stmt)
    session.commit()  # or session.flush()
```

---

## Step 2: Understand the Data Model

### Tables involved
- **Source:** `dw_invoice_header` + `dw_invoice_line` (join on invoice_no)
- **Target:** `FactSalesWeeklyItem`

### Key columns to track
**From source:**
- `dw_invoice_header.sale_date` → convert to week_start using `date_trunc('week', ...)`
- `dw_invoice_header.invoice_no` (join key)
- `dw_invoice_line.item_code_365` (the SKU)
- `dw_invoice_line.qty` (quantity)
- `dw_invoice_line.net_excl` or similar (revenue ex VAT)

**To target:**
- `week_start` (DATE, Monday of week)
- `item_code_365` (VARCHAR)
- `gross_qty` (NUMERIC, sum of qty)
- `sales_ex_vat` (NUMERIC, sum of revenue)
- `updated_at` (TIMESTAMP)

### Unique key constraint
The table likely has a **unique constraint on (week_start, item_code_365)** or composite primary key. Verify this exists; if not, add it.

---

## Step 3: Build the New SQL Statement

### Template (adjust column names to match your schema)

```sql
INSERT INTO fact_sales_weekly_item (week_start, item_code_365, gross_qty, sales_ex_vat, updated_at)
SELECT
    (date_trunc('week', h.sale_date))::date AS week_start,
    l.item_code_365,
    SUM(l.qty) AS gross_qty,
    SUM(l.net_excl) AS sales_ex_vat,
    NOW() AS updated_at
FROM dw_invoice_line l
INNER JOIN dw_invoice_header h
    ON h.invoice_no = l.invoice_no
WHERE
    h.sale_date >= :cutoff_date
    AND h.sale_date < :end_date
    AND h.status NOT IN ('cancelled', 'void')  -- adjust filter as needed
GROUP BY 1, 2
ON CONFLICT (week_start, item_code_365)
DO UPDATE SET
    gross_qty = EXCLUDED.gross_qty,
    sales_ex_vat = EXCLUDED.sales_ex_vat,
    updated_at = NOW();
```

### What to customize
1. **Column names:** Replace `l.qty`, `l.net_excl`, `h.sale_date` with actual column names from your schema
2. **Table names:** Confirm `dw_invoice_header`, `dw_invoice_line`, `fact_sales_weekly_item` are correct
3. **WHERE clause:** Adjust date range and any status filters based on your business rules
4. **CONFLICT columns:** The conflict target must match the actual unique constraint on the fact table (likely `(week_start, item_code_365)`)

---

## Step 4: Identify and Add Missing Indexes (if needed)

### Required indexes for the query to be performant
Run these checks in the database:

```sql
-- Check if indexes exist
\d dw_invoice_header
\d dw_invoice_line
\d fact_sales_weekly_item
```

### Indexes that should exist
If missing, create them:

```sql
-- On source tables
CREATE INDEX IF NOT EXISTS idx_invoice_header_sale_date 
  ON dw_invoice_header(sale_date);

CREATE INDEX IF NOT EXISTS idx_invoice_header_invoice_no 
  ON dw_invoice_header(invoice_no);

CREATE INDEX IF NOT EXISTS idx_invoice_line_invoice_no 
  ON dw_invoice_line(invoice_no);

CREATE INDEX IF NOT EXISTS idx_invoice_line_item_code 
  ON dw_invoice_line(item_code_365);

-- On target table (unique key for upsert)
CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_sales_weekly_item_week_sku 
  ON fact_sales_weekly_item(week_start, item_code_365);
```

---

## Step 5: Update the weekly_sales_builder.py Code

### Before: Replace This Pattern

```python
from sqlalchemy import insert
from models import FactSalesWeeklyItem

# Bad pattern: loop and execute one by one
for item_code, week_start, total_qty, total_revenue in results:
    stmt = insert(FactSalesWeeklyItem).values(
        week_start=week_start,
        item_code_365=item_code,
        gross_qty=total_qty,
        sales_ex_vat=total_revenue,
        updated_at=datetime.utcnow()
    ).on_conflict_do_update(
        index_elements=['week_start', 'item_code_365'],
        set_={
            'gross_qty': total_qty,
            'sales_ex_vat': total_revenue,
            'updated_at': datetime.utcnow()
        }
    )
    session.execute(stmt)
    session.commit()  # or session.flush()

return len(results)
```

### After: Replace With This Pattern

```python
from sqlalchemy import text
from datetime import datetime

# Good pattern: one SQL statement
sql_stmt = """
INSERT INTO fact_sales_weekly_item (week_start, item_code_365, gross_qty, sales_ex_vat, updated_at)
SELECT
    (date_trunc('week', h.sale_date))::date AS week_start,
    l.item_code_365,
    SUM(l.qty) AS gross_qty,
    SUM(l.net_excl) AS sales_ex_vat,
    NOW() AS updated_at
FROM dw_invoice_line l
INNER JOIN dw_invoice_header h
    ON h.invoice_no = l.invoice_no
WHERE
    h.sale_date >= :cutoff_date
    AND h.sale_date < :end_date
    AND h.status NOT IN ('cancelled', 'void')
GROUP BY 1, 2
ON CONFLICT (week_start, item_code_365)
DO UPDATE SET
    gross_qty = EXCLUDED.gross_qty,
    sales_ex_vat = EXCLUDED.sales_ex_vat,
    updated_at = NOW();
"""

result = session.execute(text(sql_stmt), {
    'cutoff_date': cutoff_date,
    'end_date': end_date
})
session.commit()

# Return number of rows affected (for logging)
rows_affected = result.rowcount
return rows_affected
```

### Key changes
- **Single `execute()` call** instead of loop
- **No individual upserts** — all aggregation and conflict handling in SQL
- **Parameters passed as dict** (`:cutoff_date`, `:end_date`)
- **Use `text()` wrapper** for raw SQL in SQLAlchemy
- **`session.commit()` once** instead of per-row

---

## Step 6: Update Any Related Function Signatures

### Check what calls `build_weekly_sales()`
Look in `services/forecast/run_service.py`:

```python
# Current call (around line 127)
sales_result = build_weekly_sales(session, weeks_back=52, mode=mode, ...)
```

### Ensure the function signature includes
- `session`: DB session
- `weeks_back`: how many weeks to aggregate (e.g., 52 for full rebuild, 8 for incremental)
- `mode`: "incremental" or "full_rebuild" (optional, for future use)
- Return: dict with keys `{"upserted": row_count, "mode": mode}`

---

## Step 7: Test the Change

### Before testing, verify:
1. ✅ Backup or work in a non-production database
2. ✅ The SQL statement has correct column names (run it manually first)
3. ✅ Indexes exist (or run CREATE INDEX commands)
4. ✅ Your unique constraint on `(week_start, item_code_365)` is in place

### Manual test in psql
```sql
-- Run the INSERT...SELECT...ON CONFLICT directly
INSERT INTO fact_sales_weekly_item (week_start, item_code_365, gross_qty, sales_ex_vat, updated_at)
SELECT
    (date_trunc('week', h.sale_date))::date AS week_start,
    l.item_code_365,
    SUM(l.qty) AS gross_qty,
    SUM(l.net_excl) AS sales_ex_vat,
    NOW() AS updated_at
FROM dw_invoice_line l
INNER JOIN dw_invoice_header h
    ON h.invoice_no = l.invoice_no
WHERE
    h.sale_date >= '2025-01-01'
    AND h.sale_date < '2026-04-23'
GROUP BY 1, 2
ON CONFLICT (week_start, item_code_365)
DO UPDATE SET
    gross_qty = EXCLUDED.gross_qty,
    sales_ex_vat = EXCLUDED.sales_ex_vat,
    updated_at = NOW();
```

### After manual test, run the code
1. Deploy updated `weekly_sales_builder.py`
2. Trigger a test forecast run
3. Monitor logs for timing:
   - Old: "weekly_sales completed in 45s; upserted=5234"
   - New: "weekly_sales completed in 5s; upserted=5234" (10x faster)

---

## Step 8: Verify Row Counts Match

### Sanity check
After the first run, verify the new code produces the same row count:

```python
# Add logging to compare
logger.info(f"[Run {run_id}] weekly_sales: {elapsed:.2f}s, rows_affected={rows_affected}")

# Manually count for verification
select count(*) from fact_sales_weekly_item where updated_at > '2025-04-23'
```

The count should match what the old code produced (or be identical if re-running the same data).

---

## Step 9: Monitor for Side Effects

### Things to watch after deployment
- ✅ Forecast runs complete successfully (check status = 'completed')
- ✅ Row counts in `fact_sales_weekly_item` stay consistent
- ✅ No duplicate keys or constraint violations in logs
- ✅ Downstream processes (seasonality, classification) still work
- ✅ Base forecasts are still generated

### If something breaks
- **Constraint violation:** The unique constraint on `(week_start, item_code_365)` might be missing. Add it:
  ```sql
  ALTER TABLE fact_sales_weekly_item ADD CONSTRAINT uniq_week_sku UNIQUE (week_start, item_code_365);
  ```
- **Column name mismatch:** Double-check column names in the INSERT...SELECT
- **Date logic:** Verify `date_trunc('week', ...)` matches how you define "week_start" elsewhere

---

## Step 10: Before/After Comparison (What to Report Back)

When complete, provide:

1. **File changed:**
   - `services/forecast/weekly_sales_builder.py`

2. **Before/after snippet:**
   - Show the old loop-based code
   - Show the new bulk SQL statement

3. **Timing comparison:**
   - Old: "weekly_sales completed in X.XXs; upserted=N rows"
   - New: "weekly_sales completed in Y.YYs; upserted=N rows"
   - Speedup: `X / Y` times faster

4. **Any indexes created:**
   - List any `CREATE INDEX` statements you added

5. **Confirmation:**
   - Does the code still make zero PS365 API calls? ✅
   - Are row counts identical to the old version? ✅

---

## Summary of Changes

| Item | Before | After |
|------|--------|-------|
| DB round-trips | ~2000–5000 per run | 1 |
| Code location | Python loop | SQL `INSERT...SELECT...ON CONFLICT` |
| Upsert method | Individual SQLAlchemy `insert()` statements | Native SQL `ON CONFLICT DO UPDATE` |
| Time estimate | 45 seconds | 5 seconds |
| Lines of code | ~30 (loop + logging) | ~10 (single statement) |

---

## Questions to Ask Yourself Before Starting

1. **What are the actual column names in your schema?** (not the pseudo-code names)
   - Invoice header: `sale_date` or `invoice_date`?
   - Invoice line: `qty` or `quantity`?
   - Revenue: `net_excl` or `amount_ex_vat` or `subtotal`?

2. **Does `fact_sales_weekly_item` have a unique constraint on `(week_start, item_code_365)`?**
   - If not, you must create it or the `ON CONFLICT` clause won't work

3. **How are weeks defined in your system?**
   - Sunday-to-Saturday? Monday-to-Sunday? (affects `date_trunc`)

4. **Are there any invoice statuses to exclude?**
   - Cancelled? Void? Returned? Add to WHERE clause

5. **What is the current date range being processed?**
   - Full 52 weeks? Last 8 weeks? (Use in `:cutoff_date` parameter)

---

## Do NOT
- ❌ Change the demand classification logic
- ❌ Modify seasonality computation
- ❌ Touch base forecast formulas
- ❌ Add PS365 API calls
- ❌ Change the ordering refresh service
- ❌ Add complex window functions or CTEs (keep it simple)

---

## Do
- ✅ Test the SQL statement manually first
- ✅ Verify indexes exist
- ✅ Keep logging for debugging
- ✅ Ensure `session.commit()` is called once
- ✅ Compare row counts before/after
- ✅ Monitor the first few forecast runs

