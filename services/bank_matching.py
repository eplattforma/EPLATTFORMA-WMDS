"""Bank statement import service."""
import io
import re
import uuid
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime

import pandas as pd

from app import db
from models import BankTransaction

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
        df = pd.read_csv(io.StringIO(text), header=None)
    elif filename_lower.endswith(('.xlsx', '.xls')):
        df = pd.read_excel(io.BytesIO(file_obj.read()), header=None)
    else:
        raise ValueError("Unsupported file format. Please upload CSV or Excel.")

    df, header_row_idx = _find_header_row(df)

    df.columns = [str(c).strip() if pd.notna(c) else f'col_{i}' for i, c in enumerate(df.columns)]
    logger.info(f"Columns after header detection: {list(df.columns)}")
    col_map = _detect_columns(df)
    if not col_map.get('credit') and not col_map.get('amount'):
        for i, col_name in enumerate(df.columns):
            sample_vals = df[col_name].dropna().head(10)
            numeric_count = 0
            for v in sample_vals:
                parsed = _parse_amount(v)
                if parsed is not None and parsed > 0:
                    numeric_count += 1
            if numeric_count >= 3:
                logger.info(f"Fallback: using column '{col_name}' (index {i}) as credit — {numeric_count}/10 positive values")
                col_map['credit'] = col_name
                break

    if not col_map.get('credit') and not col_map.get('amount'):
        raise ValueError("Could not detect a credit/amount column in the file. "
                         "Expected columns like: Credit, Amount, Deposit, etc.")

    logger.info(f"Bank statement parsed: header at row {header_row_idx}, "
                f"{len(df)} data rows, columns mapped: {col_map}")
    return df, col_map


def _find_header_row(df):
    for i in range(min(20, len(df))):
        row_vals = [str(v).strip().lower() for v in df.iloc[i] if pd.notna(v)]
        has_date = any(v in ('date', 'value date', 'txn date', 'transaction date', 'posting date') for v in row_vals)
        has_credit = any(v in ('credit', 'amount', 'deposit', 'credit amount', 'cr') for v in row_vals)
        has_desc = any(v in ('description', 'narrative', 'details', 'particulars') for v in row_vals)
        if (has_date and has_credit) or (has_date and has_desc) or (has_credit and has_desc):
            new_header = df.iloc[i]
            df = df.iloc[i + 1:].reset_index(drop=True)
            df.columns = new_header.values
            logger.info(f"Found header at row {i}: {[str(v) for v in new_header.values if pd.notna(v)]}")
            return df, i

    logger.warning(f"No header row detected in first 20 rows. Row 0 values: {list(df.iloc[0])}")
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    return df, 0


def _detect_columns(df):
    col_map = {}
    cols_lower = {c: c.lower().strip() for c in df.columns}

    date_patterns = ['date', 'txn date', 'transaction date', 'value date', 'posting date']
    desc_patterns = ['description', 'narrative', 'details', 'particulars', 'memo', 'remarks', 'beneficiary', 'transaction type']
    ref_patterns = ['reference', 'ref', 'cheque no', 'transaction ref', 'ref no', 'reference number', 'bank reference']
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

    old_count = BankTransaction.query.filter(
        BankTransaction.match_status.in_(['UNMATCHED', 'SUGGESTED'])
    ).delete(synchronize_session=False)
    if old_count:
        db.session.flush()
        logger.info(f"Cleared {old_count} old bank transactions before new import")

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

        db.session.add(bt)
        transactions.append(bt)

    db.session.commit()

    return {
        'batch_id': batch_id,
        'total_rows': len(df),
        'credit_rows': len(transactions),
    }


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
