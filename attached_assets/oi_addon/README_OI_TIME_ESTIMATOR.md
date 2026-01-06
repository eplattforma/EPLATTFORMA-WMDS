# OI Order Time Estimation (ETC) — Add-on Module (Option A: Auto on Import)

## What this is
This package adds **Estimated Time to Complete (ETC)** per invoice into your existing Flask/SQLAlchemy picking system.

You already have OI item classification stored on `ps_items_dw` (fields like `wms_fragility`, `wms_pick_difficulty`, etc.).  
This module **does not change classification** — it only reads those attributes and estimates time.

### Outputs written
- `invoice_items.exp_time` → expected minutes **per line**
- `invoices.total_exp_time` → expected minutes **per invoice**

Optionally, you can store a breakdown JSON (walk/pick/pack) later; v1 keeps it in-memory and returns it.

---

## Warehouse model assumptions (from your requirements)
### Location format: `10-01-A02`
- `10` = corridor
- `01` = shelf position (bay/section) inside corridor
- `A`  = level (A closest to ground)
- `02` = bin/slot (optional for travel; v1 uses it lightly)

### Corridors
- Main floor wide aisles: `09,10,11,12,13,14` (fast movement)
- Upper floor (stairs required): `70,80,90`
- **One upstairs trip per order** (route is ground first, then upstairs group)

### Ladder rule
- Level `C` is usually high and requires ladder (D treated as ladder by default; adjust if needed)

---

## Time model (explainable)
For each invoice:

**ETC = overhead + travel + Σ pick(line) + packing**

- **overhead**: start/end admin time (open order, labels, close order)
- **travel**: time to move between stops based on corridor/bay/pos changes + (stairs roundtrip once if upper corridors exist)
- **pick(line)**: base by unit_type + per-qty + ladder level + difficulty + handling penalties from OI
- **packing**: base + per line + per special handling group

---

## Administrator guide: parameters (what each one means)
All parameters are stored in `settings` as JSON:
- `settings.key = "oi_time_params_v1"`
- `settings.value = <JSON>`

### A) overhead
- `start_seconds`: time to start/prepare an order
- `end_seconds`: time to finish/close/stage an order

### B) location parsing
- `regex`: how to parse location string (corridor/bay/level/pos)
- `upper_corridors`: corridors upstairs (stairs needed)
- `ladder_levels`: which letters require ladder (typically C/D)

### C) travel (walking)
- `sec_align_per_stop`: stop + visually confirm + reach time at each pick stop
- `sec_per_corridor_change`: penalty when changing corridor
- `sec_per_corridor_step`: extra penalty if corridor jump is >1 (optional)
- `sec_per_bay_step`: time per bay movement within a corridor (01→02 etc.)
- `sec_per_pos_step`: small time within bay (A02→A05)
- `sec_stairs_up`, `sec_stairs_down`: one-time overhead if invoice includes any upper corridor item
- `upper_walk_multiplier`: speed adjustment upstairs (1.00 = same speed)

### D) pick handling
- `base_by_unit_type`: base seconds to pick 1 unit for each unit_type
- `per_qty_by_unit_type`: marginal seconds per additional unit/piece
- `level_seconds`: access penalty by level (C = ladder)
- `difficulty_seconds`: add seconds by `wms_pick_difficulty` (1–5)
- `handling_seconds`:
  - `fragility_yes`, `fragility_semi`
  - `spill_true`
  - `pressure_high`
  - `heat_sensitive_summer` (only when summer_mode ON)

### E) packing
- `base_seconds`: setup time at packing bench
- `per_line_seconds`: per order line handling
- `special_group_seconds`: extra seconds per special group present (fragile/spill/pressure/heat-sensitive in summer)

### F) summer mode
Stored separately as:
- `settings.key="summer_mode"` → "true"/"false"

When true:
- `wms_temperature_sensitivity == "heat_sensitive"` triggers extra handling + packing group.

---

## Recommended starting parameter JSON
Create/Update in DB (settings table) with key `oi_time_params_v1`:

See `oi_time_params_v1.json` in this package.

---

## Option A integration (automatic on import)
After your invoice import finishes inserting/updating `invoice_items` for an invoice:

Call:
```python
from services.oi_time_estimator import estimate_and_persist_invoice_time
estimate_and_persist_invoice_time(invoice_no)
```

This will update:
- each `invoice_items.exp_time` (minutes)
- invoice `invoices.total_exp_time` (minutes)

---

## Calibration (how you improve accuracy)
You already log:
- `item_time_tracking.total_item_time` (seconds)
- `order_time_breakdown` (picking/packing timestamps)

Weekly tuning loop:
1) Compare predicted vs actual by invoice (total minutes)
2) Adjust in this order:
   - travel parameters (corridor/bay/pos, stairs)
   - ladder penalties
   - unit_type base & per-qty
   - packing parameters
3) Re-run estimates (admin button) for open invoices to see effect.

---

## Files in this package
- `services/oi_time_estimator.py` → core estimator and persistence
- `oi_time_params_v1.json` → starter parameters
- `admin_ui_snippets.md` → copy/paste snippets to add buttons + parameter editor on `/admin/oi/dashboard`

