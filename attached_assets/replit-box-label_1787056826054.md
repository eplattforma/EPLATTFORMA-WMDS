# Replit — 105×70 mm box label on the Deli 750W

A stick-on label for the pallet/box: **big STOP number**, customer name, route/date/driver, and the order barcode. Printed on the USB Deli 750W attached to the office PC, alongside the A4 slip on the Konica. The app routes each document to the right printer.

## Label
- **Size:** 105 mm × 70 mm, landscape. Set this as the **default paper size** in the Deli 750W Windows driver so it doesn't scale.
- **Content:** `STOP N` (largest), `customer_name`, `Route · date · driver`, Code‑39 barcode of `invoice_no` + the number under it.

## Generator — `services/label_pdf.py` (ReportLab, same lib you already use)
```python
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code39
from io import BytesIO

def build_box_label_pdf(invoice, stop_number, route_name, driver_name, delivery_date) -> bytes:
    W, H = 105*mm, 70*mm
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    c.setFont("Helvetica-Bold", 9);  c.drawString(6*mm, H-9*mm, "STOP")
    c.setFont("Helvetica-Bold", 52); c.drawString(5*mm, H-30*mm, str(stop_number or "-"))

    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(W-6*mm, H-9*mm, (invoice.customer_name or "")[:26])
    c.setFont("Helvetica", 8)
    c.drawRightString(W-6*mm, H-15*mm, f"Route {route_name} · {delivery_date}")
    c.drawRightString(W-6*mm, H-20*mm, str(driver_name or ""))

    bc = code39.Standard39(invoice.invoice_no, barHeight=13*mm, stop=1, checksum=0)
    bc.drawOn(c, (W - bc.width)/2, 12*mm)
    c.setFont("Courier", 10)
    c.drawCentredString(W/2, 7*mm, invoice.invoice_no)

    c.showPage(); c.save()
    return buf.getvalue()
```
(Tune the mm offsets to taste; if the customer name is long, drop the font a point or wrap to two lines.)

## Multiple invoices per stop + cooler marker
A stop usually holds several invoices for the same customer (3 is common), and an order may include a chilled **cooler box**. Both go on the label AND the slip so the driver takes everything.

**Data (compute per invoice):**
```sql
SELECT
  (SELECT count(*) FROM invoices x WHERE x.stop_id = i.stop_id)                                   AS stop_total,
  (SELECT count(*) FROM invoices x WHERE x.stop_id = i.stop_id AND x.invoice_no <= i.invoice_no)  AS stop_index,
  EXISTS (SELECT 1 FROM cooler_box_items c WHERE c.invoice_no = i.invoice_no)                      AS has_cooler
FROM invoices i WHERE i.invoice_no = :inv;
```
`stop_index / stop_total` → "Order 2 of 3". `has_cooler` → show the cooler marker.

**Label additions** (in `build_box_label_pdf`, pass `stop_index, stop_total, has_cooler`):
```python
c.setFont("Helvetica-Bold", 12)
c.drawString(6*mm, H-38*mm, f"Order {stop_index} of {stop_total}")
if has_cooler:
    c.setFillColorRGB(0,0,0); c.rect(W-40*mm, H-30*mm, 34*mm, 7*mm, fill=1)
    c.setFillColorRGB(1,1,1); c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W-23*mm, H-28*mm, "COOLER BOX")
    c.setFillColorRGB(0,0,0)
```

**Slip additions:** put "Order {{ stop_index }} of {{ stop_total }}" next to the stop, and when `has_cooler` show a bold **COOLER BOX** tag in the header. (When a stop has multiple invoices, consider a "Stop 4 — 3 orders" line so the driver reconciles the whole stop.)

## Routing — which printer gets what
Give each print job a **doc type** and let the bridge pick the printer.

**Queue column** (extend `print_jobs`):
```sql
ALTER TABLE print_jobs ADD COLUMN doc_type VARCHAR(12) NOT NULL DEFAULT 'slip';  -- 'slip' | 'label'
```

**Two enqueue endpoints** (or one with a `?doc=` param):
```python
@app.route('/print/delivery-slip/<invoice_no>', methods=['POST'])   # -> doc_type 'slip'
@app.route('/print/box-label/<invoice_no>', methods=['POST'])       # -> doc_type 'label'
# both just INSERT INTO print_jobs (invoice_no, doc_type, status) VALUES (:n, :d, 'queued')
```

**Poll returns the doc type + the right PDF:**
```python
# in /print/agent/poll, after selecting the job:
if row.doc_type == 'label':
    pdf = build_box_label_pdf(invoice, stop_number, route_name, driver_name, delivery_date)
else:
    pdf = build_delivery_slip_pdf(invoice)
return jsonify({'job': {'job_id': row.id, 'doc_type': row.doc_type,
                        'pdf_base64': base64.b64encode(pdf).decode()}})
```

## Office-PC agent — pick the printer by doc type
Add both printer names and route on `doc_type` (extends the DIY `agent.ps1`):
```powershell
$konica = "KONICA MINOLTA C300i"      # exact Windows names
$deli   = "Deli DL-750W"
# after fetching $r.job:
$printer = if ($r.job.doc_type -eq 'label') { $deli } else { $konica }
& $sumatra -print-to $printer -silent $tmp
```
(PrintNode version: keep two settings — `konica_printnode_id` and `deli_printnode_id` — and pick the id by `doc_type` before the API call.)

## Pack-screen buttons
Add a label button next to the slip:
```html
<button type="button" class="btn-secondary" onclick="printDoc('box-label')"><i class="ti ti-tag"></i> Print box label</button>
<button type="button" class="btn-secondary" onclick="printDoc('delivery-slip')"><i class="ti ti-printer"></i> Print delivery slip</button>
<!-- printDoc(kind) POSTs to /print/<kind>/<invoice_no>, same fetch as before -->
```

## Paste to the Replit Agent
> Add a 105×70 mm box label printed on the Deli 750W. Create `services/label_pdf.py` (ReportLab, page size 105×70 mm) rendering: large "STOP N", customer name, route/date/driver, "Order X of Y" (from invoices sharing `stop_id`), a bold "COOLER BOX" marker when the order has any `cooler_box_items`, and a Code‑39 barcode of the invoice number. Add the same "Order X of Y" and "COOLER BOX" indicators to the delivery slip header. Add a `doc_type` column to `print_jobs` ('slip'|'label') and a `POST /print/box-label/<invoice_no>` enqueue endpoint. In the agent poll, build the label PDF when `doc_type='label'` else the slip PDF, and return `doc_type`. Route printing by doc type — labels to the Deli 750W, slips to the Konica (by printer name in the DIY agent, or by PrintNode printer id). Add a "Print box label" button on the pack screen next to the slip button. On the office PC, set the Deli driver default paper to 105×70 mm.

## Setup checklist (office PC)
1. Plug in the Deli 750W (USB), install its Windows driver.
2. Set its **default paper size to 105×70 mm** and note the exact **printer name**.
3. Put that name in the agent (`$deli`) or its PrintNode id in settings.
4. Test: tap "Print box label" → a label prints; "Print delivery slip" → the Konica prints.
