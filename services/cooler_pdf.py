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
    """Render a QR code as a vector drawing."""
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

    c.setFont("Helvetica-Bold", 14)
    cy -= 14
    route_label = box.get("route_label") or f"Route {box.get('route_id') or '-'}"
    c.drawCentredString(page_w / 2, cy, route_label)

    cy -= 14
    c.setFont("Helvetica", 11)
    c.drawCentredString(page_w / 2, cy, str(box.get("delivery_date") or "-"))

    cy -= 8
    c.line(margin, cy, page_w - margin, cy)

    cy -= 36
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(page_w / 2, cy, f"BOX {box.get('box_no') or '-'}")

    cy -= 22
    c.setFont("Helvetica", 14)
    c.drawCentredString(page_w / 2, cy, _stop_range_text(box))

    cy -= 16
    c.line(margin, cy, page_w - margin, cy)

    cy -= 26
    c.setFillColor(colors.HexColor("#0b5ed7"))
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(page_w / 2, cy, "SENSITIVE ITEMS")
    cy -= 22
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(page_w / 2, cy, "KEEP COOL")
    c.setFillColor(colors.black)

    qr_size = min(40 * mm, page_w - 2 * margin - 10 * mm)
    qr_x = (page_w - qr_size) / 2
    qr_y = margin + 14
    _draw_qr(c, box.get("id"), qr_x, qr_y, qr_size)

    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, margin + 4, f"cooler_box_id={box.get('id')}")


def render_cooler_label(box, size="thermal"):
    """Return PDF bytes for the cooler box label."""
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


# ---------------------------------------------------------------------------
# Manifest rendering — redesigned for warehouse clarity
# ---------------------------------------------------------------------------

def _fmt_qty(v):
    if v is None:
        return "0"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.3f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_unit(it):
    """Short pack descriptor — e.g. '1X30'."""
    pack = (it.get("pack") or "").strip()
    unit = (it.get("unit_type") or "").strip()
    return pack or unit or ""


def _stop_key(it):
    seq = it.get("delivery_sequence")
    seq_n = float(seq) if seq is not None else 9_999_999.0
    return (seq_n, it.get("customer_code") or "", it.get("invoice_no") or "")


# Column widths: Code 72 | Description 349 | Qty 60  (total ~481pt)
_MAN_COLW = (72.0, 349.0, 60.0)
_MAN_HEADERS = ("Code", "Description", "Qty")

_NAV      = colors.HexColor("#0d1b2a")
_ACCENT   = colors.HexColor("#0b5ed7")
_STOP_BG  = colors.HexColor("#f0f4ff")
_ALT_ROW  = colors.HexColor("#f8f9fa")
_MUTED    = colors.HexColor("#6c757d")
_DIVIDER  = colors.HexColor("#dee2e6")


def _draw_manifest_header(c, page_w, page_h, box, generated_at):
    """Full-width navy band header."""
    margin = 15 * mm
    band_h = 28 * mm
    band_y = page_h - margin - band_h

    c.setFillColor(_NAV)
    c.rect(margin, band_y, page_w - 2 * margin, band_h, fill=1, stroke=0)

    qr_size = 22 * mm
    qr_x = page_w - margin - qr_size - 4
    qr_y = band_y + (band_h - qr_size) / 2
    box_id = box.get("id")
    if box_id:
        _draw_qr(c, box_id, qr_x, qr_y, qr_size)

    text_x = margin + 8
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 32)
    c.drawString(text_x, band_y + band_h - 26, f"BOX  {box.get('box_no') or '-'}")

    route_label = box.get("route_label") or f"Route {box.get('route_id') or '-'}"
    stop_text = _stop_range_text(box)
    c.setFont("Helvetica", 11)
    c.setFillColor(colors.HexColor("#adb5bd"))
    c.drawString(text_x, band_y + band_h - 42, f"{route_label}   /   {stop_text}")

    date_str = str(box.get("delivery_date") or "-")
    right_edge = qr_x - 8
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(colors.white)
    c.drawRightString(right_edge, band_y + band_h - 20, date_str)

    btype = str(box.get("box_type_name") or "")
    if btype:
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#adb5bd"))
        c.drawRightString(right_edge, band_y + band_h - 34, btype.upper())

    strip_y = band_y - 10
    meta_parts = []
    driver = box.get("driver_name") or ""
    if driver:
        meta_parts.append(f"Driver: {driver}")
    meta_parts.append(f"Status: {(box.get('status') or '').upper()}")
    total_items = box.get("total_items")
    if total_items:
        meta_parts.append(f"Items: {total_items}")
    if generated_at:
        ts = str(generated_at)[:16].replace("T", "  ")
        meta_parts.append(f"Printed: {ts}")

    c.setFont("Helvetica", 9)
    c.setFillColor(_MUTED)
    c.drawString(margin, strip_y, "   /   ".join(meta_parts))

    rule_y = strip_y - 6
    c.setStrokeColor(_ACCENT)
    c.setLineWidth(1.5)
    c.line(margin, rule_y, page_w - margin, rule_y)
    c.setLineWidth(1)
    c.setStrokeColor(colors.black)
    c.setFillColor(colors.black)

    return rule_y - 10


def _draw_stop_header(c, x0, y, total_w, stop_seq, customer, invoice_nos):
    """Stop banner with left accent stripe."""
    bar_w = 4
    bg_h = 26

    c.setFillColor(_STOP_BG)
    c.rect(x0, y - bg_h + 4, total_w, bg_h, fill=1, stroke=0)
    c.setFillColor(_ACCENT)
    c.rect(x0, y - bg_h + 4, bar_w, bg_h, fill=1, stroke=0)

    c.setFillColor(_ACCENT)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(x0 + bar_w + 8, y - 14, f"STOP  {_fmt_seq(stop_seq)}")

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0 + bar_w + 8 + 68, y - 14,
                 (customer or "(no customer)").upper())

    if invoice_nos:
        c.setFillColor(_MUTED)
        c.setFont("Helvetica", 8)
        c.drawRightString(x0 + total_w - 4, y - 14,
                          "inv. " + "  /  ".join(invoice_nos))

    c.setFillColor(colors.black)
    return y - bg_h + 2


def _man_row_height(it):
    desc = str(it.get("item_name") or "").strip() or "(no description)"
    pack = _fmt_unit(it)
    desc_lines = _wrap_desc(desc, "Helvetica-Bold", 10, _MAN_COLW[1] - 8)
    line_h = 13
    extra_h = 11 if pack else 0
    return max(line_h + 4, line_h * len(desc_lines) + extra_h + 6), desc_lines


def _draw_man_col_headers(c, x0, y, total_w):
    c.setStrokeColor(_DIVIDER)
    c.setLineWidth(0.5)
    c.line(x0, y - 2, x0 + total_w, y - 2)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(_MUTED)
    cx = x0 + 4
    for h, w in zip(_MAN_HEADERS, _MAN_COLW):
        if h == "Qty":
            c.drawRightString(cx + w - 4, y - 11, h)
        else:
            c.drawString(cx, y - 11, h)
        cx += w
    c.line(x0, y - 14, x0 + total_w, y - 14)
    c.setFillColor(colors.black)
    c.setLineWidth(1)
    return y - 18


def _draw_man_item_row(c, x0, y, it, shade=False):
    row_h, desc_lines = _man_row_height(it)
    pack = _fmt_unit(it)

    if shade:
        c.setFillColor(_ALT_ROW)
        c.rect(x0, y - row_h, sum(_MAN_COLW), row_h, fill=1, stroke=0)

    cx = [x0]
    for w in _MAN_COLW[:-1]:
        cx.append(cx[-1] + w)

    top_y = y - 14

    c.setFont("Helvetica", 8)
    c.setFillColor(_MUTED)
    c.drawString(cx[0] + 4, top_y, str(it.get("item_code") or "-"))

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.black)
    dy = top_y
    for ln in desc_lines:
        c.drawString(cx[1] + 4, dy, ln)
        dy -= 13

    if pack:
        c.setFont("Helvetica", 8)
        c.setFillColor(_MUTED)
        c.drawString(cx[1] + 4, dy, pack)

    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(colors.black)
    c.drawRightString(cx[2] + _MAN_COLW[2] - 6, top_y - 2,
                      _fmt_qty(it.get("expected_qty")))

    c.setStrokeColor(_DIVIDER)
    c.setLineWidth(0.5)
    c.line(x0, y - row_h, x0 + sum(_MAN_COLW), y - row_h)
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    return y - row_h


def _draw_manifest_footer(c, page_w, page_no, is_last=False):
    margin = 15 * mm
    fy = 10 * mm

    c.setStrokeColor(_DIVIDER)
    c.setLineWidth(0.5)
    c.line(margin, fy + 12, page_w - margin, fy + 12)
    c.setLineWidth(1)

    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(_ACCENT)
    c.drawString(margin, fy + 4, "SENSITIVE ITEMS -- KEEP COOL")

    c.setFont("Helvetica", 8)
    c.setFillColor(_MUTED)
    c.drawRightString(page_w - margin, fy + 4, f"Page {page_no}")

    if is_last:
        sig_y = fy + 30
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.black)
        c.drawString(margin, sig_y, "Packed by: ________________________")
        c.drawString(margin + 175, sig_y, "Driver: ________________________")
        c.drawRightString(page_w - margin, sig_y, "Date: ___________")

    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)


def _draw_manifest_table(c, page_w, page_h, items, y_start, box=None):
    """Manifest body — clean 3-column layout grouped by stop."""
    margin = 15 * mm
    x0 = margin
    total_w = page_w - 2 * margin
    y = y_start
    page_no = 1
    bottom_limit = margin + 44

    if not items:
        c.setFont("Helvetica-Oblique", 10)
        c.setFillColor(_MUTED)
        c.drawString(x0, y - 12, "(this box is empty)")
        c.setFillColor(colors.black)
        _draw_manifest_footer(c, page_w, page_no, is_last=True)
        return y - 14

    items_sorted = sorted(items, key=_stop_key)

    def _group_key(it):
        return (it.get("delivery_sequence"), it.get("customer_code"),
                it.get("customer_name"))

    groups = [(k, list(g)) for k, g in groupby(items_sorted, key=_group_key)]

    for g_idx, ((seq, _ccode, cname), grp) in enumerate(groups):
        invoice_nos = list(dict.fromkeys(
            it.get("invoice_no") or "" for it in grp if it.get("invoice_no")
        ))
        row_heights = [_man_row_height(it)[0] for it in grp]
        first_block = 30 + 18 + (row_heights[0] if row_heights else 0)

        if y - first_block < bottom_limit:
            _draw_manifest_footer(c, page_w, page_no, is_last=False)
            c.showPage()
            page_no += 1
            y = page_h - margin

        y = _draw_stop_header(c, x0, y, total_w, seq, cname, invoice_nos)
        y = _draw_man_col_headers(c, x0, y, total_w)

        for row_idx, (it, rh) in enumerate(zip(grp, row_heights)):
            if y - rh < bottom_limit:
                _draw_manifest_footer(c, page_w, page_no, is_last=False)
                c.showPage()
                page_no += 1
                y = page_h - margin
                y = _draw_stop_header(
                    c, x0, y, total_w, seq,
                    f"{cname} (cont.)" if cname else "(cont.)",
                    invoice_nos,
                )
                y = _draw_man_col_headers(c, x0, y, total_w)

            y = _draw_man_item_row(c, x0, y, it, shade=(row_idx % 2 == 1))

        y -= 10

    _draw_manifest_footer(c, page_w, page_no, is_last=True)
    return y


def render_cooler_manifest(box, items, generated_at=""):
    """A4 portrait manifest for a single cooler box."""
    buf = BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    y = _draw_manifest_header(c, page_w, page_h, box, generated_at)
    _draw_manifest_table(c, page_w, page_h, items, y, box=box)
    c.showPage()
    c.save()
    return buf.getvalue()


def render_route_manifest(route_id, delivery_date, boxes_with_items,
                          generated_at=""):
    """A4 portrait combined manifest — one page per active box.

    Cancelled boxes are silently excluded.
    """
    active_boxes = [
        (box, items) for box, items in boxes_with_items
        if (box.get("status") or "").lower() != "cancelled"
    ]

    buf = BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)

    if not active_boxes:
        margin = 15 * mm
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.black)
        c.drawString(margin, page_h - margin,
                     "No active cooler boxes on this route.")
        c.showPage()
        c.save()
        return buf.getvalue()

    for idx, (box, items) in enumerate(active_boxes):
        if idx > 0:
            c.showPage()
        y = _draw_manifest_header(c, page_w, page_h, box, generated_at)
        _draw_manifest_table(c, page_w, page_h, items, y, box=box)

    c.showPage()
    c.save()
    return buf.getvalue()
