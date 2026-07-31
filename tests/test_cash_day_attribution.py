"""
Cash-day attribution for route settlement/reconciliation.

Rule: a non-VOIDED COD receipt collected (local Cyprus date) BEFORE its own
route's delivery date, when the same driver has another route dated the
collection day, is counted on that collection-day route's cash totals
("incoming") and excluded from its own route's totals ("outgoing").

Covers:
  - early receipt moves from the later route to the collection-day route
  - conservation: each live receipt counted exactly once across both routes
  - VOIDED receipts excluded everywhere
  - no same-driver route on collection day -> receipt stays on its own route
  - different driver's route on the collection day does not attract the receipt
  - local-date conversion (late-evening UTC timestamp rolls into next Cyprus day)
"""

from datetime import date, datetime
from decimal import Decimal

import pytest


def _mk_route(db, driver, day, name='R'):
    from models import Shipment
    s = Shipment(driver_name=driver, route_name=name, delivery_date=day,
                 status='COMPLETED')
    db.session.add(s)
    db.session.flush()
    return s


def _mk_stop(db, route, seq=1):
    from models import RouteStop
    rs = RouteStop(shipment_id=route.id, seq_no=seq, stop_name=f'STOP{seq}')
    db.session.add(rs)
    db.session.flush()
    return rs


def _mk_receipt(db, route, stop, amount, created_at, status='ISSUED'):
    from models import CODReceipt
    r = CODReceipt(route_id=route.id, route_stop_id=stop.route_stop_id,
                   driver_username='test_driver_user',
                   invoice_nos=['IN1'],
                   expected_amount=Decimal(str(amount)),
                   received_amount=Decimal(str(amount)),
                   payment_method='cash', status=status)
    db.session.add(r)
    db.session.flush()
    # bypass default: set collection timestamp explicitly (naive UTC)
    r.created_at = created_at
    db.session.flush()
    return r


@pytest.fixture()
def ctx(app):
    from app import db
    with app.app_context():
        yield db


def test_early_receipt_moves_to_collection_day_route(ctx):
    db = ctx
    import services_reconciliation as recon
    day1, day2 = date(2026, 7, 29), date(2026, 7, 30)
    r_day1 = _mk_route(db, 'Ricardo', day1)
    r_day2 = _mk_route(db, 'Ricardo', day2)
    s1 = _mk_stop(db, r_day1)
    s2 = _mk_stop(db, r_day2)

    normal1 = _mk_receipt(db, r_day1, s1, 100, datetime(2026, 7, 29, 9, 0))
    early = _mk_receipt(db, r_day2, s2, 228.19, datetime(2026, 7, 29, 12, 19))
    normal2 = _mk_receipt(db, r_day2, s2, 50, datetime(2026, 7, 30, 9, 0))
    voided = _mk_receipt(db, r_day2, s2, 999, datetime(2026, 7, 29, 13, 0),
                         status='VOIDED')

    res1 = recon.get_settlement_receipts(r_day1.id)
    res2 = recon.get_settlement_receipts(r_day2.id)

    ids1 = {r.id for r in res1['counted']}
    ids2 = {r.id for r in res2['counted']}

    # early receipt counted on day-1 route, excluded from day-2 route
    assert early.id in ids1 and early.id not in ids2
    assert [r.id for r in res1['incoming']] == [early.id]
    assert [r.id for r in res2['outgoing']] == [early.id]
    # normals stay put; VOIDED nowhere; conservation (counted exactly once)
    assert normal1.id in ids1 and normal2.id in ids2
    assert voided.id not in ids1 | ids2
    assert ids1 & ids2 == set()
    assert ids1 | ids2 == {normal1.id, early.id, normal2.id}

    # cash totals reflect the move
    assert recon.get_cash_totals(r_day1.id)['cash_collected'] == Decimal('328.19')
    assert recon.get_cash_totals(r_day2.id)['cash_collected'] == Decimal('50')


def test_no_same_driver_route_on_collection_day_keeps_receipt(ctx):
    db = ctx
    import services_reconciliation as recon
    r_day2 = _mk_route(db, 'Ricardo', date(2026, 7, 30))
    s2 = _mk_stop(db, r_day2)
    # other driver's route on the collection day must not attract it
    _mk_route(db, 'Maria', date(2026, 7, 29))
    early = _mk_receipt(db, r_day2, s2, 75, datetime(2026, 7, 29, 12, 0))

    res2 = recon.get_settlement_receipts(r_day2.id)
    assert [r.id for r in res2['counted']] == [early.id]
    assert res2['outgoing'] == []


def test_local_date_conversion_late_evening_utc(ctx):
    import services_reconciliation as recon
    # 22:30 UTC on the 29th = 01:30 Cyprus time on the 30th (EEST, UTC+3)
    assert recon.receipt_collection_date(datetime(2026, 7, 29, 22, 30)) == date(2026, 7, 30)
    assert recon.receipt_collection_date(datetime(2026, 7, 29, 12, 19)) == date(2026, 7, 29)


def test_late_evening_receipt_not_flagged_early(ctx):
    db = ctx
    import services_reconciliation as recon
    r_day1 = _mk_route(db, 'Ricardo', date(2026, 7, 29))
    r_day2 = _mk_route(db, 'Ricardo', date(2026, 7, 30))
    s2 = _mk_stop(db, r_day2)
    # collected 22:30 UTC on the 29th -> Cyprus date is the 30th -> not early
    r = _mk_receipt(db, r_day2, s2, 60, datetime(2026, 7, 29, 22, 30))
    res2 = recon.get_settlement_receipts(r_day2.id)
    assert [x.id for x in res2['counted']] == [r.id]
    assert res2['outgoing'] == []
    assert recon.get_settlement_receipts(r_day1.id)['incoming'] == []
