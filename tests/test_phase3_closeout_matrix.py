"""Phase 3 closeout: 7-key x 5-role decorator behavior matrix (Task #16).

Captures actual HTTP status codes for every (key, role) cell with the
``permissions_enforcement_enabled`` flag temporarily flipped ON for
the duration of each test cell. The captured matrix is written to
``PHASE3_CLOSEOUT_MATRIX.txt`` so the closeout audit trail can be
embedded in ``PHASE_TEST_RESULTS.md`` (Section 1.3 of the Verification
& Closeout brief).

Flag isolation (honest description): the ``enforcement_on`` fixture
**does** call ``Setting.set(...)`` followed by ``db.session.commit()``
to flip ``permissions_enforcement_enabled`` to ``'true'`` (and
``permissions_role_fallback_enabled`` to ``'true'``) -- so the new
values are briefly committed to the dev DB. The fixture's teardown
then writes the previous values back via the same ``Setting.set`` +
``commit`` pair, so the dev DB ends in its prior state. This is
**not** a SQL transaction; if the test process crashes between
``yield`` and teardown, the flag may be left in the flipped state
until the next run. No production database is touched -- this test
suite runs against the dev DB only. Same pattern as
``tests/test_permission_enforcement.py``.

Each (key, role) cell is its own parametrised test so pytest gives a
fresh fixture activation per cell -- this reliably exercises the
decorator. A module-scoped capture dict accumulates the actual codes
across the run; the matrix file is written in the dict's teardown.

Routes selected per key (one route per key suffices to verify the
decorator behavior; cells reflect the @require_permission gate or the
``_role_ok`` helper for ``menu.communications`` -- see
ASSUMPTION-018):

  picking.manage_batches  GET  /admin/batch/manage
  sync.run_manual         GET  /datawarehouse/full-sync
  settings.manage_users   GET  /admin/users
  menu.datawarehouse      GET  /datawarehouse/menu
  menu.warehouse          GET  /stock-dashboard
  routes.manage           POST /admin/update-stop-sequence
  menu.communications     GET  /admin/communications/history/customer/<code>
"""
import os
import sys
import uuid
from pathlib import Path

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from sqlalchemy import text


@pytest.fixture
def app_ctx():
    assert os.environ.get("DATABASE_URL"), "DATABASE_URL required"
    import main  # noqa: F401
    from app import app, db
    with app.app_context():
        db.session.remove()
        yield app, db
        db.session.remove()


def _seed_user(db, username, role):
    from werkzeug.security import generate_password_hash
    db.session.execute(
        text(
            "INSERT INTO users (username, password, role, is_active) "
            "VALUES (:u, :p, :r, true) "
            "ON CONFLICT (username) DO NOTHING"
        ),
        {"u": username, "p": generate_password_hash("dummy123"), "r": role},
    )


def _cleanup(db, username):
    db.session.execute(
        text("DELETE FROM user_permissions WHERE username = :u"),
        {"u": username},
    )
    db.session.execute(
        text("DELETE FROM users WHERE username = :u"),
        {"u": username},
    )
    db.session.commit()


def _login(client, username):
    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True


ROLES = ["admin", "warehouse_manager", "crm_admin", "picker", "driver"]

# Module-level seeded usernames; populated once via the autouse module
# fixture below so we don't pay 35x INSERT/DELETE cycles.
_USERS = {}


@pytest.fixture(scope="module", autouse=True)
def _seed_role_users():
    """Seed one user per role for the whole module, then clean up."""
    assert os.environ.get("DATABASE_URL"), "DATABASE_URL required"
    import main  # noqa: F401
    from app import app, db
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        for role in ROLES:
            name = f"t16_{role}_{suffix}"
            _USERS[role] = name
            _seed_user(db, name, role)
        db.session.commit()
    try:
        yield _USERS
    finally:
        with app.app_context():
            for name in list(_USERS.values()):
                _cleanup(db, name)
            _USERS.clear()


@pytest.fixture
def role_users(app_ctx):
    return dict(_USERS)


@pytest.fixture
def enforcement_on(app_ctx):
    _, db = app_ctx
    from models import Setting
    prev_e = Setting.get(db.session, "permissions_enforcement_enabled", "false")
    prev_f = Setting.get(db.session, "permissions_role_fallback_enabled", "true")
    Setting.set(db.session, "permissions_enforcement_enabled", "true")
    Setting.set(db.session, "permissions_role_fallback_enabled", "true")
    db.session.commit()
    yield
    Setting.set(db.session, "permissions_enforcement_enabled", prev_e)
    Setting.set(db.session, "permissions_role_fallback_enabled", prev_f)
    db.session.commit()


# (key, method, path)
ROUTES = [
    ("picking.manage_batches", "GET",  "/admin/batch/manage"),
    ("sync.run_manual",        "GET",  "/datawarehouse/full-sync"),
    ("settings.manage_users",  "GET",  "/admin/users"),
    ("menu.datawarehouse",     "GET",  "/datawarehouse/menu"),
    ("menu.warehouse",         "GET",  "/stock-dashboard"),
    ("routes.manage",          "POST", "/admin/update-stop-sequence"),
    ("menu.communications",    "GET",
        "/admin/communications/history/customer/CLOSEOUT_TEST"),
]

# Per-cell EXACT expected outcome. Each value is either "deny" (decorator/
# helper aborts(403)) or an int -- the precise HTTP status code the cell
# is pinned to. Pinning ALLOW cells to specific codes (rather than the
# loose "any non-403") ensures a route-body 500/502 would FAIL the cell
# instead of silently passing as "ALLOW".
#
# Most ALLOW cells expect 200. The single 302 cell is wm hitting
# /datawarehouse/menu, where @require_permission allows wm but the
# route body has `if current_user.role != 'admin': redirect(...)`.
EXPECTED = {
    "picking.manage_batches": {
        "admin": 200, "warehouse_manager": 200,
        "crm_admin": "deny", "picker": "deny", "driver": "deny",
    },
    "sync.run_manual": {
        "admin": 200, "warehouse_manager": "deny",
        "crm_admin": "deny", "picker": "deny", "driver": "deny",
    },
    "settings.manage_users": {
        "admin": 200, "warehouse_manager": "deny",
        "crm_admin": "deny", "picker": "deny", "driver": "deny",
    },
    "menu.datawarehouse": {
        "admin": 200, "warehouse_manager": 302,  # wm: body redirect
        "crm_admin": "deny", "picker": "deny", "driver": "deny",
    },
    "menu.warehouse": {
        "admin": 200, "warehouse_manager": 200,
        "crm_admin": "deny", "picker": "deny", "driver": "deny",
    },
    "routes.manage": {
        "admin": 200, "warehouse_manager": 200,
        "crm_admin": "deny", "picker": "deny", "driver": "deny",
    },
    "menu.communications": {
        "admin": 200, "warehouse_manager": 200,
        "crm_admin": 200, "picker": "deny", "driver": "deny",
    },
}


def _is_allow(expected):
    return expected != "deny"


def _format_matrix(captured):
    lines = []
    lines.append("Phase 3 closeout matrix -- captured HTTP status codes")
    lines.append("Generated by tests/test_phase3_closeout_matrix.py")
    lines.append("")
    header_cells = [r.replace("warehouse_manager", "wm") for r in ROLES]
    lines.append(f"| Key | { ' | '.join(header_cells) } |")
    lines.append("|" + "---|" * (len(ROLES) + 1))
    for key, _, _ in ROUTES:
        cells = []
        for role in ROLES:
            code = captured.get((key, role), "MISSING")
            mark = "ALLOW" if _is_allow(EXPECTED[key][role]) else "DENY "
            cells.append(f"{mark} {code}")
        lines.append(f"| `{key}` | { ' | '.join(cells) } |")
    lines.append("")
    lines.append("Routes hit:")
    for key, method, path in ROUTES:
        lines.append(f"  - `{key}` -- {method} `{path}`")
    lines.append("")
    lines.append(
        "Legend: ALLOW = decorator/helper passes (response code is "
        "whatever the body returned; may be 200/302/4xx/5xx). DENY = "
        "decorator/helper aborts via abort(403). All cells captured "
        "with `permissions_enforcement_enabled = 'true'` inside the "
        "test fixture."
    )
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module", autouse=True)
def matrix_capture():
    """Module-scoped dict that accumulates (key, role) -> status_code
    across all parametrised test cells.

    Regeneration workflow: by default this fixture **does not** write
    ``PHASE3_CLOSEOUT_MATRIX.txt`` -- normal ``pytest`` runs assert
    behaviour without churning the tracked closeout artefact. To
    explicitly regenerate the snapshot, run with the env var
    ``PHASE3_REGEN_MATRIX=1`` set, e.g.::

        PHASE3_REGEN_MATRIX=1 pytest -q tests/test_phase3_closeout_matrix.py

    This pattern keeps the captured matrix under deliberate operator
    control rather than letting any test invocation overwrite it.
    """
    captured = {}
    _CAPTURE_REF.append(captured)
    yield captured
    if captured and os.environ.get("PHASE3_REGEN_MATRIX") == "1":
        out_path = Path(PROJECT_ROOT) / "PHASE3_CLOSEOUT_MATRIX.txt"
        out_path.write_text(_format_matrix(captured))


# Indirect handle so individual parametrised tests can append without
# taking the matrix_capture fixture as a parameter dep (autouse already
# instantiates it once per module).
_CAPTURE_REF: list = []


def _hit(client, method, path):
    if method == "POST":
        return client.post(
            path, json={"invoice_no": "CLOSEOUT", "sequence": 1.0}
        )
    return client.get(path)


@pytest.mark.parametrize("key,method,path", ROUTES, ids=[r[0] for r in ROUTES])
@pytest.mark.parametrize("role", ROLES)
def test_decorator_cell(
    app_ctx, role_users, enforcement_on, key, method, path, role
):
    """One (key, role) cell of the 7x5 closeout matrix. Asserts that
    the decorator/helper allows or denies the role per ``EXPECTED`` and
    appends the actual HTTP status code to the module-scoped capture
    dict so the snapshot file can be assembled at teardown."""
    client = app_ctx[0].test_client()
    _login(client, role_users[role])
    resp = _hit(client, method, path)
    if _CAPTURE_REF:
        _CAPTURE_REF[0][(key, role)] = resp.status_code
    expected = EXPECTED[key][role]
    if expected == "deny":
        assert resp.status_code == 403, (
            f"{key} via {role} on {method} {path}: decorator should "
            f"DENY (403), got {resp.status_code}"
        )
    else:
        # Pin to the exact expected status code (e.g. 200 or 302) so
        # that a body 500/502 fails the cell instead of silently
        # passing as ALLOW. See EXPECTED dict for per-cell rationale.
        assert resp.status_code == expected, (
            f"{key} via {role} on {method} {path}: decorator should "
            f"ALLOW with status {expected}, got {resp.status_code}"
        )


# ------------------------------------------------------------------
# Supplemental per-role ALLOW evidence
# ------------------------------------------------------------------
# The 7-key matrix above only enforces *admin-tier* permission keys
# (picking/sync/settings/menu.{warehouse,datawarehouse,communications}/
# routes), so picker and driver cannot earn an ALLOW cell inside it --
# their roles legitimately deny on all 7. To honour the closeout
# requirement that each role has at least one captured ALLOW, the two
# tests below hit picker- and driver-facing routes (gated by
# ``current_user.role`` checks rather than ``@require_permission``,
# but still subject to login + the same enforcement-on fixture).
#
# Admin DENY is design-impossible: admin holds the ``*`` wildcard so
# no @require_permission can deny them, and admin satisfies every
# role-string body check we ship. This is documented as Residual Risk
# RR-001 in PHASE_TEST_RESULTS.md Section 1.3 (no test attempts to
# fabricate an admin DENY).


def test_picker_allow_dashboard(app_ctx, role_users, enforcement_on):
    """Supplemental ALLOW evidence: picker reaches /picker/dashboard
    cleanly with enforcement on. The route is @login_required only,
    with a body-level role check that returns the dashboard HTML for
    pickers and a 302 redirect for everyone else -- so picker → 200
    is the right ALLOW signal under enforcement."""
    client = app_ctx[0].test_client()
    _login(client, role_users["picker"])
    resp = client.get("/picker/dashboard")
    assert resp.status_code == 200, (
        f"picker → /picker/dashboard should ALLOW (200), got "
        f"{resp.status_code}"
    )


def test_driver_allow_routes_list(app_ctx, role_users, enforcement_on):
    """Supplemental ALLOW evidence: driver reaches /driver/routes
    cleanly with enforcement on. The route uses the
    ``driver_required`` decorator (role in ['driver', 'admin']) and
    returns the routes list HTML for drivers."""
    client = app_ctx[0].test_client()
    _login(client, role_users["driver"])
    resp = client.get("/driver/routes")
    assert resp.status_code == 200, (
        f"driver → /driver/routes should ALLOW (200), got "
        f"{resp.status_code}"
    )
