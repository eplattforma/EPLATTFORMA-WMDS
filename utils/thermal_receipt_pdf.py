from io import BytesIO
from decimal import Decimal
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit

COLS = 48
FONT = "Courier"
FONT_B = "Courier-Bold"
FS = 13
LEADING = 16


def money(v):
    try:
        return f"EUR {Decimal(str(v)):,.2f}"
    except Exception:
        return "EUR 0.00"


def _pad_right(label, value, width=COLS):
    gap = width - len(label) - len(value)
    if gap < 1:
        gap = 1
    return label + " " * gap + value


def build_delivery_receipt_pdf(data):
    """
    Build an 80mm thermal receipt PDF.

    data keys:
      is_collected   bool   - True if payment was collected
      is_preview     bool   - True if receipt not final
      is_amended     bool   - True if stop was reopened
      receipt_no     str
      date_str       str    - e.g. "2026-02-19 09:06"
      route_no       int/str
      stop_no        str    - 3-digit e.g. "012"
      driver_name    str
      customer_code  str
      customer_name  str
      customer_addr  str
      invoices       list of dict {invoice_no, total}
      expected       Decimal
      collected      Decimal
      balance_due    Decimal
      payment_method str
      cheque_number  str (optional)
      cheque_date    str (optional)
      cash_received  Decimal (optional, for cash change calc)
      change_given   Decimal (optional)
      notes          str (optional)
      exceptions     list of dict {type, item_name, qty_expected, qty_actual, note}
    """
    buf = BytesIO()

    PAGE_W = 150 * mm
    LEFT = 6 * mm
    RIGHT = 6 * mm
    TOP = 6 * mm
    BOT = 8 * mm

    lines_out = []

    def add(text, style="L"):
        lines_out.append((style, text))

    def add_center(text):
        add(text, "C")

    def add_bold(text):
        add(text, "B")

    def add_bold_center(text):
        add(text, "BC")

    def rule():
        add("-" * COLS)

    def blank():
        add("")

    avail_w = PAGE_W - LEFT - RIGHT

    def wrap(text):
        wrapped = simpleSplit(text, FONT, FS, avail_w)
        for w in wrapped:
            add(w)

    is_collected = data.get("is_collected", False)
    is_credit = data.get("is_credit", False)
    is_preview = data.get("is_preview", False)
    is_amended = data.get("is_amended", False)

    if is_preview:
        add_bold_center("*** PREVIEW - NOT A FINAL RECEIPT ***")
        blank()

    if is_amended:
        add_bold_center("*** AMENDED DELIVERY ***")
        blank()

    add_bold_center("STEP EPLATTFORMA LTD")
    add_center("Digeni Akrita 13BC, 1055 Lefkosia")
    add_center("Tel: 7000 0394  VAT: CY10353264O")
    rule()

    if is_credit:
        add_bold_center("DELIVERY CONFIRMATION")
        add_bold_center("CREDIT ACCOUNT")
    elif is_collected:
        add_bold_center("PAYMENT RECEIPT")
        add_bold_center("STATUS: PAID")
    else:
        add_bold_center("DELIVERY CONFIRMATION / PAYMENT DUE")
        add_bold_center("STATUS: NOT COLLECTED")
    rule()

    receipt_no = str(data.get("receipt_no", ""))
    date_str = str(data.get("date_str", ""))
    id_line1 = f"Receipt: {receipt_no}"
    if len(id_line1) + len(date_str) + 8 <= COLS:
        add(_pad_right(id_line1, f"Date: {date_str}"))
    else:
        add(f"Receipt: {receipt_no}")
        add(f"Date: {date_str}")

    route_no = str(data.get("route_no", ""))
    stop_no = str(data.get("stop_no", ""))
    driver = str(data.get("driver_name", ""))
    add(f"Route: {route_no}  Stop: {stop_no}")
    add(f"Driver: {driver}")
    cust_code = data.get("customer_code", "")
    if cust_code:
        add(f"Cust Code: {cust_code}")
    rule()

    add_bold("CUSTOMER")
    customer_name = data.get("customer_name", "")
    if customer_name:
        wrap(customer_name)
    customer_addr = data.get("customer_addr", "")
    if customer_addr:
        wrap(customer_addr)
    rule()

    invoices = data.get("invoices", [])
    add_bold(f"INVOICES ({len(invoices)})")
    for inv in invoices:
        inv_no = inv.get("invoice_no", "")
        inv_total = inv.get("total")
        if inv_total is not None:
            add(_pad_right(f"  {inv_no}", money(inv_total)))
        else:
            add(f"  {inv_no}")
    rule()

    add_bold("AMOUNTS")
    expected = data.get("expected", Decimal("0"))
    collected = data.get("collected", Decimal("0"))
    balance = data.get("balance_due", expected - collected)

    add(_pad_right("  Expected:", money(expected)))
    add(_pad_right("  Collected:", money(collected)))

    if is_collected:
        pm = data.get("payment_method", "").upper().replace("_", " ")
        add(_pad_right("  Method:", pm or "-"))
        if data.get("payment_method", "").lower() == "cheque":
            if data.get("cheque_number"):
                add(_pad_right("  Cheque No:", data["cheque_number"]))
            if data.get("cheque_date"):
                add(_pad_right("  Cheque Date:", str(data["cheque_date"])))
        if data.get("cash_received") is not None and data.get("payment_method", "").lower() == "cash":
            add(_pad_right("  Cash Received:", money(data["cash_received"])))
            if data.get("change_given") is not None:
                add(_pad_right("  Change Given:", money(data["change_given"])))

    if is_credit:
        add_bold("  ON ACCOUNT - NO BALANCE DUE")
    elif not is_collected or balance > 0:
        add_bold(_pad_right("  BALANCE DUE:", money(balance)))
    rule()

    exceptions = data.get("exceptions", [])
    if exceptions:
        add_bold(f"EXCEPTIONS ({len(exceptions)})")
        for exc in exceptions:
            etype = exc.get("type", "").upper()
            item = exc.get("item_name", "Unknown")
            qe = exc.get("qty_expected", "")
            qa = exc.get("qty_actual", "")
            add(f"  {etype}: {item}")
            add(f"    Exp: {qe}  Act: {qa}")
            if exc.get("note"):
                wrap(f"    Note: {exc['note']}")
        rule()

    notes = data.get("notes", "")
    if notes:
        add_bold("Notes:")
        wrap(f"  {notes}")
        rule()

    blank()
    add("Customer Signature (Delivery):")
    blank()
    add("________________________________")
    blank()

    if is_collected:
        add("Customer Signature (Payment):")
        blank()
        add("________________________________")
        blank()

    add("Driver Signature:")
    blank()
    add("________________________________")

    total_lines = len(lines_out) + 4
    PAGE_H = TOP + BOT + (total_lines * LEADING)

    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    y = PAGE_H - TOP
    c.setFont(FONT, FS)

    for style, text in lines_out:
        if style == "C":
            c.setFont(FONT, FS)
            c.drawCentredString(PAGE_W / 2, y, text)
        elif style == "B":
            c.setFont(FONT_B, FS)
            c.drawString(LEFT, y, text)
            c.setFont(FONT, FS)
        elif style == "BC":
            c.setFont(FONT_B, FS)
            c.drawCentredString(PAGE_W / 2, y, text)
            c.setFont(FONT, FS)
        else:
            c.drawString(LEFT, y, text)
        y -= LEADING

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def build_thermal_receipt_pdf(receipt):
    """Legacy wrapper kept for backward compatibility."""
    buf = BytesIO()
    PAGE_W = 72 * mm
    LEFT = 2.5 * mm
    RIGHT = 2.5 * mm
    TOP = 3 * mm
    BOT = 3 * mm
    LEADING_OLD = 12

    lines_out = []

    def add_center(text):
        lines_out.append(("C", text))

    def add_left(text):
        lines_out.append(("L", text))

    def add_rule():
        lines_out.append(("L", "-" * 42))

    avail_w = PAGE_W - LEFT - RIGHT

    def wrap_left(text, max_w_pts):
        wrapped = simpleSplit(text, FONT, 10, max_w_pts)
        for w in wrapped:
            add_left(w)

    add_center(receipt.get("company_name", ""))
    if receipt.get("company_vat"):
        add_center(f"VAT: {receipt['company_vat']}")
    add_rule()
    add_left(f"Receipt: {receipt.get('receipt_no', '')}")
    add_left(f"Date:    {receipt.get('date_str', '')}")
    add_rule()

    cust = receipt.get("customer_name", "")
    code = receipt.get("customer_code", "")
    if cust or code:
        wrap_left(f"Customer: {cust} ({code})".strip(), avail_w)

    add_rule()
    add_left("QTY   PRICE     TOTAL")
    add_rule()

    for ln in receipt.get("lines", []):
        name = (ln.get("name") or "").strip()
        qty = ln.get("qty", 0)
        price = f"{Decimal(str(ln.get('price', 0))):.2f}"
        total = f"{Decimal(str(ln.get('total', 0))):.2f}"
        if name:
            wrap_left(name, avail_w)
        qty_s = f"{qty}".rjust(3)
        price_s = price.rjust(7)
        total_s = total.rjust(8)
        add_left(f"{qty_s}  {price_s}  {total_s}")
        add_left("")

    add_rule()
    totals = receipt.get("totals", {}) or {}
    add_left(f"SUBTOTAL: {Decimal(str(totals.get('subtotal', 0))):.2f}".rjust(42))
    add_left(f"VAT:      {Decimal(str(totals.get('vat', 0))):.2f}".rjust(42))
    add_left(f"TOTAL:    {Decimal(str(totals.get('total', 0))):.2f}".rjust(42))
    add_rule()

    if receipt.get("notes"):
        wrap_left(receipt["notes"], avail_w)
        add_rule()

    add_center("THANK YOU")

    total_lines = len(lines_out) + 2
    PAGE_H = TOP + BOT + (total_lines * LEADING_OLD)
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    y = PAGE_H - TOP
    c.setFont(FONT, 10)

    for align, text in lines_out:
        if align == "C":
            c.setFont(FONT_B, 10)
            c.drawCentredString(PAGE_W / 2, y, text)
            c.setFont(FONT, 10)
        else:
            c.drawString(LEFT, y, text)
        y -= LEADING_OLD

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()
