# EP SmartGrowth CRM — Customer Tier Classification

Build a 5-tier customer performance classification system into the CRM dashboard. Each customer gets a tier badge based on their Recency, Frequency, Monetary value and trend direction. The tier is filterable, auditable, and self-updating on every page load.

---

## Overview of changes

| # | File | What |
|---|------|------|
| 1 | `services/crm_tier_service.py` (new) | Tier logic — isolated, testable |
| 2 | `routes_crm_dashboard.py` | Call tier service, add tier to each row, add tier filter |
| 3 | `templates/crm/dashboard.html` | Tier badge on each row, tier filter dropdown, tier KPI counts |
| 4 | `routes.py` (admin settings) | Configurable thresholds via existing Settings table |

---

## The 5 tiers

| Tier | Code | Colour | Profile |
|------|------|--------|---------|
| Champion | `champion` | Green | High spender, buying frequently and recently |
| Active | `active` | Blue | Regular buyer, consistent engagement |
| At Risk | `at_risk` | Orange/Red | Has spending history but going quiet |
| Dormant | `dormant` | Grey | No recent purchasing activity |
| Potential | `potential` | Purple | No invoice history yet, prospect stage |

Each tier also carries a **trend indicator**:
- ▲ Growing — last 4 weeks annualised > 6-month average by 15%+
- → Stable — within ±15%
- ▼ Declining — last 4 weeks annualised < 6-month average by 15%+

---

## CHANGE 1 — New file `services/crm_tier_service.py`

Create this file:

```python
"""
EP SmartGrowth CRM — Customer Tier Classification Service

Computes a performance tier for each customer based on:
  - Recency:   days since last invoice
  - Frequency: invoice count in last 90 days
  - Monetary:  6-month spend value
  - Trend:     4-week annualised vs 6-month spend

Thresholds are read from the Settings table so they can be tuned
without code changes. Sensible defaults are applied if not set.
"""
from __future__ import annotations


# ── Default thresholds (overridden by Settings if present) ──────────────────
DEFAULTS = {
    "tier_champion_min_value_6m":  2000.0,   # € minimum 6-month spend
    "tier_champion_max_invoice_days": 30,     # days since last invoice
    "tier_champion_min_inv_count":    4,      # invoices in last 90 days
    "tier_active_max_invoice_days":   45,     # days since last invoice
    "tier_active_min_inv_count":      2,      # invoices in last 90 days
    "tier_atrisk_min_value_6m":     300.0,   # must have some spend history
    "tier_atrisk_min_invoice_days":  45,      # invoice days that triggers at-risk
    "tier_dormant_min_invoice_days": 90,      # beyond this = dormant
    "tier_trend_growth_threshold":   0.15,    # 15% above average = growing
    "tier_trend_decline_threshold":  0.15,    # 15% below average = declining
}

# Badge colours (Bootstrap colour names for template use)
TIER_META = {
    "champion": {
        "label": "Champion",
        "color": "success",
        "icon":  "fa-trophy",
        "description": "High value, buying frequently and recently",
        "order": 1,
    },
    "active": {
        "label": "Active",
        "color": "primary",
        "icon":  "fa-check-circle",
        "description": "Regular buyer, consistent engagement",
        "order": 2,
    },
    "at_risk": {
        "label": "At Risk",
        "color": "warning",
        "icon":  "fa-exclamation-triangle",
        "description": "Has spending history but activity declining",
        "order": 3,
    },
    "dormant": {
        "label": "Dormant",
        "color": "secondary",
        "icon":  "fa-moon",
        "description": "No significant recent purchasing activity",
        "order": 4,
    },
    "potential": {
        "label": "Potential",
        "color": "info",
        "icon":  "fa-star",
        "description": "Prospect — no invoice history yet",
        "order": 5,
    },
}

TREND_META = {
    "growing":  {"label": "▲", "color": "success", "title": "Growing"},
    "stable":   {"label": "→", "color": "muted",   "title": "Stable"},
    "declining":{"label": "▼", "color": "danger",  "title": "Declining"},
    None:       {"label": "",  "color": "muted",   "title": ""},
}


def _load_thresholds(db_session) -> dict:
    """Load tier thresholds from Settings table, falling back to DEFAULTS."""
    try:
        from models import Setting
        thresholds = {}
        for key, default in DEFAULTS.items():
            raw = Setting.get(db_session, key, None)
            if raw is not None:
                try:
                    thresholds[key] = float(raw) if "." in str(default) else int(raw)
                except (ValueError, TypeError):
                    thresholds[key] = default
            else:
                thresholds[key] = default
        return thresholds
    except Exception:
        return dict(DEFAULTS)


def compute_trend(value_6m: float, value_4w: float, growth_threshold: float,
                  decline_threshold: float) -> str | None:
    """Return 'growing', 'stable', 'declining', or None if insufficient data."""
    if not value_6m or not value_4w:
        return None
    # Annualise the 4-week figure to a 26-week equivalent for comparison
    annualised_4w = value_4w * 6.5
    ratio = annualised_4w / value_6m
    if ratio > (1.0 + growth_threshold):
        return "growing"
    elif ratio < (1.0 - decline_threshold):
        return "declining"
    return "stable"


def compute_tier(
    value_6m: float,
    value_4w: float,
    r_invoice_days: int | None,
    inv_cnt_90d: int,
    classification: str | None,
    thresholds: dict,
) -> tuple[str, str | None]:
    """
    Return (tier_code, trend_code).

    Rules are evaluated top-down; first match wins.
    """
    trend = compute_trend(
        value_6m, value_4w,
        thresholds["tier_trend_growth_threshold"],
        thresholds["tier_trend_decline_threshold"],
    )

    # ── Potential: no invoice history ───────────────────────────────────────
    if not value_6m and (r_invoice_days is None or r_invoice_days > 365):
        return ("potential", trend)

    # ── Champion: best customers ─────────────────────────────────────────────
    if (
        value_6m >= thresholds["tier_champion_min_value_6m"]
        and r_invoice_days is not None
        and r_invoice_days <= thresholds["tier_champion_max_invoice_days"]
        and inv_cnt_90d >= thresholds["tier_champion_min_inv_count"]
    ):
        return ("champion", trend)

    # ── At Risk: spending history exists but going quiet ─────────────────────
    if (
        value_6m >= thresholds["tier_atrisk_min_value_6m"]
        and (
            r_invoice_days is None
            or r_invoice_days > thresholds["tier_atrisk_min_invoice_days"]
        )
    ):
        return ("at_risk", trend)

    # ── Active: buying regularly ─────────────────────────────────────────────
    if (
        r_invoice_days is not None
        and r_invoice_days <= thresholds["tier_active_max_invoice_days"]
        and inv_cnt_90d >= thresholds["tier_active_min_inv_count"]
    ):
        return ("active", trend)

    # ── Dormant: no meaningful recent activity ───────────────────────────────
    if r_invoice_days is None or r_invoice_days > thresholds["tier_dormant_min_invoice_days"]:
        return ("dormant", trend)

    # ── Default: active but low frequency ───────────────────────────────────
    return ("active", trend)


def classify_customers(rows: list[dict], db_session) -> list[dict]:
    """
    Add 'tier', 'tier_meta', 'trend', 'trend_meta' to each row dict.

    rows must be the dashboard_rows list as built in customer_slot_dashboard().
    Each row must contain: value_6m, value_4w, r_invoice_days, inv_cnt_90d, classification.
    """
    thresholds = _load_thresholds(db_session)

    for row in rows:
        tier_code, trend_code = compute_tier(
            value_6m=float(row.get("value_6m") or 0),
            value_4w=float(row.get("value_4w") or 0),
            r_invoice_days=row.get("r_invoice_days"),
            inv_cnt_90d=int(row.get("inv_cnt_90d") or 0),
            classification=row.get("classification"),
            thresholds=thresholds,
        )
        row["tier"]       = tier_code
        row["tier_meta"]  = TIER_META[tier_code]
        row["trend"]      = trend_code
        row["trend_meta"] = TREND_META[trend_code]

    return rows


def tier_summary(rows: list[dict]) -> dict:
    """Return counts per tier for the KPI strip."""
    counts = {k: 0 for k in TIER_META}
    for row in rows:
        t = row.get("tier")
        if t in counts:
            counts[t] += 1
    return counts
```

---

## CHANGE 2 — `routes_crm_dashboard.py`

### Step 2a — Read the tier filter param

In `customer_slot_dashboard()`, find where the other filter params are read (`slot`, `classification`, `agent_code`, etc.) and add:

```python
    tier_filter = request.args.get("tier", "").strip()
```

### Step 2b — Call the tier service after building `dashboard_rows`

Find the block at the bottom of `customer_slot_dashboard()` that runs after `dashboard_rows` is fully assembled (after the sort and pagination). Before the `return render_template(...)` call, add:

```python
    # Compute customer tiers
    try:
        from services.crm_tier_service import classify_customers, tier_summary, TIER_META
        dashboard_rows = classify_customers(dashboard_rows, db.session)
        kpi_tier_summary = tier_summary(dashboard_rows)
    except Exception as _te:
        logger.warning("Tier classification failed: %s", _te)
        kpi_tier_summary = {}
        TIER_META = {}

    # Apply tier filter AFTER tier classification (tier is computed in Python)
    if tier_filter:
        dashboard_rows = [r for r in dashboard_rows if r.get("tier") == tier_filter]
```

### Step 2c — Pass tier data to the template

In the `return render_template(...)` call, add these arguments:

```python
        tier_meta=TIER_META,
        kpi_tier_summary=kpi_tier_summary,
        filters={
            ...existing filters...,
            "tier": tier_filter,       # add this line
        },
```

---

## CHANGE 3 — `templates/crm/dashboard.html`

### Step 3a — Tier filter dropdown in the filter bar

Find the filter bar (where Agent, Classification, District etc. live). Add a Tier dropdown alongside them:

```html
<!-- Tier filter -->
<div>
  <label class="form-label small text-muted mb-1">Tier</label>
  <select class="form-select form-select-sm" id="filterTier" style="min-width:130px">
    <option value="">All Tiers</option>
    {% for code, meta in tier_meta.items() %}
    <option value="{{ code }}"
      {% if filters.tier == code %}selected{% endif %}>
      {{ meta.label }}
    </option>
    {% endfor %}
  </select>
</div>
```

Wire it into the existing filter JS function that builds the URL query string:

```javascript
params.set("tier", document.getElementById("filterTier").value);
```

### Step 3b — Tier KPI strip

Find the KPI summary cards row (Total, Need Action, Has Cart, On Orders, etc.). Add a Tier breakdown card after the existing cards:

```html
<div class="card px-3 py-2 border-secondary">
  <div class="small text-muted mb-1">By Tier</div>
  <div class="d-flex gap-2 flex-wrap">
    {% for code, meta in tier_meta.items() %}
    {% set count = kpi_tier_summary.get(code, 0) %}
    {% if count > 0 %}
    <a href="?{{ request.query_string.decode() | replace('tier=' ~ filters.tier, '') }}&tier={{ code }}"
       class="badge bg-{{ meta.color }} text-decoration-none"
       title="{{ meta.description }}">
      <i class="fas {{ meta.icon }} me-1"></i>{{ meta.label }} {{ count }}
    </a>
    {% endif %}
    {% endfor %}
  </div>
</div>
```

> Each tier badge is a clickable link that filters the list to that tier. This gives a one-click way to see all At Risk customers, all Champions, etc.

### Step 3c — Tier badge on each customer row

Find the `{% for row in rows %}` loop. In the table row, after the Agent column (or after Classification), add the tier badge:

```html
<td>
  <span class="badge bg-{{ row.tier_meta.color }}"
        title="{{ row.tier_meta.description }}">
    <i class="fas {{ row.tier_meta.icon }} me-1"></i>{{ row.tier_meta.label }}
  </span>
  {% if row.trend %}
  <span class="text-{{ row.trend_meta.color }} ms-1 small"
        title="{{ row.trend_meta.title }}">{{ row.trend_meta.label }}</span>
  {% endif %}
</td>
```

Also add the column header in `<thead>`:

```html
<th>Tier</th>
```

---

## CHANGE 4 — Configurable thresholds in Admin Settings

The tier thresholds are read from the Settings table. To make them editable, add a "Customer Tier Thresholds" section to the admin settings page (`/admin/settings`).

In `routes.py`, inside the `admin_settings` POST handler, add saving for tier threshold keys:

```python
        # Tier classification thresholds
        tier_keys = [
            "tier_champion_min_value_6m",
            "tier_champion_max_invoice_days",
            "tier_champion_min_inv_count",
            "tier_active_max_invoice_days",
            "tier_active_min_inv_count",
            "tier_atrisk_min_value_6m",
            "tier_atrisk_min_invoice_days",
            "tier_dormant_min_invoice_days",
        ]
        for key in tier_keys:
            val = request.form.get(key, "").strip()
            if val:
                save_setting(key, val)
```

In the GET handler, add reading:

```python
    tier_thresholds = {}
    for key in [
        "tier_champion_min_value_6m", "tier_champion_max_invoice_days",
        "tier_champion_min_inv_count", "tier_active_max_invoice_days",
        "tier_active_min_inv_count", "tier_atrisk_min_value_6m",
        "tier_atrisk_min_invoice_days", "tier_dormant_min_invoice_days",
    ]:
        tier_thresholds[key] = Setting.get(db.session, key,
            str(crm_tier_service.DEFAULTS.get(key, "")))
```

In the settings template, add a section:

```html
<h5 class="mt-4">Customer Tier Thresholds</h5>
<p class="text-muted small">
  These thresholds control how customers are classified. Changes take effect immediately on the next CRM dashboard load.
</p>
<div class="row g-3">
  <div class="col-md-4">
    <label class="form-label small">Champion — min 6-month spend (€)</label>
    <input type="number" class="form-control form-control-sm"
           name="tier_champion_min_value_6m"
           value="{{ tier_thresholds.tier_champion_min_value_6m }}">
  </div>
  <div class="col-md-4">
    <label class="form-label small">Champion — max days since last invoice</label>
    <input type="number" class="form-control form-control-sm"
           name="tier_champion_max_invoice_days"
           value="{{ tier_thresholds.tier_champion_max_invoice_days }}">
  </div>
  <div class="col-md-4">
    <label class="form-label small">Champion — min invoices in 90 days</label>
    <input type="number" class="form-control form-control-sm"
           name="tier_champion_min_inv_count"
           value="{{ tier_thresholds.tier_champion_min_inv_count }}">
  </div>
  <div class="col-md-4">
    <label class="form-label small">Active — max days since last invoice</label>
    <input type="number" class="form-control form-control-sm"
           name="tier_active_max_invoice_days"
           value="{{ tier_thresholds.tier_active_max_invoice_days }}">
  </div>
  <div class="col-md-4">
    <label class="form-label small">At Risk — min 6-month spend to trigger (€)</label>
    <input type="number" class="form-control form-control-sm"
           name="tier_atrisk_min_value_6m"
           value="{{ tier_thresholds.tier_atrisk_min_value_6m }}">
  </div>
  <div class="col-md-4">
    <label class="form-label small">Dormant — min days since last invoice</label>
    <input type="number" class="form-control form-control-sm"
           name="tier_dormant_min_invoice_days"
           value="{{ tier_thresholds.tier_dormant_min_invoice_days }}">
  </div>
</div>
```

---

## How it works after applying

### On the CRM dashboard:
- Every customer row shows a coloured **tier badge** (Champion / Active / At Risk / Dormant / Potential) plus a trend arrow (▲ → ▼)
- The **Tier filter dropdown** in the filter bar narrows the list to one tier
- The **KPI strip** shows clickable tier counts — one click on "At Risk 8" filters the whole list instantly
- All existing filters (Agent, Classification, District etc.) combine with the tier filter

### Tier logic applied to the screenshot (MANOLIS portfolio, 72 customers):
- **Champion**: High spenders (€2,000+ in 6 months) buying at least 4 times in 90 days with invoice ≤ 30 days → e.g., AL MAMOUN (€2,469, 3d ago)
- **At Risk**: Previously spent but last invoice > 45 days → e.g., 1 MINUTE KIOSK (€735, 67d ago)
- **Active**: Buying regularly within 45 days → e.g., AC&MK MINIMARKET (€331, 21d ago)
- **Dormant**: No meaningful recent activity
- **Potential**: POTENTIAL classification with no invoice history

### Thresholds:
Default thresholds are reasonable starting points. Once the system is live, review the tier distribution — if Champion is too small (< 10%) or too large (> 30% of customers) adjust the `tier_champion_min_value_6m` threshold in Admin Settings until the split feels right for your portfolio.

### Trend arrow:
- ▲ Growing: last 4 weeks × 6.5 is 15%+ above 6-month average (customer is accelerating)
- → Stable: within ±15%
- ▼ Declining: below 15% of 6-month average (customer is spending less)

A Champion with a ▼ arrow is the most urgent account to call.
