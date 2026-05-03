"""Cockpit Ticket 3 — Greek Claude advice + Recommended Actions panel.

Spec: cockpit-brief §12. Tests cover the deterministic surfaces only —
snapshot builder, endpoint failure modes, permission gating, cache key
prefix isolation, and template wiring. The actual Anthropic API call is
not exercised (no live key in CI).
"""
import os
import sys
import json
from unittest.mock import patch

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from sqlalchemy import text

from app import app, db
import routes  # noqa: F401
import main  # noqa: F401  -- registers cockpit_bp
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
    yield row[0]


@pytest.fixture
def cockpit_on(appctx):
    prev = Setting.get(db.session, "cockpit_enabled", "false")
    Setting.set(db.session, "cockpit_enabled", "true")
    db.session.commit()
    yield
    Setting.set(db.session, "cockpit_enabled", prev)
    db.session.commit()


# ─── Snapshot builder ──────────────────────────────────────────────────

FAKE_FULL = {
    "header": {"customer_name": "Acme", "customer_code_365": "X1"},
    "target": {"achievement": {"mtd": {"gap": 1234.0}}},
    "kpis": {"engagement_score": {"value": 72}},
    "trend": {"monthly": [{"month": "2026-01", "sales": 100}]},
    "pvm": {"price": 0, "volume": 0, "mix": 0},
    "top_items_by_gp": [{"item_code": "A"}],
    "active_offers": {"summary": {}, "lines": [{"sku": "S1"}]},
    "offer_opportunities": [{"sku": "O1"}],
    "white_space": [{"item_code": "W1"}],
    "lapsed_items": [{"item_code": "L1"}],
    "cross_sell": [{"item_code": "C1"}],
    "churn_risk_by_category": [{"category": "Cheese"}],
    "price_index_outliers": [{"item_code": "P1"}],
    "activity_timeline": [{"type": "login"}],
}


def test_advice_snapshot_all_includes_section_keys():
    from blueprints.cockpit import _build_advice_snapshot
    s = _build_advice_snapshot(FAKE_FULL, "all")
    assert s["section"] == "all"
    for k in ("header", "target", "kpis", "trend", "pvm",
              "top_items_by_gp", "active_offers", "offer_opportunities",
              "white_space", "lapsed_items", "churn_risk_by_category"):
        assert k in s


def test_advice_snapshot_offers_is_scoped():
    from blueprints.cockpit import _build_advice_snapshot
    s = _build_advice_snapshot(FAKE_FULL, "offers")
    assert s["section"] == "offers"
    assert "active_offers" in s and "offer_opportunities" in s
    # Always-included
    assert "header" in s and "target" in s
    # Other section data must NOT be present
    for k in ("kpis", "trend", "pvm", "top_items_by_gp",
              "white_space", "churn_risk_by_category"):
        assert k not in s


def test_advice_snapshot_risk_synthesises_engagement_from_kpis():
    from blueprints.cockpit import _build_advice_snapshot
    s = _build_advice_snapshot(FAKE_FULL, "risk")
    assert s["engagement"] == {"value": 72}
    assert "churn_risk_by_category" in s
    assert "activity_timeline" in s


def test_advice_snapshot_unknown_section_falls_back_to_all():
    from blueprints.cockpit import _build_advice_snapshot
    s = _build_advice_snapshot(FAKE_FULL, "garbage")
    assert s["section"] == "all"


def test_advice_snapshot_handles_missing_full_payload():
    from blueprints.cockpit import _build_advice_snapshot
    s = _build_advice_snapshot(None, "all")
    assert s["header"] == {} and s["target"] == {}


# ─── Endpoint failure modes ────────────────────────────────────────────

def test_advice_endpoint_404_when_master_flag_off(appctx):
    Setting.set(db.session, "cockpit_enabled", "false")
    db.session.commit()
    client = app.test_client()
    r = client.post("/cockpit/api/X1/advice", json={"section": "all"})
    assert r.status_code == 404


def test_service_raises_value_error_when_unconfigured():
    """Brief §12.6: missing key → service raises ValueError, which the
    endpoint maps to 503 + ``configured: false``."""
    from services import claude_advice_service as svc
    with patch.object(svc, "_get_client", return_value=None):
        with pytest.raises(ValueError, match="not configured"):
            svc.generate_cockpit_advice({"header": {}, "target": {}})


def test_service_propagates_anthropic_error_to_endpoint_500_path():
    """Brief §12.6: Anthropic API error → service raises, endpoint
    catches and returns 500. Verified at the service-call boundary."""
    from services import claude_advice_service as svc

    class _Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("rate_limit")

    with patch.object(svc, "_get_client", return_value=_Boom()), \
         patch.object(svc, "_cache_get", return_value=None):
        with pytest.raises(RuntimeError, match="rate_limit"):
            svc.generate_cockpit_advice({"header": {}, "target": {}})


def test_advice_endpoint_registered_and_master_flag_404(cockpit_on, real_customer):
    """Endpoint exists at the brief's URL. With cockpit ON but no auth on
    the test client, login_required redirects (302). With cockpit OFF the
    before_request gate returns 404 unconditionally."""
    client = app.test_client()
    r = client.post(f"/cockpit/api/{real_customer}/advice",
                    json={"section": "all"})
    # Either redirects to login (302) or 401 — never 404 when flag is on
    # (which would mean the route itself is missing).
    assert r.status_code != 404, "advice endpoint not registered"
    assert r.status_code in (302, 401), r.status_code

    Setting.set(db.session, "cockpit_enabled", "false")
    db.session.commit()
    r = client.post(f"/cockpit/api/{real_customer}/advice",
                    json={"section": "all"})
    assert r.status_code == 404


# ─── Permission key registered ─────────────────────────────────────────

def test_ask_claude_permission_in_editor():
    from routes import ALL_EDITOR_KEYS
    assert "customers.ask_claude" in ALL_EDITOR_KEYS


# ─── Cache key prefix isolation ────────────────────────────────────────

def test_cache_key_prefix_disjoint_from_openai():
    """Brief §12 requires Claude entries prefixed with ``cockpit_`` so
    they never collide with OpenAI rows in ``ai_feedback_cache``."""
    from services.claude_advice_service import CACHE_KEY_PREFIX
    assert CACHE_KEY_PREFIX == "cockpit_"


def test_cache_set_writes_prefixed_key(appctx):
    from services.claude_advice_service import (
        _cache_set, _cache_get, CACHE_KEY_PREFIX, _hash_payload,
    )
    db.session.rollback()  # any leftover failed-tx state from prior tests
    payload = {"_test_only_": "ticket3-cache"}
    h = _hash_payload(payload)
    response = {"summary": "ok", "next_actions": []}
    # Pre-clean any stale row from an aborted prior run.
    db.session.execute(text(
        "DELETE FROM ai_feedback_cache WHERE payload_hash = :h"
    ), {"h": CACHE_KEY_PREFIX + h})
    db.session.commit()
    try:
        _cache_set(h, response)
        # Direct lookup using the prefixed key proves where the row landed.
        row = db.session.execute(text(
            "SELECT 1 FROM ai_feedback_cache WHERE payload_hash = :h"
        ), {"h": CACHE_KEY_PREFIX + h}).first()
        assert row is not None
        # The bare hash (OpenAI namespace) must not collide.
        bare = db.session.execute(text(
            "SELECT 1 FROM ai_feedback_cache WHERE payload_hash = :h"
        ), {"h": h}).first()
        assert bare is None
        # Service-layer get returns what we wrote.
        got = _cache_get(h)
        assert got is not None
    finally:
        db.session.execute(text(
            "DELETE FROM ai_feedback_cache WHERE payload_hash = :h"
        ), {"h": CACHE_KEY_PREFIX + h})
        db.session.commit()


# ─── Template wiring ───────────────────────────────────────────────────

def test_recommended_actions_partial_exists():
    path = os.path.join(PROJECT_ROOT, "templates", "cockpit",
                        "_partials", "recommended_actions.html")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "recommendedActionsCard" in src
    assert "claudeAdviceModal" in src


def test_endpoint_error_responses_are_greek_only():
    """Brief §12.6 + reviewer info-leak finding: 500 must NOT echo
    raw exception detail; 503 must carry the Greek ``message`` field."""
    import re
    path = os.path.join(PROJECT_ROOT, "blueprints", "cockpit.py")
    src = open(path, encoding="utf-8").read()
    assert "Συμβουλές μη διαθέσιμες" in src
    assert "Σφάλμα κατά τη δημιουργία συμβουλής" in src
    # No "detail": str(e) leak to client.
    assert not re.search(r'"detail"\s*:\s*str\(e\)', src), \
        "500 path is leaking raw exception detail to the client"


def test_service_reads_from_app_config_not_env_at_import():
    """Reviewer finding: the service must read API key/model at call
    time from app.config (set in app.py at boot), not from os.environ
    captured at import. Module-level constants are forbidden."""
    src = open(os.path.join(PROJECT_ROOT, "services",
                            "claude_advice_service.py"),
               encoding="utf-8").read()
    assert "ANTHROPIC_API_KEY = os.environ" not in src
    assert "CLAUDE_MODEL = os.environ" not in src
    assert "current_app.config" in src


def test_top_items_has_ask_claude_button():
    """Reviewer finding: Top Items section needs an Ask Claude button
    too (it's the buying surface)."""
    path = os.path.join(PROJECT_ROOT, "templates", "cockpit", "cockpit.html")
    src = open(path, encoding="utf-8").read()
    # Find the Top Items header block and confirm an askClaude button is in it.
    idx = src.index("Top Items")
    window = src[idx:idx + 600]
    assert "askClaude(" in window, "Top Items section missing Ask Claude button"


def test_cockpit_js_exposes_askclaude_helper():
    path = os.path.join(PROJECT_ROOT, "static", "cockpit", "cockpit.js")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "function askClaude" in src
    # Brief §12.6 Greek failure strings must be present verbatim.
    assert "Συμβουλές μη διαθέσιμες" in src
    assert "Σφάλμα κατά τη δημιουργία συμβουλής" in src


def test_existing_openai_service_unchanged():
    """Coexistence check (brief §12 acceptance): the OpenAI module must
    still expose ``generate_feedback`` with no Claude-specific imports."""
    import importlib
    mod = importlib.import_module("ai_feedback_service")
    assert hasattr(mod, "generate_feedback")
    src = open(mod.__file__, encoding="utf-8").read()
    assert "anthropic" not in src.lower()


# ─── Helpers ───────────────────────────────────────────────────────────

class _FakeUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    username = "test_admin"
    role = "admin"

    def get_id(self):
        return self.username
