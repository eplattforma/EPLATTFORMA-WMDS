"""FIX-008: shared invoice → (route_name, stop_seq) lookup.

The legacy ``Invoice.routing`` field died when routing moved to the Routes
module, so anything that needs a stop number/route for an invoice must read
it from the ACTIVE ``route_stop_invoice`` link instead. This module is the
single source for that lookup so the batch reports, the Sequential picking
order and the pick-screen header all agree.
"""
from app import db
from models import RouteStop, RouteStopInvoice, Shipment


def route_links_for_invoices(invoice_nos):
    """Return ``{invoice_no: {'seq': float|None, 'route_name': str}}`` for
    every invoice with an ACTIVE route link on a live (not COMPLETED /
    CANCELLED) shipment. First active link wins. Invoices with no active
    link are absent from the result.
    """
    inv_nos = [n for n in (invoice_nos or []) if n]
    if not inv_nos:
        return {}
    rows = db.session.query(
        RouteStopInvoice.invoice_no, RouteStop.seq_no, Shipment.route_name,
    ).join(
        RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id
    ).join(
        Shipment, Shipment.id == RouteStop.shipment_id
    ).filter(
        RouteStopInvoice.invoice_no.in_(inv_nos),
        RouteStopInvoice.is_active.is_(True),
        RouteStop.deleted_at.is_(None),
        Shipment.deleted_at.is_(None),
        Shipment.status.notin_(['COMPLETED', 'CANCELLED']),
    ).order_by(
        RouteStopInvoice.invoice_no,
        RouteStop.seq_no.asc(),
        RouteStop.route_stop_id.asc(),
    ).all()
    out = {}
    for inv_no, seq, rname in rows:
        if inv_no in out:
            continue  # first active link wins
        try:
            fseq = float(seq) if seq is not None else None
        except (TypeError, ValueError):
            fseq = None
        out[inv_no] = {'seq': fseq, 'route_name': rname or ''}
    return out


def stop_label(entry):
    """Human label for a lookup entry: ``"PAFOS THU2 · STOP 9"``.

    Route name is included because one zone batch can span several routes —
    a bare stop number is ambiguous for the loaders.
    """
    if not entry:
        return None
    seq = entry.get('seq')
    rname = entry.get('route_name') or ''
    if seq is None:
        return rname or None
    n = int(seq) if float(seq).is_integer() else seq
    return f"{rname} · STOP {n}" if rname else f"STOP {n}"


def stop_sort_key(entry):
    """Sort key (route_name, seq) for grouping pages/invoices per route in
    stop order. ``None`` entries sort last via the caller's sentinel."""
    return (
        entry.get('route_name') or '',
        entry['seq'] if entry.get('seq') is not None else float('inf'),
    )


# Sentinel that sorts after every real (route_name, seq) tuple.
UNROUTED_SORT_KEY = ('\uffff', float('inf'))
