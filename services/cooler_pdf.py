"""Phase 5: cooler box label + manifest PDF generation.

Reuses ReportLab (already in the dependency set and used by
``utils_pdf.py`` and ``utils/thermal_receipt_pdf.py``). No new library.

Three renderers:

  - ``render_cooler_label(box, size='thermal')`` — 4x6" thermal default,
    A4 portrait fallback when ``size='a4'``. Includes route, date, box
    number, stop range, "SENSITIVE ITEMS / KEEP COOL" warning, and a
    QR code encoding the cooler_box_id.
  - ``render_cooler_manifest(box, items)`` — A4 portrait manifest table
    sorted by delivery sequence.
  - ``render_route_manifest(route_id, delivery_date, boxes_with_items)``
    — combined manifest, all boxes for the route grouped one per page.

Each function returns ``bytes`` so the route handler can wrap it in a
``send_file(BytesIO(...))`` response.
"""
from io import BytesIO

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


THERMAL_W = 100 * mm
THERMAL_H = 150 * mm


def _stop_range_text(box):
    first = box.get("first_stop_sequence")
    last = box.get("last_stop_sequence")
    if first is None and last is None:
        return "Stops: (open box)"
    if first == last or last is None:
        return f"Stop {_fmt_seq(first)}"
    return f"Stops {_fmt_seq(first)} to {_fmt_seq(last)}"


def _fmt_seq(v):
    if v is None:
        return "-"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def _draw_qr(c, value, x, y, size):
    """Render a QR code as a vector drawing centered at (x, y)."""
    code = qr.QrCodeWidget(str(value))
    bounds = code.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    if w <= 0 or h <= 0:
        return
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(code)
    renderPDF.draw(d, c, x, y)


def _draw_label_content(c, page_w, page_h, box):
    """Shared content layout used by both thermal and A4 label sizes."""
    margin = 6 * mm
    cy = page_h - margin

    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)

    # Top: route + date
    c.setFont("Helvetica-Bold", 14)
    cy -= 14
    route_label = box.get("route_label") or f"Route {box.get('route_id') or '-'}"
    c.drawCentredString(page_w / 2, cy, route_label)

    cy -= 14
    c.setFont("Helvetica", 11)
    c.drawCentredString(page_w / 2, cy, str(box.get("delivery_date") or "-"))

    cy -= 8
    c.line(margin, cy, page_w - margin, cy)

    # Middle: BOX N + stop range
    cy -= 36
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(page_w / 2, cy, f"BOX {box.get('box_no') or '-'}")

    cy -= 22
    c.setFont("Helvetica", 14)
    c.drawCentredString(page_w / 2, cy, _stop_range_text(box))

    cy -= 16
    c.line(margin, cy, page_w - margin, cy)

    # KEEP COOL warning
    cy -= 26
    c.setFillColor(colors.HexColor("#0b5ed7"))
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(page_w / 2, cy, "SENSITIVE ITEMS")
    cy -= 22
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(page_w / 2, cy, "KEEP COOL")
    c.setFillColor(colors.black)

    # QR with cooler_box_id
    qr_size = min(40 * mm, page_w - 2 * margin - 10 * mm)
    qr_x = (page_w - qr_size) / 2
    qr_y = margin + 14
    _draw_qr(c, box.get("id"), qr_x, qr_y, qr_size)

    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, margin + 4, f"cooler_box_id={box.get('id')}")


def render_cooler_label(box, size="thermal"):
    """Return PDF bytes for the cooler box label.

    ``box`` is a dict with keys: id, route_id, route_label,
    delivery_date, box_no, first_stop_sequence, last_stop_sequence.
    """
    buf = BytesIO()
    if size == "a4":
        page_w, page_h = A4
        c = canvas.Canvas(buf, pagesize=A4)
    else:
        page_w, page_h = THERMAL_W, THERMAL_H
        c = canvas.Canvas(buf, pagesize=(THERMAL_W, THERMAL_H))
    _draw_label_content(c, page_w, page_h, box)
    c.showPage()
    c.save()
    return buf.getvalue()


def _draw_manifest_header(c, page_w, page_h, box, generated_at):
    margin = 15 * mm
    y = page_h - margin
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Cooler Box Manifest")
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Route: {box.get('route_label') or box.get('route_id') or '-'}")
    y -= 12
    c.drawString(margin, y, f"Delivery date: {box.get('delivery_date') or '-'}")
    y -= 12
    c.drawString(margin, y, f"Box no: {box.get('box_no') or '-'}    Status: {box.get('status') or '-'}")
    y -= 12
    c.drawString(margin, y, f"Generated: {generated_at}")
    y -= 8
    c.line(margin, y, page_w - margin, y)
    y -= 14
    return y


def _draw_manifest_table(c, page_w, page_h, items, y_start):
    margin = 15 * mm
    y = y_start
    headers = ("Stop", "Customer", "Invoice", "Item code", "Item name", "Qty")
    col_x = (
        margin,
        margin + 18 * mm,
        margin + 70 * mm,
        margin + 110 * mm,
        margin + 138 * mm,
        page_w - margin - 12 * mm,
    )
    c.setFont("Helvetica-Bold", 9)
    for h, x in zip(headers, col_x):
        c.drawString(x, y, h)
    y -= 10
    c.line(margin, y, page_w - margin, y)
    y -= 12

    c.setFont("Helvetica", 9)
    for it in items:
        if y < margin + 30:
            c.showPage()
            y = page_h - margin
            c.setFont("Helvetica-Bold", 9)
            for h, x in zip(headers, col_x):
                c.drawString(x, y, h)
            y -= 10
            c.line(margin, y, page_w - margin, y)
            y -= 12
            c.setFont("Helvetica", 9)
        cells = (
            _fmt_seq(it.get("delivery_sequence")),
            _truncate(it.get("customer_name") or it.get("customer_code") or "", 32),
            it.get("invoice_no") or "",
            it.get("item_code") or "",
            _truncate(it.get("item_name") or "", 18),
            _fmt_qty(it.get("expected_qty")),
        )
        for txt, x in zip(cells, col_x):
            c.drawString(x, y, str(txt))
        y -= 11
    return y


def _truncate(s, n):
    s = str(s or "")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _fmt_qty(v):
    if v is None:
        return "0"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.3f}"
    except (TypeError, ValueError):
        return str(v)


def render_cooler_manifest(box, items, generated_at=""):
    """A4 portrait manifest for a single cooler box."""
    buf = BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    y = _draw_manifest_header(c, page_w, page_h, box, generated_at)
    _draw_manifest_table(c, page_w, page_h, items, y)
    c.showPage()
    c.save()
    return buf.getvalue()


def render_route_manifest(route_id, delivery_date, boxes_with_items, generated_at=""):
    """A4 portrait combined manifest covering every cooler box on a route.

    ``boxes_with_items`` is a list of ``(box_dict, items_list)`` tuples
    sorted by box_no.
    """
    buf = BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)

    margin = 15 * mm
    y = page_h - margin
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, "Route Cooler Manifest")
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Route: {route_id}    Delivery date: {delivery_date}")
    y -= 12
    c.drawString(margin, y, f"Boxes: {len(boxes_with_items)}    Generated: {generated_at}")
    y -= 8
    c.line(margin, y, page_w - margin, y)
    y -= 14

    if not boxes_with_items:
        c.setFont("Helvetica-Oblique", 10)
        c.drawString(margin, y, "(no cooler boxes on this route)")
        c.showPage()
        c.save()
        return buf.getvalue()

    for idx, (box, items) in enumerate(boxes_with_items):
        if idx > 0:
            c.showPage()
            y = page_h - margin
        else:
            y -= 4
        y_after_header = _draw_manifest_header(c, page_w, page_h, box, generated_at)
        _draw_manifest_table(c, page_w, page_h, items, y_after_header)
    c.showPage()
    c.save()
    return buf.getvalue()
