"""Print bridge: queue endpoints + office-PC agent poll.

Picker pack screens enqueue delivery slips for the Konica, while the
manager Picking Dashboard enqueues box labels for the Deli 750W. A small
agent on the office PC polls /print/agent/poll with the
PRINT_AGENT_TOKEN secret, receives a base64 PDF plus doc_type, sends it
to the matching printer, then acks the job.
"""
import base64
import hmac
import logging
import os

from flask import Blueprint, jsonify, request, session
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

printing_bp = Blueprint('printing', __name__)

_DOC_TYPES = {'delivery-slip': 'slip', 'box-label': 'label'}


def _agent_authorized():
    # Header only — never accept the token in a query string (proxy logs).
    token = os.environ.get('PRINT_AGENT_TOKEN')
    supplied = request.headers.get('X-Print-Agent-Token') or ''
    return bool(token) and hmac.compare_digest(supplied, token)


@printing_bp.route('/print/<kind>/<invoice_no>', methods=['POST'])
@login_required
def enqueue_print(kind, invoice_no):
    from models import db, Invoice
    doc_type = _DOC_TYPES.get(kind)
    if not doc_type:
        return jsonify({'ok': False, 'error': 'Unknown document type'}), 404
    # CSRF: same session-token scheme the rest of the app uses.
    supplied = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
    session_token = session.get('csrf_token')
    if not supplied or not session_token or not hmac.compare_digest(supplied, session_token):
        return jsonify({'ok': False, 'error': 'Invalid CSRF token'}), 400
    if current_user.role not in ('picker', 'warehouse_manager', 'admin'):
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    if doc_type == 'label' and current_user.role == 'picker':
        return jsonify({'ok': False, 'error': 'Box labels print from the Picking Dashboard'}), 403
    invoice = Invoice.query.get(invoice_no)
    if not invoice:
        return jsonify({'ok': False, 'error': 'Invoice not found'}), 404
    if current_user.role == 'picker' and invoice.assigned_to != current_user.username:
        return jsonify({'ok': False, 'error': 'Not your invoice'}), 403

    if doc_type == 'label':
        # Avoid stacking duplicate labels while the office agent has not yet
        # finished the first one. The agent calls this state "printing".
        existing = db.session.execute(db.text("""
            SELECT id FROM print_jobs
            WHERE invoice_no = :inv
              AND doc_type = 'label'
              AND status IN ('queued', 'printing')
            ORDER BY id DESC
            LIMIT 1
        """), {'inv': invoice_no}).fetchone()
        if existing:
            return jsonify({
                'ok': True,
                'job_id': existing[0],
                'doc_type': doc_type,
                'duplicate': True,
            })

    row = db.session.execute(db.text("""
        INSERT INTO print_jobs (invoice_no, doc_type, status, requested_by)
        VALUES (:inv, :doc, 'queued', :usr) RETURNING id
    """), {'inv': invoice_no, 'doc': doc_type, 'usr': current_user.username}).fetchone()
    db.session.commit()
    return jsonify({'ok': True, 'job_id': row[0], 'doc_type': doc_type})


@printing_bp.route('/print/job/<int:job_id>/status')
@login_required
def print_job_status(job_id):
    """Return a print job's state to the user who is allowed to print it."""
    from models import db, Invoice

    if current_user.role not in ('picker', 'warehouse_manager', 'admin'):
        return jsonify({'error': 'Access denied'}), 403

    row = db.session.execute(db.text("""
        SELECT invoice_no, status
        FROM print_jobs
        WHERE id = :job_id
    """), {'job_id': job_id}).fetchone()
    if not row:
        return jsonify({'error': 'Print job not found'}), 404

    if current_user.role == 'picker':
        invoice = Invoice.query.get(row.invoice_no)
        if not invoice or invoice.assigned_to != current_user.username:
            return jsonify({'error': 'Not your invoice'}), 403

    return jsonify({'status': row.status})


@printing_bp.route('/print/agent/poll', methods=['GET', 'POST'])
def agent_poll():
    from models import db, Invoice
    if not _agent_authorized():
        return jsonify({'error': 'unauthorized'}), 401

    # Requeue jobs stuck in 'printing' (agent died mid-print) after a 5-minute
    # lease, up to 3 attempts; beyond that mark them failed.
    db.session.execute(db.text("""
        UPDATE print_jobs SET status = 'queued', attempts = attempts + 1
        WHERE status = 'printing' AND claimed_at < now() - interval '5 minutes'
          AND attempts < 3
    """))
    db.session.execute(db.text("""
        UPDATE print_jobs SET status = 'error', error = 'print agent never acknowledged', done_at = now()
        WHERE status = 'printing' AND claimed_at < now() - interval '5 minutes'
          AND attempts >= 3
    """))
    db.session.commit()

    # Claim the oldest queued job atomically so two agents never double-print.
    row = db.session.execute(db.text("""
        UPDATE print_jobs SET status = 'printing', claimed_at = now()
        WHERE id = (
            SELECT id FROM print_jobs WHERE status = 'queued'
            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
        )
        RETURNING id, invoice_no, doc_type
    """)).fetchone()
    db.session.commit()
    if not row:
        return jsonify({'job': None})

    job_id, invoice_no, doc_type = row
    try:
        invoice = Invoice.query.get(invoice_no)
        if not invoice:
            raise ValueError(f'Invoice {invoice_no} not found')
        from services.print_data import get_slip_context
        ctx = get_slip_context(invoice)
        if doc_type == 'label':
            from services.label_pdf import build_box_label_pdf
            ri = ctx['route_info'] or {}
            stop_seq = ri.get('stop_seq')
            if stop_seq is not None and stop_seq == int(stop_seq):
                stop_seq = int(stop_seq)
            pdf = build_box_label_pdf(
                invoice, stop_number=stop_seq,
                route_name=ri.get('route_name'), driver_name=ri.get('driver_name'),
                delivery_date=ri.get('delivery_date'),
                stop_index=ctx['stop_index'], stop_total=ctx['stop_total'],
                has_cooler=ctx['has_cooler'])
        else:
            from services.slip_pdf import build_delivery_slip_pdf
            pdf = build_delivery_slip_pdf(
                invoice, ctx['slip_items'], route_info=ctx['route_info'],
                stop_index=ctx['stop_index'], stop_total=ctx['stop_total'],
                has_cooler=ctx['has_cooler'])
    except Exception as e:
        logger.exception('print job %s failed to build', job_id)
        db.session.execute(db.text(
            "UPDATE print_jobs SET status='error', error=:e, done_at=now() WHERE id=:i"
        ), {'e': str(e)[:500], 'i': job_id})
        db.session.commit()
        return jsonify({'job': None, 'skipped_error': job_id})

    return jsonify({'job': {
        'job_id': job_id,
        'invoice_no': invoice_no,
        'doc_type': doc_type,
        'pdf_base64': base64.b64encode(pdf).decode(),
    }})


@printing_bp.route('/print/agent/ack', methods=['POST'])
def agent_ack():
    from models import db
    if not _agent_authorized():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    ok = data.get('ok', True)
    if not job_id:
        return jsonify({'error': 'job_id required'}), 400
    res = db.session.execute(db.text("""
        UPDATE print_jobs SET status = :st, error = :err, done_at = now()
        WHERE id = :i AND status = 'printing'
    """), {'st': 'done' if ok else 'error',
           'err': None if ok else (data.get('error') or 'agent error')[:500],
           'i': job_id})
    db.session.commit()
    if res.rowcount == 0:
        return jsonify({'ok': False, 'error': 'job not in printing state'}), 409
    return jsonify({'ok': True})
