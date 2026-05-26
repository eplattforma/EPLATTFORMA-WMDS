# Supplier Returns — V6 UI Redesign Instructions

Three changes. Apply in order.

---

## What changes and why

| Problem in current UI | Fix |
|---|---|
| All suppliers expand at once — hard to find one supplier | Supplier selector dropdown at top — JS filter, instant |
| Open POs are a separate panel at the bottom, disconnected from the supplier | Move open POs inside each supplier's card |
| No way to record that items were physically collected | "Mark as Collected" button per PO |
| PO modal shows cases only — staff can't easily count | Add pieces column to PO modal |

---

## CHANGE 1 — Add `collected_at` column to tracking table

Edit `update_supplier_return_po_tracking_schema.py`. Add two lines inside the `try:` block, after the existing `CREATE TABLE IF NOT EXISTS` call:

```python
db.session.execute(text("""
    ALTER TABLE supplier_return_po_tracking
    ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP
"""))
db.session.execute(text("""
    ALTER TABLE supplier_return_po_tracking
    ADD COLUMN IF NOT EXISTS collected_by VARCHAR(64)
"""))
```

The full function should look like this:

```python
def update_supplier_return_po_tracking_schema():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS supplier_return_po_tracking (
                id                SERIAL PRIMARY KEY,
                cart_code         VARCHAR(128) NOT NULL,
                po_id_365         VARCHAR(64),
                supplier_code_365 VARCHAR(64)  NOT NULL,
                supplier_name     VARCHAR(255),
                sent_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
                sent_by           VARCHAR(64),
                CONSTRAINT uq_srpt_cart_code UNIQUE (cart_code)
            )
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_srpt_supplier
                ON supplier_return_po_tracking (supplier_code_365)
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_srpt_sent_at
                ON supplier_return_po_tracking (sent_at)
        """))
        # V6 additions
        db.session.execute(text("""
            ALTER TABLE supplier_return_po_tracking
            ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP
        """))
        db.session.execute(text("""
            ALTER TABLE supplier_return_po_tracking
            ADD COLUMN IF NOT EXISTS collected_by VARCHAR(64)
        """))
        db.session.commit()
        logger.info("supplier_return_po_tracking schema ensured")
    except Exception as e:
        db.session.rollback()
        logger.warning("supplier_return_po_tracking schema update failed (non-fatal): %s", e)
```

---

## CHANGE 2 — Add `mark-collected` route to `blueprints/supplier_returns.py`

Add this new route at the end of the file (after `api_create_po`):

```python
@supplier_returns_bp.route("/mark-collected", methods=["POST"])
@login_required
def mark_collected():
    """
    Record that a supplier physically collected the items on a return PO.
    This is a local record only — does NOT update PS365.
    The Purchase Return must still be processed in PS365 to deduct stock.
    """
    payload   = request.get_json(silent=True) or {}
    po_id_365 = (payload.get("po_id_365") or "").strip()
    cart_code = (payload.get("cart_code") or "").strip()

    if not po_id_365 and not cart_code:
        return jsonify({"success": False, "error": "po_id_365 or cart_code required"}), 400

    try:
        from app import db
        from models import SupplierReturnPoTracking
        from sqlalchemy import text
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = getattr(current_user, "username", "system")

        # Find by po_id_365 or cart_code
        row = None
        if po_id_365:
            row = SupplierReturnPoTracking.query.filter_by(po_id_365=po_id_365).first()
        if not row and cart_code:
            row = SupplierReturnPoTracking.query.filter_by(cart_code=cart_code).first()

        if not row:
            # PO exists in PS365 but not in our tracking table (e.g. created externally)
            # Insert a minimal tracking row
            if cart_code:
                row = SupplierReturnPoTracking(
                    cart_code         = cart_code,
                    po_id_365         = po_id_365 or cart_code,
                    supplier_code_365 = payload.get("supplier_code_365", ""),
                    supplier_name     = payload.get("supplier_name", ""),
                    collected_at      = now,
                    collected_by      = user,
                )
                db.session.add(row)
            else:
                return jsonify({"success": False, "error": "PO not found in tracking table"}), 404
        else:
            row.collected_at = now
            row.collected_by = user

        db.session.commit()
        logger.info("[Returns] PO %s marked as collected by %s", po_id_365 or cart_code, user)
        return jsonify({"success": True, "collected_at": now.strftime("%d/%m/%Y %H:%M")})

    except Exception as e:
        logger.exception("[Returns] mark-collected failed for %s", po_id_365 or cart_code)
        return jsonify({"success": False, "error": str(e)}), 500
```

Also add `collected_at` to the `SupplierReturnPoTracking` model in `models.py`. Find the class and add two lines:

```python
class SupplierReturnPoTracking(db.Model):
    ...
    sent_by          = db.Column(db.String(64),  nullable=True)
    collected_at     = db.Column(db.DateTime,    nullable=True)   # ADD THIS
    collected_by     = db.Column(db.String(64),  nullable=True)   # ADD THIS
```

---

## CHANGE 3 — Replace `templates/supplier_returns/index.html`

Replace the entire file with the following:

```html
{% extends "base.html" %}
{% block title %}Supplier Returns — Store 100{% endblock %}

{% block content %}
<div class="container-fluid py-3">

  {# ── Header ── #}
  <div class="d-flex align-items-center justify-content-between mb-3">
    <div>
      <h4 class="mb-0">Supplier Returns <span class="badge bg-secondary ms-2">Store 100</span></h4>
      <small class="text-muted">
        {% if data.fetched_at %}
          Last synced: {{ data.fetched_at }}
        {% else %}
          <span class="text-warning">Not yet loaded — click Refresh</span>
        {% endif %}
      </small>
    </div>
    <div class="d-flex gap-2">
      <button class="btn btn-outline-info btn-sm" data-bs-toggle="modal" data-bs-target="#helpModal">
        <i class="fas fa-question-circle"></i>
      </button>
      <button class="btn btn-outline-secondary btn-sm" id="btnRefresh">
        <span id="refreshIcon"><i class="fas fa-sync-alt me-1"></i></span>Refresh
      </button>
    </div>
  </div>

  {# ── Error banner ── #}
  {% if data.error %}
  <div class="alert alert-danger py-2">
    <i class="fas fa-exclamation-triangle me-2"></i>{{ data.error }}
  </div>
  {% endif %}

  {# ── Summary strip ── #}
  <div class="row g-2 mb-3">
    <div class="col-auto">
      <div class="card border-secondary px-3 py-2 text-center">
        <div class="small text-muted">Suppliers</div>
        <div class="fw-bold fs-5">{{ data.groups|length }}</div>
      </div>
    </div>
    <div class="col-auto">
      <div class="card border-secondary px-3 py-2 text-center">
        <div class="small text-muted">Lines</div>
        <div class="fw-bold fs-5">{{ data.total_items }}</div>
      </div>
    </div>
    <div class="col-auto">
      <div class="card border-secondary px-3 py-2 text-center">
        <div class="small text-muted">Available value</div>
        <div class="fw-bold fs-5">€{{ "%.2f"|format(data.total_value) }}</div>
      </div>
    </div>
    {% if data.pending_pos %}
    <div class="col-auto">
      <div class="card border-warning px-3 py-2 text-center">
        <div class="small text-warning">Open collections</div>
        <div class="fw-bold fs-5 text-warning">{{ data.pending_pos|length }}</div>
      </div>
    </div>
    {% endif %}
  </div>

  {# ── Supplier selector ── #}
  {% if data.groups %}
  <div class="mb-3 d-flex align-items-center gap-2">
    <label class="form-label mb-0 text-muted small fw-semibold text-nowrap">Filter supplier:</label>
    <select class="form-select form-select-sm" id="supplierFilter" style="max-width:320px">
      <option value="">— All suppliers —</option>
      {% for group in data.groups %}
      <option value="{{ group.supplier_code_365 or '__unknown__' }}">
        {{ group.supplier_name }}{% if group.supplier_code_365 %} ({{ group.supplier_code_365 }}){% endif %}
        — {{ group.item_rows|length }} line(s)
      </option>
      {% endfor %}
    </select>
    <button class="btn btn-outline-secondary btn-sm" id="btnClearFilter" style="display:none">
      <i class="fas fa-times me-1"></i>Show all
    </button>
  </div>
  {% endif %}

  {# ── No stock ── #}
  {% if not data.groups %}
  <div class="text-center text-muted py-5">
    <i class="fas fa-box-open fa-2x mb-2 d-block"></i>
    No stock in store 100 (RETURNS).
    {% if not data.fetched_at %}
    <div class="mt-2 small">Click <strong>Refresh</strong> to load data from PS365.</div>
    {% endif %}
  </div>
  {% endif %}

  {# ── Supplier groups ── #}
  {% for group in data.groups %}
  {% set gid = loop.index %}
  {% set is_unknown = not group.supplier_code_365 %}

  {# Find open POs for this supplier #}
  {% set supplier_pos = data.pending_pos | selectattr("supplier_code_365", "equalto", group.supplier_code_365) | list %}

  <div class="card mb-3 supplier-group"
       data-supplier-code="{{ group.supplier_code_365 or '__unknown__' }}">

    {# ── Supplier header ── #}
    <div class="card-header d-flex align-items-center justify-content-between py-2
                {% if is_unknown %}bg-light{% endif %}">
      <div class="d-flex align-items-center gap-2 flex-wrap">
        {% if not is_unknown %}
        <input class="form-check-input mt-0 select-all-cb" type="checkbox"
               data-gid="{{ gid }}" title="Select all items">
        {% endif %}
        <span class="fw-bold">{{ group.supplier_name }}</span>
        {% if group.supplier_code_365 %}
        <span class="badge bg-secondary fw-normal">{{ group.supplier_code_365 }}</span>
        {% endif %}
        <span class="badge bg-light text-dark border">{{ group.item_rows|length }} lines</span>
        {% if supplier_pos %}
        <span class="badge bg-warning text-dark">
          <i class="fas fa-clock me-1"></i>{{ supplier_pos|length }} open collection{% if supplier_pos|length > 1 %}s{% endif %}
        </span>
        {% endif %}
      </div>
      <div class="d-flex align-items-center gap-2">
        <span class="text-muted small">€{{ "%.2f"|format(group.total_value) }}</span>
        {% if not is_unknown %}
        <button class="btn btn-primary btn-sm create-po-btn"
                data-gid="{{ gid }}"
                data-supplier="{{ group.supplier_code_365 }}"
                data-supplier-name="{{ group.supplier_name }}">
          <i class="fas fa-truck-loading me-1"></i>New Collection
        </button>
        {% endif %}
        <button class="btn btn-link btn-sm p-0 text-muted collapse-btn"
                data-bs-toggle="collapse" data-bs-target="#grp{{ gid }}">
          <i class="fas fa-chevron-down"></i>
        </button>
      </div>
    </div>

    <div id="grp{{ gid }}" class="collapse show">

      {# ── Open collections for this supplier ── #}
      {% if supplier_pos %}
      <div class="border-bottom bg-warning bg-opacity-10 px-3 py-2">
        <div class="small fw-semibold text-warning-emphasis mb-2">
          <i class="fas fa-clock me-1"></i>Open Collections
        </div>
        {% for po in supplier_pos %}
        {% set outstanding_lines = po.lines | selectattr("outstanding", "gt", 0) | list %}
        <div class="d-flex align-items-center justify-content-between py-1 border-bottom border-warning-subtle po-row"
             data-po-id="{{ po.po_id }}"
             data-cart-code="">
          <div class="d-flex align-items-center gap-3 flex-wrap">
            <span class="font-monospace small fw-bold">{{ po.po_id }}</span>
            <span class="badge bg-secondary">{{ po.status_code }}</span>
            <span class="text-muted small">{{ po.order_date }}</span>
            {% if outstanding_lines %}
            <span class="badge bg-warning text-dark">
              {{ outstanding_lines|length }} line(s) outstanding
            </span>
            {% else %}
            <span class="badge bg-success">All received</span>
            {% endif %}
            {% if po.comments %}
            <span class="text-muted small fst-italic">{{ po.comments }}</span>
            {% endif %}
          </div>
          <div class="d-flex align-items-center gap-2">
            {# Expandable item detail #}
            <button class="btn btn-link btn-sm p-0 text-muted po-detail-btn"
                    data-bs-toggle="collapse"
                    data-bs-target="#po-detail-{{ gid }}-{{ loop.index }}">
              <i class="fas fa-list-ul me-1"></i><span class="small">Items</span>
            </button>
            {# Mark as collected #}
            {% if outstanding_lines %}
            <button class="btn btn-success btn-sm mark-collected-btn"
                    data-po-id="{{ po.po_id }}"
                    data-supplier-code="{{ po.supplier_code_365 }}"
                    data-supplier-name="{{ po.supplier_name }}">
              <i class="fas fa-check me-1"></i>Mark Collected
            </button>
            {% else %}
            <span class="badge bg-success py-2">
              <i class="fas fa-check-circle me-1"></i>Collected
            </span>
            {% endif %}
          </div>
        </div>
        {# PO item detail (collapsible) #}
        <div class="collapse" id="po-detail-{{ gid }}-{{ loop.index }}">
          <table class="table table-sm table-borderless mb-0 mt-1 ms-3" style="max-width:600px">
            <thead class="small text-muted">
              <tr>
                <th>Item</th>
                <th class="text-end">Ordered</th>
                <th class="text-end">Received</th>
                <th class="text-end">Outstanding</th>
              </tr>
            </thead>
            <tbody class="small">
              {% for ln in po.lines %}
              <tr class="{% if ln.outstanding > 0 %}text-warning-emphasis{% else %}text-muted{% endif %}">
                <td class="font-monospace">{{ ln.item_code_365 }}</td>
                <td class="text-end">{{ "%.4g"|format(ln.qty) }}</td>
                <td class="text-end">{{ "%.4g"|format(ln.received) }}</td>
                <td class="text-end fw-bold">
                  {% if ln.outstanding > 0 %}{{ "%.4g"|format(ln.outstanding) }}
                  {% else %}<span class="text-success">✓</span>{% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% endfor %}
      </div>
      {% endif %}

      {# ── Available stock table ── #}
      {% set available_rows = group.item_rows | rejectattr("fully_committed") | list %}
      {% set committed_rows = group.item_rows | selectattr("fully_committed") | list %}

      {% if available_rows %}
      <div class="table-responsive">
        <table class="table table-sm table-hover mb-0 returns-table">
          <thead class="table-light">
            <tr>
              {% if not is_unknown %}<th style="width:36px"></th>{% endif %}
              <th>Code</th>
              <th>Description</th>
              <th class="text-end">Cases</th>
              <th class="text-end">Pieces</th>
              <th class="text-end">Cost/case</th>
              <th class="text-end">Value</th>
              <th class="text-end" style="width:100px">
                Count <i class="fas fa-info-circle text-muted small"
                         title="Enter physical piece count to compare with system"></i>
              </th>
              <th class="text-end" style="width:70px">Diff</th>
            </tr>
          </thead>
          <tbody>
            {% for item in available_rows %}
            <tr class="item-row"
                data-item="{{ item.item_code_365 }}"
                data-qty="{{ item.available_cases }}"
                data-cost="{{ item.cost_price or 0 }}"
                data-pieces="{{ item.pieces_available if item.pieces_available is not none else '' }}"
                data-gid="{{ gid }}">
              {% if not is_unknown %}
              <td>
                <input class="form-check-input item-cb" type="checkbox"
                       data-gid="{{ gid }}"
                       data-item="{{ item.item_code_365 }}"
                       data-qty="{{ item.available_cases }}"
                       data-cost="{{ item.cost_price or 0 }}"
                       data-pieces="{{ item.pieces_available if item.pieces_available is not none else '' }}">
              </td>
              {% endif %}
              <td class="font-monospace small text-muted">{{ item.item_code_365 }}</td>
              <td>{{ item.item_name }}</td>
              <td class="text-end fw-bold text-success">{{ item.available_display }}</td>
              <td class="text-end fw-bold">
                {% if item.pieces_available is not none %}{{ item.pieces_available }}
                {% else %}<span class="text-muted">—</span>{% endif %}
              </td>
              <td class="text-end text-muted small">
                {% if item.cost_price %}€{{ "%.4f"|format(item.cost_price) }}{% else %}—{% endif %}
              </td>
              <td class="text-end">
                {% if item.value_available %}€{{ "%.2f"|format(item.value_available) }}{% else %}—{% endif %}
              </td>
              <td class="text-end">
                <input type="number" min="0" step="1"
                       class="form-control form-control-sm text-end physical-input"
                       style="width:80px;display:inline-block"
                       placeholder="pcs"
                       data-system-pieces="{{ item.pieces_available if item.pieces_available is not none else '' }}">
              </td>
              <td class="text-end diff-cell">
                <span class="diff-badge text-muted">—</span>
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% endif %}

      {# ── Fully committed rows (collapsed by default) ── #}
      {% if committed_rows %}
      <div class="px-3 py-2 border-top bg-light">
        <button class="btn btn-link btn-sm p-0 text-muted" data-bs-toggle="collapse"
                data-bs-target="#committed{{ gid }}">
          <i class="fas fa-lock me-1"></i>
          {{ committed_rows|length }} item(s) fully on open PO
          <i class="fas fa-chevron-down ms-1 small"></i>
        </button>
        <div class="collapse" id="committed{{ gid }}">
          <table class="table table-sm table-borderless mt-2 mb-0">
            <tbody>
              {% for item in committed_rows %}
              <tr class="text-muted">
                <td class="font-monospace small">{{ item.item_code_365 }}</td>
                <td>{{ item.item_name }}</td>
                <td class="text-end">
                  <span class="badge bg-warning text-dark">Fully on PO</span>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      {% endif %}

      {% if not available_rows and not committed_rows %}
      <div class="px-3 py-2 text-muted small">No available stock for this supplier.</div>
      {% endif %}

    </div>{# /collapse #}
  </div>
  {% endfor %}

</div>{# /container #}

{# ── Help modal ── #}
<div class="modal fade" id="helpModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">How this works</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body small">
        <p><strong>Stock</strong> comes from PS365 store 100 (RETURNS). Click <strong>Refresh</strong> to sync. Page loads use the local database — no API call needed on navigation.</p>
        <p><strong>Cases vs Pieces:</strong> Cases = raw PS365 quantity. Pieces = cases × pack size (e.g. 0.07 cases × 15 = 1 piece).</p>
        <p><strong>New Collection:</strong> Select items for a supplier and click New Collection. This sends a Purchase Order to PS365 with status RETURN. The items show as "on PO" and are excluded from future collections.</p>
        <p><strong>Open Collections:</strong> Shown inside each supplier card. Outstanding = ordered − already received by PS365.</p>
        <p><strong>Mark Collected:</strong> Click when the supplier has physically taken the items. This records the handover locally. You must also process the <strong>Purchase Return in PS365</strong> to deduct the stock — this system cannot do that automatically.</p>
        <p><strong>Count column:</strong> Enter the physical piece count to compare against the system. Diff shows instantly. Nothing is saved.</p>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>
      </div>
    </div>
  </div>
</div>

{# ── Mark Collected confirmation modal ── #}
<div class="modal fade" id="collectModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title"><i class="fas fa-check-circle me-2 text-success"></i>Mark as Collected</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p>Confirm that <strong id="collectSupplierName"></strong> has physically collected all items from:</p>
        <p class="font-monospace fw-bold" id="collectPoId"></p>
        <div class="alert alert-warning py-2 mt-3 mb-0 small">
          <i class="fas fa-exclamation-triangle me-1"></i>
          <strong>Remember:</strong> You must also process the <strong>Purchase Return in PS365</strong>
          to deduct the stock from store 100. This button only records the physical handover.
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-success" id="btnConfirmCollected">
          <i class="fas fa-check me-1"></i>Yes, items collected
        </button>
      </div>
    </div>
  </div>
</div>

{# ── Create PO modal ── #}
<div class="modal fade" id="poModal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title"><i class="fas fa-truck-loading me-2"></i>New Return Collection</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p class="mb-2 text-muted">
          Supplier: <strong id="poSupplierName"></strong>
          <span class="badge bg-secondary ms-1" id="poSupplierCode"></span>
        </p>
        <table class="table table-sm" id="poLinesTable">
          <thead class="table-light">
            <tr>
              <th>Item Code</th>
              <th>Description</th>
              <th class="text-end">Cases</th>
              <th class="text-end">Pieces</th>
              <th class="text-end">Cost</th>
            </tr>
          </thead>
          <tbody></tbody>
          <tfoot class="table-light fw-bold" id="poLinesFooter" style="display:none">
            <tr>
              <td colspan="4" class="text-end">Total value</td>
              <td class="text-end" id="poTotalValue"></td>
            </tr>
          </tfoot>
        </table>
        <div class="mt-2">
          <label class="form-label small">Comments (optional)</label>
          <input type="text" class="form-control form-control-sm" id="poComments"
                 placeholder="e.g. Damaged stock — supplier reference 12345">
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" id="btnSendPO">
          <i class="fas fa-paper-plane me-1"></i>Send to PS365
        </button>
      </div>
    </div>
  </div>
</div>

{# ── Toast ── #}
<div class="position-fixed bottom-0 end-0 p-3" style="z-index:1100">
  <div id="poToast" class="toast align-items-center" role="alert">
    <div class="d-flex">
      <div class="toast-body" id="poToastMsg"></div>
      <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>
  </div>
</div>
{% endblock %}

{% block scripts %}
<script>
// ---------------------------------------------------------------------------
// Supplier filter
// ---------------------------------------------------------------------------
var filterEl  = document.getElementById("supplierFilter");
var clearBtn  = document.getElementById("btnClearFilter");

function applyFilter(code) {
  document.querySelectorAll(".supplier-group").forEach(function(card) {
    if (!code || card.dataset.supplierCode === code) {
      card.style.display = "";
    } else {
      card.style.display = "none";
    }
  });
  clearBtn.style.display = code ? "" : "none";
}

if (filterEl) {
  filterEl.addEventListener("change", function() { applyFilter(this.value); });
}
if (clearBtn) {
  clearBtn.addEventListener("click", function() {
    filterEl.value = "";
    applyFilter("");
  });
}

// ---------------------------------------------------------------------------
// Physical stock diff
// ---------------------------------------------------------------------------
document.querySelectorAll(".physical-input").forEach(function(inp) {
  inp.addEventListener("input", function() {
    var sys   = inp.dataset.systemPieces;
    var badge = inp.closest("tr").querySelector(".diff-badge");
    if (!inp.value || sys === "") {
      badge.textContent = "—"; badge.className = "diff-badge text-muted"; return;
    }
    var diff = parseInt(inp.value, 10) - parseInt(sys, 10);
    if (diff === 0)    { badge.textContent = "✓";        badge.className = "diff-badge text-success fw-bold"; }
    else if (diff > 0) { badge.textContent = "+" + diff; badge.className = "diff-badge text-warning fw-bold"; }
    else               { badge.textContent = "" + diff;  badge.className = "diff-badge text-danger fw-bold"; }
  });
});

// ---------------------------------------------------------------------------
// Select-all checkboxes
// ---------------------------------------------------------------------------
document.querySelectorAll(".select-all-cb").forEach(function(cb) {
  cb.addEventListener("change", function() {
    document.querySelectorAll(".item-cb[data-gid='" + cb.dataset.gid + "']")
      .forEach(function(c) { if (!c.disabled) c.checked = cb.checked; });
  });
});
document.querySelectorAll(".item-cb").forEach(function(cb) {
  cb.addEventListener("change", function() {
    var all   = document.querySelectorAll(".item-cb[data-gid='" + cb.dataset.gid + "']");
    var allCb = document.querySelector(".select-all-cb[data-gid='" + cb.dataset.gid + "']");
    if (allCb) allCb.checked = Array.from(all).every(function(c) { return c.checked || c.disabled; });
  });
});

// ---------------------------------------------------------------------------
// Collapse chevrons
// ---------------------------------------------------------------------------
document.querySelectorAll("[data-bs-toggle='collapse']").forEach(function(btn) {
  var target = document.querySelector(btn.getAttribute("data-bs-target"));
  if (!target) return;
  target.addEventListener("hidden.bs.collapse", function() {
    var i = btn.querySelector("i.fa-chevron-down");
    if (i) { i.classList.remove("fa-chevron-down"); i.classList.add("fa-chevron-up"); }
  });
  target.addEventListener("shown.bs.collapse", function() {
    var i = btn.querySelector("i.fa-chevron-up");
    if (i) { i.classList.remove("fa-chevron-up"); i.classList.add("fa-chevron-down"); }
  });
});

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------
document.getElementById("btnRefresh").addEventListener("click", function() {
  var btn = this;
  btn.disabled = true;
  document.getElementById("refreshIcon").innerHTML =
    '<span class="spinner-border spinner-border-sm me-1"></span>';
  fetch("/supplier-returns/refresh", {
    method: "POST",
    headers: { "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "" }
  }).finally(function() { location.reload(); });
});

// ---------------------------------------------------------------------------
// Mark as Collected
// ---------------------------------------------------------------------------
var _collectPoId        = null;
var _collectCartCode    = null;
var _collectSupCode     = null;
var _collectSupName     = null;
var _collectBtn         = null;

document.querySelectorAll(".mark-collected-btn").forEach(function(btn) {
  btn.addEventListener("click", function() {
    _collectPoId     = btn.dataset.poId;
    _collectCartCode = btn.dataset.cartCode || "";
    _collectSupCode  = btn.dataset.supplierCode;
    _collectSupName  = btn.dataset.supplierName;
    _collectBtn      = btn;
    document.getElementById("collectSupplierName").textContent = _collectSupName || _collectSupCode;
    document.getElementById("collectPoId").textContent = "PO " + _collectPoId;
    new bootstrap.Modal(document.getElementById("collectModal")).show();
  });
});

document.getElementById("btnConfirmCollected").addEventListener("click", function() {
  var confirmBtn = this;
  confirmBtn.disabled = true;
  confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';

  fetch("/supplier-returns/mark-collected", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
    },
    body: JSON.stringify({
      po_id_365:         _collectPoId,
      cart_code:         _collectCartCode,
      supplier_code_365: _collectSupCode,
      supplier_name:     _collectSupName,
    }),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    confirmBtn.disabled = false;
    confirmBtn.innerHTML = '<i class="fas fa-check me-1"></i>Yes, items collected';
    bootstrap.Modal.getInstance(document.getElementById("collectModal")).hide();

    if (data.success) {
      // Replace the Mark Collected button with a "Collected" badge
      if (_collectBtn) {
        _collectBtn.outerHTML =
          '<span class="badge bg-success py-2">' +
          '<i class="fas fa-check-circle me-1"></i>Collected ' + data.collected_at + '</span>';
      }
      showToast("success", "✓ Collection recorded for PO " + _collectPoId);
    } else {
      showToast("danger", "Error: " + (data.error || "Unknown"));
    }
  })
  .catch(function(err) {
    confirmBtn.disabled = false;
    confirmBtn.innerHTML = '<i class="fas fa-check me-1"></i>Yes, items collected';
    showToast("danger", "Network error: " + err.message);
  });
});

// ---------------------------------------------------------------------------
// Create PO (New Collection)
// ---------------------------------------------------------------------------
var _currentSupplier = null;
var _currentLines    = [];

document.querySelectorAll(".create-po-btn").forEach(function(btn) {
  btn.addEventListener("click", function() {
    var gid  = btn.dataset.gid;
    var code = btn.dataset.supplier;
    var name = btn.dataset.supplierName;

    var checked = Array.from(
      document.querySelectorAll(".item-cb[data-gid='" + gid + "']:checked")
    );
    if (checked.length === 0) {
      checked = Array.from(
        document.querySelectorAll(".item-cb[data-gid='" + gid + "']:not(:disabled)")
      );
    }
    if (checked.length === 0) {
      showToast("warning", "No items available for this supplier.");
      return;
    }

    _currentSupplier = { code: code, name: name };
    _currentLines    = checked.map(function(c) {
      return {
        item_code_365: c.dataset.item,
        line_quantity: parseFloat(c.dataset.qty),
        cost_price:    c.dataset.cost ? parseFloat(c.dataset.cost) : null,
        pieces:        c.dataset.pieces ? parseInt(c.dataset.pieces, 10) : null,
      };
    });

    document.getElementById("poSupplierName").textContent = name;
    document.getElementById("poSupplierCode").textContent = code;

    var tbody = document.querySelector("#poLinesTable tbody");
    tbody.innerHTML = "";
    var totalValue = 0;

    _currentLines.forEach(function(ln) {
      // Find item name from the row
      var row  = document.querySelector("tr[data-item='" + ln.item_code_365 + "']");
      var name = row ? row.querySelectorAll("td")[2].textContent.trim() : "";
      var lineVal = ln.cost_price && ln.line_quantity
          ? (ln.line_quantity * ln.cost_price) : null;
      if (lineVal) totalValue += lineVal;

      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td class='font-monospace small'>" + ln.item_code_365 + "</td>" +
        "<td class='small'>" + name + "</td>" +
        "<td class='text-end'>" + ln.line_quantity + "</td>" +
        "<td class='text-end'>" + (ln.pieces !== null ? ln.pieces : "—") + "</td>" +
        "<td class='text-end'>" + (ln.cost_price ? "€" + ln.cost_price.toFixed(4) : "—") + "</td>";
      tbody.appendChild(tr);
    });

    var footer = document.getElementById("poLinesFooter");
    if (totalValue > 0) {
      document.getElementById("poTotalValue").textContent = "€" + totalValue.toFixed(2);
      footer.style.display = "";
    } else {
      footer.style.display = "none";
    }

    document.getElementById("poComments").value = "";
    new bootstrap.Modal(document.getElementById("poModal")).show();
  });
});

document.getElementById("btnSendPO").addEventListener("click", function() {
  var btn = this;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Sending…';

  fetch("/supplier-returns/create-po", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
    },
    body: JSON.stringify({
      supplier_code_365: _currentSupplier.code,
      supplier_name:     _currentSupplier.name,
      lines:             _currentLines,
      comments:          document.getElementById("poComments").value.trim(),
    }),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-paper-plane me-1"></i>Send to PS365';
    bootstrap.Modal.getInstance(document.getElementById("poModal")).hide();

    if (data.success) {
      showToast("success", "✓ PO " + data.po_code + " sent — " + data.lines_sent + " lines");
      _currentLines.forEach(function(ln) {
        document.querySelectorAll("tr[data-item='" + ln.item_code_365 + "']").forEach(function(row) {
          row.style.opacity = "0.45";
          var cb = row.querySelector(".item-cb");
          if (cb) cb.disabled = true;
          var cells = row.querySelectorAll("td");
          if (cells[3]) cells[3].innerHTML =
            '<span class="badge bg-warning text-dark">On PO</span>';
        });
      });
      var firstCb = _currentLines[0]
        ? document.querySelector(".item-cb[data-item='" + _currentLines[0].item_code_365 + "']")
        : null;
      var gid = firstCb ? firstCb.dataset.gid : null;
      if (gid) {
        var poBtn = document.querySelector(".create-po-btn[data-gid='" + gid + "']");
        if (poBtn) { poBtn.disabled = true; poBtn.textContent = "PO sent — refresh"; }
      }
    } else {
      showToast("danger", "Error: " + (data.error || "Unknown"));
    }
  })
  .catch(function(err) {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-paper-plane me-1"></i>Send to PS365';
    showToast("danger", "Network error: " + err.message);
  });
});

// ---------------------------------------------------------------------------
// Toast helper
// ---------------------------------------------------------------------------
function showToast(type, message) {
  var toast = document.getElementById("poToast");
  var msg   = document.getElementById("poToastMsg");
  toast.className = "toast align-items-center border-0 bg-" + type + " " +
    (type === "warning" ? "text-dark" : "text-white");
  msg.textContent = message;
  new bootstrap.Toast(toast, { delay: 6000 }).show();
}
</script>
{% endblock %}
```

---

## What this looks like after applying

**Top of page:** Supplier filter dropdown. Select a supplier → all others hide instantly. "Show all" button reappears.

**Each supplier card contains:**
- Header: supplier name, code, line count, "open collections" badge if applicable, **New Collection** button
- Open Collections section (only shown if POs exist): each PO with status, date, outstanding lines, "Items" expand button, and **Mark Collected** button
- Available stock table: item code, description, cases, pieces, cost, value, physical count input, diff
- Fully committed items: collapsed by default, shown as "X items fully on open PO"

**New Collection modal:** Now shows description and pieces column alongside cases and cost.

**Mark Collected modal:** Confirms the physical handover and warns the user to process the Purchase Return in PS365.
