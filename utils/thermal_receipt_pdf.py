from io import BytesIO
from decimal import Decimal
import textwrap
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics

FONT = "Courier"
FONT_B = "Courier-Bold"
FS_NORMAL = 9
FS_TITLE = 11
LEADING = 11

PAGE_W = 80 * mm
LEFT = 3 * mm
RIGHT = 3 * mm
TOP = 3 * mm
BOT = 4 * mm

def get_dynamic_cols(font_name, font_size):
    avail_w = PAGE_W - LEFT - RIGHT
    char_w = pdfmetrics.stringWidth("M", font_name, font_size)
    return max(24, int(avail_w / char_w))

def money(v):
    try:
        return f"EUR {Decimal(str(v)):,.2f}"
    except Exception:
        return "EUR 0.00"

def _pad_right(label, value, width):
    label = str(label)
    value = str(value)
    gap = width - len(label) - len(value)
    if gap < 1:
        gap = 1
    return label + (" " * gap) + value

def _wrap_cols(s, width):
    s = (s or "").strip()
    if not s:
        return [""]
    return textwrap.wrap(s, width=width, break_long_words=True, replace_whitespace=False)

def format_stop_no(stop_no):
    try:
        # Handle cases like "1.0" or "1" to "001"
        return str(int(float(stop_no))).zfill(3)
    except Exception:
        return str(stop_no).zfill(3)

def build_delivery_receipt_pdf(data: dict) -> bytes:
    """
    Builds a PDF optimized for 80mm thermal printers (printable width 72mm).
    """
    cols = get_dynamic_cols(FONT, FS_NORMAL)
    cols_title = get_dynamic_cols(FONT_B, FS_TITLE)
    sep = "-" * cols
    sig_line = "_" * (cols - 2)
    lines = []

    def add(s=""):
        lines.append(("L", s[:cols]))

    def add_b(s):
        lines.append(("B", s[:cols]))

    def add_c(s):
        lines.append(("C", s[:cols]))

    def add_bc(s):
        # Use title-specific column count for bold titles
        lines.append(("BC", s[:cols_title]))

    # Diagnostic line (can be removed later)
    if data.get("calibrate"):
        add(f"DEBUG W={PAGE_W/mm:.0f}mm COLS={cols} FS={FS_NORMAL}")
        add("|" + ("-" * (cols - 2)) + "|")
        add(sep)

    is_collected = bool(data.get("is_collected", False))
    is_credit = bool(data.get("is_credit", False))
    is_preview = bool(data.get("is_preview", False))
    is_amended = bool(data.get("is_amended", False))

    # Flags
    if is_preview:
        add_bc("*** PREVIEW - NOT A FINAL RECEIPT ***")
    if is_amended:
        add_bc("*** AMENDED DELIVERY ***")

    # Header
    add_bc("STEP EPLATTFORMA LTD")
    add_c("Digeni Akrita 13BC, 1055 Lefkosia")
    add_c("Tel: 7000 0394  VAT: CY103532640")
    add(sep)

    # Title / Status
    if is_credit:
        add_bc("DELIVERY CONFIRMATION")
        add_bc("CREDIT ACCOUNT")
    elif is_collected:
        add_bc("PAYMENT RECEIPT")
        if is_preview:
            add_bc("STATUS: PENDING CONFIRMATION")
        else:
            add_bc("STATUS: PAID")
    else:
        add_bc("DELIVERY CONFIRMATION / PAYMENT DUE")
        add_bc("STATUS: NOT COLLECTED")
    add(sep)

    # IDs
    receipt_no = str(data.get("receipt_no", "")).strip()
    date_str = str(data.get("date_str", "")).strip()
    add_b(_pad_right(f"Receipt: {receipt_no}", f"Date: {date_str}", cols))

    route_no = str(data.get("route_no", "")).strip()
    stop_no = format_stop_no(data.get("stop_no", ""))
    driver = str(data.get("driver_name", "")).strip()

    add_b(f"Route: {route_no}  Stop: {stop_no}")
    add_b(f"Driver: {driver}")

    cust_code = str(data.get("customer_code", "")).strip()
    if cust_code:
        add(f"Cust Code: {cust_code}")
    add(sep)

    # Customer
    add_b("CUSTOMER")
    for w in _wrap_cols(data.get("customer_name", ""), cols):
        add(w)
    for w in _wrap_cols(data.get("customer_addr", ""), cols):
        add(w)
    add(sep)

    # Invoices
    invoices = data.get("invoices") or []
    add_b(f"INVOICES ({len(invoices)})")
    for inv in invoices:
        inv_no = str(inv.get("invoice_no", "")).strip()
        inv_total = inv.get("total", None)
        if inv_total is not None:
            add(_pad_right(f"  {inv_no}", money(inv_total), cols))
        else:
            add(f"  {inv_no}"[:cols])
    add(sep)

    # Amounts
    add_b("AMOUNTS")
    expected = Decimal(str(data.get("expected", "0") or "0"))
    collected = Decimal(str(data.get("collected", "0") or "0"))
    balance = Decimal(str(data.get("balance_due", "") or (expected - collected)))

    add_b(_pad_right("  Expected:", money(expected), cols))
    add_b(_pad_right("  Collected:", money(collected), cols))

    if is_collected:
        pm = str(data.get("payment_method", "")).upper().replace("_", " ").strip()
        add_b(_pad_right("  Method:", pm or "-", cols))

        if (data.get("payment_method") or "").lower() == "cheque":
            if data.get("cheque_number"):
                add_b(_pad_right("  Cheque No:", str(data["cheque_number"]), cols))
            if data.get("cheque_date"):
                add_b(_pad_right("  Cheque Date:", str(data["cheque_date"]), cols))

        if (data.get("payment_method") or "").lower() == "cash":
            if data.get("cash_received") is not None:
                add_b(_pad_right("  Cash Received:", money(data["cash_received"]), cols))
            if data.get("change_given") is not None:
                add_b(_pad_right("  Change Given:", money(data["change_given"]), cols))

    if is_credit:
        add_b("  ON ACCOUNT - NO BALANCE DUE")
    else:
        if (not is_collected) or (balance > 0):
            add_b(_pad_right("  BALANCE DUE:", money(balance), cols))

    add(sep)

    # Notes
    notes = (data.get("notes") or "").strip()
    if notes:
        add_b("NOTES")
        for w in _wrap_cols(notes, cols):
            add("  " + w if len(w) <= cols - 2 else w[:cols])
        add(sep)

    # Signatures
    add("")
    add("Customer Signature (Delivery):")
    add(sig_line)
    add("")

    if is_collected:
        add("Customer Signature (Payment):")
        add(sig_line)
        add("")

    add("Driver Signature:")
    add(sig_line)

    # Dynamic height
    total_lines = len(lines)
    page_h = TOP + BOT + (total_lines * LEADING)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, page_h))
    y = page_h - TOP

    for style, txt in lines:
        if style == "C":
            c.setFont(FONT, FS_NORMAL)
            c.drawCentredString(PAGE_W / 2, y, txt)
        elif style == "B":
            c.setFont(FONT_B, FS_NORMAL)
            c.drawString(LEFT, y, txt)
        elif style == "BC":
            c.setFont(FONT_B, FS_TITLE)
            c.drawCentredString(PAGE_W / 2, y, txt)
        else:
            c.setFont(FONT, FS_NORMAL)
            c.drawString(LEFT, y, txt)
        y -= LEADING

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()

def build_thermal_receipt_pdf(receipt):
    """Legacy wrapper kept for backward compatibility."""
    return build_delivery_receipt_pdf(receipt)
