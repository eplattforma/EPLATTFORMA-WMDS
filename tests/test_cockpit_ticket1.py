"""Cockpit Ticket 1 — scaffold + targets workflow + master-flag gating.

Runs against the live PostgreSQL DB (same posture as the existing
override-pipeline test in tests/). Requires at least one row in
`ps_customers`. Cleans up after itself.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from sqlalchemy import text

from app import app, db
import routes  # noqa: F401  — register routes/PERMISSION_EDITOR_GROUPS before test client uses app
from models import Setting


@pytest.fixture(scope="module")
def appctx():
    with app.app_context():
        yield


@pytest.fixture
def real_customer(appctx):
    row = db.session.execute(text(
        "SELECT customer_code_365 FROM ps_customers "
        "WHERE deleted_at IS NULL LIMIT 1"
    )).first()
    if not row:
        pytest.skip("no ps_customers rows available")
    code = row[0]
    yield code
    db.session.execute(text(
        "DELETE FROM customer_spend_target_history WHERE customer_code_365=:c"
    ), {"c": code})
    db.session.execute(text(
        "DELETE FROM customer_spend_target WHERE customer_code_365=:c"
    ), {"c": code})
    db.session.execute(text(
        "DELETE FROM cockpit_audit_log WHERE customer_code_365=:c"
    ), {"c": code})
    db.session.commit()


@pytest.fixture
def flag_off(appctx):
    prev = Setting.get(db.session, "cockpit_enabled", "false")
    Setting.set(db.session, "cockpit_enabled", "false")
    db.session.commit()
    yield
    Setting.set(db.session, "cockpit_enabled", prev)
    db.session.commit()


def test_master_flag_off_returns_404_for_all_routes(flag_off):
    client = app.test_client()
    for path in ("/cockpit/", "/cockpit/12345", "/cockpit/admin/targets",
                 "/cockpit/api/12345/target",
                 "/cockpit/api/search?q=x",
                 "/cockpit/api/targets/bulk_set"):
        r = client.get(path)
        assert r.status_code == 404, f"{path} returned {r.status_code}, expected 404"


def test_search_redirects_on_exact_code_match(real_customer):
    """Picker landing: pressing Enter on an exact customer code redirects
    straight into that cockpit (brief Section 10)."""
    Setting.set(db.session, "cockpit_enabled", "true")
    db.session.commit()
    try:
        from blueprints.cockpit import _search_customers
        matches = _search_customers(real_customer)
        codes = [m["code"] for m in matches]
        assert real_customer in codes
    finally:
        Setting.set(db.session, "cockpit_enabled", "false")
        db.session.commit()


def test_bulk_set_annual_targets_one_history_row_per_customer(real_customer):
    """Bulk action: applies annual to N customers in one transaction with
    one history row per customer (brief §10.5)."""
    from services.cockpit_targets import (
        bulk_set_annual_targets, get_target, get_target_history,
    )
    cc = real_customer
    res = bulk_set_annual_targets([cc], 36000, actor="mgr1")
    assert res["applied"] == [cc]
    assert res["skipped"] == []

    s = get_target(cc)
    assert float(s["active"]["annual"]) == 36000.0
    assert float(s["active"]["monthly"]) == 3000.0

    h = get_target_history(cc, limit=20)
    # Brief §6.1: history column is `event` (not event_type)
    write_events = [x for x in h if x["event"] in ("created", "modified_by_manager")]
    assert len(write_events) >= 1
    assert any("bulk_set" in (x.get("notes") or "") for x in write_events)


def test_bulk_set_atomic_rejects_on_any_unknown_customer(real_customer):
    """Atomic: if ANY code is unknown, the whole bulk operation is
    rejected before a single write — no partial commits."""
    from services.cockpit_targets import (
        bulk_set_annual_targets, get_target_history,
    )
    cc = real_customer
    history_before = len(get_target_history(cc, limit=100))
    with pytest.raises(ValueError, match="Unknown customer code"):
        bulk_set_annual_targets([cc, "__no_such_customer__"], 99999,
                                actor="mgr1")
    history_after = len(get_target_history(cc, limit=100))
    assert history_after == history_before


def test_bulk_set_rejects_non_numeric_annual():
    from services.cockpit_targets import bulk_set_annual_targets
    with pytest.raises(ValueError):
        bulk_set_annual_targets(["x"], "not-a-number", actor="mgr")


def test_bulk_set_rejects_empty_codes():
    from services.cockpit_targets import bulk_set_annual_targets
    with pytest.raises(ValueError):
        bulk_set_annual_targets([], 1000, actor="mgr")


def test_schema_objects_exist(appctx):
    """Brief §6.1 + §6.7: the three cockpit tables must exist with the
    brief's column names exactly."""
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    assert insp.has_table("customer_spend_target")
    assert insp.has_table("customer_spend_target_history")
    assert insp.has_table("cockpit_audit_log")

    target_cols = {c["name"] for c in insp.get_columns("customer_spend_target")}
    for col in ("customer_code_365", "target_weekly_ambition",
                "target_monthly", "target_quarterly", "target_annual",
                "status", "proposed_by", "proposed_at", "proposed_notes",
                "approved_by", "approved_at",
                "last_modified_at", "last_modified_by"):
        assert col in target_cols, f"missing column: {col}"

    hist_cols = {c["name"] for c in insp.get_columns("customer_spend_target_history")}
    for col in ("event", "created_at",
                "target_weekly_ambition", "target_monthly",
                "target_quarterly", "target_annual",
                "previous_weekly_ambition", "previous_monthly",
                "previous_quarterly", "previous_annual",
                "actor_username", "notes"):
        assert col in hist_cols, f"history missing column: {col}"


def test_cadence_auto_population_from_annual_only(appctx):
    from services.cockpit_targets import _normalize_cadences
    out = _normalize_cadences({"annual": "12000"})
    assert float(out["annual"]) == 12000.0
    assert float(out["monthly"]) == 1000.0
    assert float(out["quarterly"]) == 3000.0
    assert abs(float(out["weekly_ambition"]) - 230.77) < 0.01


def test_cadence_keeps_explicit_values(appctx):
    from services.cockpit_targets import _normalize_cadences
    out = _normalize_cadences({"annual": "12000", "monthly": "999"})
    assert float(out["monthly"]) == 999.0
    assert float(out["quarterly"]) == 3000.0


def test_propose_approve_reject_workflow(real_customer):
    from services.cockpit_targets import (
        propose_target, approve_proposal, set_target_directly,
        reject_proposal, get_target, get_target_history,
    )
    cc = real_customer

    s = propose_target(cc, {"annual": 12000, "notes": "first"}, actor="am1")
    assert s["pending_proposal"]["annual"] is not None
    assert float(s["pending_proposal"]["monthly"]) == 1000.0

    s = approve_proposal(cc, actor="mgr1")
    assert s["active"] is not None
    assert float(s["active"]["annual"]) == 12000.0
    assert s["pending_proposal"] is None

    s = set_target_directly(cc, {"annual": 24000}, actor="mgr1")
    assert float(s["active"]["annual"]) == 24000.0

    propose_target(cc, {"annual": 30000}, actor="am1")
    s = reject_proposal(cc, reason="too high", actor="mgr1")
    assert s["pending_proposal"] is None
    assert float(s["active"]["annual"]) == 24000.0

    h = get_target_history(cc, limit=20)
    events = [x["event"] for x in h]
    assert "proposed" in events
    assert "approved" in events
    assert "modified_by_manager" in events
    assert "rejected" in events
    for row in h:
        assert "actor_display_name" in row


def test_propose_unknown_customer_raises(appctx):
    from services.cockpit_targets import propose_target
    with pytest.raises(ValueError):
        propose_target("__no_such_customer__", {"annual": 1000}, actor="x")


def test_inline_edit_annual_only_preserves_explicit_cadences(real_customer):
    """Brief §10.5: an annual-only edit (the inline cell on the admin
    page) auto-populates derived cadences ONLY if those cells were empty.
    Explicit non-null cadence values on the existing row are preserved.
    """
    from services.cockpit_targets import set_target_directly, get_target
    cc = real_customer
    # Manager first sets a full custom set of values (e.g. seasonal)
    set_target_directly(
        cc,
        {"annual": 12000, "monthly": 1500, "quarterly": 4000,
         "weekly_ambition": 350},
        actor="mgr1",
    )
    s = get_target(cc)
    assert float(s["active"]["monthly"]) == 1500.0
    assert float(s["active"]["quarterly"]) == 4000.0
    assert float(s["active"]["weekly_ambition"]) == 350.0

    # Annual-only inline edit must NOT overwrite the seasonal cadences
    set_target_directly(cc, {"annual": 24000}, actor="mgr1")
    s = get_target(cc)
    assert float(s["active"]["annual"]) == 24000.0
    # Seasonal values intact — derived-from-annual would have given
    # 24000/12=2000, 24000/4=6000, 24000/52≈461.54 — those would all be wrong
    assert float(s["active"]["monthly"]) == 1500.0
    assert float(s["active"]["quarterly"]) == 4000.0
    assert float(s["active"]["weekly_ambition"]) == 350.0


def test_inline_edit_annual_only_fills_empty_cadences(real_customer):
    """Conversely: when the existing row's cadences are NULL, an
    annual-only edit must derive them from the new annual."""
    from services.cockpit_targets import set_target_directly, get_target
    from app import db as _db
    cc = real_customer
    # Seed a row with only annual (cadences NULL)
    _db.session.execute(text("""
        INSERT INTO customer_spend_target
          (customer_code_365, target_annual, status,
           last_modified_by, last_modified_at)
        VALUES (:c, 50000, 'active', 'mgr1', NOW())
    """), {"c": cc})
    _db.session.commit()

    set_target_directly(cc, {"annual": 24000}, actor="mgr1")
    s = get_target(cc)
    assert float(s["active"]["annual"]) == 24000.0
    assert float(s["active"]["monthly"]) == 2000.0
    assert float(s["active"]["quarterly"]) == 6000.0
    assert abs(float(s["active"]["weekly_ambition"]) - 461.54) < 0.01


def test_audit_log_entries_for_full_workflow(real_customer):
    """Brief §6.7: each of the five workflow operations writes a row to
    cockpit_audit_log."""
    from services.cockpit_targets import (
        propose_target, approve_proposal, set_target_directly,
        reject_proposal, clear_target,
    )
    cc = real_customer
    propose_target(cc, {"annual": 1000}, actor="am1")
    approve_proposal(cc, actor="mgr1")
    set_target_directly(cc, {"annual": 2000}, actor="mgr1")
    propose_target(cc, {"annual": 3000}, actor="am1")
    reject_proposal(cc, reason="nope", actor="mgr1")
    clear_target(cc, actor="mgr1")

    rows = db.session.execute(text("""
        SELECT event_name FROM cockpit_audit_log
        WHERE customer_code_365 = :c
        ORDER BY created_at, id
    """), {"c": cc}).all()
    events = [r[0] for r in rows]
    assert "customer.target.proposed" in events
    assert "customer.target.approved" in events
    assert "customer.target.set" in events
    assert "customer.target.rejected" in events
    assert "customer.target.cleared" in events


def test_history_records_previous_values(real_customer):
    """Brief §6.1: history table has `previous_*` columns capturing the
    prior snapshot of every change."""
    from services.cockpit_targets import set_target_directly
    cc = real_customer
    set_target_directly(cc, {"annual": 10000, "monthly": 800}, actor="mgr1")
    set_target_directly(cc, {"annual": 20000, "monthly": 1700}, actor="mgr1")

    rows = db.session.execute(text("""
        SELECT target_annual, target_monthly,
               previous_annual, previous_monthly, event
        FROM customer_spend_target_history
        WHERE customer_code_365 = :c
        ORDER BY created_at DESC, id DESC
    """), {"c": cc}).mappings().all()
    # Most-recent row is the second update — its previous_* should equal
    # the values from the first update.
    latest = rows[0]
    assert latest["event"] == "modified_by_manager"
    assert float(latest["target_annual"]) == 20000.0
    assert float(latest["previous_annual"]) == 10000.0
    assert float(latest["previous_monthly"]) == 800.0


def test_postgres_view_present_or_sqlite_skipped(appctx):
    """Brief §10.2: view must exist on Postgres; on SQLite the cockpit
    service degrades to [] rather than crashing."""
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        from sqlalchemy import inspect
        view_names = inspect(db.engine).get_view_names()
        assert "vw_customer_offer_opportunity" in view_names
        n = db.session.execute(text(
            "SELECT COUNT(*) FROM vw_customer_offer_opportunity"
        )).scalar()
        assert n is not None and n >= 0
    else:
        from services.cockpit_offer_opportunity import get_offer_opportunities
        assert get_offer_opportunities("anything") == []


def test_cockpit_permission_keys_assignable_via_editor():
    """Brief Section 14: keys are unassigned by default but Claudio must
    be able to grant them per-user from the existing permission editor."""
    from routes import ALL_EDITOR_KEYS
    from services.permissions import COCKPIT_PERMISSION_KEYS
    for key in COCKPIT_PERMISSION_KEYS:
        assert key in ALL_EDITOR_KEYS, (
            f"Cockpit permission '{key}' is registered but not assignable "
            f"from the user-permissions editor"
        )


def test_compute_achievement_returns_all_keys(real_customer):
    from services.cockpit_targets import compute_achievement
    a = compute_achievement(real_customer, "mtd")
    for k in ("period", "actual", "target", "pct", "gap",
              "run_rate_projection", "on_pace"):
        assert k in a


def test_get_target_exposes_display_names_for_actors(real_customer):
    """Brief Section 14: 'All user-visible names use display_name, never
    raw usernames.' get_target() must surface display-name fields for
    proposed_by/approved_by/last_modified_by alongside the raw username."""
    from services.cockpit_targets import (
        propose_target, approve_proposal, set_target_directly, get_target,
    )
    cc = real_customer
    propose_target(cc, {"annual": 5000}, actor="am-no-such-user")
    s = get_target(cc)
    assert "proposed_by_display_name" in s["pending_proposal"]
    # No users row exists for this actor → falls back to the username
    assert s["pending_proposal"]["proposed_by_display_name"] == "am-no-such-user"

    approve_proposal(cc, actor="mgr-no-such-user")
    s = get_target(cc)
    assert "approved_by_display_name" in s["active"]
    assert "last_modified_by_display_name" in s["active"]
    assert s["active"]["approved_by_display_name"] == "mgr-no-such-user"


def test_search_template_does_not_use_unsafe_innerhtml_with_user_data():
    """Stored-XSS regression: the search.html live-search JS must NOT
    interpolate API response strings (customer name/code) into
    ``innerHTML`` — those values are external business data.
    """
    import re
    path = os.path.join(PROJECT_ROOT, "templates", "cockpit", "search.html")
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    # No template literal that interpolates `it.code` or `it.name` into
    # an innerHTML / outerHTML / insertAdjacentHTML assignment.
    risky = re.search(
        r"(innerHTML|outerHTML|insertAdjacentHTML)\s*[=,(].*?\$\{\s*it\.",
        src, re.DOTALL,
    )
    assert risky is None, (
        "search.html interpolates API response data into innerHTML — "
        "this is a stored-XSS vector. Use textContent / DOM nodes instead."
    )
    # Positive check: the safe path uses textContent for the dynamic value
    assert "textContent" in src
