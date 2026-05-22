"""Phase 5: cooler box label + manifest PDF generation.

Reuses ReportLab (already in the dependency set). No new library.

Three renderers:

  - ``render_cooler_label(box, size='thermal')`` — 4x6" thermal default,
    A4 portrait fallback when ``size='a4'``. Includes route, date, box
    number, stop range, "SENSITIVE ITEMS / KEEP COOL" warning, and a
    QR code encoding the cooler_box_id.
  - ``render_cooler_manifest(box, items)`` — A4 portrait manifest table
    sorted by delivery sequence.
  - ``render_route_manifest(route_id, delivery_date, boxes_with_items)``
    — combined manifest, all non-cancelled boxes for the route grouped
    one per page.

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


# ── Palette ────────────────────────────────────────────────────────────────
_NAVY      = colors.HexColor("#0d2b4e")
_BLUE      = colors.HexColor("#0b5ed7")
_LIGHT_BLU = colors.HexColor("#e7f1ff")
_GREEN     = colors.HexColor("#198754")
_GREY_LT   = colors.HexColor("#dddddd")
_GREY_MD   = colors.HexColor("#aaaaaa")
_GREY_DK   = colors.HexColor("#444444")
_MUTED     = colors.HexColor("#6c757d")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _wrap_desc(text, font, size, max_w):
    """Wrap ``text`` to ``max_w``, hard-breaking tokens wider than the column."""
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


def _fmt_seq(v):
    if v is None:
        return "-"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_qty(v):
    if v is None:
        return "0"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.3f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_unit(it):
    unit = (it.get("unit_type") or "").strip()
    pack = (it.get("pack") or "").strip()
    if unit and pack:
        return f"{unit} ({pack})"
    return unit or pack or ""


def _stop_range_text(box):
    first = box.get("first_stop_sequence")
    last  = box.get("last_stop_sequence")
    if first is None and last is None:
        return "Stops: (open box)"
    if first == last or last is None:
        return f"Stop {_fmt_seq(first)}"
    return f"Stops {_fmt_seq(first)} \u2013 {_fmt_seq(last)}"


def _stop_key(it):
    seq = it.get("delivery_sequence")
    seq_n = float(seq) if seq is not None else 9_999_999.0
    return (seq_n, it.get("customer_code") or "", it.get("invoice_no") or "")


def _draw_qr(c, value, x, y, size):
    code = qr.QrCodeWidget(str(value))
    bounds = code.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    if w <= 0 or h <= 0:
        return
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(code)
    renderPDF.draw(d, c, x, y)


# ── Label ───────────────────────────────────────────────────────────────────

THERMAL_W = 100 * mm
THERMAL_H = 150 * mm


def _draw_label_content(c, page_w, page_h, box):
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
    c.setFillColor(_BLUE)
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


# ── Manifest ─────────────────────────────────────────────────────────────────

# Column geometry (A4 portrait, 15 mm margin).
# Item code : 60 pt  (small / muted)
# Description: 295 pt (wrapped; pack info on 2nd line)
# Unit       : 65 pt
# Qty        : 40 pt  (18 pt bold — biggest number on the row)
# Status     : 25 pt  (✓ checkmark)
_COLW    = (60.0, 295.0, 65.0, 40.0, 25.0)
_HEADERS = ("Item code", "Description", "Unit", "Qty", "\u2713")


def _draw_manifest_header_band(c, page_w, page_h, box, driver, generated_at):
    """Dark navy header band with BOX number large, QR in the corner."""
    margin   = 15 * mm
    band_h   = 38 * mm
    band_y   = page_h - band_h
    qr_size  = 28 * mm

    c.setFillColor(_NAVY)
    c.rect(0, band_y, page_w, band_h, fill=1, stroke=0)

    text_x = margin
    text_y = page_h - 10 * mm

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 28)
    c.drawString(text_x, text_y - 8, f"BOX {box.get('box_no') or '-'}")

    c.setFont("Helvetica", 11)
    c.setFillColor(colors.HexColor("#b0c4de"))
    route_label = box.get("route_label") or f"Route {box.get('route_id') or '-'}"
    c.drawString(text_x, text_y - 22, route_label)
    c.drawString(text_x, text_y - 34,
                 f"Date: {box.get('delivery_date') or '-'}   "
                 f"Driver: {driver or '-'}")
    c.drawString(text_x, text_y - 46, _stop_range_text(box))

    status_txt = (box.get("status") or "").upper()
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.HexColor("#ffc107") if status_txt == "OPEN" else colors.HexColor("#6ee7b7"))
    c.drawString(text_x, text_y - 58, f"Status: {status_txt}")

    qr_x = page_w - margin - qr_size
    qr_y = band_y + (band_h - qr_size) / 2
    _draw_qr(c, box.get("id"), qr_x, qr_y, qr_size)
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#b0c4de"))
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 7,
                        f"box id {box.get('id')}")

    c.setFillColor(colors.black)
    return band_y - 6


def _draw_table_header(c, x0, y, total_w):
    c.setFillColor(_BLUE)
    c.rect(x0, y - 4, total_w, 14, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    cx = x0 + 4
    for h, w in zip(_HEADERS, _COLW):
        if h in ("Qty",):
            c.drawRightString(cx + w - 4, y + 2, h)
        elif h == "\u2713":
            c.drawCentredString(cx + w / 2, y + 2, h)
        else:
            c.drawString(cx, y + 2, h)
        cx += w
    c.setFillColor(colors.black)
    return y - 14


def _row_height(it):
    desc       = str(it.get("item_name") or "").strip() or "(no description)"
    pack_str   = _fmt_unit(it)
    desc_lines = _wrap_desc(desc, "Helvetica", 9, _COLW[1] - 8)
    pack_lines = _wrap_desc(pack_str, "Helvetica-Oblique", 8, _COLW[1] - 8) if pack_str else []
    line_h     = 11
    total_lines = len(desc_lines) + (len(pack_lines) + 1 if pack_lines else 0)
    return max(line_h, line_h * total_lines + 2), desc_lines, pack_lines


def _draw_item_row(c, x0, y, it):
    row_h, desc_lines, pack_lines = _row_height(it)
    line_h = 11

    cx = [x0]
    for w in _COLW[:-1]:
        cx.append(cx[-1] + w)

    # Item code — small, muted
    c.setFont("Helvetica", 8)
    c.setFillColor(_MUTED)
    c.drawString(cx[0] + 4, y - line_h + 2, str(it.get("item_code") or "-"))
    c.setFillColor(colors.black)

    # Description (wrapped, top-aligned)
    c.setFont("Helvetica", 9)
    dy = y - line_h + 2
    for ln in desc_lines:
        c.drawString(cx[1] + 4, dy, ln)
        dy -= line_h

    # Pack info as italic subtitle
    if pack_lines:
        dy -= 1
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(_MUTED)
        for ln in pack_lines:
            c.drawString(cx[1] + 6, dy, ln)
            dy -= line_h
        c.setFillColor(colors.black)

    # Unit
    c.setFont("Helvetica", 9)
    c.drawString(cx[2] + 4, y - line_h + 2, (it.get("unit_type") or "").strip() or "-")

    # Qty — 18 pt bold, most important number
    c.setFont("Helvetica-Bold", 18)
    c.drawRightString(cx[3] + _COLW[3] - 4, y - line_h - 2, _fmt_qty(it.get("expected_qty")))

    # Status checkmark
    c.setFont("Helvetica-Bold", 11)
    if (it.get("status") or "").lower() == "picked":
        c.setFillColor(_GREEN)
        c.drawCentredString(cx[4] + _COLW[4] / 2, y - line_h + 2, "\u2713")
        c.setFillColor(colors.black)
    else:
        c.setFillColor(_GREY_MD)
        c.drawCentredString(cx[4] + _COLW[4] / 2, y - line_h + 2, "\u00b7")
        c.setFillColor(colors.black)

    c.setStrokeColor(_GREY_LT)
    c.line(x0, y - row_h, x0 + sum(_COLW), y - row_h)
    c.setStrokeColor(colors.black)
    return y - row_h


def _draw_stop_header(c, x0, y, total_w, stop_seq, customer, invoice_nos):
    c.setFillColor(_LIGHT_BLU)
    c.rect(x0, y - 18, total_w, 22, fill=1, stroke=0)
    c.setFillColor(_BLUE)
    c.setFont("Helvetica-Bold", 12)
    label = f"Stop {_fmt_seq(stop_seq)} \u2014 {customer or '(no customer)'}"
    c.drawString(x0 + 6, y - 12, label)
    c.setFillColor(_GREY_DK)
    c.setFont("Helvetica", 9)
    inv_text = "Invoice: " + ", ".join(invoice_nos)
    c.drawRightString(x0 + total_w - 6, y - 12, inv_text)
    c.setFillColor(colors.black)
    return y - 22


def _draw_footer(c, page_w, page_no, total_pages=None):
    """Footer on every page: SENSITIVE ITEMS — KEEP COOL + page number."""
    margin = 15 * mm
    fy     = 8 * mm
    c.setFillColor(_NAVY)
    c.rect(0, 0, page_w, fy + 4, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(page_w / 2, fy - 4,
                        "\u2603  SENSITIVE ITEMS \u2014 KEEP COOL  \u2603")
    c.setFont("Helvetica", 8)
    page_str = f"Page {page_no}" if total_pages is None else f"Page {page_no} / {total_pages}"
    c.drawRightString(page_w - margin, fy - 4, page_str)
    c.setFillColor(colors.black)


def _draw_signature_block(c, page_w, y):
    """Packed by / Driver / Date signature lines on the last page."""
    margin   = 15 * mm
    line_w   = 55 * mm
    spacing  = 65 * mm
    labels   = ["Packed by", "Driver", "Date"]
    x_starts = [margin, margin + spacing, margin + spacing * 2]

    c.setFont("Helvetica", 9)
    c.setFillColor(_GREY_DK)
    for lbl, xs in zip(labels, x_starts):
        c.drawString(xs, y - 10, lbl + ":")
        c.setStrokeColor(_GREY_DK)
        c.line(xs, y - 22, xs + line_w, y - 22)
    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)


def _draw_manifest_table(c, page_w, page_h, items, y_start,
                         bottom_limit=None, page_no_ref=None):
    """Render item rows.

    Returns (final_y, page_no) so the caller can draw the footer at the right
    position.  ``page_no_ref`` is a mutable list ``[n]`` so page numbers
    accumulate across boxes in the route manifest.
    """
    margin = 15 * mm
    x0     = margin
    total_w = page_w - 2 * margin
    y = y_start
    if bottom_limit is None:
        bottom_limit = margin + 30

    if page_no_ref is None:
        page_no_ref = [1]

    if not items:
        c.setFont("Helvetica-Oblique", 10)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(x0, y - 12, "(this box is empty \u2014 no items assigned)")
        c.setFillColor(colors.black)
        return y - 14, page_no_ref[0]

    items_sorted = sorted(items, key=_stop_key)

    def _group_key(it):
        return (it.get("delivery_sequence"), it.get("customer_code"),
                it.get("customer_name"))

    for (seq, _ccode, cname), grp in groupby(items_sorted, key=_group_key):
        grp = list(grp)
        invoice_nos = []
        seen = set()
        for it in grp:
            inv = it.get("invoice_no") or ""
            if inv and inv not in seen:
                seen.add(inv)
                invoice_nos.append(inv)

        row_heights = [_row_height(it)[0] for it in grp]
        first_block = 22 + 14 + (row_heights[0] if row_heights else 0)
        if y - first_block < bottom_limit:
            _draw_footer(c, page_w, page_no_ref[0])
            c.showPage()
            page_no_ref[0] += 1
            y = page_h - margin

        y = _draw_stop_header(c, x0, y, total_w, seq, cname, invoice_nos)
        y = _draw_table_header(c, x0, y, total_w)
        for it, rh in zip(grp, row_heights):
            if y - rh < bottom_limit:
                _draw_footer(c, page_w, page_no_ref[0])
                c.showPage()
                page_no_ref[0] += 1
                y = page_h - margin
                y = _draw_stop_header(
                    c, x0, y, total_w, seq,
                    f"{cname} (cont.)" if cname else "(cont.)",
                    invoice_nos,
                )
                y = _draw_table_header(c, x0, y, total_w)
            y = _draw_item_row(c, x0, y, it)
        y -= 8

    return y, page_no_ref[0]


def render_cooler_manifest(box, items, generated_at="", driver=""):
    """A4 portrait manifest for a single cooler box."""
    buf    = BytesIO()
    page_w, page_h = A4
    c      = canvas.Canvas(buf, pagesize=A4)
    margin = 15 * mm
    pno    = [1]

    y = _draw_manifest_header_band(c, page_w, page_h, box, driver, generated_at)
    y, _ = _draw_manifest_table(c, page_w, page_h, items, y,
                                bottom_limit=margin + 50, page_no_ref=pno)

    # Signature block on last page
    if y - 40 > margin + 20:
        _draw_signature_block(c, page_w, y - 8)
    _draw_footer(c, page_w, pno[0])
    c.showPage()
    c.save()
    return buf.getvalue()


def render_route_manifest(route_id, delivery_date, boxes_with_items,
                          generated_at="", driver=""):
    """A4 portrait combined manifest covering every non-cancelled cooler box.

    ``boxes_with_items`` is a list of ``(box_dict, items_list)`` tuples
    sorted by box_no.  Cancelled boxes are skipped entirely.
    """
    # Filter out cancelled boxes before rendering
    active_boxes = [
        (box, items) for box, items in boxes_with_items
        if (box.get("status") or "").lower() != "cancelled"
    ]

    buf    = BytesIO()
    page_w, page_h = A4
    c      = canvas.Canvas(buf, pagesize=A4)
    margin = 15 * mm
    pno    = [1]

    if not active_boxes:
        y = page_h - margin
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(_NAVY)
        c.drawString(margin, y, "Route Cooler Manifest")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.black)
        c.drawString(margin, y, f"Route: {route_id}    Delivery date: {delivery_date}")
        y -= 14
        c.setFont("Helvetica-Oblique", 10)
        c.setFillColor(_MUTED)
        c.drawString(margin, y, "(no active cooler boxes on this route)")
        _draw_footer(c, page_w, 1)
        c.showPage()
        c.save()
        return buf.getvalue()

    for idx, (box, items) in enumerate(active_boxes):
        if idx > 0:
            _draw_footer(c, page_w, pno[0])
            c.showPage()
            pno[0] += 1

        y = _draw_manifest_header_band(c, page_w, page_h, box, driver, generated_at)
        last_y, _ = _draw_manifest_table(
            c, page_w, page_h, items, y,
            bottom_limit=margin + 50, page_no_ref=pno,
        )

        # Signature block on the very last box's last page
        if idx == len(active_boxes) - 1:
            if last_y - 40 > margin + 20:
                _draw_signature_block(c, page_w, last_y - 8)

    _draw_footer(c, page_w, pno[0])
    c.showPage()
    c.save()
    return buf.getvalue()
