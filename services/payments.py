import logging
import re
from datetime import date, datetime
from decimal import Decimal

from app import db
from models import PaymentEntry

logger = logging.getLogger(__name__)

_TIMEOUT_PATTERNS = [
    'timed out', 'timeout', 'connecttimeouterror',
    'max retries exceeded', 'connectionerror',
    'connection refused', 'connection reset',
]


def _is_timeout_error(exc):
    msg = str(exc).lower()
    return any(p in msg for p in _TIMEOUT_PATTERNS)


def _friendly_error(exc):
    if _is_timeout_error(exc):
        return "PS365 server temporarily unavailable. Tap Retry shortly."
    msg = str(exc)
    if len(msg) > 120:
        match = re.search(r"(Caused by \w+Error\([^)]*\))", msg)
        if match:
            return match.group(1)[:120]
        return msg[:120] + '…'
    return msg


def decide_commit_and_doc(method, cheque_date_val=None):
    today = date.today()
    is_future_cheque = cheque_date_val and cheque_date_val > today

    if method == 'cash':
        return 'COMMIT', 'official', 'NEW'
    elif method == 'card':
        return 'COMMIT', 'official', 'NEW'
    elif method == 'cheque':
        if is_future_cheque:
            return 'SKIP', 'pdc_ack', 'SKIPPED'
        else:
            return 'COMMIT', 'official', 'NEW'
    elif method == 'online':
        return 'SKIP', 'online_notice', 'SKIPPED'
    else:
        return 'COMMIT', 'official', 'NEW'


def upsert_active_payment(route_stop_id, payload):
    method = payload['method']
    amount = Decimal(str(payload.get('amount', 0)))
    cheque_no = payload.get('cheque_no') or None
    cheque_date_str = payload.get('cheque_date') or None
    cheque_date_val = None
    if cheque_date_str:
        cheque_date_val = datetime.strptime(cheque_date_str, '%Y-%m-%d').date()

    commit_mode, doc_type, initial_status = decide_commit_and_doc(method, cheque_date_val)

    old = PaymentEntry.query.filter_by(route_stop_id=route_stop_id, is_active=True).first()
    if old:
        old.is_active = False
        old.updated_at = datetime.utcnow()
        db.session.flush()

    pe = PaymentEntry(
        route_stop_id=route_stop_id,
        method=method,
        amount=amount,
        cheque_no=cheque_no,
        cheque_date=cheque_date_val,
        commit_mode=commit_mode,
        doc_type=doc_type,
        ps_status=initial_status,
        is_active=True,
    )
    db.session.add(pe)
    db.session.flush()
    return pe


MAX_RETRY_ATTEMPTS = 10


def _check_existing_receipt(route_stop_id):
    from models import ReceiptLog
    return ReceiptLog.query.filter_by(route_stop_id=route_stop_id).first()


def commit_to_ps365(pe, customer_code, invoice_nos, driver_username):
    if pe.commit_mode == 'SKIP':
        pe.ps_status = 'SKIPPED'
        pe.updated_at = datetime.utcnow()
        db.session.flush()
        return pe

    if pe.ps_status == 'SUCCESS':
        return pe

    if pe.attempt_count >= MAX_RETRY_ATTEMPTS:
        pe.ps_status = 'FAILED'
        pe.ps_error = f'Gave up after {pe.attempt_count} attempts. Contact admin.'
        pe.updated_at = datetime.utcnow()
        db.session.flush()
        logger.error(f"PaymentEntry {pe.id} exceeded max retries ({pe.attempt_count}), marking FAILED")
        return pe

    is_retry = pe.attempt_count > 0

    if is_retry:
        existing = _check_existing_receipt(pe.route_stop_id)
        if existing:
            pe.ps_status = 'SUCCESS'
            pe.ps_reference = existing.reference_number
            pe.ps_error = None
            pe.updated_at = datetime.utcnow()
            db.session.flush()
            logger.info(f"PaymentEntry {pe.id} matched existing receipt ref={existing.reference_number}, marking SUCCESS")
            return pe

    pe.attempt_count += 1
    pe.last_attempt_at = datetime.utcnow()

    try:
        from routes_receipts import create_receipt_core

        comments = f"Driver payment {pe.method}"
        inv_str = ",".join(invoice_nos) if invoice_nos else ""

        ok, ref_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code=customer_code,
            amount_val=float(pe.amount),
            comments=comments,
            user_code=driver_username,
            invoice_no=inv_str,
            driver_username=driver_username,
            route_stop_id=pe.route_stop_id,
            cheque_number=pe.cheque_no or "",
            cheque_date=pe.cheque_date.strftime('%Y-%m-%d') if pe.cheque_date else "",
            allow_duplicate_stop=is_retry,
        )

        pe.ps_status = 'SUCCESS'
        pe.ps_reference = ref_number
        pe.ps_error = None
        pe.updated_at = datetime.utcnow()
        db.session.flush()
        logger.info(f"PS365 commit SUCCESS for PaymentEntry {pe.id}, ref={pe.ps_reference}")
        return pe

    except Exception as exc:
        is_temp = _is_timeout_error(exc)
        pe.ps_status = 'PENDING_RETRY' if is_temp else 'FAILED'
        pe.ps_error = _friendly_error(exc)
        pe.updated_at = datetime.utcnow()
        db.session.flush()
        if is_temp:
            logger.warning(f"PS365 timeout for PaymentEntry {pe.id} — marked PENDING_RETRY (attempt {pe.attempt_count})")
        else:
            logger.warning(f"PS365 commit FAILED for PaymentEntry {pe.id}: {exc}")
        return pe


def get_active_payment(route_stop_id):
    return PaymentEntry.query.filter_by(
        route_stop_id=route_stop_id,
        is_active=True,
    ).first()
