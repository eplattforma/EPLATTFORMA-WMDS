"""Virtual pack (VPACK) handling in batch picking.

Batch picking must instruct in PIECES like normal picking: a line of
4 virtual packs of 3 pieces means "Pick 12 Pieces", not
"Pick 4 VIRTUAL PACK (3)".
"""


def _mk_dw_vpack(db, item_code, pieces):
    from models import DwItem
    from timezone_utils import get_utc_now
    db.session.add(DwItem(
        item_code_365=item_code,
        item_name='VPACK ITEM',
        active=True,
        attr_hash='x',
        last_sync_at=get_utc_now(),
        attribute_1_code_365='VPACK',
        number_of_pieces=pieces,
    ))
    db.session.flush()


def _mk_invoice(db, invoice_no):
    from models import Invoice
    db.session.add(Invoice(
        invoice_no=invoice_no, customer_name="C", customer_code="CC",
        status="Not Started", upload_date="2026-07-08",
    ))
    db.session.flush()


def _mk_item(db, invoice_no, item_code, qty, unit_type="VIRTUAL PACK", pack="3"):
    from models import InvoiceItem
    ii = InvoiceItem(
        invoice_no=invoice_no, item_code=item_code, qty=qty,
        unit_type=unit_type, pack=pack, zone="A1", location="90-02-B 01",
        pick_status="not_picked", is_picked=False,
    )
    db.session.add(ii)
    db.session.flush()
    return ii


def test_pieces_required_for_source_multiplies_vpack(app):
    from app import db
    from services.batch_picking import pieces_required_for_source

    with app.app_context():
        _mk_dw_vpack(db, "SNA-0095", 3)
        _mk_invoice(db, "IN-VP-1")
        _mk_item(db, "IN-VP-1", "SNA-0095", qty=4)

        assert pieces_required_for_source("IN-VP-1", "SNA-0095", 4) == 12


def test_pieces_required_for_source_non_vpack_unchanged(app):
    from app import db
    from services.batch_picking import pieces_required_for_source

    with app.app_context():
        _mk_invoice(db, "IN-VP-2")
        _mk_item(db, "IN-VP-2", "PLAIN-1", qty=5, unit_type="ITEM", pack=None)

        assert pieces_required_for_source("IN-VP-2", "PLAIN-1", 5) == 5


def test_pieces_required_missing_line_falls_back(app):
    from services.batch_picking import pieces_required_for_source

    with app.app_context():
        assert pieces_required_for_source("NO-SUCH", "NOPE", 7) == 7


def test_apply_vpack_display_sets_pieces(app):
    from app import db
    from services.batch_picking import apply_vpack_display

    with app.app_context():
        _mk_dw_vpack(db, "SNA-0096", 3)
        _mk_invoice(db, "IN-VP-3")
        _mk_item(db, "IN-VP-3", "SNA-0096", qty=4)

        item = {
            'item_code': "SNA-0096",
            'unit_type': "VIRTUAL PACK",
            'total_qty': 4,
            'source_items': [
                {'invoice_no': "IN-VP-3", 'item_code': "SNA-0096", 'qty': 4},
            ],
        }
        apply_vpack_display(item)
        assert item['display_qty'] == 12
        assert item['display_unit_type'] == 'Pieces'


def test_apply_vpack_display_consolidated_sums_sources(app):
    from app import db
    from services.batch_picking import apply_vpack_display

    with app.app_context():
        _mk_dw_vpack(db, "SNA-0097", 4)
        _mk_invoice(db, "IN-VP-4")
        _mk_invoice(db, "IN-VP-5")
        _mk_item(db, "IN-VP-4", "SNA-0097", qty=2)
        _mk_item(db, "IN-VP-5", "SNA-0097", qty=1)

        item = {
            'item_code': "SNA-0097",
            'unit_type': "VIRTUAL PACK",
            'total_qty': 3,
            'source_items': [
                {'invoice_no': "IN-VP-4", 'item_code': "SNA-0097", 'qty': 2},
                {'invoice_no': "IN-VP-5", 'item_code': "SNA-0097", 'qty': 1},
            ],
        }
        apply_vpack_display(item)
        assert item['display_qty'] == 12  # (2 + 1) packs x 4 pieces
        assert item['display_unit_type'] == 'Pieces'


def test_apply_vpack_display_non_vpack_keeps_qty(app):
    from app import db
    from services.batch_picking import apply_vpack_display

    with app.app_context():
        _mk_invoice(db, "IN-VP-6")
        _mk_item(db, "IN-VP-6", "PLAIN-2", qty=5, unit_type="ITEM", pack=None)

        item = {
            'item_code': "PLAIN-2",
            'unit_type': "ITEM",
            'total_qty': 5,
            'source_items': [
                {'invoice_no': "IN-VP-6", 'item_code': "PLAIN-2", 'qty': 5},
            ],
        }
        apply_vpack_display(item)
        assert item['display_qty'] == 5
        assert item['display_unit_type'] == 'ITEM'
