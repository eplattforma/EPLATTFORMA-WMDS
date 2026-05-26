# Supplier Returns — V7 Supplier Search

Template-only change. No backend, no new routes. Two things added:
1. A live search box — type any part of the supplier name or code, non-matching suppliers hide instantly
2. A Collapse All / Expand All toggle button

---

## CHANGE — Edit `templates/supplier_returns/index.html`

### Step 1 — Replace the header toolbar

Find the existing header block:
```html
<div class="d-flex gap-2">
  <button class="btn btn-outline-info btn-sm" data-bs-toggle="modal" data-bs-target="#helpModal">
    <i class="fas fa-question-circle"></i>
  </button>
  <button class="btn btn-outline-secondary btn-sm" id="btnRefresh">
    <span id="refreshIcon"><i class="fas fa-sync-alt me-1"></i></span> Refresh
  </button>
</div>
```

Replace with:
```html
<div class="d-flex gap-2 align-items-center">
  <button class="btn btn-outline-info btn-sm" data-bs-toggle="modal" data-bs-target="#helpModal">
    <i class="fas fa-question-circle"></i>
  </button>
  <button class="btn btn-outline-secondary btn-sm" id="btnCollapseAll" title="Collapse all suppliers">
    <i class="fas fa-compress-alt me-1"></i>Collapse All
  </button>
  <button class="btn btn-outline-secondary btn-sm" id="btnRefresh">
    <span id="refreshIcon"><i class="fas fa-sync-alt me-1"></i></span>Refresh
  </button>
</div>
```

---

### Step 2 — Add the search bar

Find the summary strip block (the row of stat cards). It starts with:
```html
<div class="row g-2 mb-3">
```

**Immediately before** that row, insert:
```html
{# ── Supplier search ── #}
{% if data.groups %}
<div class="mb-3">
  <div class="input-group" style="max-width:400px">
    <span class="input-group-text bg-body-secondary border-secondary">
      <i class="fas fa-search text-muted"></i>
    </span>
    <input type="text" class="form-control" id="supplierSearch"
           placeholder="Search supplier name or code…"
           autocomplete="off">
    <button class="btn btn-outline-secondary" id="btnClearSearch"
            style="display:none" title="Clear search">
      <i class="fas fa-times"></i>
    </button>
  </div>
  <div id="searchNoResults" class="text-muted small mt-1" style="display:none">
    No matching suppliers.
  </div>
</div>
{% endif %}
```

---

### Step 3 — Add the JS (inside the `{% block scripts %}` section)

Add the following block at the **top** of the existing `<script>` tag, before any other JS:

```javascript
// ---------------------------------------------------------------------------
// Supplier search + Collapse All
// ---------------------------------------------------------------------------
var searchEl    = document.getElementById("supplierSearch");
var clearBtn    = document.getElementById("btnClearSearch");
var noResults   = document.getElementById("searchNoResults");
var collapseBtn = document.getElementById("btnCollapseAll");
var allCollapsed = false;

function applySearch(query) {
  var q       = (query || "").trim().toLowerCase();
  var visible = 0;

  document.querySelectorAll(".supplier-group").forEach(function(card) {
    var name = (card.dataset.supplierName || "").toLowerCase();
    var code = (card.dataset.supplierCode || "").toLowerCase();
    var match = !q || name.includes(q) || code.includes(q);
    card.style.display = match ? "" : "none";
    if (match) visible++;

    // If a search is active, expand matching suppliers so content is visible
    if (match && q) {
      var collapseEl = card.querySelector(".collapse");
      if (collapseEl && !collapseEl.classList.contains("show")) {
        new bootstrap.Collapse(collapseEl, { toggle: false }).show();
      }
    }
  });

  clearBtn.style.display = q ? "" : "none";
  if (noResults) noResults.style.display = (visible === 0 && q) ? "" : "none";
}

if (searchEl) {
  searchEl.addEventListener("input", function() {
    applySearch(this.value);
  });
  // Allow Escape key to clear
  searchEl.addEventListener("keydown", function(e) {
    if (e.key === "Escape") {
      this.value = "";
      applySearch("");
      this.blur();
    }
  });
}

if (clearBtn) {
  clearBtn.addEventListener("click", function() {
    searchEl.value = "";
    applySearch("");
    searchEl.focus();
  });
}

if (collapseBtn) {
  collapseBtn.addEventListener("click", function() {
    allCollapsed = !allCollapsed;
    document.querySelectorAll(".supplier-group .collapse").forEach(function(el) {
      var bsCollapse = bootstrap.Collapse.getOrCreateInstance(el, { toggle: false });
      allCollapsed ? bsCollapse.hide() : bsCollapse.show();
    });
    collapseBtn.innerHTML = allCollapsed
      ? '<i class="fas fa-expand-alt me-1"></i>Expand All'
      : '<i class="fas fa-compress-alt me-1"></i>Collapse All';
  });
}
```

---

### Step 4 — Add `data-supplier-name` to each supplier card

The search JS reads `card.dataset.supplierName` and `card.dataset.supplierCode`. The cards already have `data-supplier-code` — add the name too.

Find:
```html
<div class="card mb-3 supplier-group" data-supplier="{{ group.supplier_code_365 }}">
```

Replace with:
```html
<div class="card mb-3 supplier-group"
     data-supplier-code="{{ group.supplier_code_365 or '' }}"
     data-supplier-name="{{ group.supplier_name or '' }}">
```

---

## How it works after applying

- **Search box** is at the top of the page, above the summary cards
- Typing instantly hides non-matching suppliers and expands the matching one(s) so their items are visible
- Pressing **Escape** or clicking **×** clears the search and shows all suppliers again
- **Collapse All** button collapses every supplier card to just the header — useful for getting an overview of all 25 suppliers at once
- Clicking **Collapse All** again becomes **Expand All** — one more click opens everything

The search is purely client-side — no server call, no page reload.
