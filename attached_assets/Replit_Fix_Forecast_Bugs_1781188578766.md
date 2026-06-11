# WMDS Fix — Forecasting & Replenishment Bugs

Five bugs in the forecasting / replenishment pipeline. Each section gives the exact current code (FIND) and the exact replacement (REPLACE). Every FIND block appears exactly once in its target file — use exact find-and-replace. Do not reformat surrounding code.

---

## Bug 1 — PO pagination: only page 1 of purchase orders is fetched

**File:** `services/replenishment_mvp/ps365_client.py` (approx lines 88–116, inside `_fetch_ordered_from_purchase_orders`)

`_fetch_ordered_from_purchase_orders` calls `list_purchase_orders` once with `page_number: 1, page_size: 100` and never fetches subsequent pages. Suppliers with more than 100 open POs in the 180-day-back / 365-day-forward window have their incoming ("ordered") quantities understated, so the replenishment planner over-orders. Fix: loop pages, accumulating all POs, stopping when a page returns fewer rows than the page size (the rest of the function already iterates the `pos` list, so only the fetch needs changing).

**FIND (appears once):**

```python
        resp = call_ps365("list_purchase_orders", method="POST", payload={
            "filter_define": {
                "page_number": 1,
                "page_size": 100,
                "only_counted": "N",
                "orders_supplier_selection": supplier_code,
                "order_status_selection": "",
                "from_date": from_date,
                "to_date": to_date,
                "items_selection": "",
                "stores_selection": "",
                "orders_type": "all",
                "shopping_cart_code_selection": "",
            }
        })

        if not resp or resp.get("api_response", {}).get("response_code") != "1":
            error_msg = resp.get("api_response", {}).get("response_msg", "Unknown") if resp else "No response"
            logger.warning(f"Failed to fetch POs from PS365: {error_msg}")
            return {}

        pos = resp.get("list_purchase_orders") or []
        logger.info(f"Found {len(pos)} purchase orders for supplier {supplier_code}")
```

**REPLACE WITH:**

```python
        page_size = 100
        max_pages = 100  # safety cap: 10,000 POs
        pos = []
        page_number = 1
        while page_number <= max_pages:
            resp = call_ps365("list_purchase_orders", method="POST", payload={
                "filter_define": {
                    "page_number": page_number,
                    "page_size": page_size,
                    "only_counted": "N",
                    "orders_supplier_selection": supplier_code,
                    "order_status_selection": "",
                    "from_date": from_date,
                    "to_date": to_date,
                    "items_selection": "",
                    "stores_selection": "",
                    "orders_type": "all",
                    "shopping_cart_code_selection": "",
                }
            })

            if not resp or resp.get("api_response", {}).get("response_code") != "1":
                error_msg = resp.get("api_response", {}).get("response_msg", "Unknown") if resp else "No response"
                logger.warning(f"Failed to fetch POs page {page_number} from PS365: {error_msg}")
                if page_number == 1:
                    return {}
                break  # keep what we already accumulated

            page_pos = resp.get("list_purchase_orders") or []
            pos.extend(page_pos)
            logger.debug(f"PO page {page_number}: {len(page_pos)} orders (running total {len(pos)})")
            if len(page_pos) < page_size:
                break
            page_number += 1

        logger.info(f"Found {len(pos)} purchase orders for supplier {supplier_code}")
```

This mirrors the multi-page pattern already used by `fetch_supplier_stock` in the same file (which pages `list_items_stock`), but uses fetch-until-short-page because `list_purchase_orders` is called with `only_counted: "N"` and no total count is read.

**Testing checklist:**
- Run a replenishment generation for a supplier with fewer than 100 open POs — `ordered_now_units` values must be identical to before the change.
- Mock `call_ps365` to return 100 POs on page 1 and 5 on page 2 — assert the log says "Found 105 purchase orders" and quantities from page-2 POs appear in the returned dict.
- Mock page 1 success and page 2 failure — assert page-1 results are still returned (not an empty dict).
- Mock page 1 failure — assert `{}` is returned and a warning is logged.
- Confirm no infinite loop when the API keeps returning exactly 100 rows (the `max_pages` cap stops it).

---

## Bug 2 — Dead expiry warning: wrong dictionary key, EXPIRY_SOON can never fire

**File:** `services/replenishment_mvp/planner.py` (approx line 298, inside `_build_warnings`)

`planner.py` checks `expiry_data.get("has_expiry_within_30d")`, but `get_expiry_summary` in `services/replenishment_mvp/repositories.py` returns per-item dicts with exactly these keys: `earliest_expiry_date`, `qty_at_earliest_expiry`, `expiring_within_30_days_units` (a float count of units, 0 when nothing expires). The key `has_expiry_within_30d` never exists, so `.get()` always returns `None` and the `EXPIRY_SOON` review flag can never be added. Fix planner.py only — do not change repositories.py.

**FIND (appears once):**

```python
    if expiry_data and expiry_data.get("has_expiry_within_30d"):
        warnings.append(("EXPIRY_SOON", WARNING_TEXT_MAP["EXPIRY_SOON"]))
```

**REPLACE WITH:**

```python
    if expiry_data and float(expiry_data.get("expiring_within_30_days_units", 0) or 0) > 0:
        warnings.append(("EXPIRY_SOON", WARNING_TEXT_MAP["EXPIRY_SOON"]))
```

**Testing checklist:**
- Unit test `_build_warnings` with `expiry_data={"expiring_within_30_days_units": 12.0, "earliest_expiry_date": None, "qty_at_earliest_expiry": 0}` — assert `("EXPIRY_SOON", "Stock expiring within 30 days")` is in the result.
- Unit test with `expiring_within_30_days_units: 0` — assert EXPIRY_SOON is absent.
- Unit test with `expiry_data={}` and `expiry_data=None` — no exception, EXPIRY_SOON absent.
- Generate a replenishment run for a supplier with a `StockPosition` row whose expiry date is within 30 days — the line's `warning_code` priority ordering must still work (EXPIRY_SOON outranks HAS_ORDERED_STOCK per `WARNING_PRIORITY`).

---

## Bug 3 — Partial current week included in moving average after Friday 10:00 rollover

**File:** `services/forecast/week_utils.py` (function `get_completed_week_cutoff`, approx lines 35–79; two edits below)

`get_completed_week_cutoff` returns the exclusive upper bound for `week_start < :cutoff` queries. After the configured rollover moment (default Friday 10:00 Athens) it returns `this_monday + 1 week`, which pulls the current, still-in-progress week into the history window. `base_forecast_service.py` uses this cutoff to build the MA8 window, the MEDIAN6 window, the 26-week history, and the last-2-week trend comparison (`forecast_qtys[:2]`) — so from Friday 10:00 to Sunday, a ~60–70% complete week enters as if it were a full week, biasing weekend forecast runs downward and spuriously firing the "down" trend flag. The invariant: a week is complete only when the current date is strictly past its last day (Sunday). The current week can therefore never be complete, regardless of rollover settings, and the cutoff must always be `this_monday`.

### Edit 3a — correct the docstring

**FIND (appears once):**

```python
    * **Before** the configured rollover moment → returns this_monday
      (current week is excluded from the forecast).
    * **At / after** the configured rollover moment → returns next_monday
      (current week is included in the forecast).
```

**REPLACE WITH:**

```python
    A week is only complete when 'now' is strictly past the last day of that
    week (its Sunday). The current week is always in progress, so the cutoff
    is always this_monday: the latest completed week is the one starting
    this_monday - 7 days. Rollover settings no longer shift the cutoff —
    a partial week must never enter the forecast history window.
```

### Edit 3b — correct the logic

**FIND (appears once):**

```python
    rollover_day = this_monday + timedelta(days=rollover_weekday)
    rollover_dt = datetime(
        rollover_day.year, rollover_day.month, rollover_day.day,
        hour, minute, 0,
        tzinfo=_ATHENS_TZ,
    )

    if now_athens >= rollover_dt:
        return this_monday + timedelta(weeks=1)
    return this_monday
```

**REPLACE WITH:**

```python
    # Invariant: a week is only complete when 'now' is strictly past its last
    # day (Sunday). The current week (starting this_monday) is by definition
    # still in progress, so it must never enter the history window, regardless
    # of the configured rollover weekday/time. The exclusive cutoff is
    # therefore always this_monday. rollover_weekday / hour / minute are kept
    # in the signature for backward compatibility with callers and tests, but
    # they no longer shift the cutoff forward.
    _ = (rollover_weekday, hour, minute)
    return this_monday
```

Do not change `get_data_through_date` — it derives from the cutoff (`cutoff - 1 day`) and is automatically correct after this fix (it now always returns the Sunday of the last fully completed week). Do not change `base_forecast_service.py` — it consumes the cutoff correctly.

**Testing checklist:**
- `get_completed_week_cutoff(_now_athens=datetime(2026, 6, 12, 11, 0, tzinfo=ZoneInfo("Europe/Athens")))` (a Friday after 10:00) must return Monday 2026-06-08, NOT 2026-06-15.
- Same call on Friday 09:59 and on Monday 00:01 must also return Monday 2026-06-08 of the respective week — the result no longer depends on the rollover moment.
- `get_data_through_date` for any time during the week of 2026-06-08 must return Sunday 2026-06-07.
- Run `compute_base_forecasts` on a weekend with a smooth item that has stable history — MA8 must not drop versus a mid-week run, and `trend_flag` must stay "flat".
- Existing unit tests that injected `_now_athens` after the rollover and expected `this_monday + 1 week` must be updated to expect `this_monday` (this behaviour change is the fix).

---

## Bug 4 — `int(qty)` truncates fractional PO quantities before sending

Two locations send/normalize PO line quantities with truncating `int()` casts. A snapshot quantity of 9.8 becomes 9 — a systematic underorder. Round up with `math.ceil` instead, since target cover must be met. Three edits in two files.

### Edit 4a — `blueprints/forecast_workbench.py`: add `math` import

**File:** `blueprints/forecast_workbench.py` (approx lines 3–5)

**FIND (appears once — top-of-file imports; do NOT touch the indented `import logging` inside the function around line 109):**

```python
import json
import logging
import re
```

**REPLACE WITH:**

```python
import json
import logging
import math
import re
```

### Edit 4b — `blueprints/forecast_workbench.py`: ceil the order quantity

**File:** `blueprints/forecast_workbench.py` (approx lines 1726–1730, inside `send_supplier_po`)

**FIND (appears once):**

```python
        if qty > 0:
            order_lines.append({
                "item_code_365": dw.item_code_365,
                "line_quantity": int(qty),
            })
```

**REPLACE WITH:**

```python
        if qty > 0:
            order_lines.append({
                "item_code_365": dw.item_code_365,
                "line_quantity": int(math.ceil(qty)),
            })
```

### Edit 4c — `services/ps365_purchase_order_service.py`: add `math` import and ceil in `_normalize_order_lines`

**File:** `services/ps365_purchase_order_service.py` (imports approx lines 11–14; quantity cast approx line 39)

**FIND (appears once):**

```python
import os
import json
import logging
```

**REPLACE WITH:**

```python
import os
import json
import logging
import math
```

**FIND (appears once):**

```python
        try:
            qty = int(float(ln.get("line_quantity") or 0))
        except (TypeError, ValueError):
            qty = 0
```

**REPLACE WITH:**

```python
        try:
            qty = int(math.ceil(float(ln.get("line_quantity") or 0)))
        except (TypeError, ValueError):
            qty = 0
```

**Testing checklist:**
- Unit test `_normalize_order_lines([{"item_code_365": "X1", "line_quantity": 9.8}])` — assert the output line has `"line_quantity": "10"` (the function stringifies qty), not `"9"`.
- Unit test with `line_quantity: 9.0` — assert `"9"` (whole numbers unchanged).
- Unit test with `line_quantity: 0.2` — assert `"1"` (was previously dropped as 0; rounding up is the intended cover-meeting behaviour).
- Unit test with `line_quantity: None` and `"abc"` — assert the line is skipped (qty 0 path unchanged).
- In `send_supplier_po`, a snapshot `rounded_order_qty` of 9.8 must produce `line_quantity: 10` in the logged order_lines.
- Both files import cleanly (`python -c "import services.ps365_purchase_order_service"` and app boot) — no `NameError: math`.

---

## Bug 5 — Lead time silently defaults to zero when no supplier mapping exists

**File:** `services/forecast/ordering_refresh_service.py` (approx lines 340–345, inside the per-item loop of `refresh_ordering_snapshot`)

`_resolve_supplier_context` initialises `"lead_time_days": 0.0` and only overwrites it when a `ForecastItemSupplierMap` row exists (`smap`). Items with no mapping get `lead_time = 0.0` silently: `lead_time_cover` is 0, `target_stock` is understated by a full lead time's demand, and the snapshot looks like a normal order. Do NOT change the calculation — only flag it so planners can see which items need supplier parameters set up. The flag goes into the snapshot's `explanation_json` and onto the profile's `review_flag`/`review_reason`. There is no existing review_reason writer in this file; the append pattern below copies the established semicolon-join pattern from `services/forecast/base_forecast_service.py` (its `review_notes` block: split existing `profile.review_reason` on `";"`, append if absent, re-join).

**FIND (appears once — note `"stock_source": "db_snapshot",` also appears in the function's return dict near line 416 with 8-space indent; this FIND is unique because of the 12-space indent plus the `if override:` line):**

```python
            "stock_source": "db_snapshot",
        }
        if override:
```

**REPLACE WITH:**

```python
            "stock_source": "db_snapshot",
        }

        # Lead time silently defaulted to 0 because no ForecastItemSupplierMap
        # row exists for this item. Do not change the calculation - flag the
        # item so planners set up its supplier parameters.
        if smap is None and lead_time == 0.0:
            explanation["review_reason"] = "LEAD_TIME_MISSING"
            if profile is not None:
                existing = profile.review_reason or ""
                parts = [r.strip() for r in existing.split(";") if r.strip()] if existing else []
                if "LEAD_TIME_MISSING" not in parts:
                    parts.append("LEAD_TIME_MISSING")
                profile.review_reason = "; ".join(parts)
                profile.review_flag = True

        if override:
```

Context for variable names (already in scope in the loop, no changes needed): `smap = supplier_map_cache.get(item_code)` , `lead_time = sup_ctx["lead_time_days"]`, `profile = profile_cache.get(item_code)`, and `explanation` is the dict assigned to `SkuOrderingSnapshot.explanation_json` a few lines below.

**Testing checklist:**
- Run `refresh_ordering_snapshot` for an item with no `ForecastItemSupplierMap` row — assert the new snapshot's `explanation_json["review_reason"] == "LEAD_TIME_MISSING"` and the profile has `review_flag == True` with `"LEAD_TIME_MISSING"` in `review_reason`.
- Assert the snapshot's `rounded_order_qty` for that item is byte-identical to the pre-fix value (calculation unchanged).
- Run for an item WITH a mapping whose `lead_time_days` is 0 (explicit zero) — assert NO flag is added (`smap is not None`).
- Run for an item with a mapping and `lead_time_days=3` — assert no flag and `lead_time_cover > 0` as before.
- Run the refresh twice for the unmapped item — assert `review_reason` contains `LEAD_TIME_MISSING` exactly once (no duplicate appends), matching the dedupe behaviour of the pattern in `base_forecast_service.py`.

---

## Order of application

Apply in any order; the edits are independent. After all edits, run the app and execute one forecast run + one ordering refresh + one replenishment generation end-to-end to confirm no import errors or regressions.
