"""
Helper module to fetch invoice headers and lines from PS365 for preview purposes.
This is a temporary in-memory preview tool - no data is saved to the database.
"""
from ps365_client import call_ps365
from datetime import datetime, timedelta

PAGE_SIZE = 100  # PS365 max


def fetch_invoice_headers_from_date(date_from: str):
    """
    Fetch invoice headers from PS365 starting from the given date.
    date_from must be in 'YYYY-MM-DD' format (date only, no time).
    Returns a list of invoice header dicts.
    """
    page = 1
    all_headers = []
    
    # Calculate to_date as 30 days after the from_date
    # PS365 API uses from_date and to_date in yyyy-mm-dd format
    try:
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        to_dt = from_dt + timedelta(days=30)
        from_date_str = from_dt.strftime("%Y-%m-%d")
        to_date_str = to_dt.strftime("%Y-%m-%d")
    except Exception:
        # Fallback
        from_date_str = date_from
        to_date_str = date_from

    while True:
        payload = {
            "filter_define": {
                "only_counted": "N",
                "page_number": page,
                "page_size": PAGE_SIZE,
                "invoice_type": "all",
                "invoice_number_selection": "",
                "invoice_customer_code_selection": "",
                "invoice_customer_name_selection": "",
                "invoice_customer_email_selection": "",
                "invoice_customer_phone_selection": "",
                "from_date": from_date_str,
                "to_date": to_date_str,
                "session_date_from_utc0": "",
                "session_date_to_utc0": "",
            }
        }

        response = call_ps365("list_loyalty_invoices_header", payload)
        api_resp = response.get("api_response", {})
        if api_resp.get("response_code") != "1":
            # stop on error
            print("PS365 API Error for list_loyalty_invoices_header:", api_resp)
            break

        invoices = response.get("list_invoices", []) or []
        if not invoices:
            break

        for inv in invoices:
            # Response format: flat invoice object (no nested "invoice" wrapper)
            all_headers.append(inv)

        page += 1

    return all_headers


def fetch_invoice_lines_from_date(date_from: str):
    """
    Fetch invoice lines from PS365 starting from the given date.
    date_from must be in 'YYYY-MM-DD' format (date only, no time).
    Returns a list of flattened line dicts suitable for tabular display.
    """
    page = 1
    all_lines = []
    
    # Calculate to_date as 30 days after the from_date
    # PS365 API uses from_date and to_date in yyyy-mm-dd format
    try:
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        to_dt = from_dt + timedelta(days=30)
        from_date_str = from_dt.strftime("%Y-%m-%d")
        to_date_str = to_dt.strftime("%Y-%m-%d")
    except Exception:
        # Fallback
        from_date_str = date_from
        to_date_str = date_from

    while True:
        payload = {
            "filter_define": {
                "only_counted": "N",
                "page_number": page,
                "page_size": PAGE_SIZE,
                "invoice_type": "all",
                "invoice_number_selection": "",
                "invoice_customer_code_selection": "",
                "invoice_customer_name_selection": "",
                "invoice_customer_email_selection": "",
                "invoice_customer_phone_selection": "",
                "from_date": from_date_str,
                "to_date": to_date_str,
                "session_date_from_utc0": "",
                "session_date_to_utc0": "",
            }
        }

        response = call_ps365("list_loyalty_invoices", payload)
        api_resp = response.get("api_response", {})
        if api_resp.get("response_code") != "1":
            # stop on error
            print("PS365 API Error for list_loyalty_invoices:", api_resp)
            break

        invoices = response.get("list_invoices", []) or []
        if not invoices:
            break

        for inv in invoices:
            # response shape:
            # { "invoice": { "invoice_header": {...}, "list_invoice_details": [ {...}, ... ] } }
            inv_obj = inv.get("invoice", inv)  # in case it's not nested
            header = inv_obj.get("invoice_header", {})
            lines = inv_obj.get("list_invoice_details", []) or []

            for line in lines:
                # Build a flattened row for the table
                row = {
                    "invoice_no_365": header.get("invoice_no_365"),
                    "invoice_date": header.get("invoice_date_utc0"),
                    "store_code_365": header.get("store_code_365"),
                    "customer_code_365": header.get("customer_code_365"),
                    "customer_name": header.get("customer_name"),
                    "item_code_365": line.get("item_code_365"),
                    "item_name": line.get("item_name"),
                    "qty": line.get("line_quantity"),
                    "price_excl": line.get("line_price_exclude_vat"),
                    "price_incl": line.get("line_price_include_vat"),
                    "vat_percent": line.get("line_vat_percentage"),
                    "line_total_excl": line.get("line_total_sub"),
                    "line_total_incl": line.get("line_total_grand"),
                }
                all_lines.append(row)

        page += 1

    return all_lines
