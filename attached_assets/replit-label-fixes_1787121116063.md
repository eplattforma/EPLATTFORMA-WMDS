# Replit — box label fixes (3 changes)

Three fixes to the box-label feature: (1) show the label button only on the Picking Dashboard, (2) add send feedback and stop duplicate labels, (3) rotate the label so it prints upright. Paste the prompt below to the Replit Agent; the code blocks give the exact logic.

---

## Paste to the Replit Agent

> Make three changes to box-label printing. Keep the `/print/box-label/<invoice_no>` route and the print bridge as they are.
>
> **1. Label button on the Picking Dashboard only — remove it from pickers.** Remove the "Print box label" / tag button from all picker-facing screens (the ready-for-packing / pack screen and any picker order view). Keep the label (tag) button only on the Picking Dashboard order list. Leave the delivery-slip print untouched — this is about the box label only.
>
> **2. Send feedback + no duplicate labels.** Tapping the tag button currently gives no visible response, so users tap repeatedly and stack duplicate labels. On the client, disable the button on tap, show "Sending…" then "Sent ✓", ignore further taps while in flight, re-enable after ~5s, and show a short toast "Label sent to printer". On the server, in the box-label enqueue endpoint, skip inserting if a job for the same invoice with doc_type 'label' is already 'queued' or 'sending'. Use the code below.
>
> **3. Fix label orientation (prints rotated 90°).** The label is generated 105×70 mm landscape but the labels feed portrait, so it prints sideways. In `services/label_pdf.py`, rotate the whole layout 90° onto a portrait page, keeping the existing design coordinates. Use the code below. After the change, print one test label; if it's upside-down, flip the rotation sign as noted.
>
> After all three: the tag button appears only on the Picking Dashboard, shows "Sent ✓" on tap, duplicate taps can't create duplicate labels, and the label prints upright.

---

## Change 2 — code

**Client (dashboard tag button):**
```javascript
async function printLabel(invoiceNo, btn){
  if (btn.dataset.busy) return;                 // ignore repeat taps
  btn.dataset.busy = "1"; btn.disabled = true;
  const original = btn.innerHTML; btn.textContent = "Sending…";
  try {
    const r = await fetch(`/print/box-label/${invoiceNo}`, {method:'POST'});
    const j = await r.json();
    btn.textContent = j.ok ? "Sent ✓" : "Failed — try again";
  } catch(e){ btn.textContent = "Failed — try again"; }
  setTimeout(()=>{ btn.innerHTML = original; btn.disabled = false; delete btn.dataset.busy; }, 5000);
}
```

**Server (box-label enqueue endpoint):**
```python
exists = db.session.execute(text(
  "SELECT 1 FROM print_jobs WHERE invoice_no=:n AND doc_type='label' AND status IN ('queued','sending') LIMIT 1"),
  {'n': invoice_no}).first()
if not exists:
    db.session.execute(text("INSERT INTO print_jobs (invoice_no, doc_type, status) VALUES (:n,'label','queued')"),
                       {'n': invoice_no})
    db.session.commit()
return jsonify({'ok': True})
```

---

## Change 3 — code

**`services/label_pdf.py` — rotate the layout onto the portrait label:**
```python
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from io import BytesIO

def build_box_label_pdf(...):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(70*mm, 105*mm))   # physical label as it feeds (portrait)
    c.translate(0, 105*mm)
    c.rotate(-90)                                       # draw using the original 105-wide x 70-tall coordinates
    # ... existing drawing code UNCHANGED (STOP, customer, cooler flag, barcode) ...
    c.showPage(); c.save()
    return buf.getvalue()
```
If the test label prints upside-down, replace those two transform lines with:
```python
    c.translate(70*mm, 0)
    c.rotate(90)
```

---

## After republishing — quick test
1. Picker screens: the tag/label button is gone; slip printing still there.
2. Picking Dashboard: tag button shows "Sent ✓" on tap; tapping 5× fast still prints one label.
3. Print one label: it comes out upright and reads correctly. If upside-down, apply the sign flip in Change 3.
