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
    # Keep the existing design coordinates landscape (105×70), but rotate
    # them onto the physical portrait page used by the label feed.
    W, H = 105 * mm, 70 * mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(H, W))
    c.translate(0, W)
    c.rotate(-90)

    # STOP number — hero element
    c.setFont("Helvetica-Bold", 9)
    c.drawString(6 * mm, H - 9 * mm, "STOP")
    c.setFont("Helvetica-Bold", 52)
    c.drawString(5 * mm, H - 30 * mm, str(stop_number if stop_number is not None else "-"))

    # Right column: customer / route / date / driver
    cust = (invoice.customer_name or "")
    c.setFont("Helvetica-Bold", 13 if len(cust) <= 26 else 10)
    c.drawRightString(W - 6 * mm, H - 9 * mm, cust[:40])
    c.setFont("Helvetica", 8)
    date_str = delivery_date.strftime("%d/%m/%Y") if delivery_date else ""
    c.drawRightString(W - 6 * mm, H - 15 * mm, f"Route {route_name or '-'} · {date_str}")
    c.drawRightString(W - 6 * mm, H - 20 * mm, str(driver_name or ""))

    # Order X of Y
    if stop_index and stop_total and stop_total > 1:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(6 * mm, H - 38 * mm, f"Order {stop_index} of {stop_total}")

    # COOLER BOX marker
    if has_cooler:
        c.setFillColorRGB(0, 0, 0)
        c.rect(W - 40 * mm, H - 30 * mm, 34 * mm, 7 * mm, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(W - 23 * mm, H - 25.5 * mm, "COOLER BOX")
        c.setFillColorRGB(0, 0, 0)

    # Code 39 barcode + human-readable number
    bc = code39.Standard39(invoice.invoice_no, barHeight=13 * mm, stop=1, checksum=0)
    bc.drawOn(c, (W - bc.width) / 2, 12 * mm)
    c.setFont("Courier", 10)
    c.drawCentredString(W / 2, 7 * mm, invoice.invoice_no)

    c.showPage()
    c.save()
    return buf.getvalue()
