# Quick Reference: Weekly Sales Builder Fix

## What to Fix
File: `services/forecast/weekly_sales_builder.py`

**Problem:** Loop-based row-by-row upserts
```python
# SLOW (current)
for row in results:
    session.execute(insert_stmt)
    session.commit()  # thousands of commits
```

**Solution:** Single bulk SQL INSERT...SELECT...ON CONFLICT
```python
# FAST (new)
session.execute(text(sql_stmt_with_aggregation))
session.commit()  # one commit
```

---

## The SQL Statement Template

```sql
INSERT INTO fact_sales_weekly_item (week_start, item_code_365, gross_qty, sales_ex_vat, updated_at)
SELECT
    (date_trunc('week', h.sale_date))::date AS week_start,
    l.item_code_365,
    SUM(l.qty) AS gross_qty,
    SUM(l.net_excl) AS sales_ex_vat,
    NOW() AS updated_at
FROM dw_invoice_line l
INNER JOIN dw_invoice_header h ON h.invoice_no = l.invoice_no
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
```

**Before you use it:**
- Replace column names to match YOUR schema
- Verify `fact_sales_weekly_item` has unique constraint on `(week_start, item_code_365)`
- Add indexes if missing (see full instructions)

---

## Python Code Replacement

### BEFORE (remove this)
```python
from sqlalchemy import insert
from models import FactSalesWeeklyItem
from datetime import datetime

# Bad: loop through Python
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
    session.commit()

return len(results)
```

### AFTER (add this)
```python
from sqlalchemy import text

# Good: one SQL statement
sql_stmt = """
INSERT INTO fact_sales_weekly_item (week_start, item_code_365, gross_qty, sales_ex_vat, updated_at)
SELECT
    (date_trunc('week', h.sale_date))::date AS week_start,
    l.item_code_365,
    SUM(l.qty) AS gross_qty,
    SUM(l.net_excl) AS sales_ex_vat,
    NOW() AS updated_at
FROM dw_invoice_line l
INNER JOIN dw_invoice_header h ON h.invoice_no = l.invoice_no
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

rows_affected = result.rowcount
logger.info(f"[Run {run_id}] weekly_sales: bulk upsert completed; {rows_affected} rows affected")

return rows_affected
```

---

## Checklist Before Running

- [ ] Column names match your schema (qty, net_excl, sale_date, etc.)
- [ ] Table names correct (dw_invoice_header, dw_invoice_line, fact_sales_weekly_item)
- [ ] Unique constraint exists: `ALTER TABLE fact_sales_weekly_item ADD CONSTRAINT uniq_week_sku UNIQUE (week_start, item_code_365)`
- [ ] Indexes created (see full instructions)
- [ ] Test SQL manually in psql first
- [ ] Backup database
- [ ] Working in non-production first

---

## Expected Results

| Metric | Before | After |
|--------|--------|-------|
| Time | ~45 seconds | ~5 seconds |
| DB calls | 2000–5000 | 1 |
| Rows affected | Same (e.g., 5234) | Same (e.g., 5234) |

---

## If Something Breaks

**Error: "duplicate key value violates unique constraint"**
→ Add or verify the unique constraint exists

**Error: "column not found"**
→ Column names don't match your schema; check actual names in DB

**Error: "function date_trunc not found"**
→ PostgreSQL version issue or wrong syntax; check your DB dialect

**Result: Row count is different**
→ Your WHERE clause filters are wrong; compare old SQL vs new

---

## Files Changed
- `services/forecast/weekly_sales_builder.py` (main fix)

## Files NOT Changed
- `services/forecast/run_service.py` (no changes needed for this fix)
- `services/forecast/seasonality_service.py` (no changes needed for this fix)
- `services/forecast/base_forecast_service.py` (no changes needed for this fix)
- Any ordering/replenishment code (stay away)
