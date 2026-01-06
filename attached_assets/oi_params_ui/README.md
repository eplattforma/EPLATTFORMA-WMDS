# OI – ETC Parameters UI (Replit Upload Package)

This package adds a **user-friendly admin UI** to edit *Order Time Estimation (ETC)* parameters and to **recalculate** order estimates.

It assumes your existing system already has:
- `invoices.total_exp_time` (float) used as “estimated minutes per order”
- `invoice_items.exp_time` (float) used as “estimated minutes per line”
- OI item attributes already stored on your items dimension table (your existing OI classification work)

## What you get

- Admin UI page: **`/admin/oi/time-params`**
  - Structured editor (forms) for all parameters
  - “Advanced JSON” modal editor
  - Import/Export JSON buttons
  - “Summer Mode” toggle
  - Recalculate for a single invoice or bulk open invoices
- Service: `services/oi_time_estimator.py` (parameter-driven estimator)
- Blueprint route: `routes/oi_time_admin.py`

## Files included

- `services/oi_time_estimator.py`
- `routes/oi_time_admin.py`
- `templates/admin/oi_time_params.html`
- `static/js/oi_time_params.js`
- `static/css/oi_time_params.css`

## Integration steps (copy/paste)

1) Copy the folders into your Replit project (same level as `app.py`):

- `services/oi_time_estimator.py`
- `routes/oi_time_admin.py`
- `templates/admin/oi_time_params.html`
- `static/js/oi_time_params.js`
- `static/css/oi_time_params.css`

2) Register the blueprint in `app.py` (after app creation):

```python
from routes.oi_time_admin import oi_time_admin_bp
app.register_blueprint(oi_time_admin_bp)
```

3) Add a button on your existing **`/admin/oi/dashboard`** page.

In the dashboard template (where your other admin buttons live), add:

```html
<a class="btn btn-outline-primary" href="/admin/oi/time-params">
  ETC Parameters
</a>
```

## Administrator guide (what each parameter means)

### Summer Mode
Stored in: `settings.summer_mode` (`true` / `false`).

When enabled:
- Items with `wms_temperature_sensitivity = heat_sensitive` get extra handling seconds (`pick.handling_seconds.heat_sensitive_summer`).
- You can later use the same flag to enforce cooler-bag packing rules.

### Location parsing
- `location.regex`: Parses `invoice_items.location` like `10-01-A02`
  - corridor = `10`
  - bay = `01`
  - level = `A`
  - pos = `02`

- `location.upper_corridors`: Corridors requiring stairs (e.g., `70,80,90`)
- `location.ladder_levels`: Levels usually requiring ladder (e.g., `C,D`)

### Overhead
- `overhead.start_seconds`: fixed time at the start of every order
- `overhead.end_seconds`: fixed time at the end of every order

### Travel
Travel is computed based on the ordered sequence of unique stops.

- `travel.sec_align_per_stop`: micro-delay per stop (look/scan/confirm)
- `travel.zone_switch_seconds`: cost when zone changes
- `travel.sec_per_corridor_change`: fixed penalty when corridor changes
- `travel.sec_per_corridor_step`: variable penalty per corridor difference (10 → 12 adds 2 steps)
- `travel.sec_per_bay_step`: penalty per bay difference within corridor
- `travel.sec_per_pos_step`: penalty per position difference within bay
- `travel.upper_walk_multiplier`: multiplier for upstairs travel movement (narrower, slower, stairs)
- `travel.sec_stairs_up`, `travel.sec_stairs_down`: added once if any stop is upstairs

### Pick
Per line:
- `pick.base_by_unit_type[unit_type]`: base seconds to pick the first unit
- `pick.per_qty_by_unit_type[unit_type]`: extra seconds for each additional unit after the first
- `pick.level_seconds[level]`: penalty based on shelf level (A/B/C/D)
- `pick.difficulty_seconds[1..5]`: penalty based on item pick difficulty
- `pick.handling_seconds.*`: penalties for fragile/spill/pressure/summer heat-sensitive items

Data read from your items table (expected field names):
- `wms_fragility` (YES/SEMI/NO)
- `wms_spill_risk` (TRUE/FALSE)
- `wms_pressure_sensitivity` (high/medium/low)
- `wms_temperature_sensitivity` (heat_sensitive/normal)
- `wms_pick_difficulty` (1–5)

If your actual column names differ, update the getters inside:
- `estimate_pick_seconds_for_line()`
- `estimate_pack_seconds()`

### Packing
- `pack.base_seconds`: fixed packing start time
- `pack.per_line_seconds`: incremental packing per line
- `pack.special_group_seconds`: added for each special group present (fragile/spill/pressure/heat)

## Notes / constraints

- Bulk recalc is capped at 500 invoices per run (safety).
- Role gate: `admin` and `warehouse_manager` are allowed by default. Adjust `_require_admin()` as needed.
- Template extends `base.html`. If your admin templates use a different base, edit:
  - `templates/admin/oi_time_params.html`

## Verification checklist

1) Open: `/admin/oi/time-params`
2) Save parameters → confirm row updated in `settings` table with key `oi_time_params_v1`
3) Toggle Summer Mode → confirm `settings.summer_mode` updates
4) Recalc a known invoice → confirm:
   - `invoices.total_exp_time` populated
   - `invoice_items.exp_time` populated

