# EP SmartGrowth — Supplier Returns Performance + Cost Pipeline Fixes

Two separate sets of changes confirmed against the latest code (EPLATTFORMA-WMDS-main latest.zip).

---

## PART A — Previous Cost Pipeline Fixes: Status vs Latest Code

| Fix | Status in latest code | Action needed |
|---|---|---|
| Fix 1: Write `cost_price_updated_at` | ❌ NOT applied — `dropbox_service.py` still does not set this field | Apply as written |
| Fix 2: Surface unmatched codes in UI | ❌ NOT applied — template change not in zip | Apply as written |
| Fix 3a: `CronRunLog` model | ❌ NOT applied — not in `models.py` | Apply as written |
| Fix 3b: `sync_invoices_from_date` return dict | ❌ NOT applied — still returns `h_ins, h_upd` tuple | Apply as written |
| Fix 3c: `cron_daily_invoice_sync.py` | ❌ NOT applied — still the original stdout-only version | Apply as written |
| Fix 3d: Cron logs admin view | ❌ NOT applied | Apply as written |

**All four fixes from `Replit_CostPipeline_Fixes.md` are still needed in full. None have been applied yet.**

---

## PART B — Supplier Returns Performance Improvements

Three improvements to `services/supplier_returns_service.py` and `blueprints/supplier_returns.py`.

---

### IMPROVEMENT 1 — Batch the DB upsert in `_write_stock_to_db` (highest impact)

**Problem:** The current code loops over every item and fires one `db.session.execute(...)` per row. For 150 items in store 100 that is 150 individual database round-trips on every Refresh.

**Fix:** Collect all parameter dicts into a list and pass them in a single `executemany` call. SQLAlchemy + psycopg2 batch these into one network round-trip.

**File:** `services/supplier_returns_service.py`

Find the entire `for r in ps365_rows:` loop inside `_write_stock_to_db`:

```python
    for r in ps365_rows:
        code = r["item_code_365"]
        dw   = dw_map.get(code)

        db.session.execute(text("""
            INSERT INTO supplier_returns_stock_cache
                (item_code_365, item_name, stock_cases, supplier_code_365,
                 supplier_name, selling_qty, cost_price, barcode, last_synced_at)
            VALUES
                (:code, :name, :stock, :sup_code,
                 :sup_name, :selling_qty, :cost_price, :barcode, :now)
            ON CONFLICT (item_code_365) DO UPDATE SET
                item_name         = EXCLUDED.item_name,
                stock_cases       = EXCLUDED.stock_cases,
                supplier_code_365 = EXCLUDED.supplier_code_365,
                supplier_name     = EXCLUDED.supplier_name,
                selling_qty       = EXCLUDED.selling_qty,
                cost_price        = EXCLUDED.cost_price,
                barcode           = EXCLUDED.barcode,
                last_synced_at    = EXCLUDED.last_synced_at
        """), {
            "code":        code,
            "name":        (dw.item_name or r["item_name"]) if dw else r["item_name"],
            "stock":       float(r["stock_cases"]),
            "sup_code":    (dw.supplier_code_365 or "").strip() if dw else "",
            "sup_name":    (dw.supplier_name     or "").strip() if dw else "",
            "selling_qty": float(dw.selling_qty) if dw and dw.selling_qty is not None else None,
            "cost_price":  float(dw.cost_price)  if dw and dw.cost_price  is not None else None,
            "barcode":     (dw.barcode or "").strip() if dw else "",
            "now":         now,
        })
```

**Replace with:**

```python
    batch = []
    for r in ps365_rows:
        code = r["item_code_365"]
        dw   = dw_map.get(code)
        batch.append({
            "code":        code,
            "name":        (dw.item_name or r["item_name"]) if dw else r["item_name"],
            "stock":       float(r["stock_cases"]),
            "sup_code":    (dw.supplier_code_365 or "").strip() if dw else "",
            "sup_name":    (dw.supplier_name     or "").strip() if dw else "",
            "selling_qty": float(dw.selling_qty) if dw and dw.selling_qty is not None else None,
            "cost_price":  float(dw.cost_price)  if dw and dw.cost_price  is not None else None,
            "barcode":     (dw.barcode or "").strip() if dw else "",
            "supplier_item_code": (dw.supplier_item_code or "").strip() if dw else "",
            "now":         now,
        })

    db.session.execute(text("""
        INSERT INTO supplier_returns_stock_cache
            (item_code_365, item_name, stock_cases, supplier_code_365,
             supplier_name, selling_qty, cost_price, barcode, supplier_item_code, last_synced_at)
        VALUES
            (:code, :name, :stock, :sup_code,
             :sup_name, :selling_qty, :cost_price, :barcode, :supplier_item_code, :now)
        ON CONFLICT (item_code_365) DO UPDATE SET
            item_name           = EXCLUDED.item_name,
            stock_cases         = EXCLUDED.stock_cases,
            supplier_code_365   = EXCLUDED.supplier_code_365,
            supplier_name       = EXCLUDED.supplier_name,
            selling_qty         = EXCLUDED.selling_qty,
            cost_price          = EXCLUDED.cost_price,
            barcode             = EXCLUDED.barcode,
            supplier_item_code  = EXCLUDED.supplier_item_code,
            last_synced_at      = EXCLUDED.last_synced_at
    """), batch)
```

> Note: `supplier_item_code` is added here — see Improvement 3 for why. The column must be added to the schema before deploying this (see Improvement 3, Step A).

---

### IMPROVEMENT 2 — Eliminate the redundant `_get_last_synced_at()` query

**Problem:** After `_read_stock_from_db()` already fetches `last_synced_at` for every row, `_get_last_synced_at()` fires a second `SELECT MAX(...)` query to get the same information.

**Fix:** Derive the max from the rows already in memory.

**File:** `services/supplier_returns_service.py`

**Step 2a — Update `_read_stock_from_db`** to return the max timestamp alongside the rows.

Find the function `_read_stock_from_db`. At the very end, change:

```python
    return result
```

**Replace with:**

```python
    max_synced = None
    for row in rows:
        if row.last_synced_at and (max_synced is None or row.last_synced_at > max_synced):
            max_synced = row.last_synced_at
    return result, max_synced
```

**Step 2b — Update `get_returns_stock`** to unpack the new return value and remove the separate call.

Find:

```python
    try:
        db_rows = _read_stock_from_db()
    except Exception as e:
        logger.exception("[Returns] DB read failed")
        db_rows   = []
        error_msg = error_msg or str(e)
```

**Replace with:**

```python
    last_synced = None
    try:
        db_rows, last_synced = _read_stock_from_db()
    except Exception as e:
        logger.exception("[Returns] DB read failed")
        db_rows   = []
        error_msg = error_msg or str(e)
```

**Step 2c** — Find the line that calls `_get_last_synced_at()` in `get_returns_stock`:

```python
    last_synced   = _get_last_synced_at()
```

**Delete this line entirely** — `last_synced` is now set by the step above.

You can also delete the `_get_last_synced_at` function itself — it is no longer called anywhere.

---

### IMPROVEMENT 3 — Add `supplier_item_code` to cache table; remove redundant DwItem query from `print_slip`

**Problem:** The `print_slip` route calls `DwItem.query.filter(...).all()` to fetch `barcode` and `supplier_item_code` for the print slip. `barcode` is already in the cache table and in the item dict — it is fetched twice. `supplier_item_code` is the only thing missing from the cache.

**Fix:** Add `supplier_item_code` to the cache table (written during Refresh alongside the other DwItem fields). The `print_slip` route then reads everything from the already-loaded cache — zero extra DB query.

**Step 3a — Schema migration**

Add a column to `supplier_returns_stock_cache`. Find the schema migration file that creates this table (called from `main.py` as `update_supplier_returns_stock_cache_schema`). In that file, inside `update_supplier_returns_stock_cache_schema`, add after the `CREATE TABLE IF NOT EXISTS` block:

```python
        db.session.execute(text("""
            ALTER TABLE supplier_returns_stock_cache
            ADD COLUMN IF NOT EXISTS supplier_item_code VARCHAR(100) NOT NULL DEFAULT ''
        """))
        db.session.commit()
```

**Step 3b — Write it during Refresh**

Already handled by the batch upsert in Improvement 1 above — `supplier_item_code` is included in the batch dict and the INSERT statement.

**Step 3c — Read it from the cache in `_read_stock_from_db`**

Find the SELECT in `_read_stock_from_db`:

```python
        SELECT item_code_365, item_name, stock_cases,
               supplier_code_365, supplier_name,
               selling_qty, cost_price, barcode, last_synced_at
        FROM   supplier_returns_stock_cache
```

**Replace with:**

```python
        SELECT item_code_365, item_name, stock_cases,
               supplier_code_365, supplier_name,
               selling_qty, cost_price, barcode, supplier_item_code, last_synced_at
        FROM   supplier_returns_stock_cache
```

And add `supplier_item_code` to the dict built in the loop:

```python
            "supplier_item_code": (r.supplier_item_code or "").strip() if r.supplier_item_code else "",
```

**Step 3d — Remove the DwItem query from `print_slip`**

**File:** `blueprints/supplier_returns.py`

Find the `print_slip` route. Replace this block:

```python
    item_codes = [item["item_code_365"] for item in group.get("item_rows", [])]
    if item_codes:
        dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        dw_map   = {d.item_code_365: d for d in dw_items}
    else:
        dw_map = {}

    for item in group.get("item_rows", []):
        dw = dw_map.get(item["item_code_365"])
        item["barcode"]            = (dw.barcode            or "") if dw else ""
        item["supplier_item_code"] = (dw.supplier_item_code or "") if dw else ""
```

**Replace with:**

```python
    # barcode and supplier_item_code are already in the cache — no extra DB query needed
    for item in group.get("item_rows", []):
        item.setdefault("barcode", "")
        item.setdefault("supplier_item_code", "")
```

Also remove the `from models import DwItem` import in this route if `DwItem` is no longer used anywhere else in the blueprint.

---

## Summary of all changes

### Cost pipeline (from previous doc — all still needed)
All fixes in `Replit_CostPipeline_Fixes.md` are confirmed unapplied. Send that doc to Replit unchanged.

### Supplier returns — apply in this order

| # | File | Change |
|---|---|---|
| 3a | `update_supplier_returns_stock_cache_schema.py` | Add `supplier_item_code` column |
| 1 | `services/supplier_returns_service.py` | Batch upsert + include `supplier_item_code` |
| 2 | `services/supplier_returns_service.py` | Remove redundant `_get_last_synced_at()` call |
| 3c | `services/supplier_returns_service.py` | Read `supplier_item_code` from cache SELECT |
| 3d | `blueprints/supplier_returns.py` | Remove DwItem query from `print_slip` |

> Apply 3a (schema) before deploying the service changes, otherwise the INSERT will fail on the new column.
