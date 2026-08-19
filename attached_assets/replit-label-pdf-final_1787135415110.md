# Replit — final box-label PDF (70×105 media, design rotated to landscape)

**Correction:** the Deli media is **70 mm wide (across the head) × 105 mm long (feed) = portrait**. The label content should read **landscape**, so the design is drawn in 105×70 landscape coordinates and **rotated 90° onto a 70×105 page**. This makes the PDF page match the physical media (so SumatraPDF never rotates it), and the content reads landscape when the label is turned. A 5 mm safe margin keeps everything off the edges.

Replace the body of `build_box_label_pdf` in `services/label_pdf.py` with this (match variable names to yours):

```python
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code39
from io import BytesIO

def build_box_label_pdf(invoice, stop_number, route_name, driver_name,
                        delivery_date, stop_index=None, stop_total=None, has_cooler=False):
    W, H = 105*mm, 70*mm            # DESIGN space (landscape)
    M = 5*mm                        # safe margin so nothing clips
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(70*mm, 105*mm))   # PAGE = portrait, matches the media

    c.translate(0, 105*mm); c.rotate(-90)              # rotate the landscape design onto the page

    # ---- draw in landscape coords: x in [0,105mm], y in [0,70mm] ----
    # STOP (left)
    c.setFont("Helvetica-Bold", 9);  c.drawString(M, H-M-9, "STOP")
    c.setFont("Helvetica-Bold", 44); c.drawString(M-1, H-M-46, str(stop_number or "-"))
    if stop_index and stop_total:
        c.setFont("Helvetica-Bold", 11); c.drawString(M, H-M-60, f"Order {stop_index} of {stop_total}")

    # Customer + route (right)
    c.setFont("Helvetica-Bold", 12); c.drawRightString(W-M, H-M-7, (invoice.customer_name or "")[:26])
    c.setFont("Helvetica", 8)
    c.drawRightString(W-M, H-M-19, f"Route {route_name} - {delivery_date}")
    c.drawRightString(W-M, H-M-29, str(driver_name or ""))
    if has_cooler:
        c.setFillColorRGB(0,0,0); c.rect(W-M-34*mm, H-M-42, 34*mm, 7*mm, fill=1)
        c.setFillColorRGB(1,1,1); c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(W-M-17*mm, H-M-40, "COOLER BOX"); c.setFillColorRGB(0,0,0)

    # Barcode (centered, bottom)
    bc = code39.Standard39(invoice.invoice_no, barHeight=12*mm, stop=1, checksum=0)
    bc.drawOn(c, (W-bc.width)/2, M+5)
    c.setFont("Courier", 9); c.drawCentredString(W/2, M-2, invoice.invoice_no)

    c.showPage(); c.save()
    return buf.getvalue()
```

**If it comes out upside-down (180°)** — swap only the transform line:
```python
c.translate(70*mm, 0); c.rotate(90)
```

## Settings that go with it
- **Deli driver (EPIC_LABEL_PRINTER):** paper size **70 mm × 105 mm** (Width 70, Height 105 — portrait), Orientation **Portrait**, **Rotate 180 = OFF**, Scale **100%**. (The rotation is in the code now — none in the driver.)
- **Agent:** keep `-print-settings "noscale"` on the label branch; restart the agent.
- **Republish** the Replit app after editing.

## Why 70×105 (not 105×70)
The print head is 70 mm wide, so it physically lays down 70 mm across and up to 105 mm along the feed. The page must be 70×105 to match the media; the design is drawn landscape and rotated 90° so it reads correctly. Setting the page to 105×70 makes SumatraPDF rotate it to fit → the "prints vertically" result you saw.

## Paste to the Replit Agent
> The Deli label media is 70 mm wide × 105 mm long (portrait). Replace `build_box_label_pdf` in `services/label_pdf.py` so the canvas page is `pagesize=(70*mm, 105*mm)` and the landscape design (105×70 coordinates) is rotated onto it with `c.translate(0,105*mm); c.rotate(-90)`. Keep all elements (STOP number, customer, route, driver, order X of Y, cooler flag, barcode) within a 5 mm margin. If the result is upside-down, use `c.translate(70*mm,0); c.rotate(90)` instead. Then republish. [paste the function]
