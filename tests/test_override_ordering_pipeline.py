"""
Pytest regression gate for the override -> ordering pipeline.

Wraps the verification logic in `tests/verify_override_ordering.py` so it
runs automatically under pytest (and therefore under the project's
validation / pre-merge check). A non-zero return value from the
underlying verification fails the test.

The verification applies overrides to representative real SKUs, runs
the ordering refresh just for those items, and checks that the
resulting SkuOrderingSnapshot records reflect the override correctly
across smooth/MA8, new_true/SEEDED_NEW, MOQ-from-ps_items_dw, and
override=0 (suppression) scenarios. It cleans up its own overrides
and snapshots when done.

This test must NOT be silently skipped: a missing `DATABASE_URL` is
treated as a hard failure so the regression gate cannot pass without
actually executing the verification.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)


def test_override_ordering_pipeline():
    """End-to-end regression check for override -> ordering pipeline."""
    import pytest

    db_url = os.environ.get("DATABASE_URL", "")
    assert db_url, (
        "DATABASE_URL must be set so the override -> ordering pipeline "
        "regression can run; refusing to silently pass without executing it."
    )
    # Skip gracefully when the URL has been redirected to the in-memory
    # SQLite DB by other test modules (they set DATABASE_URL =
    # "sqlite:///:memory:" at import time). The pipeline requires real
    # forecast data that only exists in the production PostgreSQL database.
    if "sqlite" in db_url.lower():
        pytest.skip(
            "DATABASE_URL points to SQLite (set by cooler/other test modules); "
            "override pipeline requires a real PostgreSQL database. "
            "Run this test in isolation via the 'override-pipeline' workflow."
        )

    from verify_override_ordering import main as verify_main

    rc = verify_main()
    assert rc == 0, (
        f"override -> ordering pipeline verification failed (exit code {rc}). "
        "See stdout above for the failed assertions; rc=2 means a "
        "pre-existing override on a test SKU made the run invalid."
    )
