# Picking & Packing Intelligence – Implementation Guide (Single-File Spec for Replit)

This document is a **single, uploadable implementation spec** for your existing Flask + SQLAlchemy warehouse app to add:

1) **Explainable, confidence-gated item classification** for picking & packing  
2) **Manual overrides** (SKU-level) and **category defaults** (optional)  
3) A **clean Admin UI** to manage, review, and re-run classification  
4) **Manual “Reclassify” only** execution (no cron / background jobs)

> Scope notes (as agreed)
- **No API endpoints required** (use server-rendered routes + POST forms).
- Uses your existing DB and existing item master table: **`ps_items_dw`** (SQLAlchemy model: `DwItem`).
- Classifies **only active items** (`DwItem.active == True`).
- Classification runs **only when Admin clicks “Reclassify Items”**.

---

## 1) Data Model

### 1.1 Existing source table
- DB table: `ps_items_dw`
- SQLAlchemy model: `DwItem` (already present in your `models.py`)

This table already contains the raw signals:
- `category_code_365`, `brand_code_365`
- `attribute_1_code_365 … attribute_6_code_365`
- `item_length`, `item_width`, `item_height`, `item_weight`
- `number_of_pieces`, `item_name`
- `active`

### 1.2 New Operational Intelligence attributes (stored per SKU on `ps_items_dw`)
Add these fields to the table and to the `DwItem` model:

**Classification outputs**
- `wms_zone` *(TEXT)*: `MAIN`, `SENSITIVE`, `SNACKS`, `CROSS_SHIPPING`
- `wms_unit_type` *(TEXT)*: `item`, `pack`, `box`, `case`, `virtual_pack`
- `wms_fragility` *(TEXT)*: `YES`, `SEMI`, `NO`
- `wms_stackability` *(TEXT)*: `YES`, `LIMITED`, `NO`
- `wms_temperature_sensitivity` *(TEXT)*: `normal`, `heat_sensitive`, `cool_required`
- `wms_pressure_sensitivity` *(TEXT)*: `low`, `medium`, `high`
- `wms_shape_type` *(TEXT)*: `cubic`, `flat`, `round`, `irregular`
- `wms_spill_risk` *(BOOLEAN)*
- `wms_pick_difficulty` *(INTEGER 1–5)*
- `wms_shelf_height` *(TEXT)*: `LOW`, `MID`, `HIGH`
- `wms_box_fit_rule` *(TEXT)*: `BOTTOM`, `MIDDLE`, `TOP`, `COOLER_BAG`

**Audit / explainability**
- `wms_class_confidence` *(INTEGER 0–100)* overall confidence for the last run
- `wms_class_source` *(TEXT)*: `RULES` | `CATEGORY_DEFAULT` | `MANUAL`
- `wms_class_notes` *(TEXT)* human-readable summary
- `wms_classified_at` *(DATETIME UTC)*
- `wms_class_evidence` *(TEXT JSON)* optional but recommended: per-attribute `{value, confidence, reason}`

> Naming: prefix `wms_` to avoid collisions with PS365 fields.

### 1.3 New tables for maintainability (recommended)
You can start with code-only rules, but confidence will improve dramatically with these two small tables.

#### A) Category defaults
Table: `wms_category_defaults`

Fields:
- `category_code_365` *(TEXT, PK)*
- `default_zone`, `default_fragility`, `default_stackability`, `default_temperature_sensitivity`, `default_pressure_sensitivity`,
  `default_shape_type`, `default_spill_risk`, `default_pick_difficulty`, `default_shelf_height`, `default_box_fit_rule`
- `is_active` *(BOOLEAN default True)*
- `notes` *(TEXT)*
- `updated_by` *(TEXT, FK users.username optional)*
- `updated_at` *(DATETIME UTC)*

Rules:
- If a default field is **NULL** => it **does not force** a value.
- Category defaults are applied only when **no SKU override exists**.

#### B) SKU overrides
Table: `wms_item_overrides`

Fields:
- `item_code_365` *(TEXT, PK)*
- Per-attribute override columns (nullable), e.g. `fragility_override`, `spill_risk_override`, etc.
- `override_reason` *(TEXT)*
- `updated_by` *(TEXT)*
- `updated_at` *(DATETIME UTC)*
- `is_active` *(BOOLEAN default True)*

Rules:
- If a SKU override column is non-NULL => it **wins**.

#### C) Classification run log (optional but helpful)
Table: `wms_classification_runs`
- `id` *(PK)*
- `started_at`, `finished_at` (UTC)
- `run_by` (username)
- `mode` (`moderate_60`)
- `active_items_scanned`, `items_updated`, `items_needing_review`
- `notes` (text)

---

## 2) Confidence-Gated Classification (Moderate Mode)

### 2.1 Threshold
- **Moderate threshold = 60**
- For each attribute: compute `(value, confidence, reason)`
- Store the value **only if confidence >= 60**, else store `NULL` for that attribute (unless default/override fills it).

### 2.2 Resolution precedence (final attribute value)
For each attribute, resolve in this order:

1) **SKU override** (highest priority)  
2) **Category default** (only if explicitly set for that field)  
3) **Computed rule result** (only if confidence >= 60)  
4) Else `NULL`

Additionally:
- `wms_class_source` is:
  - `MANUAL` if any override field was used
  - else `CATEGORY_DEFAULT` if any default field was used
  - else `RULES`

### 2.3 “Needs Review” definition
An item is flagged as Needs Review if:
- `active == True` AND
- Any critical attribute is NULL:
  - `wms_fragility`, `wms_spill_risk`, `wms_pressure_sensitivity`, `wms_temperature_sensitivity`, `wms_box_fit_rule`
OR
- `wms_class_confidence < 60`

---

## 3) Rule Engine Design

### 3.1 File structure (recommended)
Create a small module tree:

```
classification/
  __init__.py
  rules.py          # attribute rules
  resolver.py       # override/default/rule resolution logic
  engine.py         # reclassify loop + DB writes
  mappings.py       # category code mappings (editable in one place)
```

### 3.2 Attribute computation contract
Each function must return:

```
(value: Any | None, confidence: int, reason: str)
```

Example signature:

```python
def compute_fragility(dw_item: DwItem) -> tuple[str|None, int, str]:
    ...
```

### 3.3 Computation rules – baseline logic (starting point)

> IMPORTANT: this is a safe baseline. It must be validated in your Admin UI, then improved with defaults/overrides.

#### A) Unit type (SKU-level)
Derived from `attribute_1_code_365`:
- `VPACK` => `virtual_pack` (piece pick)
- `PAC`   => `pack`
- `BOX`   => `box`
- `CASE`  => `case`
- else    => `item`

Confidence:
- 90 if attribute_1_code_365 is known
- 40 if missing/unknown

#### B) Spill risk
High-confidence if:
- category indicates liquids OR
- item_name suggests liquid packaging (ml, L, “bottle”, “spray”)

Confidence:
- 90 for known liquid categories (e.g., cleaning, juices, spirits)
- 75 for strong name keyword match
- 30 otherwise

#### C) Fragility
Interpretation: breaks/melts/bends/crushes in normal handling.

High-confidence if:
- category indicates **glass bottles** (spirits) => YES
- category indicates **chocolate** => YES
- category indicates **chips/snacks** => YES (crushable)
- category indicates biscuits/cookies => SEMI
- robust staples (pet food, many cleaning packs) => NO

Confidence:
- 85–95 for strong category
- 70 for strong name keyword match
- <60 otherwise => NULL in moderate mode

#### D) Pressure sensitivity
- Snacks/chips => high
- Cartons (e.g., cereals) => medium
- Glass bottles => medium
- Robust cases/cartons => low

#### E) Stackability
Derived primarily from fragility + pressure sensitivity:
- If fragile YES => NO
- If pressure high => NO
- If semi/medium => LIMITED
- Else YES

Confidence depends on inputs:
- If inputs were stored (>=60) => 70+
- If inputs are missing => 40 (leave NULL)

#### F) Temperature sensitivity
- Chocolate => heat_sensitive (90)
- Else normal (60 if category is known; else 40)

#### G) Shape type
- Bottles/sprays => round
- Envelopes/folders => flat
- Else cubic
- Irregular for “organizer/set/kit” type items (optional)

#### H) Pick difficulty (1–5)
Composite score from:
- weight bands (if weight present)
- fragility / pressure sensitivity
- shelf height (if known)
Default safe:
- if missing key inputs => set NULL (confidence < 60)

#### I) Shelf height
If you do not have real shelf/level fields yet:
- leave NULL (recommended) OR set a weak heuristic with confidence < 60 (won’t be stored)
When you later add `shelf/level` signals, make it deterministic.

#### J) Box-fit rule
Derived only if prerequisites exist (fragility/spill/pressure/temperature):
- if heat_sensitive and summer_mode => COOLER_BAG
- else if spill_risk True and heavy/case => BOTTOM
- else if fragile YES or pressure high => TOP
- else => MIDDLE

Confidence:
- 75+ only if prerequisites are high-confidence
- else <60 => NULL

---

## 4) Implementing Overrides and Defaults

### 4.1 Resolver function (copy/paste spec)
Implement a generic resolver:

```python
def resolve_attribute(
    attr_name: str,
    computed_value, computed_conf, computed_reason,
    item_override_value,
    category_default_value,
    threshold: int = 60
):
    if item_override_value is not None:
        return item_override_value, 100, f"MANUAL override for {attr_name}"
    if category_default_value is not None:
        return category_default_value, 85, f"CATEGORY default for {attr_name}"
    if computed_conf >= threshold:
        return computed_value, computed_conf, computed_reason
    return None, computed_conf, f"AMBIGUOUS (<{threshold}) – {computed_reason}"
```

### 4.2 How improvements happen (operational loop)
1) Admin runs **Reclassify Items** (moderate 60).
2) Admin opens **Needs Review** list.
3) For repeated patterns:
   - set **category defaults** if the category is consistent
   - set **SKU overrides** for exceptions
4) Re-run classification to propagate.

This is the expected, controlled way to reach confidence.

---

## 5) Admin UI Design (Nice, practical, and fast)

### 5.1 Navigation
Add a top-level Admin menu item:
- **Operational Intelligence**
  - Item Classifications
  - Category Defaults
  - SKU Overrides
  - Runs / Audit

### 5.2 Page 1 — Dashboard (`/admin/oi/dashboard`)
**Goal:** immediate trust and monitoring.

Layout (cards + tables):
- Card: Active items count
- Card: Classified last run time + run_by
- Card: Needs Review count
- Card: % coverage of critical attributes (non-NULL)

Buttons:
- **Reclassify Items** (POST)
- **Reclassify Needs Review Only** (optional later)

Tables:
- “Top ambiguous categories” (category with most Needs Review SKUs)
- “Recent overrides” (last 20)

### 5.3 Page 2 — Items list (`/admin/oi/items`)
**Goal:** review and edit quickly.

Filters row:
- Search: item code / name
- Category dropdown (from `dw_item_categories`)
- Brand dropdown (from `dw_brands`)
- Toggle: Active only (default ON)
- Toggle: Needs Review only
- Zone dropdown
- Fragility dropdown

Table columns (recommended):
- Item code
- Item name
- Category (code + name)
- Brand
- **Badges** for: zone, fragility, spill, pressure, temp
- Box-fit rule badge
- Confidence (progress bar)
- Source (`RULES`, `CATEGORY_DEFAULT`, `MANUAL`)
- Actions: **Override…** (opens modal), View evidence

Row UX:
- Click row => detail drawer/modal with:
  - Computed values vs Final values
  - Evidence reasons per attribute
  - Override editor

### 5.4 Page 3 — Category Defaults (`/admin/oi/categories`)
**Goal:** “Fix a whole family” without editing code.

Table columns:
- category_code_365, category_name
- default fragility, default spill, default pressure, default temp, default box-fit, default zone
- “coverage” = number of active SKUs in category
- “needs review” count in category
- Actions: Edit defaults

Editing:
- Inline dropdowns for each default
- Save button per row (POST)
- Leave field blank to mean “no default”

### 5.5 Page 4 — SKU Overrides (`/admin/oi/overrides`)
**Goal:** precise exceptions.

Table:
- item_code_365, item_name
- overridden fields shown as chips
- reason, updated_by, updated_at
- Actions: Edit / Disable

### 5.6 UI styling guidelines (Bootstrap 5)
Use Bootstrap 5 (fast, clean):
- Badges for categorical values
- Soft background cards
- Progress bar for confidence
- Modal for editing overrides
- Sticky filter row

Badge suggestions:
- Fragility: YES (danger), SEMI (warning), NO (secondary)
- Spill risk: True (info), False (secondary)
- Temp heat_sensitive: (warning)
- Box-fit: BOTTOM (dark), MIDDLE (primary), TOP (light), COOLER_BAG (info)

---

## 6) Routes and Templates (No API Endpoints)

### 6.1 Route list
All server-rendered routes (admin only):

- `GET  /admin/oi/dashboard`
- `POST /admin/oi/reclassify`  (runs engine)
- `GET  /admin/oi/items`
- `GET  /admin/oi/item/<item_code_365>` (optional detail)
- `POST /admin/oi/item/<item_code_365>/override`
- `GET  /admin/oi/categories`
- `POST /admin/oi/category/<category_code_365>/defaults`
- `GET  /admin/oi/overrides`
- `POST /admin/oi/override/<item_code_365>/disable` (optional)
- `GET  /admin/oi/runs` (optional)

### 6.2 Security
- Require `current_user.role == 'admin'` (or warehouse_manager).
- Log actions to your `ActivityLog`.

---

## 7) Database Changes (Practical in Replit)

### 7.1 If you do not use Alembic migrations
Implement a one-time admin script or CLI route that:
- checks for missing columns using `information_schema`
- executes `ALTER TABLE` statements

Example approach:
- Add an “Admin → DB Upgrade” page (or run a script once).

### 7.2 Example SQL (Postgres-style)
(Adjust types for your DB if needed.)

```sql
ALTER TABLE ps_items_dw
  ADD COLUMN IF NOT EXISTS wms_zone TEXT,
  ADD COLUMN IF NOT EXISTS wms_unit_type TEXT,
  ADD COLUMN IF NOT EXISTS wms_fragility TEXT,
  ADD COLUMN IF NOT EXISTS wms_stackability TEXT,
  ADD COLUMN IF NOT EXISTS wms_temperature_sensitivity TEXT,
  ADD COLUMN IF NOT EXISTS wms_pressure_sensitivity TEXT,
  ADD COLUMN IF NOT EXISTS wms_shape_type TEXT,
  ADD COLUMN IF NOT EXISTS wms_spill_risk BOOLEAN,
  ADD COLUMN IF NOT EXISTS wms_pick_difficulty INTEGER,
  ADD COLUMN IF NOT EXISTS wms_shelf_height TEXT,
  ADD COLUMN IF NOT EXISTS wms_box_fit_rule TEXT,
  ADD COLUMN IF NOT EXISTS wms_class_confidence INTEGER,
  ADD COLUMN IF NOT EXISTS wms_class_source TEXT,
  ADD COLUMN IF NOT EXISTS wms_class_notes TEXT,
  ADD COLUMN IF NOT EXISTS wms_classified_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS wms_class_evidence TEXT;
```

Create defaults/overrides tables:

```sql
CREATE TABLE IF NOT EXISTS wms_category_defaults (
  category_code_365 TEXT PRIMARY KEY,
  default_zone TEXT,
  default_fragility TEXT,
  default_stackability TEXT,
  default_temperature_sensitivity TEXT,
  default_pressure_sensitivity TEXT,
  default_shape_type TEXT,
  default_spill_risk BOOLEAN,
  default_pick_difficulty INTEGER,
  default_shelf_height TEXT,
  default_box_fit_rule TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  notes TEXT,
  updated_by TEXT,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wms_item_overrides (
  item_code_365 TEXT PRIMARY KEY,
  zone_override TEXT,
  unit_type_override TEXT,
  fragility_override TEXT,
  stackability_override TEXT,
  temperature_sensitivity_override TEXT,
  pressure_sensitivity_override TEXT,
  shape_type_override TEXT,
  spill_risk_override BOOLEAN,
  pick_difficulty_override INTEGER,
  shelf_height_override TEXT,
  box_fit_rule_override TEXT,
  override_reason TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  updated_by TEXT,
  updated_at TIMESTAMP
);
```

---

## 8) Engine Execution Spec (“Reclassify Items”)

### 8.1 Engine loop
On POST `/admin/oi/reclassify`:

1) Read mode settings:
   - `threshold = 60`
   - `summer_mode` (from `Setting.get('summer_mode')` optional)
2) Query:
   - `items = DwItem.query.filter(DwItem.active == True).all()`
3) For each item:
   - compute each attribute → `(val, conf, reason)`
   - load category defaults row for item category (if any)
   - load item override row for item (if any)
   - resolve each attribute
   - build `evidence` dict per attribute
   - store final values on item + audit fields
4) Commit once at end (or batch commit)
5) Save a run log row
6) Redirect to dashboard with summary.

### 8.2 Overall confidence
Compute `wms_class_confidence` as:
- average confidence of **stored** critical attributes
- if none stored => 0

---

## 9) Testing & Validation

### 9.1 Add “Known cases” tests
Create a simple test dataset for categories like:
- ALD: spirits in glass bottles => fragility YES, spill True, shape round
- CER: cereals cartons => pressure medium, stackability limited
- CHO: chocolate => heat_sensitive, fragility YES
- SNA: chips => pressure high, stackability NO

### 9.2 UI-based validation
Use the dashboard:
- Needs Review count should **drop** as you add defaults/overrides.
- Unknown/blank attributes should be visible and actionable.

---

## 10) Implementation Checklist

**DB**
- [ ] Add `wms_*` columns to `ps_items_dw`
- [ ] Create `wms_category_defaults`
- [ ] Create `wms_item_overrides`
- [ ] (Optional) Create `wms_classification_runs`

**Backend**
- [ ] Add SQLAlchemy models for the two new tables
- [ ] Create `classification/` module with rules + engine
- [ ] Add Admin routes (server-rendered)
- [ ] Add “Needs Review” query

**UI**
- [ ] Add Admin navigation entry “Operational Intelligence”
- [ ] Dashboard page with KPIs + actions
- [ ] Items page with filters, badges, confidence bar, override modal
- [ ] Category defaults page with inline editing
- [ ] Overrides page listing + edit

**Governance**
- [ ] Use overrides/defaults rather than code edits for day-to-day tuning
- [ ] Keep code changes for structural rule improvements only

---

## 11) Notes on Practical Improvements (Why this will build confidence)
- Confidence gating prevents bad guesses from being stored.
- Overrides/defaults give you control without changing code.
- Evidence strings make every outcome explainable.
- Needs Review workflow ensures ambiguous items are surfaced early.

---

## 12) UI Wireframe (Text)

### Dashboard
- [Reclassify Items] [View Needs Review]
- Cards: Active Items | Needs Review | Last Run | Coverage %
- Table: Ambiguous Categories (category, active SKUs, needs review)
- Table: Recent Overrides (item, fields, user, date)

### Items
- Filter bar (sticky)
- Table rows with badges + confidence bar
- Action: Override… (modal)
- Modal tabs: Final vs Computed | Evidence | Set Overrides

### Category Defaults
- Row-per-category with inline dropdowns
- Save per row
- Leave blank => no default

### Overrides
- Search by item code/name
- Clear/disable override

---

If you want me to tailor the mapping rules to your real category codes (e.g., ALD, CER, CHO, SNA), implement them in `classification/mappings.py` as a single dict and keep everything else generic.
