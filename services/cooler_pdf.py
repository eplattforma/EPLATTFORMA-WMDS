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
from itertools import groupby

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def _wrap_desc(text, font, size, max_w):
    """Wrap ``text`` to ``max_w``, hard-breaking single tokens that are
    themselves wider than the column (``simpleSplit`` is word-based and
    leaves long unbroken tokens overflowing).
    """
    text = str(text or "").strip()
    if not text:
        return [""]
    lines = []
    for raw in simpleSplit(text, font, size, max_w) or [""]:
        if stringWidth(raw, font, size) <= max_w:
            lines.append(raw)
            continue
        # Hard-break a too-long line character-by-character.
        cur = ""
        for ch in raw:
            if stringWidth(cur + ch, font, size) <= max_w:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [""]


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


def _fmt_qty(v):
    if v is None:
        return "0"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.3f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_unit(it):
    """Compose a short unit/pack descriptor — e.g. 'Box (1X30)' or 'Pcs'."""
    unit = (it.get("unit_type") or "").strip()
    pack = (it.get("pack") or "").strip()
    if unit and pack:
        return f"{unit} ({pack})"
    return unit or pack or "-"


def _stop_key(it):
    """Sort/group key: (delivery_sequence, customer_code, invoice_no)."""
    seq = it.get("delivery_sequence")
    seq_n = float(seq) if seq is not None else 9_999_999.0
    return (seq_n, it.get("customer_code") or "", it.get("invoice_no") or "")


# Column geometry for the per-customer item table (A4 portrait, 15mm margin).
# Total usable width ~565pt - 2*42pt = ~481pt.
#   Item code    : 70pt
#   Description  : 280pt (wrapped)
#   Unit         : 70pt
#   Qty          : 35pt  (right-aligned)
#   Status       : 26pt  (✓ when picked)
_COLW = (70.0, 280.0, 70.0, 35.0, 26.0)
_HEADERS = ("Item code", "Description", "Unit", "Qty", "✓")


def _draw_table_header(c, x0, y, total_w):
    """Draw the column headers + an underline."""
    c.setFillColor(colors.HexColor("#0b5ed7"))
    c.rect(x0, y - 4, total_w, 14, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    cx = x0 + 4
    for h, w in zip(_HEADERS, _COLW):
        if h in ("Qty",):
            c.drawRightString(cx + w - 4, y + 2, h)
        elif h == "✓":
            c.drawCentredString(cx + w / 2, y + 2, h)
        else:
            c.drawString(cx, y + 2, h)
        cx += w
    c.setFillColor(colors.black)
    return y - 14


def _row_height(it):
    """Compute the rendered height of one item row, including wrap."""
    desc = str(it.get("item_name") or "").strip() or "(no description)"
    desc_lines = _wrap_desc(desc, "Helvetica", 9, _COLW[1] - 8)
    line_h = 11
    return max(line_h, line_h * len(desc_lines) + 2), desc_lines


def _draw_item_row(c, x0, y, it):
    """Draw one item row; description wraps to multiple lines if needed.

    Returns the new ``y`` after the row, accounting for wrapped lines.
    """
    row_h, desc_lines = _row_height(it)
    line_h = 11

    # Column x positions
    cx = [x0]
    for w in _COLW[:-1]:
        cx.append(cx[-1] + w)

    c.setFont("Helvetica", 9)
    # Item code
    c.drawString(cx[0] + 4, y - line_h + 2, str(it.get("item_code") or "-"))
    # Description (wrapped, top-aligned)
    dy = y - line_h + 2
    for ln in desc_lines:
        c.drawString(cx[1] + 4, dy, ln)
        dy -= line_h
    # Unit
    c.drawString(cx[2] + 4, y - line_h + 2, _fmt_unit(it))
    # Qty (right-aligned, bold)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(cx[3] + _COLW[3] - 4, y - line_h + 2, _fmt_qty(it.get("expected_qty")))
    # Status checkmark
    c.setFont("Helvetica-Bold", 11)
    if (it.get("status") or "").lower() == "picked":
        c.setFillColor(colors.HexColor("#198754"))
        c.drawCentredString(cx[4] + _COLW[4] / 2, y - line_h + 2, "✓")
        c.setFillColor(colors.black)
    else:
        c.setFillColor(colors.HexColor("#aaaaaa"))
        c.drawCentredString(cx[4] + _COLW[4] / 2, y - line_h + 2, "·")
        c.setFillColor(colors.black)

    # Light divider between rows
    c.setStrokeColor(colors.HexColor("#dddddd"))
    c.line(x0, y - row_h, x0 + sum(_COLW), y - row_h)
    c.setStrokeColor(colors.black)
    return y - row_h


def _draw_stop_header(c, x0, y, total_w, stop_seq, customer, invoice_nos):
    """Draw the per-stop / per-customer banner."""
    c.setFillColor(colors.HexColor("#e7f1ff"))
    c.rect(x0, y - 18, total_w, 22, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0b5ed7"))
    c.setFont("Helvetica-Bold", 12)
    label = f"Stop {_fmt_seq(stop_seq)} — {customer or '(no customer)'}"
    c.drawString(x0 + 6, y - 12, label)
    c.setFillColor(colors.HexColor("#444444"))
    c.setFont("Helvetica", 9)
    inv_text = "Invoice: " + ", ".join(invoice_nos)
    c.drawRightString(x0 + total_w - 6, y - 12, inv_text)
    c.setFillColor(colors.black)
    return y - 22


def _draw_manifest_table(c, page_w, page_h, items, y_start):
    """Driver-friendly cooler manifest body.

    Items are grouped by ``(delivery_sequence, customer)``. Each group gets
    a coloured stop banner, then a table with full-width wrapped item
    descriptions and unit/pack info. Picked status is shown as a green
    check so the driver can confirm the box contents at a glance.
    """
    margin = 15 * mm
    x0 = margin
    total_w = page_w - 2 * margin
    y = y_start

    # Empty box → friendly note
    if not items:
        c.setFont("Helvetica-Oblique", 10)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(x0, y - 12, "(this box is empty — no items assigned)")
        c.setFillColor(colors.black)
        return y - 14

    # Group by (delivery_sequence, customer_code) so each customer at a
    # given stop gets its own banner. Pre-sort to satisfy ``groupby``.
    items_sorted = sorted(items, key=_stop_key)

    def _group_key(it):
        return (it.get("delivery_sequence"), it.get("customer_code"),
                it.get("customer_name"))

    bottom_limit = margin + 30
    for (seq, _ccode, cname), grp in groupby(items_sorted, key=_group_key):
        grp = list(grp)
        invoice_nos = []
        seen = set()
        for it in grp:
            inv = it.get("invoice_no") or ""
            if inv and inv not in seen:
                seen.add(inv)
                invoice_nos.append(inv)

        # Pre-compute row heights so we never overflow the page.
        row_heights = [_row_height(it)[0] for it in grp]

        # Try to keep the stop banner with at least its first item; otherwise
        # break the page first so the banner doesn't get orphaned at the
        # bottom.
        first_block = 22 + 14 + (row_heights[0] if row_heights else 0)
        if y - first_block < bottom_limit:
            c.showPage()
            y = page_h - margin

        y = _draw_stop_header(c, x0, y, total_w, seq, cname, invoice_nos)
        y = _draw_table_header(c, x0, y, total_w)
        for it, rh in zip(grp, row_heights):
            if y - rh < bottom_limit:
                c.showPage()
                y = page_h - margin
                # Re-stamp the stop banner so the driver knows which
                # customer the continued rows belong to.
                y = _draw_stop_header(
                    c, x0, y, total_w, seq,
                    f"{cname} (cont.)" if cname else "(cont.)",
                    invoice_nos,
                )
                y = _draw_table_header(c, x0, y, total_w)
            y = _draw_item_row(c, x0, y, it)
        y -= 8  # spacing between groups

    return y


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
