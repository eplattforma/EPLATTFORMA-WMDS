# Box label — final fix (one label, upright)

**Root cause:** the labels are 105 × 70, feeding **70 mm per label** (gap every 70 mm). The problem was the **driver media size**, not the code. When the driver paper is set to a 105-long / portrait size, the printer thinks each label is 105 mm long, feeds past the real 70 mm gap, and prints the design across **two** labels. Fix = driver media = one real label (105 × 70, 70 mm feed) + a plain 105 × 70 landscape PDF, matched, no rotation.

## 1. Deli driver (the key fix) — EPIC_LABEL_PRINTER → Printing preferences
- Paper / label size: **Width 105 mm × Height 70 mm** (landscape; 70 mm = the feed length matching your gap).
- Orientation **Portrait** (the media shape is already landscape), **Rotate 180 = OFF**, Scale **100%**.
- If there's a Media/Gap setting, set **gap sensing** so a label = 70 mm.

## 2. `services/label_pdf.py` — plain landscape, NO rotation
```python
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code39
from io import BytesIO

def build_box_label_pdf(invoice, stop_number, route_name, driver_name,
                        delivery_date, stop_index=None, stop_total=None, has_cooler=False):
    W, H = 105*mm, 70*mm
    M = 5*mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(105*mm, 70*mm))   # matches one physical label
    # NO c.translate / c.rotate

    c.setFont("Helvetica-Bold", 9);  c.drawString(M, H-M-9, "STOP")
    c.setFont("Helvetica-Bold", 44); c.drawString(M-1, H-M-46, str(stop_number or "-"))
    if stop_index and stop_total:
        c.setFont("Helvetica-Bold", 11); c.drawString(M, H-M-60, f"Order {stop_index} of {stop_total}")

    c.setFont("Helvetica-Bold", 12); c.drawRightString(W-M, H-M-7, (invoice.customer_name or "")[:26])
    c.setFont("Helvetica", 8)
    c.drawRightString(W-M, H-M-19, f"Route {route_name} - {delivery_date}")
    c.drawRightString(W-M, H-M-29, str(driver_name or ""))
    if has_cooler:
        c.setFillColorRGB(0,0,0); c.rect(W-M-34*mm, H-M-42, 34*mm, 7*mm, fill=1)
        c.setFillColorRGB(1,1,1); c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(W-M-17*mm, H-M-40, "COOLER BOX"); c.setFillColorRGB(0,0,0)

    bc = code39.Standard39(invoice.invoice_no, barHeight=12*mm, stop=1, checksum=0)
    bc.drawOn(c, (W-bc.width)/2, M+5)
    c.setFont("Courier", 9); c.drawCentredString(W/2, M-2, invoice.invoice_no)

    c.showPage(); c.save()
    return buf.getvalue()
```
Republish after editing.

## 3. Agent
Keep `-print-settings "noscale"` on the label branch; restart the agent.

## Why this works
Driver media 105 × 70 (70 mm feed) tells the printer each label is 70 mm long, so it stops at the real gap → one design per label. PDF 105 × 70 matches the media, so SumatraPDF doesn't rotate → content reads landscape as drawn. The earlier "prints across two labels" came from the driver expecting a 105 mm-long label.

## If direction is off after this
- Prints upside-down (180°): add `c.translate(105*mm,70*mm); c.rotate(180)` right after the canvas line.
- Prints rotated 90°: the driver media is still portrait — recheck it's Width 105 × Height 70.
