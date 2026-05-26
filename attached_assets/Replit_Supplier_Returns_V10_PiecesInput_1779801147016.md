# Supplier Returns — V10 Pieces-Based Input in PO Modal

Modifies the V9 modal. The editable column changes from **Return Cases** to **Return Pieces**.

- User types a whole number (pieces) — easier to count physically
- Cases are back-calculated (`pieces ÷ selling_qty`) and shown in small text as a reference
- The POST to `/supplier-returns/create-po` still sends `stock_cases` (back-calculated) — no backend change needed

---

## CHANGE 1 — Replace the modal table header in `templates/supplier_returns/index.html`

Find:
```html
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
```

Replace with:
```html
<thead class="table-secondary">
  <tr>
    <th>Item Code</th>
    <th>Description</th>
    <th class="text-end">Sys. Pieces</th>
    <th class="text-end" style="width:150px">Return Pieces</th>
    <th class="text-end" style="width:100px">Cases</th>
    <th style="width:36px"></th>
  </tr>
</thead>
```

---

## CHANGE 2 — Replace the Send PO JavaScript block

This replaces the entire Send PO JS block that was added in V9. Find the comment marker:
```javascript
// ---------------------------------------------------------------------------
// Send PO — editable quantity modal
// ---------------------------------------------------------------------------
```

Replace everything from that comment down to (and including) the `showPoWarning` function with the following:

```javascript
// ---------------------------------------------------------------------------
// Send PO — editable quantity modal (pieces input)
// ---------------------------------------------------------------------------

var currentPo = {
  supplierCode: "",
  supplierName: "",
  items: []
};

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
    var systemCases  = parseFloat(cb.dataset.cases      || "0");
    var sellingQty   = parseFloat(cb.dataset.sellingQty || "0");
    var systemPieces = calcPieces(systemCases, sellingQty);
    currentPo.items.push({
      itemCode:     cb.dataset.itemCode  || "",
      itemName:     cb.dataset.itemName  || "",
      systemCases:  systemCases,
      systemPieces: systemPieces,
      sellingQty:   sellingQty,
      barcode:      cb.dataset.barcode   || ""
    });
  });

  document.getElementById("poModalSupplierName").textContent = supplierName;
  document.getElementById("poModalSupplierCode").textContent = supplierCode;

  var tbody = document.getElementById("poModalBody");
  tbody.innerHTML = "";

  currentPo.items.forEach(function(item, idx) {
    var cases = piecesToCases(item.systemPieces, item.sellingQty);
    var row   = document.createElement("tr");
    row.dataset.idx = idx;
    row.innerHTML =
      '<td class="font-monospace small">' + escHtml(item.itemCode) + '</td>' +
      '<td class="small">' + escHtml(item.itemName) + '</td>' +
      '<td class="text-end">' + item.systemPieces + '</td>' +
      '<td class="text-end">' +
        '<input type="number" class="form-control form-control-sm text-end po-qty-input"' +
        '  data-idx="' + idx + '"' +
        '  data-system-pieces="' + item.systemPieces + '"' +
        '  data-selling-qty="' + item.sellingQty + '"' +
        '  value="' + item.systemPieces + '"' +
        '  min="1" max="' + item.systemPieces + '"' +
        '  step="1">' +
      '</td>' +
      '<td class="text-end cases-cell">' +
        '<span class="fw-bold">' + fmtCases(cases) + '</span>' +
      '</td>' +
      '<td class="text-center">' +
        '<button class="btn btn-link btn-sm text-danger p-0 remove-po-row" data-idx="' + idx + '" title="Remove line">' +
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

/** Live update cases cell as pieces input changes */
document.getElementById("poModalBody").addEventListener("input", function(e) {
  if (!e.target.classList.contains("po-qty-input")) return;
  var sellingQty = parseFloat(e.target.dataset.sellingQty || "0");
  var pieces     = parseInt(e.target.value || "0", 10);
  var cases      = piecesToCases(pieces, sellingQty);

  var row = e.target.closest("tr");
  if (row) row.querySelector(".cases-cell").innerHTML =
    '<span class="fw-bold">' + fmtCases(cases) + '</span>';

  updatePoTotals();
  validatePoModal();
});

/** Remove a line */
document.getElementById("poModalBody").addEventListener("click", function(e) {
  var btn = e.target.closest(".remove-po-row");
  if (!btn) return;
  var idx = parseInt(btn.dataset.idx);
  currentPo.items.splice(idx, 1);

  var tbody = document.getElementById("poModalBody");
  tbody.querySelectorAll("tr")[idx].remove();

  tbody.querySelectorAll("tr").forEach(function(row, i) {
    row.dataset.idx = i;
    var input  = row.querySelector(".po-qty-input");
    if (input)  input.dataset.idx  = i;
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
  document.querySelectorAll("#poModalBody tr").forEach(function(row, i) {
    var input      = row.querySelector(".po-qty-input");
    if (!input) return;
    var pieces     = parseInt(input.value || "0", 10);
    var sellingQty = parseFloat(input.dataset.sellingQty || "0");
    var cases      = piecesToCases(pieces, sellingQty);
    if (cases > 0) {
      items.push({
        item_code_365: currentPo.items[i] ? currentPo.items[i].itemCode : "",
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
      showToast("Error: " + (data.error || "Unknown error"), "danger");
    }
  })
  .catch(function() {
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

function piecesToCases(pieces, sellingQty) {
  if (!sellingQty || sellingQty === 0) return 0;
  return pieces / sellingQty;
}

function fmtCases(val) {
  var n = parseFloat(val);
  if (isNaN(n)) return "0";
  // Show up to 4 decimal places, strip trailing zeros
  return n.toFixed(4).replace(/\.?0+$/, "");
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function updatePoTotals() {
  var totalPieces = 0;
  var totalCases  = 0;
  document.querySelectorAll("#poModalBody .po-qty-input").forEach(function(input, i) {
    var pieces     = parseInt(input.value || "0", 10);
    var sellingQty = parseFloat(input.dataset.sellingQty || "0");
    totalPieces   += pieces;
    totalCases    += piecesToCases(pieces, sellingQty);
  });
  document.getElementById("poModalTotalPieces").textContent = totalPieces;
  document.getElementById("poModalTotalCases").textContent  = fmtCases(totalCases);
}

function validatePoModal() {
  var invalid = [];
  document.querySelectorAll("#poModalBody .po-qty-input").forEach(function(input, i) {
    var val          = parseInt(input.value || "0", 10);
    var systemPieces = parseInt(input.dataset.systemPieces || "0", 10);
    if (isNaN(val) || val < 1) {
      invalid.push("Line " + (i + 1) + ": pieces must be at least 1.");
      input.classList.add("is-invalid");
    } else if (val > systemPieces) {
      invalid.push("Line " + (i + 1) + ": cannot exceed system pieces (" + systemPieces + ").");
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
  new bootstrap.Toast(document.getElementById(id), { delay: 5000 }).show();
}
```

---

## CHANGE 3 — Update the modal footer totals row

Find the totals row in the modal `<tfoot>`:
```html
<tr class="fw-bold border-top border-secondary">
  <td colspan="3" class="text-end text-muted small">Totals</td>
  <td class="text-end" id="poModalTotalCases">—</td>
  <td class="text-end" id="poModalTotalPieces">—</td>
  <td></td>
</tr>
```

Replace with (columns now match the new header order — pieces input first, cases second):
```html
<tr class="fw-bold border-top border-secondary">
  <td colspan="3" class="text-end text-muted small">Totals</td>
  <td class="text-end" id="poModalTotalPieces">—</td>
  <td class="text-end" id="poModalTotalCases">—</td>
  <td></td>
</tr>
```

---

## How it works after applying

| Column | What it shows |
|--------|--------------|
| Sys. Pieces | System quantity in whole pieces — read only, for reference |
| Return Pieces | Editable whole number — what the user physically counted |
| Cases | Back-calculated from pieces ÷ selling_qty — updates live — what gets sent to PS365 |

- Validation blocks sending if pieces < 1 or pieces > system pieces
- Totals row shows total pieces and total cases
- PS365 receives cases (no backend change needed)
- The print slip still works as-is — it reads from the stock cache, not the modal
