# Supplier Returns — V13 Streamlined Handover Flow

Four things in this update:

1. **Fix broken POST payload** — field names in the frontend JS don't match what the V5 backend expects; the PO is currently failing silently
2. **One-step handover** — sending the PO automatically marks it as collected and opens the print slip with the PS365 PO number on it. The separate "Mark Collected" button is removed.
3. **PO number on the print slip** — the slip shows the PS365 reference number so the supplier has it on their copy
4. **Cost/case 2 decimal places** — trim the extra digits in the items table

---

## CHANGE 1 — Fix the POST payload in the Send PO JavaScript

The V5 backend (`blueprints/supplier_returns.py`) reads these fields:
- `supplier_code_365` (not `supplier_code`)
- `lines` (not `items`)
- each line has `line_quantity` (not `stock_cases`)

The V10 JS sends the wrong names, so every PO silently fails. In the Send PO JS block, find the `fetch("/supplier-returns/create-po", ...)` call:

**Find:**
```javascript
  fetch("/supplier-returns/create-po", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      supplier_code: currentPo.supplierCode,
      supplier_name: currentPo.supplierName,
      items: items
    })
  })
```

**Replace with:**
```javascript
  fetch("/supplier-returns/create-po", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      supplier_code_365: currentPo.supplierCode,
      supplier_name:     currentPo.supplierName,
      lines: items.map(function(i) {
        return {
          item_code_365: i.item_code_365,
          line_quantity: i.stock_cases
        };
      })
    })
  })
```

---

## CHANGE 2 — Auto mark-collected + open print slip on success

Still in the `fetch` `.then` success block. This replaces the existing success handler.

**Find the entire success block:**
```javascript
    if (data.success) {
      items.forEach(function(item) {
        var cb = document.querySelector(
          '.supplier-group[data-supplier-code="' + currentPo.supplierCode + '"]' +
          ' input[data-item-code="' + item.item_code_365 + '"]'
        );
        if (cb) {
          var row = cb.closest("tr");
          if (row) {
            row.style.opacity = "0.4";
            var availCell = row.querySelector(".available-cell");
            if (availCell) availCell.innerHTML =
              '<span class="badge bg-secondary">On PO — refresh</span>';
            cb.disabled = true;
          }
        }
      });
      showToast(
        'Purchase Return sent. <a href="/supplier-returns/print/' +
        currentPo.supplierCode + '" target="_blank" class="text-white fw-bold">Print slip</a>',
        "success"
      );
    } else {
```

**Replace with:**
```javascript
    if (data.success) {
      var poNumber = data.po_code || "";

      // 1. Fade out sent item rows on the main page
      items.forEach(function(item) {
        var cb = document.querySelector(
          '.supplier-group[data-supplier-code="' + currentPo.supplierCode + '"]' +
          ' input[data-item-code="' + item.item_code_365 + '"]'
        );
        if (cb) {
          var row = cb.closest("tr");
          if (row) {
            row.style.opacity = "0.4";
            var availCell = row.querySelector(".available-cell");
            if (availCell) availCell.innerHTML =
              '<span class="badge bg-secondary">On PO — refresh</span>';
            cb.disabled = true;
          }
        }
      });

      // 2. Open print slip immediately with PO number
      var printUrl = "/supplier-returns/print/" + currentPo.supplierCode +
                     (poNumber ? "?po_number=" + encodeURIComponent(poNumber) : "");
      window.open(printUrl, "_blank");

      // 3. Toast confirmation
      showToast(
        'PO ' + (poNumber || 'sent') + ' — slip opened for printing.',
        "success"
      );
    } else {
```

---

## CHANGE 3 — Mark as collected automatically in the backend

In `blueprints/supplier_returns.py`, in the `api_create_po` route, find the tracking row save block:

**Find:**
```python
        try:
            from app import db
            from models import SupplierReturnPoTracking
            tracking = SupplierReturnPoTracking(
                cart_code         = cart_code,
                po_id_365         = str(po_code),
                supplier_code_365 = supplier_code,
                supplier_name     = payload.get("supplier_name", ""),
                sent_by           = getattr(current_user, "username", None),
            )
            db.session.merge(tracking)
            db.session.commit()
        except Exception:
            logger.warning("[Returns PO] Could not save tracking row for %s", cart_code)
```

**Replace with:**
```python
        try:
            from app import db
            from models import SupplierReturnPoTracking
            user     = getattr(current_user, "username", None)
            now_local = datetime.now(timezone.utc).replace(tzinfo=None)
            tracking = SupplierReturnPoTracking(
                cart_code         = cart_code,
                po_id_365         = str(po_code),
                supplier_code_365 = supplier_code,
                supplier_name     = payload.get("supplier_name", ""),
                sent_by           = user,
                # Handover happens at the moment of sending — slip is printed immediately after
                collected_at      = now_local,
                collected_by      = user,
            )
            db.session.merge(tracking)
            db.session.commit()
        except Exception:
            logger.warning("[Returns PO] Could not save tracking row for %s", cart_code)
```

---

## CHANGE 4 — Add PO number to the print slip route

In `blueprints/supplier_returns.py`, in the `print_slip` route, find the `return render_template(...)` call:

**Find:**
```python
    return render_template(
        "supplier_returns/print_slip.html",
        group=group,
        print_date=datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
    )
```

**Replace with:**
```python
    po_number = request.args.get("po_number", "").strip()

    return render_template(
        "supplier_returns/print_slip.html",
        group=group,
        print_date=datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        po_number=po_number,
    )
```

---

## CHANGE 5 — Show PO number on the print slip template

In `templates/supplier_returns/print_slip.html`, find the supplier block:

**Find:**
```html
  <div class="supplier-block">
    <div>
      <span class="label">Supplier:</span>
      <strong>{{ group.supplier_name or "Unknown" }}</strong>
    </div>
    <div>
      <span class="label">Supplier Code:</span>
      {{ group.supplier_code_365 or "—" }}
    </div>
  </div>
```

**Replace with:**
```html
  <div class="supplier-block">
    <div>
      <span class="label">Supplier:</span>
      <strong>{{ group.supplier_name or "Unknown" }}</strong>
    </div>
    <div>
      <span class="label">Supplier Code:</span>
      {{ group.supplier_code_365 or "—" }}
    </div>
    {% if po_number %}
    <div style="margin-top:6px">
      <span class="label" style="font-size:11pt">PO Number:</span>
      <strong style="font-size:13pt; letter-spacing:0.5px">{{ po_number }}</strong>
    </div>
    {% endif %}
  </div>
```

---

## CHANGE 6 — Remove the "Mark Collected" button from the main template

In `templates/supplier_returns/index.html`, find the Mark Collected button. It will be inside the open POs / pending POs section, something like:

```html
<button class="btn btn-sm btn-outline-success mark-collected-btn" ...>
  <i class="fas fa-check"></i> Mark Collected
</button>
```

Delete this button entirely. The collected_at is now set automatically when the PO is sent.

Also find and remove the JS event handler for it — look for:
```javascript
document.addEventListener("click", function(e) {
  if (!e.target.closest(".mark-collected-btn")) return;
  ...
});
```
or similar. Delete that entire block.

---

## CHANGE 7 — Cost/case to 2 decimal places

In `templates/supplier_returns/index.html`, find where the cost per case is displayed in the items table. It will look like:

```html
{{ "%.4f"|format(item.cost_price) }}
```
or
```html
{{ item.cost_price | round(4) }}
```

Change to:
```html
{{ "%.2f"|format(item.cost_price) }}
```

If it appears as a currency with a symbol (e.g. `€{{ "%.4f"|format(...) }}`), same fix — just change `4` to `2`.

---

## New end-to-end flow after applying

1. Staff tick items for a supplier → click **Send Purchase Return**
2. Modal opens — adjust pieces if needed → click **Send Purchase Return**
3. PO fires to PS365 → PS365 returns a PO number
4. Print slip opens automatically in a new tab, showing the PS365 PO number
5. Staff hand the slip to the supplier — supplier signs
6. Done. The PO is already tracked as collected in the DB. No extra button clicks.

The "Mark Collected" button is gone. The open POs section still shows the PO for visibility until the next Refresh (when PS365 confirms the return is processed and it disappears).
