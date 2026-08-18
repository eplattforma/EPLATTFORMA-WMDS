"""A4 delivery-slip PDF — mirrors templates/print_picking_report.html.

Used by the print bridge so the Konica gets the same slip the browser
prints: STOP hero, customer, route/date/driver, Code 39 barcode, flat
item list (code · name · location · unit · qty with short-picks), and
pieces / kg / Boxes ___ totals.
"""
from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape as _esc

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Flowable)
from reportlab.graphics.barcode import code39


class _Barcode39(Flowable):
    def __init__(self, value, bar_height=14 * mm):
        super().__init__()
        self.bc = code39.Standard39(value, barHeight=bar_height, stop=1, checksum=0)
        self.width = self.bc.width
        self.height = self.bc.height

    def wrap(self, availWidth, availHeight):
        self._avail = availWidth
        return availWidth, self.height

    def draw(self):
        x = (self._avail - self.bc.width) / 2
        self.bc.drawOn(self.canv, max(x, 0), 0)


def build_delivery_slip_pdf(invoice, slip_items, route_info=None,
                            stop_index=None, stop_total=None,
                            has_cooler=False) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=8 * mm, bottomMargin=8 * mm)
    body = []

    s_small = ParagraphStyle('small', fontName='Helvetica', fontSize=9, leading=11)
    s_right = ParagraphStyle('right', parent=s_small, alignment=2, fontSize=10, leading=13)
    s_cust = ParagraphStyle('cust', fontName='Helvetica-Bold', fontSize=17, leading=19)
    s_stopnum = ParagraphStyle('stopnum', fontName='Helvetica-Bold', fontSize=46, leading=46)

    stop_txt = '—'
    if route_info and route_info.get('stop_seq') is not None:
        seq = route_info['stop_seq']
        stop_txt = str(int(seq)) if seq == int(seq) else str(seq)
    date_str = ''
    if route_info and route_info.get('delivery_date'):
        date_str = route_info['delivery_date'].strftime('%d/%m/%Y')
    order_of = (f"Order {stop_index} of {stop_total}"
                if stop_index and stop_total and stop_total > 1 else '')
    cooler_tag = ('<font color="white"> COOLER BOX </font>' if has_cooler else '')

    left = Paragraph(
        f'<font size="9"><b>STOP</b></font><br/>'
        f'<font size="46"><b>{stop_txt}</b></font>'
        + (f'<br/><font size="12"><b>{order_of}</b></font>' if order_of else ''),
        ParagraphStyle('l', fontName='Helvetica-Bold', fontSize=9, leading=50))
    right_lines = [f"<b>Route {_esc(str(route_info['route_name']))}</b>" if route_info else "<b>Route —</b>",
                   date_str,
                   f"Driver: {_esc(str(route_info['driver_name'] or '—'))}" if route_info else "Driver: —"]
    right = Paragraph('<br/>'.join(x for x in right_lines if x), s_right)
    body.append(Table([[left, right]], colWidths=[100 * mm, 90 * mm],
                      style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')])))
    if has_cooler:
        tag = Table([[Paragraph('<b><font color="white">COOLER BOX</font></b>',
                                ParagraphStyle('t', fontName='Helvetica-Bold',
                                               fontSize=11, alignment=1))]],
                    colWidths=[40 * mm])
        tag.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.black),
                                 ('TOPPADDING', (0, 0), (-1, -1), 3),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        body.append(tag)
        body.append(Spacer(1, 2 * mm))

    body.append(Paragraph(_esc(invoice.customer_name or ''), s_cust))
    code = invoice.customer_code_365 or invoice.customer_code or ''
    if code:
        body.append(Paragraph(f'<font face="Courier" size="9">{_esc(str(code))}</font>', s_small))
    body.append(Spacer(1, 4 * mm))

    body.append(_Barcode39(invoice.invoice_no))
    body.append(Paragraph(f'<font face="Courier" size="10">{invoice.invoice_no}</font>',
                          ParagraphStyle('bc', alignment=1)))
    body.append(Spacer(1, 4 * mm))

    s_cell = ParagraphStyle('cell', fontName='Helvetica', fontSize=9, leading=11)
    s_code = ParagraphStyle('codec', fontName='Courier', fontSize=8, leading=10,
                            textColor=colors.HexColor('#444444'))
    rows = [[Paragraph('<b>Code</b>', s_small), Paragraph('<b>Item</b>', s_small),
             Paragraph('<b>Location</b>', s_small), Paragraph('<b>Unit</b>', s_small),
             Paragraph('<b>Qty</b>', ParagraphStyle('q', parent=s_small, alignment=2))]]
    for it in slip_items:
        name = _esc(it['item_name'] or '')
        if it.get('is_chilled'):
            name += ' <font size="7" color="#0C447C">[CHILLED]</font>'
        if it['qty_picked'] < it['qty']:
            qty = (f'<font color="#A32D2D"><b>{it["qty_picked"]} / {it["qty"]}</b></font>')
        else:
            qty = str(it['qty_picked'])
        rows.append([Paragraph(_esc(it['item_code'] or ''), s_code),
                     Paragraph(name, s_cell),
                     Paragraph(_esc(it.get('location') or ''), s_cell),
                     Paragraph(_esc(it.get('unit_label') or ''), s_cell),
                     Paragraph(qty, ParagraphStyle('qr', parent=s_cell, alignment=2))])
    tbl = Table(rows, colWidths=[28 * mm, 74 * mm, 38 * mm, 28 * mm, 22 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.black),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, colors.HexColor('#cccccc')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    body.append(tbl)

    body.append(Spacer(1, 3 * mm))
    totals = Paragraph(
        f'<b>{int(invoice.total_items or 0)}</b> pieces &nbsp;&nbsp;'
        f'<b>{round(float(invoice.total_weight or 0), 1)}</b> kg &nbsp;&nbsp;'
        f'Boxes <b>_____</b>',
        ParagraphStyle('tot', fontName='Helvetica', fontSize=11, alignment=2))
    body.append(totals)
    footer = f"Printed {datetime.now().strftime('%d/%m/%y %H:%M')}"
    if invoice.assigned_to:
        footer = f"Picked by {_esc(invoice.assigned_to)} · " + footer
    body.append(Spacer(1, 4 * mm))
    body.append(Paragraph(f'<font size="7" color="#666666">{footer}</font>',
                          ParagraphStyle('f', alignment=1)))

    doc.build(body)
    return buf.getvalue()
