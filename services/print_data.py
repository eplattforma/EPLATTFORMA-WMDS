"""Shared context builder for delivery-slip / box-label printing.

Used by both the on-screen slip (print_invoice in routes.py) and the
print-bridge poll endpoint so both render from identical data.
"""
from models import (db, InvoiceItem, RouteStopInvoice, RouteStop, Shipment,
                    BatchPickedItem, BatchPickingSession, DwItem, Invoice)


def get_route_info(invoice_no):
    """Route / stop / driver context for an invoice (or None)."""
    rsi = RouteStopInvoice.query.filter_by(invoice_no=invoice_no).first()
    if not rsi:
        return None
    rs = RouteStop.query.get(rsi.route_stop_id)
    if not rs:
        return None
    sh = Shipment.query.get(rs.shipment_id)
    if not sh:
        return None
    return {
        'route_id': sh.id,
        'route_name': sh.route_name or f"Route #{sh.id}",
        'delivery_date': sh.delivery_date,
        'driver_name': sh.driver_name,
        'stop_seq': rs.seq_no,
        'stop_name': rs.stop_name,
        'stop_addr': rs.stop_addr,
        'stop_city': rs.stop_city,
        'stop_notes': rs.notes,
        'invoice_notes': rsi.notes,
        'status': sh.status,
    }


def get_stop_position(invoice):
    """(stop_index, stop_total) among invoices sharing the same stop, or (None, None)."""
    if not invoice.stop_id:
        return None, None
    rows = (Invoice.query
            .filter(Invoice.stop_id == invoice.stop_id)
            .order_by(Invoice.invoice_no)
            .with_entities(Invoice.invoice_no)
            .all())
    nos = [r[0] for r in rows]
    if invoice.invoice_no not in nos:
        return None, None
    return nos.index(invoice.invoice_no) + 1, len(nos)


def invoice_has_cooler_box(invoice_no):
    """True when the order has items packed in a cooler box."""
    try:
        row = db.session.execute(db.text(
            "SELECT 1 FROM cooler_box_items WHERE invoice_no = :inv LIMIT 1"
        ), {'inv': invoice_no}).fetchone()
        return row is not None
    except Exception:
        db.session.rollback()
        return False  # table absent on legacy DBs


def build_slip_items(invoice, all_items=None):
    """Flat delivery-slip item list: one row per invoice line.

    Picked qty comes from InvoiceItem.picked_qty (line-level source of
    truth); summed batch records are only a fallback when picked_qty was
    never set for a batch-picked line.
    """
    if all_items is None:
        all_items = InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no).all()
        try:
            from routes import sort_items_by_config
            all_items = sort_items_by_config(all_items)
        except Exception:
            pass

    batch_records = BatchPickedItem.query.filter_by(invoice_no=invoice.invoice_no).all()
    if not invoice.route_id and batch_records:
        cooler_sids = {s.id for s in BatchPickingSession.query.filter_by(session_type='cooler_route').all()}
        batch_records = [r for r in batch_records if r.batch_session_id not in cooler_sids]
    batch_qty_by_code = {}
    for r in batch_records:
        batch_qty_by_code[r.item_code] = batch_qty_by_code.get(r.item_code, 0) + (r.picked_qty or 0)

    codes = [it.item_code for it in all_items]
    chilled_codes = set()
    if codes:
        chilled_codes = {
            d.item_code_365 for d in DwItem.query.filter(
                DwItem.item_code_365.in_(codes),
                DwItem.wms_temperature_sensitivity == 'cool_required'
            ).all()
        }

    slip_items = []
    for it in all_items:
        qty_req = int(it.expected_pick_pieces or it.qty or 0)
        if it.picked_qty is not None:
            qty_pic = int(it.picked_qty)
        elif it.item_code in batch_qty_by_code:
            qty_pic = int(batch_qty_by_code[it.item_code])
        else:
            qty_pic = 0
        if it.unit_type and it.unit_type.lower() in ['virtual pack', 'item']:
            unit_label = it.unit_type
        else:
            unit_label = f"{it.unit_type}({it.pack})" if it.pack else (it.unit_type or '')
        slip_items.append({
            'item_code': it.item_code,
            'item_name': it.item_name,
            'location': it.location if it.location and it.location != 'None' else '',
            'unit_label': unit_label,
            'qty': qty_req,
            'qty_picked': qty_pic,
            'is_chilled': it.item_code in chilled_codes,
        })
    return slip_items


def get_slip_context(invoice):
    """Everything both PDF generators and the HTML slip need."""
    route_info = get_route_info(invoice.invoice_no)
    stop_index, stop_total = get_stop_position(invoice)
    return {
        'route_info': route_info,
        'slip_items': build_slip_items(invoice),
        'stop_index': stop_index,
        'stop_total': stop_total,
        'has_cooler': invoice_has_cooler_box(invoice.invoice_no),
    }
