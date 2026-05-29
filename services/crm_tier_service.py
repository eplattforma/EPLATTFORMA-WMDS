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

    if not value_6m and (r_invoice_days is None or r_invoice_days > 365):
        return ("potential", trend)

    if (
        value_6m >= thresholds["tier_champion_min_value_6m"]
        and r_invoice_days is not None
        and r_invoice_days <= thresholds["tier_champion_max_invoice_days"]
        and inv_cnt_90d >= thresholds["tier_champion_min_inv_count"]
    ):
        return ("champion", trend)

    if (
        value_6m >= thresholds["tier_atrisk_min_value_6m"]
        and (
            r_invoice_days is None
            or r_invoice_days > thresholds["tier_atrisk_min_invoice_days"]
        )
    ):
        return ("at_risk", trend)

    if (
        r_invoice_days is not None
        and r_invoice_days <= thresholds["tier_active_max_invoice_days"]
        and inv_cnt_90d >= thresholds["tier_active_min_inv_count"]
    ):
        return ("active", trend)

    if r_invoice_days is None or r_invoice_days > thresholds["tier_dormant_min_invoice_days"]:
        return ("dormant", trend)

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
