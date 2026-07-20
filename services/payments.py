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
        variance_reason=(payload.get('variance_reason') or None),
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
        exc_str = str(exc).lower()
        # If PS365 says this reference already exists, the payment went through on a
        # prior attempt and the confirmation was lost. Mark as SUCCESS so the driver
        # can proceed — the money is already in PS365.
        if "already exists" in exc_str and "reference_number" in exc_str:
            logger.warning(
                f"[Payments] PS365 reports reference already exists for PaymentEntry {pe.id} — "
                f"treating as SUCCESS (prior attempt confirmation lost). Error: {exc}"
            )
            pe.ps_status = 'SUCCESS'
            pe.ps_error = None
            pe.updated_at = datetime.utcnow()
            if not pe.ps_reference:
                pe.ps_reference = str(exc).split("reference_number")[-1][:20].strip(" :'\"") or None
            db.session.flush()
            return pe

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


def sync_receipt_ps365_at_print(cod_receipt, stop, user_code):
    """
    Deferred PS365 commit — called at print time (the natural lock point).

    Prefers the PaymentEntry path so the existing retry machinery
    (PENDING_RETRY / auto-retry / Retry button) keeps working; falls back to
    a direct create_receipt_core call for legacy receipts without a
    PaymentEntry. Copies the PS365 reference onto the CODReceipt on success.

    Never raises — a PS365 failure must not block printing; the retry
    machinery resolves it afterwards. Callers must db.session.commit().
    """
    if cod_receipt.status == 'VOIDED':
        return
    if (cod_receipt.doc_type or 'official').lower() != 'official':
        return
    if cod_receipt.ps365_reference_number:
        return

    try:
        pe = get_active_payment(cod_receipt.route_stop_id)

        if pe and pe.commit_mode == 'COMMIT':
            if pe.ps_status != 'SUCCESS':
                from models import RouteStopInvoice
                rsis = RouteStopInvoice.query.filter_by(
                    route_stop_id=cod_receipt.route_stop_id, is_active=True).all()
                invoice_nos = [r.invoice_no for r in rsis] or (cod_receipt.invoice_nos or [])
                customer_code = (stop.customer_code if stop else '') or ''
                pe = commit_to_ps365(pe, customer_code, invoice_nos,
                                     cod_receipt.driver_username or user_code)
            if pe.ps_status == 'SUCCESS' and pe.ps_reference:
                cod_receipt.ps365_reference_number = pe.ps_reference
                cod_receipt.ps365_synced_at = datetime.utcnow()
            return

        # Legacy fallback: no PaymentEntry (or SKIP mode receipt marked official)
        from routes_receipts import create_receipt_core
        invoice_nos_list = cod_receipt.invoice_nos or []
        inv_list_str = ', '.join(invoice_nos_list[:5])
        if len(invoice_nos_list) > 5:
            inv_list_str += f' +{len(invoice_nos_list) - 5}'
        ok, ref_num, resp_id, _, _ = create_receipt_core(
            customer_code=(stop.customer_code if stop else '') or '',
            amount_val=float(cod_receipt.received_amount or 0),
            comments=inv_list_str,
            driver_username=cod_receipt.driver_username,
            user_code=user_code,
            invoice_no=inv_list_str,
            route_stop_id=cod_receipt.route_stop_id,
            cheque_number=cod_receipt.cheque_number or '',
            cheque_date=cod_receipt.cheque_date.strftime('%Y-%m-%d') if cod_receipt.cheque_date else '',
            allow_duplicate_stop=True,
        )
        cod_receipt.ps365_reference_number = ref_num
        cod_receipt.ps365_receipt_id = str(resp_id) if resp_id else None
        cod_receipt.ps365_synced_at = datetime.utcnow()
    except Exception as exc:
        logger.error(f"PS365 sync at print failed for CODReceipt {cod_receipt.id}: {exc}")
