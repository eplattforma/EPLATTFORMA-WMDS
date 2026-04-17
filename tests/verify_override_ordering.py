"""
Verification script for Task #6: Verify ordering accuracy with override
scenarios on real data.

Applies overrides to representative real SKUs, runs the ordering refresh
just for those items, and checks that the resulting SkuOrderingSnapshot
records reflect the override correctly across:

  * a normal smooth/MA8 SKU
  * a new_true/SEEDED_NEW SKU
  * a SKU with an MOQ from ps_items_dw
  * an override quantity of 0 (suppression)

The script does NOT modify any pre-existing overrides; it cleans up the
overrides it creates and the snapshots it produces.
"""
import math
import sys
from decimal import Decimal

from app import app, db
from models import (
    SkuForecastOverride,
    SkuForecastResult,
    SkuForecastProfile,
    SkuOrderingSnapshot,
    DwItem,
    Setting,
)
from services.forecast.ordering_refresh_service import refresh_ordering_snapshot


TEST_USER = "task6_verify"

SCENARIOS = [
    # (item_code, override_qty, label)
    ("SNA-0105", Decimal("10.000000"), "smooth_ma8"),
    ("CLE-0188", Decimal("8.000000"), "new_true_seeded"),
    ("COF-0053", Decimal("15.000000"), "with_moq_600"),
    ("TAC-0001", Decimal("0.000000"), "override_zero"),
]


def fnum(v):
    return float(v) if v is not None else 0.0


def expect(cond, msg, errors):
    if cond:
        print(f"  OK  {msg}")
    else:
        print(f"  FAIL {msg}")
        errors.append(msg)


def _cleanup(session, override_ids, snapshot_ids):
    try:
        if snapshot_ids:
            session.query(SkuOrderingSnapshot).filter(
                SkuOrderingSnapshot.id.in_(snapshot_ids)
            ).delete(synchronize_session=False)
        if override_ids:
            session.query(SkuForecastOverride).filter(
                SkuForecastOverride.id.in_(override_ids)
            ).delete(synchronize_session=False)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"WARNING: cleanup failed ({e}); attempting fallback by created_by={TEST_USER}")
        try:
            session.query(SkuOrderingSnapshot).filter_by(created_by=TEST_USER).delete(
                synchronize_session=False
            )
            session.query(SkuForecastOverride).filter_by(created_by=TEST_USER).delete(
                synchronize_session=False
            )
            session.commit()
        except Exception as e2:
            session.rollback()
            print(f"ERROR: fallback cleanup also failed: {e2}")


def main():
    errors = []
    created_override_ids = []
    created_snapshot_ids = []

    with app.app_context():
        session = db.session

        buffer_days = float(Setting.get(session, "forecast_buffer_stock_days", "1"))
        review_cycle_default = float(Setting.get(session, "forecast_review_cycle_days", "1"))

        item_codes = [c for c, _, _ in SCENARIOS]

        pre_state = {}
        for code in item_codes:
            r = session.query(SkuForecastResult).filter_by(item_code_365=code).first()
            p = session.query(SkuForecastProfile).filter_by(item_code_365=code).first()
            d = session.query(DwItem).filter_by(item_code_365=code).first()
            assert r is not None, f"missing forecast result for {code}"
            assert d is not None, f"missing dw_item for {code}"
            pre_state[code] = {
                "system_weekly": fnum(r.final_forecast_weekly_qty),
                "system_daily": fnum(r.final_forecast_daily_qty),
                "target_weeks": fnum(p.target_weeks_of_stock) if p and p.target_weeks_of_stock is not None else 4.0,
                "moq_dw": fnum(d.min_order_qty),
                "supplier_code": d.supplier_code_365,
            }
        print("Pre-state:")
        for k, v in pre_state.items():
            print(f"  {k}: {v}")

        # Make sure none of these have an active override hanging around
        existing = session.query(SkuForecastOverride).filter(
            SkuForecastOverride.item_code_365.in_(item_codes),
            SkuForecastOverride.is_active == True,
        ).all()
        if existing:
            print(f"WARNING: pre-existing overrides on test SKUs ({[o.item_code_365 for o in existing]});"
                  " test would be invalid. Aborting without changes.")
            return 2

        try:
            # Create test overrides
            for code, qty, label in SCENARIOS:
                ov = SkuForecastOverride(
                    item_code_365=code,
                    override_weekly_qty=qty,
                    reason_code="test_task6",
                    reason_note=f"task6 verify scenario={label}",
                    created_by=TEST_USER,
                    is_active=True,
                )
                session.add(ov)
                session.flush()
                created_override_ids.append(ov.id)
            session.commit()
            print(f"Created {len(created_override_ids)} test overrides")

            # Run refresh just for these item codes
            result = refresh_ordering_snapshot(
                session,
                item_codes=item_codes,
                created_by=TEST_USER,
            )
            session.commit()
            print(f"Refresh result: {result}")

            expect(result["snapshot_count"] == len(item_codes),
                   f"refresh produced {result['snapshot_count']} snapshots (expected {len(item_codes)})",
                   errors)
            expect(result["override_count"] == len(item_codes),
                   f"override_count={result['override_count']} (expected {len(item_codes)})",
                   errors)

            # Verify each snapshot
            print("\nPer-SKU verification:")
            for code, override_qty, label in SCENARIOS:
                snap = (
                    session.query(SkuOrderingSnapshot)
                    .filter_by(item_code_365=code, created_by=TEST_USER)
                    .order_by(SkuOrderingSnapshot.snapshot_at.desc(), SkuOrderingSnapshot.id.desc())
                    .first()
                )
                assert snap is not None, f"no snapshot for {code}"
                created_snapshot_ids.append(snap.id)
                ps = pre_state[code]
                ovr = float(override_qty)
                print(f"\n[{code}] scenario={label} override={ovr}")
                print(f"  system={ps['system_weekly']:.4f}  target_weeks={ps['target_weeks']}"
                      f"  moq_dw={ps['moq_dw']}  supplier={ps['supplier_code']}")
                print(f"  snap.system={float(snap.system_forecast_weekly_qty):.4f}"
                      f"  snap.override={float(snap.override_forecast_weekly_qty) if snap.override_forecast_weekly_qty is not None else None}"
                      f"  snap.final={float(snap.final_forecast_weekly_qty):.4f}"
                      f"  src={snap.final_forecast_source}")
                print(f"  on_hand={float(snap.on_hand_qty)}  net_avail={float(snap.net_available_qty)}"
                      f"  target_stock={float(snap.target_stock_qty):.4f}"
                      f"  raw={float(snap.raw_recommended_order_qty):.4f}"
                      f"  rounded={float(snap.rounded_order_qty):.4f}"
                      f"  moq_snap={float(snap.min_order_qty) if snap.min_order_qty is not None else None}")

                # Source + override fields recorded correctly
                expect(snap.final_forecast_source == "override",
                       "final_forecast_source == 'override'", errors)
                expect(abs(float(snap.system_forecast_weekly_qty) - ps["system_weekly"]) < 1e-4,
                       "system_forecast_weekly_qty matches SkuForecastResult", errors)
                expect(snap.override_forecast_weekly_qty is not None
                       and abs(float(snap.override_forecast_weekly_qty) - ovr) < 1e-4,
                       "override_forecast_weekly_qty matches override input", errors)
                expect(abs(float(snap.final_forecast_weekly_qty) - ovr) < 1e-4,
                       "final_forecast_weekly_qty == override (NOT system)", errors)
                expect(abs(float(snap.final_forecast_daily_qty) - ovr / 7.0) < 1e-4,
                       "final_forecast_daily_qty == override/7", errors)

                # Recompute target_stock from override
                eff_w = ovr
                eff_d = ovr / 7.0
                target_weeks = ps["target_weeks"]
                lt = float(snap.lead_time_days)
                rc = float(snap.review_cycle_days)
                bd = float(snap.buffer_days)
                expected_target = (eff_w * target_weeks) + eff_d * lt + eff_d * rc + eff_d * bd
                expect(abs(float(snap.target_stock_qty) - expected_target) < 1e-3,
                       f"target_stock_qty matches override math ({expected_target:.4f})", errors)

                # raw_order = max(0, target_stock - net_avail)
                expected_raw = max(0.0, expected_target - float(snap.net_available_qty))
                expect(abs(float(snap.raw_recommended_order_qty) - expected_raw) < 1e-3,
                       f"raw_recommended_order_qty matches expected ({expected_raw:.4f})", errors)

                # Rounding
                raw = float(snap.raw_recommended_order_qty)
                moq_snap = float(snap.min_order_qty) if snap.min_order_qty is not None else 0.0
                mult = float(snap.order_multiple) if snap.order_multiple is not None else 0.0
                if raw <= 0:
                    expected_rounded = 0.0
                else:
                    if mult > 0:
                        expected_rounded = math.ceil(raw / mult) * mult
                    else:
                        expected_rounded = math.ceil(raw)
                    if moq_snap > 0:
                        expected_rounded = max(expected_rounded, moq_snap)
                expect(abs(float(snap.rounded_order_qty) - expected_rounded) < 1e-3,
                       f"rounded_order_qty matches rounding pipeline ({expected_rounded})", errors)

                # Scenario-specific extra checks
                if label == "override_zero":
                    expect(float(snap.final_forecast_weekly_qty) == 0.0
                           and float(snap.target_stock_qty) == 0.0
                           and float(snap.raw_recommended_order_qty) == 0.0
                           and float(snap.rounded_order_qty) == 0.0,
                           "override=0 fully suppresses target stock and order qty", errors)
                if label == "with_moq_600":
                    expect(moq_snap == ps["moq_dw"],
                           f"snapshot picks up MOQ from ps_items_dw ({ps['moq_dw']})", errors)
                    expect(float(snap.rounded_order_qty) >= ps["moq_dw"]
                           or float(snap.raw_recommended_order_qty) <= 0,
                           "rounded_order_qty respects MOQ floor", errors)
                if label == "new_true_seeded":
                    expect(snap.final_forecast_source == "override",
                           "override beats SEEDED_NEW system forecast", errors)

                # explanation_json sanity
                ex = snap.explanation_json or {}
                expect(ex.get("forecast_source") == "override",
                       "explanation_json.forecast_source == 'override'", errors)
                expect(abs(float(ex.get("override_weekly_qty", -1)) - ovr) < 1e-4,
                       "explanation_json.override_weekly_qty matches", errors)
                expect(ex.get("override_reason_code") == "test_task6",
                       "explanation_json.override_reason_code recorded", errors)
        finally:
            # Always clean up to avoid leaving overrides that could affect live ordering
            print("\nCleaning up test artifacts...")
            _cleanup(session, created_override_ids, created_snapshot_ids)
            print(f"Removed {len(created_snapshot_ids)} test snapshots,"
                  f" {len(created_override_ids)} test overrides")

    print("\n=== SUMMARY ===")
    if errors:
        print(f"FAILED: {len(errors)} assertions failed")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
