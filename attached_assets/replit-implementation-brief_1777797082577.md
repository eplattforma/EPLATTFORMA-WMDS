# EP WMDS — Customer Reports Consolidation
## Implementation Brief for Replit

**Goal:** Build a new, unified Customer experience under `/v2/` that coexists with the current 9 customer-related reports without changing them. After a test period the user (Claudio) will validate, then we'll retire the old views.

**Non-goals:**
- Do **not** modify, refactor, or "improve" any existing route file, blueprint, or template listed in the `do_not_touch.md` section below.
- Do **not** introduce parallel/shadow data tables. Both old and new views must read the same canonical tables.
- Do **not** add new dependencies unless explicitly listed below.
- Do **not** invent a parallel auth/permissions system. v2 must use the `@require_permission` decorator and `has_permission()` template helper introduced by the WMDS operational development batch (see "Relationship to the WMDS Operational Development Batch" below).

This brief is split into **four self-contained tickets**. Each is shippable and testable on its own. Do not start Ticket N+1 before Ticket N is verified.

---

## Relationship to the WMDS Operational Development Batch

This brief is the **second** development batch for EP WMDS. The first is the WMDS Operational Development Batch (5 phases — user access control, job runs, batch picking hardening, summer cooler, etc.). That batch introduces foundational infrastructure that this brief depends on. The two batches must be sequenced and aligned:

### Hard dependencies — these must be shipped before v2 work begins

| Dependency | From operational batch | Why v2 needs it |
|---|---|---|
| `@require_permission(key)` decorator + role fallback | Phase 1 (Section 6) | v2 routes use this instead of hardcoded role lists |
| `has_permission(key)` template helper | Phase 1 (Section 6) | v2 templates use this for menu/button visibility |
| `users.display_name` field | Phase 1 (Section 6) | v2 cockpit displays AM names from `display_name`, never `username` |
| Settings table pattern (e.g. `summer_cooler_mode_enabled`) | Phase 1 (Section 13) | v2 feature flags live here, not in env vars |
| `users.is_active` (deactivation, not deletion) | Phase 1 (Section 6) | v2 customer-list "agent" filter shows active users only |

**Sequencing rule:** Do not start v2 Ticket 1 until the operational batch's Phase 1 is shipped and verified. Phase 2 onward of the operational batch can proceed in parallel with v2 — they touch different surfaces (jobs/picking/cooler vs. customer reports).

### Soft dependencies — improve v2 if available, not required

- **Job Runs & Sync Logs (operational Phase 2)** — once available, the cockpit can show a small "data refreshed 4 min ago" indicator pulled from job-run records. If Phase 2 is not yet shipped when v2 ships, omit the indicator; do not block on it.
- **IANA timezone helpers (operational Section 5)** — the cockpit's "Tue Wk-A · 2h to close" calculation should use `Europe/Nicosia` IANA strings if helper functions exist by then; otherwise fall back to existing tz handling.

### Conventions that v2 must follow (from the operational batch)

This brief defers to the operational batch on these process conventions. Do not invent parallel conventions for v2:

1. **Feature flags live in the settings table**, with a default value, GREEN/YELLOW/RED safety classification, dependencies, and rollback procedure documented in `ROLLBACK_AND_FLAGS.md`. v2 adds entries to this file; it does not create a new flags-documentation file.
2. **Assumptions log format** matches the operational batch's format (`ASSUMPTION-NNN`, with date/phase/files/decision/reason/safer-alternative/feature-flag/reversibility/recommendation). v2 entries are numbered continuously with the operational batch's numbering.
3. **Question batching policy** matches operational Section 3. Routine questions go to end-of-ticket checkpoints, not interruptions.
4. **Definition of Done per ticket** follows operational Section 3's pattern — flags documented, tests/manual validation present, smoke tests pass, rollback path documented, files-changed list provided.
5. **Migration safety** is additive only. v2's one new SQL view (`vw_customer_offer_opportunity`) is the only schema change; it is a `CREATE OR REPLACE VIEW`, fully reversible by `DROP VIEW`.
6. **Emergency disable order** — v2's master flag `v2_customers_enabled` is added to the operational batch's emergency disable list, near the top (so v2 can be disabled without affecting operational features).

### New permission keys this brief introduces

Add to the operational batch's initial permission key list (operational Section 6):

```text
menu.customers_v2          # gate menu visibility for the v2 nav entries
customers.use_cockpit      # gate access to /v2/customers/<code>
customers.ask_claude       # gate the "Ask Claude" button (cost-bearing API calls)
```

Mapping into the operational batch's role-permission table:

```python
# Add to operational ROLE_PERMISSIONS (Section 6, Phase A — Backward-Compatible Coexistence)
{
    'admin':              ['menu.customers_v2', 'customers.use_cockpit', 'customers.ask_claude'],
    'sales_manager':      ['menu.customers_v2', 'customers.use_cockpit', 'customers.ask_claude'],
    'account_manager':    ['menu.customers_v2', 'customers.use_cockpit', 'customers.ask_claude'],
    'warehouse_manager':  ['menu.customers_v2', 'customers.use_cockpit'],   # cockpit yes, AI no
    'sales_rep':          ['menu.customers_v2', 'customers.use_cockpit'],
    'ops_manager':        ['menu.customers_v2', 'customers.use_cockpit'],
    # 'driver' deliberately excluded
    # 'picker' deliberately excluded
}
```

The v2 cockpit's action buttons (Send SMS, Assign offer, Add note) route into existing workflows that already have their own permission checks; v2 does not introduce new permissions for those.

### Regression-test alignment

The operational batch's Section 12 regression-test list explicitly includes "Customer dashboard / CRM views" and "Reports generation". Those tests are written against the **existing** customer reports (the ones in `do_not_touch.md`). v2 must not break them. Conversely, when v2 ships its own coexistence checklist (each old URL still returns the same response), that satisfies the operational batch's regression requirement for customer reports.

---

## Architectural rules (apply to every ticket)

1. **One new blueprint, one URL prefix.** Everything new lives under `/v2/`. Register it in `main.py` next to the other customer-report blueprints.
2. **Feature flag controls visibility, not behaviour.** A user without `menu.customers_v2` permission still gets `200 OK` if they navigate to `/v2/...` directly during the rollout's "test" phase — they just don't see the menu entry. After the rollout's "everyone" phase, the permission gate becomes the single source of truth and direct URL access is also gated.
3. **Same data, same auth.** The new pages reuse existing SQLAlchemy models, the operational batch's permission decorator, and existing query helpers. No copies. The only new piece of data infrastructure is one SQL view (Ticket 1).
4. **AI providers — two separate paths.** The existing `ai_feedback_service.py` (OpenAI-backed) stays untouched and continues to serve the legacy "Ask AI" button on Customer Benchmark. The new cockpit's "Ask Claude" button uses a new sibling service `services/claude_advice_service.py` backed by Anthropic's Claude API. Same JSON schema, same cache table (with a key prefix to avoid collisions), different provider. Do not add `langchain`, `pydantic-ai`, or any LLM framework — just the official `anthropic` SDK.
5. **Reuse the existing peer-group resolution** in `blueprints/peer_analytics.py::_resolve_peer_customers`. Do not reimplement.
6. **Defensive imports.** Wrap each new blueprint registration in `try/except` like the `admin_tools_bp` block in `main.py` so a v2 import error never breaks the whole app.
7. **No template inheritance changes.** New templates extend `base.html` exactly like the existing ones.
8. **`display_name` everywhere user-visible.** Wherever the v2 UI shows a person's name (agent, note author, picker), pull from `users.display_name` with `username` as the fallback. Never display `username` directly. This applies to Hero KPI cards, the activity timeline, the "Note by ..." rows, and any column showing assigned/created-by user.

---

## File map — what to add, what to read, what not to touch

### New files (you will create these)

```
blueprints/v2_customers.py                   # the new blueprint
templates/v2/customers_list.html             # unified list page
templates/v2/cockpit.html                    # single-customer cockpit page
templates/v2/_partials/cockpit_section.html  # optional: per-section partials
services/v2_customer_data.py                 # data assembly for the cockpit
services/v2_offer_opportunity.py             # query against the new SQL view
services/claude_advice_service.py            # Claude-backed Greek advice (Ticket 4)
migrations/v2_customer_offer_opportunity.sql # the new SQL view
migrations/run_v2.py                         # idempotent migration runner
static/v2/cockpit.css                        # page-specific styles (small)
static/v2/cockpit.js                         # page-specific JS (small)
```

**Note:** `services/feature_flags.py` and the permission helpers (`@require_permission`, `has_permission()`) come from the WMDS operational development batch, Phase 1. Do not duplicate them here.

### Files to read for context (do not edit)

```
routes_crm_dashboard.py            # filter logic for the list view
routes_customer_analytics.py       # KPIs, top items, monthly trend, invoices
routes_customer_benchmark.py       # white space, lapsed items, category mix, price outliers, item RFM
blueprints/peer_analytics.py       # peer resolution + missing items + brand mix
routes_pricing_analytics.py        # price index vs market + PVM
blueprints/abandoned_carts.py      # live cart data
services/crm_price_offers.py       # active offers and offer status
ai_feedback_service.py             # AI service — REFERENCE ONLY for cache/schema pattern; do not modify or import from
update_crm_offer_schema.py         # schema for crm_customer_offer_current
dw_analytics_models.py             # model definitions for invoice lines (gross_profit, gross_margin_pct)
models.py                          # other ORM models
main.py                            # blueprint registration pattern (lines 236–280)
templates/base.html                # nav structure + how to add menu items
```

### `do_not_touch.md` — files you must not edit during this work

```
routes_crm_dashboard.py
routes_customer_analytics.py
routes_customer_benchmark.py
routes_customer_reporting_groups.py
routes_pricing_analytics.py
routes_ai_feedback.py
blueprints/peer_analytics.py
blueprints/abandoned_carts.py
templates/crm/dashboard.html
templates/crm/review_ordering.html
templates/customers/abandoned_carts.html
templates/customer_analytics/customer_360.html
templates/customer_benchmark.html
templates/customer_reporting_groups.html
templates/peer_analytics/peer_dashboard.html
templates/pricing_analytics/customer_pricing.html
templates/analytics_home.html
templates/analytics_opportunities.html
ai_feedback_service.py        # do not modify; do not import from. Read for reference only.
main.py                       # only add ~3 lines to register the new blueprint
templates/base.html           # only add ~3 lines for the new menu entry inside an {% if %} block
```

You may **read** all of these freely — most of the new code is composed of calls into the services these files expose.

---

## Feature flags — set up first, before any ticket

v2 uses **two** flags, both stored in the same settings table the operational batch uses (`Setting.set_json` / `Setting.get` pattern, see operational Phase 1).

### Settings to add

```text
v2_customers_enabled            = false   # master switch — ALL of v2
v2_customers_allowed_usernames  = ""      # comma-separated list, OR "*" for everyone
```

`v2_customers_enabled = false` means the v2 blueprint can still be deployed (routes registered, code present) but the menu entries do not appear and direct `/v2/...` URLs return `404 Not Found` — this is the safe default for production. Toggle to `true` only after each ticket's acceptance criteria are met.

`v2_customers_allowed_usernames` is the rollout dial. With `v2_customers_enabled = true`:
- Empty string → no users see v2 (kill-switch state)
- `claudio,maria` → only those usernames see the menu entries; **direct URL access is also gated** by the `menu.customers_v2` permission
- `*` → everyone with the `menu.customers_v2` permission sees v2

Both values are editable via the existing admin settings UI.

### `ROLLBACK_AND_FLAGS.md` entries

Add to the file the operational batch creates:

```markdown
## v2_customers_enabled
- **Purpose:** Master switch for v2 customer reports (cockpit + unified list)
- **Default:** false
- **Safety class:** GREEN
- **Toggle during business hours:** Yes (read-only feature)
- **Dependencies:** Requires operational batch Phase 1 shipped
- **What happens if disabled:** v2 routes return 404; old customer reports continue working unchanged; no data loss
- **Owner:** Sales operations admin

## v2_customers_allowed_usernames
- **Purpose:** Rollout dial — which users see the v2 nav entries
- **Default:** "" (empty)
- **Safety class:** GREEN
- **Toggle during business hours:** Yes
- **Dependencies:** v2_customers_enabled must be true to take effect
- **What happens if cleared:** Users lose v2 menu entries; URL access still requires permission
- **Owner:** Sales operations admin
```

### Add to the operational batch's emergency disable order

Insert near the top, since v2 is independent of operational features:

```text
0a. v2_customers_enabled = false       # kills all v2 (added by this brief)
1.  summer_cooler_mode_enabled = false
2.  cooler_picking_enabled = false
... (rest unchanged)
```

### Helper function

The visibility helper is small and lives in the v2 blueprint itself (no separate `services/feature_flags.py` — that module belongs to the operational batch):

```python
# in blueprints/v2_customers.py
from app import db
from models import Setting  # the settings ORM model from operational Phase 1
from flask_login import current_user

def _v2_visible_to_current_user() -> bool:
    """Whether to show v2 menu items in base.html and allow direct URL access."""
    if not getattr(current_user, "is_authenticated", False):
        return False
    if not Setting.get_bool(db.session, "v2_customers_enabled", default=False):
        return False
    allowed = Setting.get(db.session, "v2_customers_allowed_usernames", default="") or ""
    if allowed.strip() == "*":
        # Permission gate still applies — see _gate() below
        return True
    usernames = {u.strip() for u in allowed.split(",") if u.strip()}
    return getattr(current_user, "username", None) in usernames
```

(Adjust `Setting.get` / `Setting.get_bool` to match the actual API the operational batch ships. If the API differs, follow the operational batch's pattern verbatim.)

Register as a Jinja global in `app.py` after the `db.init_app(app)` line, alongside the operational batch's existing context processors:

```python
@app.context_processor
def _inject_v2_flag():
    from blueprints.v2_customers import _v2_visible_to_current_user
    return {"v2_visible": _v2_visible_to_current_user}
```

### Menu entry

In `templates/base.html`, find the **Customers** dropdown (around line 125–215) and add **inside it**, after the existing items:

```jinja
{% if v2_visible() and has_permission('menu.customers_v2') %}
  <li><hr class="dropdown-divider"></li>
  <li>
    <a class="dropdown-item" href="{{ url_for('v2_customers.customers_list') }}">
      <i class="fas fa-sparkles me-2 text-info"></i>Customers (new) <span class="badge bg-info ms-1">v2</span>
    </a>
  </li>
{% endif %}
```

Both gates apply: the visibility flag (v2_visible) **and** the permission (`menu.customers_v2`). The visibility flag controls rollout pace; the permission controls who's allowed to see it at all. Do not remove or reorder any existing menu items.

---

# TICKET 1 — Scaffold the v2 blueprint and create the new SQL view

## Goal
A working `/v2/customers` URL that returns a placeholder page, and a working SQL view `vw_customer_offer_opportunity` that returns rows on Postgres and SQLite. No real UI yet.

## Tasks

### 1.1 — Create `blueprints/v2_customers.py`

```python
"""
v2 Customers — unified consolidation of CRM Dashboard, Review Ordering,
Abandoned Carts, Top Opportunities, Customer 360, Customer Benchmark,
Peer Analytics, Pricing Analytics, and AI Insights (per-customer).

This blueprint coexists with the existing customer-report blueprints. It
must not modify or shadow any of their data.

Auth: uses @require_permission from the WMDS operational development batch
Phase 1. Do not duplicate the permission system.
"""
import logging
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user

# Imported from operational batch Phase 1 — do not reimplement.
from services.permissions import require_permission  # adjust path if it differs

from app import db
from models import Setting

logger = logging.getLogger(__name__)

v2_bp = Blueprint("v2_customers", __name__, url_prefix="/v2", template_folder="../templates/v2")


def _v2_visible_to_current_user() -> bool:
    """
    Visibility flag check — for the rollout dial.
    Permission check happens in @require_permission separately.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False
    if not Setting.get_bool(db.session, "v2_customers_enabled", default=False):
        return False
    allowed = Setting.get(db.session, "v2_customers_allowed_usernames", default="") or ""
    if allowed.strip() == "*":
        return True
    usernames = {u.strip() for u in allowed.split(",") if u.strip()}
    return getattr(current_user, "username", None) in usernames


@v2_bp.before_request
def _gate():
    """
    Two-stage gate:
    1. Master flag must be on AND user must be in the rollout list.
    2. Permission check is handled per-route via @require_permission.
    During rollout, both gates apply. After full rollout (allowed_usernames="*"),
    the permission alone gates access.
    """
    if not _v2_visible_to_current_user():
        # 404, not 403 — we don't want to reveal the route exists during rollout.
        abort(404)


@v2_bp.route("/customers")
@login_required
@require_permission("menu.customers_v2")
def customers_list():
    return render_template("v2/customers_list.html", placeholder=True)


@v2_bp.route("/customers/<customer_code>")
@login_required
@require_permission("customers.use_cockpit")
def cockpit(customer_code):
    return render_template("v2/cockpit.html", customer_code=customer_code, placeholder=True)
```

**Important — confirm the import path of `require_permission`.** The operational batch Phase 1 introduces this decorator. Before writing the v2 blueprint, run `grep -rn "def require_permission" --include="*.py"` to find the exact path. Use it. Do not reimplement.

If Phase 1 has not yet been merged when v2 work starts, **stop and notify Claudio**. Per the Sequencing Rule in "Relationship to the WMDS Operational Development Batch", v2 cannot start until Phase 1 is shipped.

### 1.2 — Register the blueprint in `main.py`

Add **immediately after** the existing customer-report blueprint registrations (after the `crm_dashboard_bp` line, around line 280):

```python
try:
    from blueprints.v2_customers import v2_bp
    app.register_blueprint(v2_bp)
    logging.info("v2 customers blueprint registered")
except Exception as e:
    logging.warning(f"v2 customers blueprint not registered: {e}")
```

### 1.3 — Create placeholder templates

`templates/v2/customers_list.html`:
```jinja
{% extends "base.html" %}
{% block title %}Customers (v2){% endblock %}
{% block content %}
<div class="container-fluid py-4">
  <div class="alert alert-info">
    <strong>v2 Customers — placeholder.</strong> Implemented in Ticket 2.
  </div>
</div>
{% endblock %}
```

`templates/v2/cockpit.html`:
```jinja
{% extends "base.html" %}
{% block title %}Cockpit · {{ customer_code }}{% endblock %}
{% block content %}
<div class="container-fluid py-4">
  <div class="alert alert-info">
    <strong>Cockpit for {{ customer_code }} — placeholder.</strong> Implemented in Ticket 3.
  </div>
</div>
{% endblock %}
```

### 1.4 — Create the new SQL view: `vw_customer_offer_opportunity`

This is the **only** new piece of data infrastructure in the entire project. It answers: *"what does this customer regularly buy that they have no offer rule for, where peers in the same reporting group do?"*

Create `migrations/v2_customer_offer_opportunity.sql`:

```sql
-- Customer-SKU pairs where:
--   1. The customer has 90d revenue > threshold
--   2. The customer has NO active offer for that SKU in crm_customer_offer_current
--   3. At least N peer customers in the same reporting group DO have an active offer for that SKU
--
-- This view is read on demand by the Account Manager Cockpit. If volume becomes a
-- problem at scale, convert to MATERIALIZED VIEW with the same refresh schedule
-- as crm_customer_offer_current. Do not premature-optimise.

CREATE OR REPLACE VIEW vw_customer_offer_opportunity AS
WITH customer_sku_revenue AS (
    SELECT
        il.customer_code_365,
        il.item_code_365 AS sku,
        SUM(il.revenue_excl_vat) AS revenue_90d,
        SUM(il.gross_profit) AS gp_90d,
        AVG(il.gross_margin_pct) AS gm_pct_avg,
        MAX(il.invoice_date) AS last_bought
    FROM dw_invoice_lines il
    WHERE il.invoice_date >= CURRENT_DATE - INTERVAL '90 days'
      AND il.customer_code_365 IS NOT NULL
      AND il.item_code_365 IS NOT NULL
      AND il.revenue_excl_vat > 0
    GROUP BY il.customer_code_365, il.item_code_365
    HAVING SUM(il.revenue_excl_vat) >= 100  -- ignore trivial purchases
),
customer_active_offers AS (
    SELECT DISTINCT customer_code_365, item_code_365 AS sku
    FROM crm_customer_offer_current
    WHERE is_active = true
      AND customer_code_365 IS NOT NULL
      AND item_code_365 IS NOT NULL
),
peer_offer_penetration AS (
    -- For each (reporting_group, sku) count distinct customers receiving an active offer.
    SELECT
        c.reporting_group_code,
        o.item_code_365 AS sku,
        COUNT(DISTINCT o.customer_code_365) AS peer_offered_count
    FROM crm_customer_offer_current o
    JOIN dw_customers c ON c.customer_code_365 = o.customer_code_365
    WHERE o.is_active = true
      AND c.reporting_group_code IS NOT NULL
    GROUP BY c.reporting_group_code, o.item_code_365
),
customer_group AS (
    SELECT customer_code_365, reporting_group_code
    FROM dw_customers
    WHERE reporting_group_code IS NOT NULL
)
SELECT
    csr.customer_code_365,
    csr.sku,
    cg.reporting_group_code,
    csr.revenue_90d,
    csr.gp_90d,
    csr.gm_pct_avg,
    csr.last_bought,
    COALESCE(pop.peer_offered_count, 0) AS peer_offered_count
FROM customer_sku_revenue csr
JOIN customer_group cg ON cg.customer_code_365 = csr.customer_code_365
LEFT JOIN customer_active_offers cao
    ON cao.customer_code_365 = csr.customer_code_365 AND cao.sku = csr.sku
LEFT JOIN peer_offer_penetration pop
    ON pop.reporting_group_code = cg.reporting_group_code AND pop.sku = csr.sku
WHERE cao.sku IS NULL                              -- customer has no active offer
  AND COALESCE(pop.peer_offered_count, 0) >= 3     -- at least 3 peers do
;
```

**Verify these column names against the actual schema before running.** The exact column names of `dw_invoice_lines`, `crm_customer_offer_current`, and `dw_customers` may differ. Check `dw_analytics_models.py` and `update_crm_offer_schema.py` for ground truth. Adjust the view to match. The structure must remain — the names may shift.

If `dw_customers` does not have a `reporting_group_code`, find the equivalent column (it might be `customer_reporting_group_id` or a join through a separate `customer_reporting_group_member` table). Read `routes_customer_reporting_groups.py` for clues.

### 1.5 — Create a tiny migration runner

Add to `migrations/run_v2.py`:

```python
"""Run v2 SQL migrations. Idempotent. Safe to re-run."""
import os
import logging
from app import app, db
from sqlalchemy import text

def run():
    sql_path = os.path.join(os.path.dirname(__file__), "v2_customer_offer_opportunity.sql")
    with open(sql_path, "r") as f:
        sql = f.read()
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text(sql))
        logging.info("v2 SQL view created/replaced")

if __name__ == "__main__":
    run()
```

Document in the PR description: *"Run `python migrations/run_v2.py` once after deploy."*

## Acceptance criteria for Ticket 1

- [ ] `python migrations/run_v2.py` succeeds without error against the dev database.
- [ ] `SELECT COUNT(*) FROM vw_customer_offer_opportunity;` returns a non-negative integer (zero is fine if test data is sparse).
- [ ] With `v2_customers_enabled = true` and the user in `v2_customers_allowed_usernames`, visiting `/v2/customers` returns the placeholder page (assuming the user has `menu.customers_v2`).
- [ ] Visiting `/v2/customers/77701274` (or any real code) returns the cockpit placeholder page (assuming `customers.use_cockpit` permission).
- [ ] Visiting `/crm/dashboard`, `/crm/review-ordering`, `/abandoned-carts`, `/customer-benchmark`, `/analytics/customers/<code>`, `/peer-analytics/<code>`, `/pricing-analytics/customer/<code>` all still work **identically** to before. No layout, data, or speed changes.
- [ ] With `v2_customers_enabled = false`, all `/v2/...` routes return 404. The "Customers (new)" menu entry is hidden.
- [ ] With `v2_customers_enabled = true` but the user not in `v2_customers_allowed_usernames`, all `/v2/...` routes still return 404 (rollout-period behaviour — don't leak route existence).
- [ ] With `v2_customers_allowed_usernames = "*"`, the routes return 200 if the user has the relevant permission, and 403 if they don't.
- [ ] `ROLLBACK_AND_FLAGS.md` updated with `v2_customers_enabled` and `v2_customers_allowed_usernames` entries.
- [ ] Permission keys `menu.customers_v2`, `customers.use_cockpit`, `customers.ask_claude` are present in the role-permission mapping defined by the operational batch.
- [ ] No errors in application logs at startup.

---

# TICKET 2 — Unified Customers list with saved-view chips

## Goal
Replace the placeholder at `/v2/customers` with the unified list view (the wireframe `customers-list-wireframe.html`). It must support all six saved views — *All*, *Action Needed*, *In Delivery Window*, *Has Cart*, *Top Opportunities*, *Churn Risk* — backed by the existing data sources used by `routes_crm_dashboard.py`, `templates/crm/review_ordering.html`, `blueprints/abandoned_carts.py`, and `templates/analytics_opportunities.html`.

## Approach
Compose, don't duplicate. The existing `routes_crm_dashboard.py` already has all the filtering logic. Don't copy it — extract its core query function into `services/v2_customer_data.py` and import that **back into** the new blueprint *and* keep the old blueprint working by leaving its current internal function alone (i.e., the new `services/v2_customer_data.py` exists in parallel; you may *call* helpers from `routes_crm_dashboard.py` if they are pure functions, but you may not modify them).

If `routes_crm_dashboard.py` does not have a clean separation between query and rendering, write fresh query functions in `services/v2_customer_data.py` that read the same tables. Do not refactor the old code.

## Tasks

### 2.1 — `services/v2_customer_data.py`

Provide:

```python
def list_customers(
    *,
    view: str,                 # "all" | "action_needed" | "delivery_window" | "has_cart" | "top_opportunities" | "churn_risk"
    search: str | None = None,
    classification: list[str] | None = None,
    district: list[str] | None = None,
    agent: list[str] | None = None,
    slot: list[str] | None = None,
    login_max_days: int | None = None,
    sort: str = "default",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """
    Returns:
      {
        "rows": [ {customer_code, customer_name, classification, district, agent,
                   slot, state, delivery, has_cart, cart_amount, last_login_days,
                   last_invoice_days, value_6m, value_4w, offers_used, offers_total,
                   offer_share_pct, churn_risk_pct, wallet_gap_eur }, ... ],
        "total_count": int,
        "kpis": { ... view-dependent ... },
        "context": { "open_windows": [...] }  # only for delivery_window view
      }
    """
```

Each `view` is a filter preset. Implement each as a **predicate function** layered on top of the base query — not as a separate query. Saved views must be cheap to add/remove.

KPIs for each view:

| View | KPIs |
|---|---|
| `all` | Total · Active · Inactive · With Cart · On Orders · With Offers |
| `action_needed` | Total · Follow Up · No Login 14d · No Invoice 30d · Cart >€100 |
| `delivery_window` | In Window · Follow Up · Waiting · Ordered · Has Cart · Wallet Gap |
| `has_cart` | Cart Customers · Total Cart Value · Avg Cart · Add-On Eligible |
| `top_opportunities` | Top 20 · Total Wallet Gap · Avg GP Lift · Avg Confidence |
| `churn_risk` | At Risk · High Risk (>50% drop) · Total Value at Risk |

### 2.2 — `templates/v2/customers_list.html`

Use the existing wireframe (`customers-list-wireframe.html` deliverable from earlier in the project) as a faithful reference. Translate its CSS variables to fit `base.html`'s existing Bootstrap dark theme — do **not** reproduce the wireframe's standalone look. Use Bootstrap classes where they exist; only add custom CSS in `static/v2/cockpit.css` for things Bootstrap can't express (saved-view chip styling, mix bars, etc.).

The page is server-rendered for the first paint, then JS-enhanced for chip switching, sort, pagination. Each chip click hits `GET /v2/api/customers?view=<chip>&...` which returns JSON, JS replaces the table tbody and KPI strip without a full page reload.

### 2.3 — Default view per user

The default landing view (which chip is active when the user opens `/v2/customers`) is **per-user**, not per-role. Sales reps + ops want *In Delivery Window*; managers want *All*. Hardcoding by role is brittle (role names change, hybrid users exist).

Add a per-user setting to the user table or settings table:

```python
# Read on each request, with a sensible default if unset.
user_setting_key = f"v2.customers.default_view.{current_user.username}"
fallback_default = "delivery_window" if has_permission("picking.perform") else "all"

default_view = Setting.get(db.session, user_setting_key, default=fallback_default)
view = request.args.get("view", default_view)
```

The fallback uses `has_permission("picking.perform")` as a proxy for "this user is hands-on operations" — sales reps and ops typically have it; managers typically don't. Once a user explicitly switches chips and chooses to "Save as my default" (a small button in the chip row), their preference is stored in the settings table and the fallback no longer applies.

This makes Ticket 2 the natural home for sales reps + ops without needing a separate Review Ordering URL, and adapts to the actual user rather than guessing from role.

### 2.4 — JSON endpoint for chip switching

```python
@v2_bp.route("/api/customers")
@login_required
@require_permission("menu.customers_v2")
def api_customers():
    args = request.args
    data = list_customers(
        view=args.get("view", "all"),
        search=args.get("q") or None,
        classification=args.getlist("classification") or None,
        district=args.getlist("district") or None,
        agent=args.getlist("agent") or None,
        slot=args.getlist("slot") or None,
        login_max_days=request.args.get("login_max_days", type=int),
        sort=args.get("sort", "default"),
        page=args.get("page", 1, type=int),
        page_size=args.get("page_size", 50, type=int),
    )
    return jsonify(data)
```

### 2.5 — In-Delivery-Window state actions

When the `delivery_window` view is active, each row needs the four state buttons (Follow Up / Waiting / Ordered / Exclude). Wire these to a `POST /v2/api/customers/<code>/state` endpoint that **calls into the same state-mutation function** the existing `templates/crm/review_ordering.html` uses. Find this function in `routes_crm_dashboard.py` (search for `state` writes or "follow up" handlers). Reuse it; do not duplicate the state machine.

If reuse is structurally impossible (e.g. the logic is inlined in a route handler), refactor it into a helper **inside the existing file**, but make sure to keep the existing endpoint working unchanged. This is a small exception to the "do not touch" rule and only applies if the alternative is duplicating a state machine across two places.

## Acceptance criteria for Ticket 2

- [ ] `/v2/customers` renders the unified list page. Default chip is the per-user setting if set, otherwise *In Delivery Window* for users with `picking.perform`, *All* for everyone else.
- [ ] All six chips switch the view without a full page reload.
- [ ] "Save as my default" button on the chip row persists the user's preferred chip to the settings table.
- [ ] Filters (search, classification, district, agent, slot) work and persist when chips change.
- [ ] The "agent" filter dropdown shows only **active** users (`is_active = true`) and displays their `display_name`, not `username`.
- [ ] Sort dropdown works.
- [ ] Pagination works.
- [ ] State changes in the delivery-window view (Follow Up / Waiting / Ordered / Exclude) persist correctly and **also reflect** when the user opens the old `/crm/review-ordering` page (proves shared data source).
- [ ] KPI strip updates with each chip.
- [ ] Open-windows context strip appears only for the *In Delivery Window* view.
- [ ] Direct URL access `/v2/customers` returns 403 for users without `menu.customers_v2` permission. Returns 404 if `v2_customers_enabled = false` (so the route's existence isn't revealed during rollout).
- [ ] No layout, data, or speed regressions on any existing page.

---

# TICKET 3 — Account Manager Cockpit (single-page customer view)

## Goal
Replace the placeholder at `/v2/customers/<customer_code>` with the full cockpit (the wireframe `account-manager-cockpit.html`). It composes data from existing API endpoints into one server-rendered page, with the new "Offers he could get" panel using the SQL view from Ticket 1.

## Approach
The cockpit is a thin Jinja template over a single backend service function `services/v2_customer_data.py::get_cockpit_data(customer_code, period, compare, peer_group)` which calls the existing internal query functions in:

- `routes_customer_analytics.py` — for KPIs, top items, monthly trend, invoices
- `routes_customer_benchmark.py` — for white space, lapsed items, category mix, price outliers, item RFM
- `blueprints/peer_analytics.py` — for peer resolution, missing items, brand mix
- `routes_pricing_analytics.py` — for price index vs market and PVM
- `services/crm_price_offers.py` — for the active offers panel
- `blueprints/abandoned_carts.py` — for live cart contents
- `services/v2_offer_opportunity.py` — for the new "offers he could get" panel (SQL view from Ticket 1)

**Do not call the HTTP routes of these modules.** Find the underlying Python function (the one the route handler calls) and call that directly. If the logic is inlined in the route handler, write a thin function in `services/v2_customer_data.py` that re-runs the same SQL — but treat the existing handler's SQL as the source of truth and replicate it faithfully. This guarantees v2 numbers match v1 numbers.

## Tasks

### 3.1 — `services/v2_customer_data.py::get_cockpit_data`

```python
def get_cockpit_data(
    customer_code: str,
    *,
    period_days: int = 90,
    compare: str = "py",          # "py" | "prev_period" | "none"
    peer_group: str | None = None,
) -> dict:
    """
    Returns the full cockpit payload:
    {
      "header": {...},
      "kpis": {sales, gross_profit, gm_pct, share_of_wallet, engagement_score, live_cart, open_orders},
      "trend": {monthly: [{month, sales, gp, gm_pct, peer_avg_sales, target}, ...]},
      "pvm": {price, volume, mix, total_sales_delta, total_gp_delta},
      "top_items_by_gp": [...],
      "category_mix": [{category, customer_pct, peer_pct, gap}, ...],
      "active_offers": [...],            # from services.crm_price_offers
      "offer_opportunities": [...],      # from services.v2_offer_opportunity
      "white_space": [...],
      "lapsed_items": [...],
      "cross_sell": [...],
      "churn_risk_by_category": [...],
      "price_index_outliers": [...],
      "activity_timeline": [...],
      "recommended_actions": [...],      # populated by Ticket 4
    }
    """
```

This function is the single place where the cockpit's data shape is defined. It runs on every page load. If a section is slow, cache it inside this function with `functools.lru_cache` keyed on `(customer_code, period_days, compare, peer_group)` plus a 5-minute TTL.

### 3.2 — `services/v2_offer_opportunity.py`

```python
from sqlalchemy import text
from app import db

def get_offer_opportunities(customer_code: str, limit: int = 10) -> list[dict]:
    """
    Returns SKUs the customer buys regularly with no active offer, where peers do.
    Reads from vw_customer_offer_opportunity (created in Ticket 1).
    """
    rows = db.session.execute(text("""
        SELECT
            o.sku,
            i.item_name,
            o.revenue_90d,
            o.gp_90d,
            o.gm_pct_avg,
            o.peer_offered_count,
            o.last_bought
        FROM vw_customer_offer_opportunity o
        LEFT JOIN dw_items i ON i.item_code_365 = o.sku
        WHERE o.customer_code_365 = :code
        ORDER BY o.peer_offered_count DESC, o.revenue_90d DESC
        LIMIT :lim
    """), {"code": customer_code, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]
```

Adjust column names to match the actual `dw_items` table — verify before shipping.

### 3.3 — `templates/v2/cockpit.html`

Translate the wireframe `account-manager-cockpit.html` to a Jinja template. Each major section becomes either an inline block or a partial in `templates/v2/_partials/`. The structure (top to bottom):

1. ID card with page-level controls (period, compare, peer group, benchmark)
2. Hero KPIs (6)
3. Performance Evolution row (12-month trend chart + PVM)
4. What they're buying (Top items by GP + category mix)
5. Offers row (active offers + "offers he could get" — the new panel)
6. Growth opportunities (white space + lapsed + cross-sell)
7. Risk & engagement (churn risk + price index + activity timeline)
8. Recommended actions (populated in Ticket 4 — for now show a placeholder)

For charts use the existing chart library used by `templates/customer_analytics/customer_360.html` and `templates/customer_benchmark.html` (they share one — find which: Chart.js, Plotly, or D3). Reuse it; do not introduce a new charting library.

### 3.4 — Page-level controls = page-level params

The header's period/compare/peer-group/benchmark selectors update the URL query string and reload. They are **page-level**, not per-section — set once, applied everywhere. This is the key UX improvement over the existing five-page-per-customer setup.

### 3.5 — Live cart inline, not as a separate page

The cockpit's "Live Cart" hero KPI is clickable and expands the cart contents inline (or routes to an anchor on the page). It must use the same Magento data source `blueprints/abandoned_carts.py` uses. Do not write a new sync.

### 3.6 — `display_name` everywhere user-visible

Per Architectural Rule 8, every place the cockpit displays a person's name must pull `users.display_name` (with `username` as fallback if `display_name` is null). Specifically:

- **ID card** — "Agent: M. Charalambous" reads from `display_name` joined on the customer's assigned-agent foreign key.
- **Activity timeline** — "Note by M.Charalambous" reads from the note-author's `display_name`.
- **Recommended Actions panel** — when an action mentions a teammate (e.g. "Follow up with K. Pavlou"), use their `display_name`.
- **Action buttons** — "Pick as myself" continues to use the convention from the operational batch's Phase 4 batch picking work (not v2's responsibility, just don't override it).

Add a small Jinja helper if one doesn't exist:

```python
@app.template_filter('user_display')
def user_display_filter(username):
    """Return the user's display_name, falling back to username if null/empty."""
    if not username:
        return ""
    user = User.query.filter_by(username=username).first()
    if not user:
        return username
    return (user.display_name or user.username).strip() or username
```

Use as: `{{ note.created_by | user_display }}`.

If the operational batch's Phase 1 already ships a similar filter, use that one instead. Do not duplicate.

### 3.7 — Audit trail for action buttons

The cockpit's Quick Actions (Schedule call, Send SMS, Suggest order, Assign offer, Add note) route through to existing workflows that already have their own audit trails. v2 does not introduce new audit events. **However**, three new actions originate in v2 and need lightweight audit:

- "Save as my default view" (Ticket 2 — saves the user's default chip)
- "Ask Claude" button click (logs which user asked for advice on which customer/section, with timestamp — useful for cost analysis)
- "Mark this advice as helpful / not helpful" (if implemented — feedback signal for the AI prompt)

Use the operational batch's audit-event table (Phase 4 introduces a standardized audit-event pattern — `batch.created`, etc.). Add v2 events with the same prefix convention:

```text
v2.cockpit.viewed
v2.cockpit.advice_requested
v2.cockpit.advice_feedback
v2.list.default_view_saved
```

If the operational batch's audit-event API isn't ready when Ticket 3 ships, defer this to a follow-up — it's nice-to-have, not blocking.

## Acceptance criteria for Ticket 3

- [ ] `/v2/customers/77701274` (or any real code) renders the full cockpit.
- [ ] All six hero KPIs show real data sourced from existing tables.
- [ ] The 12-month trend chart matches the data shown by the existing Customer 360 page (spot-check three customers).
- [ ] Top Items by GP matches the data from Customer Benchmark and Customer 360 (the same items appear; they're just sorted by GP instead of revenue).
- [ ] Active Offers panel matches the data from `services/crm_price_offers.py` 1:1.
- [ ] "Offers he could get" panel shows SKUs the customer buys but has no offer for, where ≥3 peers do. Validate manually for one test customer.
- [ ] White space, lapsed items, cross-sell, churn risk, price outliers all match the data from `routes_customer_benchmark.py` and `blueprints/peer_analytics.py` for the same customer.
- [ ] Activity timeline shows recent invoices, logins, SMS, notes from the existing data sources.
- [ ] Page-level controls (period, compare, peer group) reload the page and update every section.
- [ ] Page loads in < 3s for a typical customer (with `lru_cache` warming, < 1s on second visit).
- [ ] No data divergence between this page and the corresponding old pages for the same customer.
- [ ] Every user-facing name in the cockpit (agent, note author, etc.) renders from `display_name`, with `username` as fallback. Verify with a test user whose `display_name` differs from `username`.
- [ ] No raw `username` strings appear anywhere in the cockpit UI.
- [ ] Direct URL access `/v2/customers/<code>` returns 403 for users without `customers.use_cockpit`.

---

# TICKET 4 — Greek Claude advice + Recommended Actions panel

## Goal
The Recommended Actions panel at the bottom of the cockpit and the contextual `✦ Ask Claude` button generate **Greek-language advice** with English trade terminology preserved, **using Anthropic's Claude API** (not OpenAI). The existing `ai_feedback_service.py` uses OpenAI for its own button on Customer Benchmark — that stays unchanged. We add a new sibling service that uses Claude.

## Why Claude, not OpenAI
- The existing OpenAI-backed `generate_feedback` function stays exactly as-is (the user wants existing behaviour preserved during coexistence).
- The cockpit is the place where Greek-with-English-trade-terms advice matters; Claude handles this kind of nuanced bilingual output reliably.
- Keeping the two paths separate also means a credit/quota issue on one provider doesn't break both buttons.

## Approach
Create a new sibling service `services/claude_advice_service.py` that mirrors the structure of the existing `ai_feedback_service.py` — same caching pattern (the `ai_feedback_cache` table can be reused with a key prefix to avoid collision), same JSON schema, but uses Anthropic's API with a Greek system prompt.

Do **not** modify `ai_feedback_service.py`. Do **not** swap providers in the existing code. Add, don't replace.

## Tasks

### 4.1 — Add Anthropic SDK to dependencies

In `pyproject.toml` (or `requirements.txt`, whichever the project uses — check first):

```
anthropic>=0.39.0
```

This is the only new dependency the entire v2 work introduces.

### 4.2 — Configure environment variables

Two new env vars, set in Replit Secrets (do not commit):

```
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-5  # or whatever's current; let Claudio confirm
```

In `app.py` near the other config:

```python
app.config["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")
app.config["CLAUDE_MODEL"] = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
```

### 4.3 — Create `services/claude_advice_service.py`

```python
"""
Claude-backed advice service for the v2 Account Manager Cockpit.

Sibling to ai_feedback_service.py (which is OpenAI-backed and serves the
existing Customer Benchmark "Ask AI" button). This service exists separately
so the two paths can coexist during the v2 rollout without either disturbing
the other.

Outputs Greek prose with English trade terminology preserved.
"""
import os, json, hashlib
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic
from sqlalchemy import text
from app import db

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")

CACHE_TTL_HOURS = 12
CACHE_KEY_PREFIX = "v2_cockpit_"   # avoids collision with the OpenAI cache entries
MAX_ROWS = 50

client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# Reuse the same JSON shape as ai_feedback_service.JSON_SCHEMA so the frontend
# treats both responses identically.
GREEK_SYSTEM_PROMPT = """\
You are an experienced wholesale sales advisor for an Italian fine-foods distributor operating in Cyprus.

LANGUAGE RULES (these are firm, not preferences):
- Respond in Greek.
- Keep these terms in English verbatim because they are standard industry usage and the sales team uses them in English daily:
  SKU, GM%, GP, Share of Wallet, PVM, Cross-sell, Churn Risk, White Space, peer group, ABC classification, HORECA, Slot, ADD-ON, RFM, Index.
- Use Greek for: verbs, reasoning, customer-friendly phrasing, connecting prose, action labels, category names where natural.
- Currency stays as €. Numbers stay as digits.

CONTENT RULES:
- Be specific. Cite the actual numbers from the snapshot in your reasoning.
- Prefer concrete actions (e.g. "Ανάθεση offer Q2-CHEESE στο SKU-2890") over generic advice.
- Avoid platitudes ("focus on the customer relationship", "consider upselling") — every line must reference snapshot data.
- If the data doesn't support a recommendation, omit it. Do not pad.

OUTPUT FORMAT:
You MUST return valid JSON with EXACTLY these top-level keys (no wrapper object):
{
  "summary": "Greek - 2-3 sentence executive summary",
  "peer_context": "Greek - peer group description and where this customer sits",
  "key_findings": ["Greek bullets with specific numbers, 3-5 items"],
  "risks": ["Greek risk statements, 2-4 items"],
  "opportunities": [
    {"title": "Greek", "why": "Greek with English terms", "expected_impact": "Greek + €amount", "confidence": 0.0-1.0}
  ],
  "next_actions": [
    {"priority": "P0|P1|P2", "action": "Greek action statement", "script_hint": "Greek - what to say to the customer"}
  ]
}
"""


def _hash_payload(payload: dict) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _clip_payload(payload: dict) -> dict:
    p = dict(payload)
    for k in ["white_space", "lapsed_items", "top_items", "top_items_by_gp",
              "price_outliers", "trend_monthly", "category_mix",
              "active_offers", "offer_opportunities", "cross_sell",
              "churn_risk_by_category", "activity_timeline"]:
        if isinstance(p.get(k), list):
            p[k] = p[k][:MAX_ROWS]
    return p


def _cache_get(payload_hash: str):
    q = text("""
      SELECT response_json
      FROM ai_feedback_cache
      WHERE payload_hash = :h AND expires_at > NOW()
      LIMIT 1
    """)
    row = db.session.execute(q, {"h": CACHE_KEY_PREFIX + payload_hash}).fetchone()
    return row[0] if row else None


def _cache_set(payload_hash: str, response_json: dict):
    q = text("""
      INSERT INTO ai_feedback_cache(payload_hash, expires_at, response_json)
      VALUES (:h, :exp, CAST(:j AS jsonb))
      ON CONFLICT (payload_hash) DO UPDATE
      SET expires_at = EXCLUDED.expires_at,
          response_json = EXCLUDED.response_json
    """)
    exp = datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS)
    db.session.execute(q, {
        "h": CACHE_KEY_PREFIX + payload_hash,
        "exp": exp,
        "j": json.dumps(response_json)
    })
    db.session.commit()


def generate_cockpit_advice(snapshot: dict) -> dict:
    """
    Generate Greek-language sales advice for the Account Manager Cockpit.

    Mirrors ai_feedback_service.generate_feedback's contract (same JSON schema)
    so the frontend can treat responses interchangeably.
    """
    if not client:
        raise ValueError(
            "Anthropic API is not configured. Set ANTHROPIC_API_KEY in Replit Secrets."
        )

    payload = _clip_payload(snapshot)
    payload_hash = _hash_payload(payload)

    cached = _cache_get(payload_hash)
    if cached:
        return cached if isinstance(cached, dict) else json.loads(cached)

    user_content = json.dumps(payload, ensure_ascii=False)

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=GREEK_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_content}
        ],
    )

    # Anthropic returns content as a list of blocks; the text block is what we want.
    out_text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            out_text = block.text
            break

    # Strip markdown code fences if Claude wrapped JSON in ```json ... ```
    out_text = out_text.strip()
    if out_text.startswith("```"):
        out_text = out_text.split("\n", 1)[1] if "\n" in out_text else out_text
        if out_text.endswith("```"):
            out_text = out_text.rsplit("```", 1)[0]
        out_text = out_text.strip()
        if out_text.startswith("json"):
            out_text = out_text[4:].lstrip()

    try:
        out = json.loads(out_text)
    except Exception:
        out = {
            "summary": out_text[:500],
            "peer_context": "",
            "key_findings": [],
            "risks": [],
            "opportunities": [],
            "next_actions": []
        }

    _cache_set(payload_hash, out)
    return out
```

### 4.4 — Add `/v2/api/claude-advice` endpoint to the v2 blueprint

```python
@v2_bp.route("/api/claude-advice", methods=["POST"])
@login_required
@require_permission("customers.ask_claude")
def api_claude_advice():
    payload = request.get_json(silent=True) or {}
    customer_code = payload.get("customer_code")
    section = payload.get("section", "all")  # "all" | "offers" | "opportunities" | etc.
    if not customer_code:
        return jsonify({"error": "customer_code required"}), 400

    # Build snapshot from get_cockpit_data + section filter.
    full = get_cockpit_data(customer_code)
    snapshot = _build_advice_snapshot(full, section)

    try:
        from services.claude_advice_service import generate_cockpit_advice
        out = generate_cockpit_advice(snapshot)
        return jsonify(out)
    except ValueError as e:
        # Configuration error (missing API key) — return a clean message
        return jsonify({"error": str(e), "configured": False}), 503
    except Exception as e:
        logger.exception("Claude advice generation failed")
        return jsonify({"error": "Advice generation failed", "detail": str(e)}), 500
```

The `customers.ask_claude` permission is the cost-control gate. Only users with this permission can trigger paid Anthropic API calls. The cockpit page-level button and the per-section buttons all hit this same endpoint and are all gated by this permission. In the UI, hide the buttons when `has_permission('customers.ask_claude')` is false (the Recommended Actions panel still renders, just without the manual "Ask Claude" buttons).

`_build_advice_snapshot` is a helper that picks only the relevant fields from the cockpit data based on the section — keeps tokens cheap and answers focused. For example, when `section == "offers"` it includes only `active_offers`, `offer_opportunities`, and the customer header; when `section == "all"` it includes everything but with shorter array clips.

### 4.5 — Wire the cockpit's `✦ Ask Claude` button to this endpoint

In `static/v2/cockpit.js`:

```javascript
async function askClaude(section) {
  const code = window.CUSTOMER_CODE;
  showAdviceLoading(section);
  try {
    const res = await fetch("/v2/api/claude-advice", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({customer_code: code, section}),
    });
    if (res.status === 503) {
      const data = await res.json();
      showAdviceError("Το Claude δεν είναι ρυθμισμένο. " + (data.error || ""));
      return;
    }
    if (!res.ok) {
      showAdviceError("Σφάλμα κατά τη δημιουργία συμβουλής.");
      return;
    }
    const data = await res.json();
    renderAdviceModal(data); // Greek text renders directly; English trade terms preserved
  } catch (err) {
    showAdviceError("Σφάλμα δικτύου: " + err.message);
  }
}
```

The button on each major section calls `askClaude("offers")`, `askClaude("opportunities")`, etc. The page-level button calls `askClaude("all")`.

### 4.6 — Recommended Actions panel populated on page load

The cockpit's Recommended Actions panel (bottom of the page) is the page-level `askClaude("all")` result, fetched on page load and cached server-side for 12h via the new service's cache.

Implementation: in `services/v2_customer_data.py::get_cockpit_data`, after assembling all the other data, call `generate_cockpit_advice(snapshot_subset)` and attach the top 3–4 `next_actions` to the returned dict as `recommended_actions`. Wrap the call in a try/except that swallows config errors (missing key) — the cockpit must still render fully if Claude is unreachable. Show "Συμβουλές μη διαθέσιμες αυτή τη στιγμή" in the panel as the fallback.

If the cache has a hit, this is essentially free (one DB lookup). If not, it adds ~2–4 seconds to first page load — acceptable given the 12h TTL.

### 4.7 — Label the button "Ask Claude", not "Ask AI"

In the cockpit template, every section button and the page-level button reads **`✦ Ask Claude`** in English. (The button label stays English; only the *response* is in Greek.) This makes the provider explicit to users — useful for trust and for separating the new flow from the old "Ask AI" button on the legacy Customer Benchmark page.

## Acceptance criteria for Ticket 4

- [ ] `anthropic` package installs cleanly via Replit's package manager.
- [ ] `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` are set in Replit Secrets.
- [ ] `POST /v2/api/claude-advice` returns valid JSON with Greek text in `summary`, `key_findings`, `next_actions[].action`, `next_actions[].script_hint`, `opportunities[].title`, `opportunities[].why`.
- [ ] English trade terms (SKU, GM%, GP, Share of Wallet, peer group, HORECA, Slot, etc.) are preserved verbatim in the Greek output. Spot-check 5 responses.
- [ ] Section-level "Ask Claude" buttons return advice scoped to that section's data only (e.g. `section="offers"` produces advice that talks about offers, not white space).
- [ ] Page-level "Ask Claude" button returns full-cockpit advice.
- [ ] Recommended Actions panel renders on page load showing 3–4 ranked actions with Greek titles, Greek reasoning, English trade terms, € impact estimates.
- [ ] Second visit to the same customer's cockpit within 12h serves from cache (verify with timing or log inspection — first call ~3s, second call <100ms).
- [ ] If `ANTHROPIC_API_KEY` is unset, the cockpit still renders fully; the Recommended Actions panel shows a clean "not configured" message; no 500 errors.
- [ ] If Anthropic API returns an error (rate limit, bad request, etc.), the cockpit still renders; the panel shows a clean error message; full error logged server-side.
- [ ] Existing `POST /api/ai/feedback` endpoint (OpenAI-backed) and `ai_feedback_service.generate_feedback` function still work unchanged. The "Ask AI" button on the legacy Customer Benchmark page continues to produce English output via OpenAI.
- [ ] No collision in the `ai_feedback_cache` table: OpenAI cache entries (no prefix) and Claude cache entries (`v2_cockpit_` prefix) coexist.

---

# Coexistence checklist (run before merge of every ticket)

Before merging each ticket, verify:

- [ ] Every URL listed in `do_not_touch.md` returns the same response it did before this work — same HTML, same JSON, same status codes. (Curl-test or Selenium-snapshot 5–10 representative URLs.)
- [ ] Database schema is unchanged except for the **one** new SQL view. No new tables, no altered columns. (The `vw_customer_offer_opportunity` view is created via `CREATE OR REPLACE VIEW` — fully reversible by `DROP VIEW`.)
- [ ] No existing Python function has changed signature.
- [ ] No existing template extends a different base or imports a different macro than before.
- [ ] App startup logs show no new warnings or errors.
- [ ] All existing tests pass without modification.
- [ ] **Operational-batch alignment:**
    - [ ] Every v2 route uses `@require_permission` from the operational batch — no hardcoded role checks.
    - [ ] Every v2 template uses `has_permission()` for visibility — no `current_user.role` checks.
    - [ ] Every user-visible name in v2 UI uses `display_name` with `username` fallback — no raw usernames.
    - [ ] All v2 feature flags are documented in `ROLLBACK_AND_FLAGS.md` with safety class, dependencies, rollback procedure.
    - [ ] All v2 assumptions are logged in the same numbered format as the operational batch.
    - [ ] No new conventions invented — v2 follows operational-batch conventions for settings, audit, timezones, migrations.

---

# Rollout plan (Claudio operates this, not Replit)

| When | Action |
|---|---|
| **Operational batch Phase 1 ships** | **Pre-condition.** v2 cannot start before this. Verify `@require_permission`, `display_name`, settings table, and `users.is_active` are deployed. |
| Before Ticket 1 starts | Add `menu.customers_v2`, `customers.use_cockpit`, `customers.ask_claude` to the operational batch's permission keys list and role-permission mapping. |
| After Ticket 1 ships | Claudio toggles `v2_customers_enabled = true` in admin settings, sets `v2_customers_allowed_usernames = "claudio"`. Visits `/v2/customers` and `/v2/customers/<code>`. Confirms placeholders render. Confirms 404 for users without the visibility flag, 403 for users without permission. |
| After Ticket 2 ships | Claudio adds 1–2 sales reps to `v2_customers_allowed_usernames`. They use the new list view in parallel with the old. Compare numbers in two tabs. |
| After Ticket 3 ships | Same testers use the cockpit for ~2 weeks. Compare against the old 5 per-customer pages. Bug reports filed by URL. |
| After Ticket 4 ships | Validate Greek Claude output makes sense for ~10 real customers. Track Anthropic API costs for one week before broadening. |
| ~30 days after Ticket 4 | Set `v2_customers_allowed_usernames = "*"`. Permission gate becomes the primary access control. Old menu items still in the nav. |
| ~7 days after that | Remove old menu items from `base.html`. Old URLs still resolve directly (bookmarks keep working). |
| ~30 days after that | Replace old route handlers with `301 redirect` to v2 equivalents. |
| ~30 days after that | Delete the files in `do_not_touch.md` (the templates and route files only — keep services and shared helpers). Reclaim ~9,000 lines. |

---

# Out of scope for this brief

- Mobile-optimised version of the cockpit (separate brief, after this is shipped).
- Materialising `vw_customer_offer_opportunity` as a refreshed table (do only if the view becomes slow).
- Full Greek UI localisation (only the Claude output is Greek; the rest stays English).
- Replacing the existing AI Feedback endpoint at `/api/ai/feedback` (it stays).
- Bulk operations on the customer list beyond what `routes_crm_dashboard.py` already supports.
- Anything from the WMDS operational development batch (permissions, batch picking, cooler, etc.) — that's a separate brief.

---

# Questions Replit should ask Claudio before starting

1. **Phase 1 of the operational batch shipped?** (Hard prerequisite. If not, do not start v2 work.) Confirm `@require_permission`, `has_permission()`, `users.display_name`, `users.is_active`, and the settings-table API are all deployed and stable.
2. **Permission keys added?** Confirm `menu.customers_v2`, `customers.use_cockpit`, `customers.ask_claude` are in the role-permission mapping with the role assignments shown in the "Relationship to the WMDS Operational Development Batch" section.
3. **Settings API.** What's the exact API for the settings table introduced in Phase 1? (e.g. `Setting.get(db.session, key, default=...)`, `Setting.set_json(...)`, etc.) The brief assumes a `Setting.get` / `Setting.get_bool` / `Setting.set` interface — verify and adjust the v2 helper functions.
4. **`gross_profit` populated?** Run `SELECT COUNT(*) FROM dw_invoice_lines WHERE gross_profit IS NOT NULL;` against the live database. The cockpit's GP-based KPIs and "Top Items by GP" depend on this column being populated, not just defined in the schema.
5. **Reporting group column.** What's the canonical column for "reporting group" on the customer table? (`reporting_group_code`, `customer_reporting_group_id`, or via a separate join table?) The new SQL view in Ticket 1 needs the right column name.
6. **Charting library.** Which charting library does `templates/customer_analytics/customer_360.html` use? (Chart.js, Plotly, D3, ApexCharts...) v2 reuses it.
7. **Anthropic API access.** Confirm `ANTHROPIC_API_KEY` will be in Replit Secrets, and confirm the model string for `CLAUDE_MODEL`. The brief defaults to `claude-sonnet-4-5` but Anthropic's current production model name should be verified at build time.
8. **Anthropic via Replit's AI Integrations gateway?** The existing `ai_feedback_service.py` uses `AI_INTEGRATIONS_OPENAI_BASE_URL`, suggesting OpenAI is going through Replit's gateway. If Replit's gateway supports Anthropic too, route Claude through the gateway instead of direct to Anthropic. If unclear, default to direct.
9. **Audit-event API.** Has the operational batch's Phase 4 audit-event API shipped by the time Ticket 3 starts? If yes, use it for the v2-specific events listed in section 3.7. If no, defer those events and note in the assumptions log.

If any of these are unclear when Replit starts, **ask first**. Don't guess.
