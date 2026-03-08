import os
import logging
import requests
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config_ps365 import PS365_BASE_URL, PS365_TOKEN

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_YEARS = int(os.getenv("PS365_BALANCE_LOOKBACK_YEARS", "10"))


def _cyprus_today():
    try:
        return datetime.now(ZoneInfo("Asia/Nicosia")).date()
    except Exception:
        return date.today()


def fetch_statement(customer_code_365: str, from_date: str, to_date: str):
    base = PS365_BASE_URL or os.getenv('POWERSOFT_BASE', '') or os.getenv('POWERSOFT_BASE_URL', '')
    token = PS365_TOKEN or os.getenv('POWERSOFT_TOKEN', '')

    if not base:
        raise RuntimeError("Missing PS365_BASE_URL")
    if not token:
        raise RuntimeError("Missing PS365_TOKEN")

    url = f"{base.rstrip('/')}/customer_statement_of_account"

    payload = {
        "api_credentials": {"token": token},
        "filter_define": {
            "customer_code_365": str(customer_code_365),
            "from_date": from_date,
            "to_date": to_date,
            "include_general_description": True,
            "include_detail_description": False,
            "include_receipt_description": False,
            "include_cheque_information": False,
            "include_entity_name": False
        }
    }

    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    api_resp = (data or {}).get("api_response", {}) or {}
    if str(api_resp.get("response_code")) != "1":
        raise RuntimeError(f"PS365 error: {api_resp.get('response_msg') or 'Unknown error'}")

    return data


def compute_balance_from_lines(statement_json: dict):
    lines = (statement_json or {}).get("list_statement_lines") or []

    signed = 0.0
    for ln in lines:
        amt = float(ln.get("transaction_amount") or 0.0)
        drcr = (ln.get("transaction_drcr") or "").upper().strip()
        if drcr == "DR":
            signed += amt
        elif drcr == "CR":
            signed -= amt

    drcr_out = "DR" if signed >= 0 else "CR"
    abs_bal = abs(round(signed, 2))

    last_lb = None
    last_drcr = None
    for ln in reversed(lines):
        if ln.get("line_balance") is not None:
            last_lb = float(ln.get("line_balance") or 0.0)
            last_drcr = (ln.get("balance_drcr") or "").upper().strip() or None
            break

    return {
        "balance": abs_bal,
        "drcr": drcr_out,
        "signed_balance": round(signed, 2),
        "ps_last_line_balance": last_lb,
        "ps_last_balance_drcr": last_drcr,
    }


def get_customer_balance_quick(customer_code_365: str):
    today = _cyprus_today()
    to_date = today.isoformat()

    stmt = fetch_statement(customer_code_365, from_date=to_date, to_date=to_date)
    lines = (stmt or {}).get("list_statement_lines") or []

    if not lines:
        return {
            "balance": 0.0,
            "drcr": "DR",
            "signed_balance": 0.0,
            "ps_last_line_balance": None,
            "ps_last_balance_drcr": None,
            "as_of": to_date,
        }

    last = lines[-1]
    lb = float(last.get("line_balance") or 0.0)
    drcr = (last.get("balance_drcr") or "DR").upper().strip()
    signed = lb if drcr == "DR" else -lb

    return {
        "balance": lb,
        "drcr": drcr,
        "signed_balance": round(signed, 2),
        "ps_last_line_balance": lb,
        "ps_last_balance_drcr": drcr,
        "as_of": to_date,
    }


def get_customer_balance_as_of_today(customer_code_365: str):
    return get_customer_balance_quick(customer_code_365)
