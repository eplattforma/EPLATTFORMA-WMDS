# Operational Intelligence Manual + Replit Implementation Instructions
## Add as a Button on `/admin/oi/dashboard`

This file contains:
1) A comprehensive **Operational Intelligence manual** (copy/paste ready).
2) **Implementation instructions** for Replit to add a **“Manual / Help”** button on `/admin/oi/dashboard` that opens the manual page.

---

# PART A — OPERATIONAL INTELLIGENCE MANUAL
## Picking & Packing Extended Item Classification (Moderate Mode)

### Version / Scope
- Mode: **Moderate** (confidence threshold **≥ 60**)
- Execution: **Manual only** (Admin clicks **Reclassify Items**)
- Scope: **Active items only** (`ps_items_dw.active = TRUE`)

---

## 1) Purpose
Operational Intelligence (OI) adds warehouse-relevant attributes to your item master so the system can:
- standardize safe packing decisions (fragility, spill, pressure, temperature, box-fit),
- create predictable handling flows (zones),
- reduce damage / returns,
- and build an explainable, auditable process for improving classification over time.

OI is designed as an **extension** of your item master (ERP remains the source of truth for core item data).

---

## 2) Key Concepts

### 2.1 Active Items Only
Only items where `active = TRUE` are processed. Inactive SKUs remain unchanged.

### 2.2 Precedence (Final Value Resolution)
For each attribute, the final stored value is chosen in this order:
1) **SKU Override** (manual per item)
2) **Category Default** (manual per category, field-by-field)
3) **Computed Rules** (automated classification)
4) If confidence is below threshold and no default/override exists → **NULL (blank)**

This prevents unsafe guesses.

### 2.3 Confidence Gating (Moderate ≥ 60)
The rule engine produces `(value, confidence, reason)` per attribute.
- If confidence **≥ 60**, the system may store the value (unless overridden).
- If confidence **< 60**, the system leaves it **blank** unless a default/override fills it.

### 2.4 Needs Review
An item is “Needs Review” when:
- Any **critical attribute** is NULL, OR
- Overall confidence < 60

Critical attributes (recommended):
- `Fragility`
- `Spill Risk`
- `Pressure Sensitivity`
- `Temperature Sensitivity`
- `Box Fit Rule`

---

## 3) Roles & Responsibilities

### Admin / Warehouse Manager
- Runs “Reclassify Items”
- Maintains Category Defaults
- Maintains SKU Overrides
- Resolves “Needs Review”
- Validates category-level assumptions by sampling SKUs

### Picking / Packing Team
- Follows handling outputs (badges/instructions)
- Reports incidents (crushed / leaked / melted / broken), feeding improvements

---

## 4) Attribute Definitions (Warehouse Meaning)

### 4.1 Zone (`wms_zone`)
Defines how the item flows through picking/packing (not just where it is stored).

Values:
- `MAIN` – standard flow
- `SENSITIVE` – special handling / special sequencing
- `SNACKS` – crush-prone snack flow
- `CROSS_SHIPPING` – same-day received-to-ship flow

### 4.2 Unit Type (`wms_unit_type`)
Defines what the picker physically handles.

Values:
- `item`, `pack`, `box`, `case`, `virtual_pack`

### 4.3 Fragility (`wms_fragility`)
Damage risk under normal handling: break, bend, crush, melt, dent.

Values:
- `YES` (high risk), `SEMI` (moderate), `NO` (robust)

### 4.4 Pressure Sensitivity (`wms_pressure_sensitivity`)
Will the item deform/become unsellable if something is placed on it?

Values:
- `high`, `medium`, `low`

Important:
- Cartons (e.g., cereals) may be **pressure-sensitive** even if not “fragile glass.”

### 4.5 Spill Risk (`wms_spill_risk`)
If it leaks, will it contaminate the order?

Values:
- `TRUE` / `FALSE`

### 4.6 Temperature Sensitivity (`wms_temperature_sensitivity`)
Heat/cold handling requirements.

Values:
- `normal`, `heat_sensitive`, `cool_required`

### 4.7 Box Fit Rule (`wms_box_fit_rule`)
Packing placement guidance.

Values:
- `BOTTOM` – heavy/stable/upright spill-contained
- `MIDDLE` – general items
- `TOP` – fragile/pressure-sensitive
- `COOLER_BAG` – heat-sensitive items when hot-weather handling is enabled

---

## 5) Summer Mode (Hot Weather Handling)
“Summer mode” is a manual operational toggle (Setting) that enables special handling for heat-sensitive items.

When ON:
- Items flagged `heat_sensitive` can trigger `COOLER_BAG` handling and/or SENSITIVE flow.

When OFF:
- Heat-sensitive flags remain but do not enforce cooler handling.

Recommended: Admin toggle, not calendar-based automation.

---

## 6) Dashboard Metric: Critical Attribute Coverage
Coverage = % of **active items** with a **non-NULL** value for that attribute.

Low early coverage is expected with confidence gating. The objective is to increase coverage safely using:
- Category Defaults (high leverage)
- SKU Overrides (exceptions)
- Improved mappings/rules (structural improvements)

---

## 7) SOP Workflow (Recommended)

1) Admin: **Reclassify Items**
2) Admin: Open **Needs Review** filter
3) Fix patterns:
   - use **Category Defaults** when the category is consistent
   - use **SKU Overrides** for exceptions
4) Re-run **Reclassify Items**
5) Monitor coverage & incident feedback

---

## 8) Governance: How Rules Improve Over Time
Day-to-day tuning:
- Category defaults and SKU overrides (auditable and reversible)

Occasional structural change:
- rules/mappings code changes, after operational review

---

## 9) Troubleshooting

- Coverage is low: add category defaults for clear categories; do not force unsafe guesses.
- A category is ambiguous: leave defaults blank and handle via overrides until you confirm patterns.
- Temperature is 100% but others are low: verify you are not forcing a universal default unintentionally.

---

## 10) Enumerations (Standardized Values)
- Fragility: `YES`, `SEMI`, `NO`
- Spill Risk: `TRUE`, `FALSE`
- Pressure Sensitivity: `low`, `medium`, `high`
- Temperature Sensitivity: `normal`, `heat_sensitive`, `cool_required`
- Box Fit Rule: `BOTTOM`, `MIDDLE`, `TOP`, `COOLER_BAG`
- Zone: `MAIN`, `SENSITIVE`, `SNACKS`, `CROSS_SHIPPING`

---

# PART B — INSTRUCTIONS TO REPLIT (IMPLEMENTATION TASK)

## Goal
Add a **Manual / Help** button on `/admin/oi/dashboard` that opens an HTML page rendering the manual above.

### Required outcomes
- Button visible on `/admin/oi/dashboard`
- Clicking opens `/admin/oi/manual`
- Manual page is admin-protected (same authorization as dashboard)
- Manual content is stored in a template or rendered from a constant string
- Uses Bootstrap styling consistent with the rest of the admin UI

---

## 1) Add Route (server-rendered, no API)

### Option A (recommended): add to your existing OI admin routes file
Create route:
- `GET /admin/oi/manual`

Example (Flask):

```python
from flask import render_template
from flask_login import login_required, current_user
from functools import wraps

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if current_user.role not in ("admin", "warehouse_manager"):
            return ("Forbidden", 403)
        return f(*args, **kwargs)
    return wrapper

@bp.route("/admin/oi/manual", methods=["GET"])
@login_required
@admin_required
def oi_manual():
    return render_template("admin/oi_manual.html")
```

If you do not use a blueprint, use `@app.route(...)` and match your existing style.

---

## 2) Create Template: `templates/admin/oi_manual.html`

Recommended template structure (Bootstrap 5):

```html
{% extends "base.html" %}
{% block content %}
<div class="container-fluid py-3">

  <div class="d-flex align-items-center justify-content-between mb-3">
    <div>
      <h3 class="mb-0">Operational Intelligence Manual</h3>
      <div class="text-muted">Picking & Packing Extended Item Classification (Moderate Mode)</div>
    </div>
    <div class="d-flex gap-2">
      <a href="/admin/oi/dashboard" class="btn btn-outline-secondary">Back to Dashboard</a>
    </div>
  </div>

  <div class="card shadow-sm">
    <div class="card-body">
      <!-- Paste the manual content here as HTML sections -->
    </div>
  </div>

</div>
{% endblock %}
```

Implementation note:
- Convert headings into `<h4>` and use lists for readability.
- Alternatively store the manual as Markdown and render it if you already have a Markdown utility.

---

## 3) Add Button on `/admin/oi/dashboard`

In the dashboard template (where “Reclassify Items” appears), add:

```html
<a href="/admin/oi/manual" class="btn btn-outline-light">
  Manual / Help
</a>
```

Place it near “Reclassify Items” so admins can reference rules while reviewing “Needs Review.”

---

## 4) Acceptance Checks
- `/admin/oi/manual` loads correctly and is styled.
- Unauthorized users cannot access the manual.
- Button appears on `/admin/oi/dashboard`.
- Manual text matches Part A.

---

## 5) Governance
Treat this manual as the source of truth. Update it whenever:
- enumerations change,
- attribute definitions change,
- or workflow changes.
