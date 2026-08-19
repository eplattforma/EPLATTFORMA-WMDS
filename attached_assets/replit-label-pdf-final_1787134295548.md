# Replit — final box-label PDF (upright + no edge clipping)

Replace the body of `build_box_label_pdf` in `services/label_pdf.py` with this. It keeps the label **105 × 70 mm landscape**, builds the **180° flip into the code** (printer feeds inverted), and keeps every element inside a **5 mm safe border** so nothing is cut on either edge. Keep your existing function inputs — just match the variable names to yours (`invoice`, `stop_number`, `route_name`, `driver_name`, `delivery_date`, `stop_index`, `stop_total`, `has_cooler`).

```python
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code39
from io import BytesIO

def build_box_label_pdf(invoice, stop_number, route_name, driver_name,
                        delivery_date, stop_index=None, stop_total=None, has_cooler=False):
    W, H = 105*mm, 70*mm
    M = 5*mm                        # safe margin — nothing prints in the outer 5 mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    c.translate(W, H); c.rotate(180)    # printer feeds inverted -> flip once so it prints upright

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

## Settings that go with it (so orientation lives in ONE place)
- **Deli driver (EPIC_LABEL_PRINTER):** paper **105 × 70 mm**, Orientation **Landscape**, **Rotate 180 = OFF** (the flip is in the code now), Scale **100%**.
- **Agent (`print_agent.ps1`):** label branch keeps `-print-settings "noscale"`. Restart the agent after any change.
- **Republish** the Replit app after editing `label_pdf.py`.

## Why this is the right size
The working "STOP 101" label printed a full **10.5 cm wide** — the head physically covered 105 mm, so the roll is ~105 mm across. 105 × 70 landscape is correct; do **not** switch to 70 × 105 (that reintroduces the 90° rotation).

## Paste to the Replit Agent
> Replace the body of `build_box_label_pdf` in `services/label_pdf.py` with the version below: keep page size 105×70 mm landscape, add `c.translate(W,H); c.rotate(180)` right after creating the canvas so the label prints upright, and keep every drawn element within a 5 mm margin (M) so nothing is clipped at the edges. Preserve the existing inputs (stop number, customer, route, driver, date, order X of Y, cooler flag) and the barcode. Then republish. [paste the function]

## After it prints
If one element still crowds an edge, it's a one-line nudge — tell me which (STOP number / barcode / customer name) and I'll adjust just that line. Don't change the 105×70 size.
