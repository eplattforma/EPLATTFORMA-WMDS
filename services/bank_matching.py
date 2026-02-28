"""Bank statement import and matching service for pending payments."""
import io
import re
import uuid
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime

import pandas as pd

from app import db
from models import BankTransaction, CODInvoiceAllocation, Invoice, Shipment

logger = logging.getLogger(__name__)


def parse_bank_statement(file_obj, filename):
    filename_lower = filename.lower()
    if filename_lower.endswith('.csv'):
        raw = file_obj.read()
        for enc in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1253', 'cp1252'):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, AttributeError):
                continue
        else:
            text = raw.decode('utf-8', errors='replace')
        df = pd.read_csv(io.StringIO(text))
    elif filename_lower.endswith(('.xlsx', '.xls')):
        df = pd.read_excel(io.BytesIO(file_obj.read()))
    else:
        raise ValueError("Unsupported file format. Please upload CSV or Excel.")

    df.columns = [c.strip() for c in df.columns]
    col_map = _detect_columns(df)
    if not col_map.get('credit') and not col_map.get('amount'):
        if len(df.columns) >= 8:
            col_h = df.columns[7]
            logger.info(f"No credit column detected by name, falling back to column H: '{col_h}'")
            col_map['credit'] = col_h
        else:
            raise ValueError("Could not detect a credit/amount column in the file. "
                             "Expected columns like: Credit, Amount, Deposit, etc.")

    return df, col_map


def _detect_columns(df):
    col_map = {}
    cols_lower = {c: c.lower().strip() for c in df.columns}

    date_patterns = ['date', 'txn date', 'transaction date', 'value date', 'posting date']
    desc_patterns = ['description', 'narrative', 'details', 'particulars', 'memo', 'remarks', 'beneficiary']
    ref_patterns = ['reference', 'ref', 'cheque no', 'transaction ref', 'ref no']
    credit_patterns = ['credit', 'deposit', 'credit amount', 'cr']
    debit_patterns = ['debit', 'withdrawal', 'debit amount', 'dr']
    amount_patterns = ['amount']
    balance_patterns = ['balance', 'running balance', 'closing balance']

    for orig_col, low_col in cols_lower.items():
        if not col_map.get('date'):
            for p in date_patterns:
                if p in low_col:
                    col_map['date'] = orig_col
                    break
        if not col_map.get('description'):
            for p in desc_patterns:
                if p in low_col:
                    col_map['description'] = orig_col
                    break
        if not col_map.get('reference'):
            for p in ref_patterns:
                if p in low_col:
                    col_map['reference'] = orig_col
                    break
        if not col_map.get('credit'):
            for p in credit_patterns:
                if p == low_col or low_col.startswith(p):
                    col_map['credit'] = orig_col
                    break
        if not col_map.get('debit'):
            for p in debit_patterns:
                if p == low_col or low_col.startswith(p):
                    col_map['debit'] = orig_col
                    break
        if not col_map.get('amount') and 'credit' not in low_col and 'debit' not in low_col:
            for p in amount_patterns:
                if p == low_col:
                    col_map['amount'] = orig_col
                    break
        if not col_map.get('balance'):
            for p in balance_patterns:
                if p in low_col:
                    col_map['balance'] = orig_col
                    break

    return col_map


def _parse_amount(val):
    if pd.isna(val) or val == '' or val is None:
        return None
    s = str(val).strip().replace(',', '').replace('€', '').replace('$', '').replace('£', '')
    s = re.sub(r'[^\d.\-]', '', s)
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(val):
    if pd.isna(val) or val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if hasattr(val, 'date'):
        return val.date()
    s = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%d.%m.%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_and_match(file_obj, filename, username):
    df, col_map = parse_bank_statement(file_obj, filename)
    batch_id = str(uuid.uuid4())[:8]

    pending = _load_pending_payments()

    transactions = []
    for _, row in df.iterrows():
        credit_val = None
        debit_val = None

        if col_map.get('credit'):
            credit_val = _parse_amount(row.get(col_map['credit']))
        if col_map.get('debit'):
            debit_val = _parse_amount(row.get(col_map['debit']))
        if col_map.get('amount') and credit_val is None:
            amt = _parse_amount(row.get(col_map['amount']))
            if amt is not None and amt > 0:
                credit_val = amt
            elif amt is not None and amt < 0:
                debit_val = abs(amt)

        if credit_val is None or credit_val <= 0:
            continue

        txn_date = _parse_date(row.get(col_map.get('date', ''), None))
        desc = str(row.get(col_map.get('description', ''), '') or '').strip()
        ref = str(row.get(col_map.get('reference', ''), '') or '').strip()
        balance = _parse_amount(row.get(col_map.get('balance', ''), None))
        raw = ' | '.join(str(row.get(c, '')) for c in df.columns)

        bt = BankTransaction(
            batch_id=batch_id,
            txn_date=txn_date,
            description=desc[:2000] if desc else None,
            reference=ref[:200] if ref else None,
            credit=credit_val,
            debit=debit_val,
            balance=balance,
            raw_row=raw[:4000] if raw else None,
            uploaded_by=username,
        )

        best_match = _find_best_match(bt, pending)
        if best_match:
            bt.matched_allocation_id = best_match['alloc_id']
            bt.match_status = 'SUGGESTED'
            bt.match_confidence = best_match['confidence']
            bt.match_reason = best_match['reason']

        db.session.add(bt)
        transactions.append(bt)

    db.session.commit()

    matched = sum(1 for t in transactions if t.match_status == 'SUGGESTED')
    return {
        'batch_id': batch_id,
        'total_rows': len(df),
        'credit_rows': len(transactions),
        'matched': matched,
        'unmatched': len(transactions) - matched,
    }


def _load_pending_payments():
    rows = db.session.query(
        CODInvoiceAllocation,
        Invoice.customer_name,
        Invoice.customer_code
    ).join(
        Shipment, CODInvoiceAllocation.route_id == Shipment.id
    ).join(
        Invoice, CODInvoiceAllocation.invoice_no == Invoice.invoice_no
    ).filter(
        Shipment.reconciliation_status == 'RECONCILED',
        CODInvoiceAllocation.is_pending == True
    ).all()

    result = []
    for alloc, cust_name, cust_code in rows:
        due = float((alloc.expected_amount or 0) - (alloc.deduct_amount or 0))
        if due <= 0.01:
            continue
        result.append({
            'alloc_id': alloc.id,
            'invoice_no': alloc.invoice_no or '',
            'customer_name': cust_name or '',
            'customer_code': cust_code or '',
            'due': round(due, 2),
            'payment_method': (alloc.payment_method or '').lower(),
        })
    return result


def _find_best_match(bt, pending_list):
    credit = float(bt.credit) if bt.credit else 0
    desc_upper = (bt.description or '').upper()
    ref_upper = (bt.reference or '').upper()
    search_text = desc_upper + ' ' + ref_upper

    candidates = []
    for p in pending_list:
        score = 0
        reasons = []

        if credit > 0 and abs(credit - p['due']) < 0.02:
            score += 50
            reasons.append(f"Exact amount match €{p['due']:.2f}")
        elif credit > 0 and abs(credit - p['due']) < 1.0:
            score += 30
            reasons.append(f"Close amount (€{credit:.2f} vs €{p['due']:.2f})")

        inv_no = p['invoice_no'].upper()
        if inv_no and inv_no in search_text:
            score += 40
            reasons.append(f"Invoice {p['invoice_no']} found in description")

        cust_name = p['customer_name'].upper()
        if cust_name and len(cust_name) >= 3:
            name_parts = [w for w in cust_name.split() if len(w) >= 3]
            matched_parts = [w for w in name_parts if w in search_text]
            if matched_parts:
                ratio = len(matched_parts) / max(len(name_parts), 1)
                if ratio >= 0.5:
                    score += 25
                    reasons.append(f"Customer name match: {' '.join(matched_parts)}")
                elif matched_parts:
                    score += 10
                    reasons.append(f"Partial name: {' '.join(matched_parts)}")

        cust_code = p['customer_code'].upper()
        if cust_code and len(cust_code) >= 3 and cust_code in search_text:
            score += 15
            reasons.append(f"Customer code {p['customer_code']} in description")

        if score >= 30:
            confidence = 'HIGH' if score >= 70 else 'MEDIUM' if score >= 50 else 'LOW'
            candidates.append({
                'alloc_id': p['alloc_id'],
                'score': score,
                'confidence': confidence,
                'reason': '; '.join(reasons),
            })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates[0]


def get_matches_for_allocations(alloc_ids):
    if not alloc_ids:
        return {}
    matches = BankTransaction.query.filter(
        BankTransaction.matched_allocation_id.in_(alloc_ids),
        BankTransaction.match_status == 'SUGGESTED',
        BankTransaction.dismissed == False
    ).order_by(BankTransaction.match_confidence.desc()).all()

    result = {}
    for m in matches:
        aid = m.matched_allocation_id
        if aid not in result:
            result[aid] = []
        result[aid].append(m)
    return result


def dismiss_match(txn_id):
    bt = db.session.get(BankTransaction, txn_id)
    if bt:
        bt.dismissed = True
        bt.match_status = 'DISMISSED'
        db.session.commit()
    return bt


def confirm_match(txn_id):
    bt = db.session.get(BankTransaction, txn_id)
    if bt:
        bt.match_status = 'CONFIRMED'
        db.session.commit()
    return bt
