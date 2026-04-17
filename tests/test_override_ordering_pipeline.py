"""
Regression test for the override -> ordering pipeline.

Ports the assertions from the one-shot manual script
``tests/verify_override_ordering.py`` into an isolated pytest test that:

  * Seeds tiny fixtures (DwItem, SkuForecastResult, SkuForecastProfile,
    ForecastItemSupplierMap, SkuForecastOverride) inside a transaction on
    the in-memory SQLite test DB provided by ``tests/conftest.py``.
  * Monkeypatches ``services.replenishment_mvp.ps365_client.fetch_supplier_stock``
    so the test never touches PS365 / the network.
  * Runs ``refresh_ordering_snapshot`` for the seeded item codes.
  * Asserts the same SkuOrderingSnapshot fields the manual script checks
    (final_forecast_source, system/override/final forecast weekly qty,
    target_stock, raw/rounded order qty, explanation_json) across the four
    scenarios:
        - smooth_ma8        : normal smooth/MA8 SKU
        - new_true_seeded   : SEEDED_NEW SKU (override beats system forecast)
        - with_moq_600      : SKU with MOQ from ps_items_dw
        - override_zero     : override = 0 fully suppresses ordering
"""
import math
import os
from decimal import Decimal

import pytest

# Ensure SQLite + a dummy session secret BEFORE app.py is imported so that
# app.py picks the SQLite-friendly engine options branch and never tries to
# attach Postgres-only pool args to the pysqlite engine.
os.environ.setdefault("SESSION_SECRET", "test-secret-for-override-pipeline")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
# Prevent app.py / scheduler.py from treating this as a production startup,
# which would trigger Postgres-only catch-up jobs at import time.
os.environ.pop("REPLIT_ENVIRONMENT", None)
os.environ.pop("REPLIT_DEPLOYMENT", None)
os.environ.pop("ENABLE_BACKGROUND_JOBS", None)

# SQLite only auto-increments columns declared exactly as INTEGER PRIMARY KEY.
# Several models in this codebase use BigInteger PKs, which SQLite emits as
# BIGINT and therefore won't autoincrement, breaking inserts in the test DB.
# Compile BigInteger as INTEGER for SQLite so autoincrement works.
from sqlalchemy import BigInteger as _BigInteger  # noqa: E402
from sqlalchemy.ext.compiler import compiles as _compiles  # noqa: E402


@_compiles(_BigInteger, "sqlite")
def _bigint_as_integer_for_sqlite(element, compiler, **kw):
    return "INTEGER"


SCENARIOS = [
    {
        "code": "TST-SMOOTH",
        "label": "smooth_ma8",
        "override_qty": Decimal("10.000000"),
        "system_weekly": 7.0,
        "target_weeks": 4.0,
        "demand_class": "smooth",
        "supplier_code": "SUP-A",
        "lead_time_days": 3.0,
        "review_cycle_days": 1.0,
        "moq_dw": 0,
        "stock_now": 5.0,
        "ordered_now": 0.0,
        "reserved_now": 0.0,
    },
    {
        "code": "TST-NEW",
        "label": "new_true_seeded",
        "override_qty": Decimal("8.000000"),
        "system_weekly": 2.5,
        "target_weeks": 4.0,
        "demand_class": "new_true",
        "supplier_code": "SUP-B",
        "lead_time_days": 2.0,
        "review_cycle_days": 1.0,
        "moq_dw": 0,
        "stock_now": 0.0,
        "ordered_now": 0.0,
        "reserved_now": 0.0,
    },
    {
        "code": "TST-MOQ",
        "label": "with_moq_600",
        "override_qty": Decimal("15.000000"),
        "system_weekly": 12.0,
        "target_weeks": 4.0,
        "demand_class": "smooth",
        "supplier_code": "SUP-C",
        "lead_time_days": 5.0,
        "review_cycle_days": 2.0,
        "moq_dw": 600,
        "stock_now": 10.0,
        "ordered_now": 0.0,
        "reserved_now": 0.0,
    },
    {
        "code": "TST-ZERO",
        "label": "override_zero",
        "override_qty": Decimal("0.000000"),
        "system_weekly": 9.0,
        "target_weeks": 4.0,
        "demand_class": "smooth",
        "supplier_code": "SUP-D",
        "lead_time_days": 4.0,
        "review_cycle_days": 1.0,
        "moq_dw": 0,
        "stock_now": 0.0,
        "ordered_now": 0.0,
        "reserved_now": 0.0,
    },
]


def _expected_rounded(raw, moq, mult):
    if raw <= 0:
        return 0.0
    if mult > 0:
        rounded = math.ceil(raw / mult) * mult
    else:
        rounded = math.ceil(raw)
    if moq > 0:
        rounded = max(rounded, moq)
    return rounded


@pytest.fixture
def seeded_override_pipeline(monkeypatch):
    """Seed tiny fixtures + monkeypatch PS365 stock fetch.

    Uses its own in-memory SQLite Flask app context (not the shared
    ``app`` fixture in tests/conftest.py) so the test stays isolated from
    route imports that issue Postgres-specific SQL at import time.
    """
    from app import app, db
    from models import (
        DwItem,
        ForecastItemSupplierMap,
        Setting,
        SkuForecastOverride,
        SkuForecastProfile,
        SkuForecastResult,
    )

    with app.app_context():
        db.create_all()
        session = db.session

        Setting.set(session, "forecast_buffer_stock_days", "1")
        Setting.set(session, "forecast_review_cycle_days", "1")

        for s in SCENARIOS:
            session.add(DwItem(
                item_code_365=s["code"],
                item_name=f"Test item {s['code']}",
                active=True,
                attr_hash="test",
                supplier_code_365=s["supplier_code"],
                min_order_qty=s["moq_dw"] or None,
            ))
            session.add(SkuForecastProfile(
                item_code_365=s["code"],
                demand_class=s["demand_class"],
                target_weeks_of_stock=Decimal(str(s["target_weeks"])),
            ))
            session.add(SkuForecastResult(
                item_code_365=s["code"],
                final_forecast_weekly_qty=Decimal(str(s["system_weekly"])),
                final_forecast_daily_qty=Decimal(str(s["system_weekly"] / 7.0)),
            ))
            session.add(ForecastItemSupplierMap(
                item_code_365=s["code"],
                supplier_code=s["supplier_code"],
                supplier_name=f"Supplier {s['supplier_code']}",
                lead_time_days=Decimal(str(s["lead_time_days"])),
                review_cycle_days=Decimal(str(s["review_cycle_days"])),
                is_active=True,
            ))
            session.add(SkuForecastOverride(
                item_code_365=s["code"],
                override_weekly_qty=s["override_qty"],
                reason_code="test_regression",
                reason_note=f"regression scenario={s['label']}",
                created_by="pytest_regression",
                is_active=True,
            ))
        session.flush()

        stock_by_supplier = {}
        for s in SCENARIOS:
            stock_by_supplier.setdefault(s["supplier_code"], {})[s["code"]] = {
                "stock_now_units": s["stock_now"],
                "ordered_now_units": s["ordered_now"],
                "reserved_now_units": s["reserved_now"],
            }

        def fake_fetch_supplier_stock(supplier_code, *args, **kwargs):
            return stock_by_supplier.get(supplier_code, {})

        monkeypatch.setattr(
            "services.replenishment_mvp.ps365_client.fetch_supplier_stock",
            fake_fetch_supplier_stock,
        )

        try:
            yield {"session": session, "scenarios": SCENARIOS}
        finally:
            session.rollback()


def test_override_ordering_pipeline_regression(seeded_override_pipeline):
    from app import db
    from models import SkuOrderingSnapshot
    from services.forecast.ordering_refresh_service import refresh_ordering_snapshot

    session = seeded_override_pipeline["session"]
    scenarios = seeded_override_pipeline["scenarios"]
    item_codes = [s["code"] for s in scenarios]

    result = refresh_ordering_snapshot(
        session,
        item_codes=item_codes,
        created_by="pytest_regression",
    )

    assert result["snapshot_count"] == len(scenarios), (
        f"refresh produced {result['snapshot_count']} snapshots,"
        f" expected {len(scenarios)}"
    )
    assert result["override_count"] == len(scenarios), (
        f"override_count={result['override_count']}, expected {len(scenarios)}"
    )

    snaps = {
        snap.item_code_365: snap
        for snap in session.query(SkuOrderingSnapshot)
        .filter(SkuOrderingSnapshot.item_code_365.in_(item_codes))
        .all()
    }
    assert set(snaps.keys()) == set(item_codes)

    for s in scenarios:
        code = s["code"]
        label = s["label"]
        snap = snaps[code]
        ovr = float(s["override_qty"])
        sys_w = float(s["system_weekly"])

        assert snap.final_forecast_source == "override", (
            f"[{code}/{label}] expected final_forecast_source='override',"
            f" got {snap.final_forecast_source}"
        )
        assert abs(float(snap.system_forecast_weekly_qty) - sys_w) < 1e-4, (
            f"[{code}/{label}] system_forecast_weekly_qty mismatch"
        )
        assert snap.override_forecast_weekly_qty is not None
        assert abs(float(snap.override_forecast_weekly_qty) - ovr) < 1e-4, (
            f"[{code}/{label}] override_forecast_weekly_qty mismatch"
        )
        assert abs(float(snap.final_forecast_weekly_qty) - ovr) < 1e-4, (
            f"[{code}/{label}] final_forecast_weekly_qty should equal override"
        )
        assert abs(float(snap.final_forecast_daily_qty) - ovr / 7.0) < 1e-4, (
            f"[{code}/{label}] final_forecast_daily_qty should equal override/7"
        )

        eff_w = ovr
        eff_d = ovr / 7.0
        target_weeks = float(s["target_weeks"])
        lt = float(snap.lead_time_days)
        rc = float(snap.review_cycle_days)
        bd = float(snap.buffer_days)
        expected_target = (eff_w * target_weeks) + eff_d * lt + eff_d * rc + eff_d * bd
        assert abs(float(snap.target_stock_qty) - expected_target) < 1e-3, (
            f"[{code}/{label}] target_stock_qty={float(snap.target_stock_qty)},"
            f" expected {expected_target}"
        )

        net_avail = float(snap.net_available_qty)
        expected_raw = max(0.0, expected_target - net_avail)
        assert abs(float(snap.raw_recommended_order_qty) - expected_raw) < 1e-3, (
            f"[{code}/{label}] raw_recommended_order_qty mismatch"
            f" (got {float(snap.raw_recommended_order_qty)}, expected {expected_raw})"
        )

        moq_snap = float(snap.min_order_qty) if snap.min_order_qty is not None else 0.0
        mult = float(snap.order_multiple) if snap.order_multiple is not None else 0.0
        expected_rounded = _expected_rounded(
            float(snap.raw_recommended_order_qty), moq_snap, mult
        )
        assert abs(float(snap.rounded_order_qty) - expected_rounded) < 1e-3, (
            f"[{code}/{label}] rounded_order_qty mismatch"
            f" (got {float(snap.rounded_order_qty)}, expected {expected_rounded})"
        )

        if label == "override_zero":
            assert float(snap.final_forecast_weekly_qty) == 0.0
            assert float(snap.target_stock_qty) == 0.0
            assert float(snap.raw_recommended_order_qty) == 0.0
            assert float(snap.rounded_order_qty) == 0.0
        if label == "with_moq_600":
            assert moq_snap == float(s["moq_dw"]), (
                f"[{code}] expected snapshot MOQ={s['moq_dw']}, got {moq_snap}"
            )
            assert (
                float(snap.rounded_order_qty) >= float(s["moq_dw"])
                or float(snap.raw_recommended_order_qty) <= 0
            ), f"[{code}] rounded_order_qty does not respect MOQ floor"
        if label == "new_true_seeded":
            assert snap.final_forecast_source == "override", (
                f"[{code}] override should beat SEEDED_NEW system forecast"
            )

        ex = snap.explanation_json or {}
        assert ex.get("forecast_source") == "override", (
            f"[{code}/{label}] explanation_json.forecast_source mismatch"
        )
        assert abs(float(ex.get("override_weekly_qty", -1)) - ovr) < 1e-4, (
            f"[{code}/{label}] explanation_json.override_weekly_qty mismatch"
        )
        assert ex.get("override_reason_code") == "test_regression", (
            f"[{code}/{label}] explanation_json.override_reason_code mismatch"
        )

    # Rollback so no rows are left behind in the (in-memory) DB.
    session.rollback()
    remaining = (
        db.session.query(SkuOrderingSnapshot)
        .filter(SkuOrderingSnapshot.item_code_365.in_(item_codes))
        .count()
    )
    assert remaining == 0, "snapshots should not survive rollback"
