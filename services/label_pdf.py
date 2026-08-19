"""105x70 mm box label PDF for the Deli 750W label printer.

Layout: big STOP number (delivery sequence), customer name, route/date/
driver, "Order X of Y" when the stop holds several invoices, a bold
COOLER BOX marker when the order has chilled items in a cooler box, and
a scannable Code 39 barcode of the invoice number.
"""
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code39


def build_box_label_pdf(invoice, stop_number=None, route_name=None,
                        driver_name=None, delivery_date=None,
                        stop_index=None, stop_total=None,
                        has_cooler=False) -> bytes:
    # 105×70 mm landscape page, drawn normally — no translate/rotate; any
    # rotation needed for the media is handled by the printer driver/agent.
    # Thermal printers cannot print the outer ~4-5 mm: keep a 5 mm margin.
    W, H = 105 * mm, 70 * mm
    M = 5 * mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    # STOP number — hero element
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M, H - M - 9, "STOP")
    c.setFont("Helvetica-Bold", 44)
    c.drawString(M - 1, H - M - 46, str(stop_number if stop_number is not None else "-"))

    # Right column: customer / route / date / driver
    cust = (invoice.customer_name or "")
    c.setFont("Helvetica-Bold", 12 if len(cust) <= 26 else 9)
    c.drawRightString(W - M, H - M - 7, cust[:40])
    c.setFont("Helvetica", 8)
    date_str = delivery_date.strftime("%d/%m/%Y") if delivery_date else ""
    c.drawRightString(W - M, H - M - 19, f"Route {route_name or '-'} · {date_str}")
    c.drawRightString(W - M, H - M - 29, str(driver_name or ""))

    # Order X of Y
    if stop_index and stop_total and stop_total > 1:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(M, H - M - 60, f"Order {stop_index} of {stop_total}")

    # COOLER BOX marker
    if has_cooler:
        c.setFillColorRGB(0, 0, 0)
        c.rect(W - M - 34 * mm, H - M - 42, 34 * mm, 7 * mm, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(W - M - 17 * mm, H - M - 40, "COOLER BOX")
        c.setFillColorRGB(0, 0, 0)

    # Code 39 barcode + human-readable number
    bc = code39.Standard39(invoice.invoice_no, barHeight=12 * mm, stop=1, checksum=0)
    bc.drawOn(c, (W - bc.width) / 2, M + 14)
    c.setFont("Courier", 9)
    c.drawCentredString(W / 2, M + 2, invoice.invoice_no)

    c.showPage()
    c.save()
    return buf.getvalue()
