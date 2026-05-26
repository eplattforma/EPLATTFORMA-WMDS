# Supplier Returns — V9 Editable Quantity Modal

Template-only change. No new routes, no backend changes.

**What changes:**
The "Send PO" button currently sends whatever quantity is in the system with no chance to review or adjust. This replaces that with a proper confirmation modal where each item shows its system quantity pre-filled in an editable input. The user can change any quantity before sending. Pieces recalculate live. The backend receives exactly what the user typed — not the system number.

---

## Context: how the current Send PO flow works

When the user clicks "Send PO" on a supplier, the JS does this:

1. Finds all checked checkboxes in that supplier's card
2. Reads `data-item-code`, `data-cases`, `data-selling-qty` from each checkbox element
3. Shows a simple confirm modal (or goes straight to POST)
4. POSTs to `/supplier-returns/create-po` with `{ supplier_code, supplier_name, items: [{item_code_365, stock_cases}, ...] }`

The fix: between steps 2 and 4, open an editable modal. The POST payload is built from the modal's input fields, not the checkbox data attributes.

---

## CHANGE — Edit `templates/supplier_returns/index.html`

### Step 1 — Replace the existing PO confirm modal

Find the existing PO confirmation modal. It will look something like:

```html
<div class="modal fade" id="poConfirmModal" ...>
```

**Delete the entire modal** (from `<div class="modal fade" id="poConfirmModal"` to its closing `</div>`) and replace it with:

```html
<!-- ── Send PO modal with editable quantities ── -->
<div class="modal fade" id="poConfirmModal" tabindex="-1" aria-labelledby="poConfirmLabel" aria-hidden="true">
  <div class="modal-dialog modal-lg modal-dialog-scrollable">
    <div class="modal-content">

      <div class="modal-header">
        <h5 class="modal-title" id="poConfirmLabel">
          <i class="fas fa-file-alt me-2"></i>Review & Confirm Purchase Return
        </h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>

      <div class="modal-body">

        <!-- Supplier info -->
        <div class="mb-3 p-2 rounded border border-secondary bg-body-secondary">
          <span class="text-muted small">Supplier:</span>
          <strong id="poModalSupplierName" class="ms-2"></strong>
          <span class="text-muted small ms-3">Code:</span>
          <span id="poModalSupplierCode" class="ms-1 font-monospace small"></span>
        </div>

        <!-- Instruction -->
        <p class="small text-muted mb-2">
          Quantities are pre-filled from the system. Adjust any line before sending.
          Cases must be greater than 0 and no more than the system quantity.
        </p>

        <!-- Items table -->
        <table class="table table-sm table-hover align-middle mb-0" id="poModalTable">
          <thead class="table-secondary">
            <tr>
              <th>Item Code</th>
              <th>Description</th>
              <th class="text-end">System Qty</th>
              <th class="text-end" style="width:130px">Return Cases</th>
              <th class="text-end" style="width:90px">Pieces</th>
              <th style="width:36px"></th>
            </tr>
          </thead>
          <tbody id="poModalBody">
            <!-- Rows injected by JS -->
          </tbody>
          <tfoot>
            <tr class="fw-bold border-top border-secondary">
              <td colspan="3" class="text-end text-muted small">Totals</td>
              <td class="text-end" id="poModalTotalCases">—</td>
              <td class="text-end" id="poModalTotalPieces">—</td>
              <td></td>
            </tr>
          </tfoot>
        </table>

        <!-- Validation warning -->
        <div id="poModalWarning" class="alert alert-warning mt-3 mb-0 py-2 small" style="display:none">
          <i class="fas fa-exclamation-triangle me-1"></i>
          <span id="poModalWarningText"></span>
        </div>

      </div>

      <div class="modal-footer">
        <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" id="btnConfirmSendPo">
          <i class="fas fa-paper-plane me-1"></i>Send Purchase Return
        </button>
      </div>

    </div>
  </div>
</div>
```

---

### Step 2 — Replace the Send PO JavaScript

In the `{% block scripts %}` section, find the existing Send PO JS. It will include a click handler for the "Send PO" button(s) and the `fetch` call to `/supplier-returns/create-po`. Replace **the entire Send PO block** with the following.

> **Note:** Keep all other JS (search, collapse, refresh, mark-collected) exactly as-is. Only replace the Send PO section.

```javascript
// ---------------------------------------------------------------------------
// Send PO — editable quantity modal
// ---------------------------------------------------------------------------

// State for the current PO being built
var currentPo = {
  supplierCode: "",
  supplierName: "",
  items: []   // [{itemCode, itemName, systemCases, sellingQty, cases, pieces}]
};

/**
 * Called when user clicks "Send PO" on a supplier card.
 * Reads checked items, populates the modal, opens it.
 */
function openPoModal(supplierCode, supplierName) {
  var card = document.querySelector('.supplier-group[data-supplier-code="' + supplierCode + '"]');
  if (!card) return;

  var checked = card.querySelectorAll('input[type="checkbox"].item-select:checked');
  if (checked.length === 0) {
    alert("Tick at least one item before sending a Purchase Return.");
    return;
  }

  currentPo.supplierCode = supplierCode;
  currentPo.supplierName = supplierName;
  currentPo.items = [];

  checked.forEach(function(cb) {
    currentPo.items.push({
      itemCode:    cb.dataset.itemCode    || "",
      itemName:    cb.dataset.itemName    || "",
      systemCases: parseFloat(cb.dataset.cases      || "0"),
      sellingQty:  parseFloat(cb.dataset.sellingQty || "0"),
      barcode:     cb.dataset.barcode    || ""
    });
  });

  // Populate modal header
  document.getElementById("poModalSupplierName").textContent = supplierName;
  document.getElementById("poModalSupplierCode").textContent = supplierCode;

  // Build table rows
  var tbody = document.getElementById("poModalBody");
  tbody.innerHTML = "";

  currentPo.items.forEach(function(item, idx) {
    var pieces = calcPieces(item.systemCases, item.sellingQty);
    var row = document.createElement("tr");
    row.dataset.idx = idx;
    row.innerHTML =
      '<td class="font-monospace small">' + escHtml(item.itemCode) + '</td>' +
      '<td class="small">' + escHtml(item.itemName) + '</td>' +
      '<td class="text-end text-muted small">' + fmtCases(item.systemCases) + '</td>' +
      '<td class="text-end">' +
        '<input type="number" class="form-control form-control-sm text-end po-qty-input"' +
        '  data-idx="' + idx + '"' +
        '  data-system-cases="' + item.systemCases + '"' +
        '  data-selling-qty="' + item.sellingQty + '"' +
        '  value="' + item.systemCases + '"' +
        '  min="0.0001" max="' + item.systemCases + '"' +
        '  step="0.0001">' +
      '</td>' +
      '<td class="text-end small pieces-cell">' + pieces + '</td>' +
      '<td class="text-center">' +
        '<button class="btn btn-link btn-sm text-danger p-0 remove-po-row" data-idx="' + idx + '" title="Remove this line">' +
          '<i class="fas fa-times"></i>' +
        '</button>' +
      '</td>';
    tbody.appendChild(row);
  });

  updatePoTotals();
  document.getElementById("poModalWarning").style.display = "none";
  document.getElementById("btnConfirmSendPo").disabled = false;

  new bootstrap.Modal(document.getElementById("poConfirmModal")).show();
}

/** Recalculate pieces for a row when cases input changes */
document.getElementById("poModalBody").addEventListener("input", function(e) {
  if (!e.target.classList.contains("po-qty-input")) return;
  var idx        = parseInt(e.target.dataset.idx);
  var sellingQty = parseFloat(e.target.dataset.sellingQty || "0");
  var val        = parseFloat(e.target.value || "0");
  var pieces     = calcPieces(val, sellingQty);

  var row = e.target.closest("tr");
  if (row) row.querySelector(".pieces-cell").textContent = pieces;

  updatePoTotals();
  validatePoModal();
});

/** Remove a line from the modal */
document.getElementById("poModalBody").addEventListener("click", function(e) {
  var btn = e.target.closest(".remove-po-row");
  if (!btn) return;
  var idx = parseInt(btn.dataset.idx);
  currentPo.items.splice(idx, 1);

  // Rebuild table (simplest re-render)
  var tbody = document.getElementById("poModalBody");
  var rows  = tbody.querySelectorAll("tr");
  rows[idx].remove();

  // Re-index remaining rows
  tbody.querySelectorAll("tr").forEach(function(row, i) {
    row.dataset.idx = i;
    var input = row.querySelector(".po-qty-input");
    if (input) input.dataset.idx = i;
    var remBtn = row.querySelector(".remove-po-row");
    if (remBtn) remBtn.dataset.idx = i;
  });

  updatePoTotals();

  if (tbody.querySelectorAll("tr").length === 0) {
    document.getElementById("btnConfirmSendPo").disabled = true;
    showPoWarning("All lines removed. Cancel and reselect items.");
  }
});

/** Confirm and send */
document.getElementById("btnConfirmSendPo").addEventListener("click", function() {
  if (!validatePoModal()) return;

  var items = [];
  document.querySelectorAll("#poModalBody .po-qty-input").forEach(function(input, i) {
    var cases = parseFloat(input.value || "0");
    if (cases > 0) {
      items.push({
        item_code_365: currentPo.items[i].itemCode,
        stock_cases:   cases
      });
    }
  });

  if (items.length === 0) {
    showPoWarning("No valid quantities to send.");
    return;
  }

  var btn = document.getElementById("btnConfirmSendPo");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Sending…';

  fetch("/supplier-returns/create-po", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      supplier_code: currentPo.supplierCode,
      supplier_name: currentPo.supplierName,
      items: items
    })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    bootstrap.Modal.getInstance(document.getElementById("poConfirmModal")).hide();
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-paper-plane me-1"></i>Send Purchase Return';

    if (data.success) {
      // Optimistic UI update — fade sent rows
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
            if (availCell) {
              availCell.innerHTML = '<span class="badge bg-secondary">On PO — refresh</span>';
            }
            cb.disabled = true;
          }
        }
      });
      showToast("Purchase Return sent. <a href='/supplier-returns/print/" + currentPo.supplierCode + "' target='_blank' class='text-white fw-bold'>Print slip</a>", "success");
    } else {
      showToast("Error: " + (data.error || "Unknown error"), "danger");
    }
  })
  .catch(function(err) {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-paper-plane me-1"></i>Send Purchase Return';
    showToast("Network error — try again.", "danger");
  });
});

// ── Helpers ──

function calcPieces(cases, sellingQty) {
  if (!sellingQty || sellingQty === 0) return 0;
  return Math.round(cases * sellingQty);
}

function fmtCases(val) {
  return parseFloat(val).toFixed(4).replace(/\.?0+$/, "");
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function updatePoTotals() {
  var totalCases  = 0;
  var totalPieces = 0;
  document.querySelectorAll("#poModalBody .po-qty-input").forEach(function(input, i) {
    var cases      = parseFloat(input.value || "0");
    var sellingQty = parseFloat(input.dataset.sellingQty || "0");
    totalCases  += cases;
    totalPieces += calcPieces(cases, sellingQty);
  });
  document.getElementById("poModalTotalCases").textContent  = fmtCases(totalCases);
  document.getElementById("poModalTotalPieces").textContent = totalPieces;
}

function validatePoModal() {
  var invalid = [];
  document.querySelectorAll("#poModalBody .po-qty-input").forEach(function(input, i) {
    var val        = parseFloat(input.value || "0");
    var systemQty  = parseFloat(input.dataset.systemCases || "0");
    if (val <= 0) {
      invalid.push("Line " + (i + 1) + ": quantity must be greater than 0.");
      input.classList.add("is-invalid");
    } else if (val > systemQty + 0.00001) {
      invalid.push("Line " + (i + 1) + ": quantity cannot exceed system quantity (" + fmtCases(systemQty) + ").");
      input.classList.add("is-invalid");
    } else {
      input.classList.remove("is-invalid");
    }
  });
  if (invalid.length > 0) {
    showPoWarning(invalid.join(" "));
    return false;
  }
  document.getElementById("poModalWarning").style.display = "none";
  return true;
}

function showPoWarning(msg) {
  var el = document.getElementById("poModalWarning");
  document.getElementById("poModalWarningText").textContent = msg;
  el.style.display = "";
}
```

---

### Step 3 — Update the "Send PO" button on each supplier card

The existing Send PO buttons call something like `sendPo('{{ group.supplier_code_365 }}')` or similar. Change those calls to use `openPoModal` instead.

Find (inside the supplier card header, for each supplier):
```html
onclick="sendPo('{{ group.supplier_code_365 }}'..."
```
or whatever the existing onclick is. Replace with:
```html
onclick="openPoModal('{{ group.supplier_code_365 }}', '{{ group.supplier_name | replace("'", "\\'") }}')"
```

---

### Step 4 — Confirm checkbox data attributes are present

The JS reads `data-item-code`, `data-item-name`, `data-cases`, `data-selling-qty`, and `data-barcode` from each item checkbox. Make sure each checkbox in the items table has these:

```html
<input type="checkbox" class="item-select form-check-input"
  data-item-code="{{ item.item_code_365 }}"
  data-item-name="{{ item.item_name or '' }}"
  data-cases="{{ item.stock_cases }}"
  data-selling-qty="{{ item.selling_qty or 0 }}"
  data-barcode="{{ item.barcode or '' }}"
  {% if item.fully_committed %}disabled{% endif %}>
```

If any of these attributes are missing from the existing checkboxes, add them. The most commonly missing ones are `data-item-name` and `data-selling-qty`.

---

### Step 5 — Confirm `showToast` helper exists

The JS above calls `showToast(message, type)`. If this helper does not already exist in the script block, add it:

```javascript
function showToast(html, type) {
  type = type || "success";
  var container = document.getElementById("toastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "toastContainer";
    container.className = "position-fixed bottom-0 end-0 p-3";
    container.style.zIndex = "1100";
    document.body.appendChild(container);
  }
  var id   = "toast-" + Date.now();
  var wrap = document.createElement("div");
  wrap.innerHTML =
    '<div id="' + id + '" class="toast align-items-center text-white bg-' + type + ' border-0" role="alert">' +
    '  <div class="d-flex">' +
    '    <div class="toast-body">' + html + '</div>' +
    '    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
    '  </div>' +
    '</div>';
  container.appendChild(wrap.firstElementChild);
  var toastEl = new bootstrap.Toast(document.getElementById(id), { delay: 5000 });
  toastEl.show();
}
```

---

## How it works after applying

1. User ticks items in a supplier's table
2. Clicks **Send Purchase Return** button on the supplier card
3. A modal opens listing every ticked item — one row per item:
   - Item code and description
   - **System Qty** (read-only, greyed — what the system holds in store 100)
   - **Return Cases** — editable number input, pre-filled with system qty
   - **Pieces** — recalculates live as cases are changed
   - A × button to remove a line entirely if it shouldn't be on this PO
4. Totals row at the bottom updates as quantities are changed
5. If any input is 0 or exceeds the system qty, a warning appears and Send is blocked
6. User clicks **Send Purchase Return** — POST fires with the user-entered quantities
7. On success: rows fade out, a toast appears with a link to print the acknowledgement slip

**Key constraints enforced:**
- Cannot send 0 cases on a line (forces conscious decision)
- Cannot send more than the system quantity on any line (prevents over-return)
- Cannot send if all lines have been removed (Send button disabled)
