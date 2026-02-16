from io import BytesIO
from decimal import Decimal
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit

def money(v):
    try:
        return f"{Decimal(str(v)):.2f}"
    except Exception:
        return "0.00"

def build_thermal_receipt_pdf(receipt):
    """
    receipt: dict with keys like:
      company_name, company_vat, receipt_no, date_str,
      customer_name, customer_code,
      lines: [{name, qty, price, total}],
      totals: {subtotal, vat, total},
      notes (optional)
    """
    buf = BytesIO()

    # SAFE printable width for 80mm printers is usually ~72mm.
    PAGE_W = 72 * mm
    LEFT = 2.5 * mm
    RIGHT = 2.5 * mm
    TOP = 3 * mm
    BOT = 3 * mm

    FONT = "Courier"
    FONT_B = "Courier-Bold"
    FS = 10
    LEADING = 12  # line height in points

    # --- 1) Build all text lines first (so we can compute dynamic height) ---
    lines_out = []

    def add_center(text):
        lines_out.append(("C", text))

    def add_left(text):
        lines_out.append(("L", text))

    def add_rule():
        lines_out.append(("L", "-" * 42))

    def wrap_left(text, max_w_pts):
        # Wrap text to available width in points
        wrapped = simpleSplit(text, FONT, FS, max_w_pts)
        for w in wrapped:
            add_left(w)

    avail_w = PAGE_W - LEFT - RIGHT

    # Header
    add_center(receipt.get("company_name", ""))
    if receipt.get("company_vat"):
        add_center(f"VAT: {receipt['company_vat']}")
    add_rule()
    add_left(f"Receipt: {receipt.get('receipt_no','')}")
    add_left(f"Date:    {receipt.get('date_str','')}")
    add_rule()

    # Customer
    cust = receipt.get("customer_name", "")
    code = receipt.get("customer_code", "")
    if cust or code:
        wrap_left(f"Customer: {cust} ({code})".strip(), avail_w)

    add_rule()

    # Items header (fixed columns)
    add_left("QTY   PRICE     TOTAL")
    add_rule()

    # Lines
    for ln in receipt.get("lines", []):
        name = (ln.get("name") or "").strip()
        qty = ln.get("qty", 0)
        price = money(ln.get("price", 0))
        total = money(ln.get("total", 0))

        # Item name wrapped
        if name:
            wrap_left(name, avail_w)

        # Numeric row (monospace aligned)
        qty_s = f"{qty}".rjust(3)
        price_s = f"{price}".rjust(7)
        total_s = f"{total}".rjust(8)
        add_left(f"{qty_s}  {price_s}  {total_s}")

        add_left("")  # small gap

    add_rule()

    totals = receipt.get("totals", {}) or {}
    add_left(f"SUBTOTAL: {money(totals.get('subtotal', 0))}".rjust(42))
    add_left(f"VAT:      {money(totals.get('vat', 0))}".rjust(42))
    add_left(f"TOTAL:    {money(totals.get('total', 0))}".rjust(42))
    add_rule()

    if receipt.get("notes"):
        wrap_left(receipt["notes"], avail_w)
        add_rule()

    add_center("THANK YOU")

    # --- 2) Compute dynamic page height ---
    total_lines = len(lines_out) + 2
    PAGE_H = TOP + BOT + (total_lines * LEADING)

    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    y = PAGE_H - TOP
    c.setFont(FONT, FS)

    for align, text in lines_out:
        if align == "C":
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
