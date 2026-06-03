# EP SmartGrowth — Admin Suppliers Page

Adds a **Suppliers** page under the Admin menu. Manages `ReplenishmentSupplier`
records: supplier code, name, email, CC, active status, notes.

Since suppliers are NOT synced from PS365, records must be added manually.
The page shows a **warning banner** listing any suppliers that exist in
`ps_items_dw` but have not yet been added here — so nothing gets missed.

---

## CHANGE 1 — `models.py` — email fields (if not already applied)

If `Replit_SupplierEmail_Setup.md` Change 1 has not been applied yet, apply it now:
add `email` and `email_cc` to `ReplenishmentSupplier`.

---

## CHANGE 2 — Schema migration (if not already applied)

If `Replit_SupplierEmail_Setup.md` Change 2 has not been applied yet, apply the
`ALTER TABLE replenishment_suppliers ADD COLUMN IF NOT EXISTS email / email_cc`
migration in `main.py`.

---

## CHANGE 3 — New admin route file `routes_admin_suppliers.py`

Create a new file `routes_admin_suppliers.py` in the project root:

```python
"""
Admin Suppliers — CRUD for ReplenishmentSupplier.
Not synced from PS365; managed manually here.
"""
import logging
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user

from app import db
from models import ReplenishmentSupplier
from sqlalchemy import text

logger = logging.getLogger(__name__)

admin_suppliers_bp = Blueprint("admin_suppliers", __name__, url_prefix="/admin/suppliers")


def _require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("login"))
        if (getattr(current_user, "role", "") or "").lower() not in ("admin", "warehouse_manager"):
            flash("Access denied.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


# ── List ────────────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/")
@login_required
@_require_admin
def supplier_list():
    suppliers = (ReplenishmentSupplier.query
                 .order_by(ReplenishmentSupplier.sort_order,
                           ReplenishmentSupplier.supplier_name)
                 .all())

    # Flag suppliers in ps_items_dw not yet added here
    known_codes = {s.supplier_code for s in suppliers}
    dw_rows = db.session.execute(text("""
        SELECT DISTINCT supplier_code_365, MIN(supplier_name) AS supplier_name
        FROM ps_items_dw
        WHERE supplier_code_365 IS NOT NULL AND supplier_code_365 <> ''
        GROUP BY supplier_code_365
        ORDER BY MIN(supplier_name)
    """)).fetchall()
    missing_suppliers = [
        {"code": r[0], "name": r[1] or r[0]}
        for r in dw_rows
        if (r[0] or "").strip().upper() not in known_codes
    ]

    return render_template("admin/suppliers.html",
                           suppliers=suppliers,
                           missing_suppliers=missing_suppliers)


# ── Create ──────────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/create", methods=["POST"])
@login_required
@_require_admin
def supplier_create():
    code = (request.form.get("supplier_code") or "").strip().upper()
    name = (request.form.get("supplier_name") or "").strip()
    if not code or not name:
        flash("Supplier code and name are required.", "danger")
        return redirect(url_for("admin_suppliers.supplier_list"))

    existing = ReplenishmentSupplier.query.filter_by(supplier_code=code).first()
    if existing:
        flash(f"Supplier '{code}' already exists.", "warning")
        return redirect(url_for("admin_suppliers.supplier_list"))

    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0

    s = ReplenishmentSupplier(
        supplier_code=code,
        supplier_name=name,
        email=(request.form.get("email") or "").strip() or None,
        email_cc=(request.form.get("email_cc") or "").strip() or None,
        notes=(request.form.get("notes") or "").strip() or None,
        sort_order=sort_order,
        is_active=True,
    )
    db.session.add(s)
    db.session.commit()
    flash(f"Supplier '{name}' created.", "success")
    return redirect(url_for("admin_suppliers.supplier_list"))


# ── Update ──────────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/<int:supplier_id>/update", methods=["POST"])
@login_required
@_require_admin
def supplier_update(supplier_id):
    s = ReplenishmentSupplier.query.get_or_404(supplier_id)
    name = (request.form.get("supplier_name") or "").strip()
    if not name:
        flash("Supplier name is required.", "danger")
        return redirect(url_for("admin_suppliers.supplier_list"))

    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = s.sort_order or 0

    s.supplier_name = name
    s.email         = (request.form.get("email")    or "").strip() or None
    s.email_cc      = (request.form.get("email_cc") or "").strip() or None
    s.notes         = (request.form.get("notes")    or "").strip() or None
    s.sort_order    = sort_order
    db.session.commit()
    flash(f"Supplier '{name}' updated.", "success")
    return redirect(url_for("admin_suppliers.supplier_list"))


# ── Toggle active ────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/<int:supplier_id>/toggle", methods=["POST"])
@login_required
@_require_admin
def supplier_toggle(supplier_id):
    s = ReplenishmentSupplier.query.get_or_404(supplier_id)
    s.is_active = not s.is_active
    db.session.commit()
    state = "activated" if s.is_active else "deactivated"
    flash(f"Supplier '{s.supplier_name}' {state}.", "success")
    return redirect(url_for("admin_suppliers.supplier_list"))


```

---

## CHANGE 4 — Register blueprint in `main.py`

Find where other blueprints are registered (look for `app.register_blueprint`
calls). Add:

```python
from routes_admin_suppliers import admin_suppliers_bp
app.register_blueprint(admin_suppliers_bp)
```

---

## CHANGE 5 — Template `templates/admin/suppliers.html`

Create this new file:

```html
{% extends "base.html" %}
{% block title %}Admin — Suppliers{% endblock %}
{% block content %}
<div class="container my-4">

  <div class="d-flex align-items-center justify-content-between mb-3 flex-wrap gap-2">
    <h2 class="mb-0"><i class="fas fa-truck me-2 text-primary"></i>Suppliers</h2>
  </div>

  {# ── Missing suppliers warning ────────────────────────────────────────── #}
  {% if missing_suppliers %}
  <div class="alert alert-warning">
    <strong><i class="fas fa-exclamation-triangle me-1"></i>
    {{ missing_suppliers|length }} supplier(s) found in the DW but not yet added here:</strong>
    <ul class="mb-1 mt-2">
      {% for s in missing_suppliers %}
      <li>
        <span class="font-monospace">{{ s.code }}</span> — {{ s.name }}
        <a href="#add-form"
           class="ms-2 small"
           onclick="document.querySelector('[name=supplier_code]').value='{{ s.code }}';
                    document.querySelector('[name=supplier_name]').value='{{ s.name }}';">
          + Add
        </a>
      </li>
      {% endfor %}
    </ul>
    <span class="small text-muted">Click "+ Add" next to any supplier to pre-fill the form below.</span>
  </div>
  {% endif %}

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in messages %}
    <div class="alert alert-{{ cat }} alert-dismissible">
      {{ msg }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    </div>
    {% endfor %}
  {% endwith %}

  {# ── Add new supplier ─────────────────────────────────────────────────── #}
  <div class="card mb-4 border-0 shadow-sm">
    <div class="card-header fw-semibold">Add New Supplier</div>
    <div class="card-body">
      <form method="POST" action="{{ url_for('admin_suppliers.supplier_create') }}">
        <div class="row g-2 align-items-end">
          <div class="col-md-2">
            <label class="form-label small">Code *</label>
            <input name="supplier_code" class="form-control form-control-sm"
                   placeholder="e.g. 10000003" required>
          </div>
          <div class="col-md-3">
            <label class="form-label small">Name *</label>
            <input name="supplier_name" class="form-control form-control-sm"
                   placeholder="Supplier name" required>
          </div>
          <div class="col-md-3">
            <label class="form-label small">
              <i class="fas fa-envelope me-1 text-muted"></i>Order Email
            </label>
            <input type="email" name="email" class="form-control form-control-sm"
                   placeholder="orders@supplier.com">
          </div>
          <div class="col-md-2">
            <label class="form-label small">CC Email(s)</label>
            <input name="email_cc" class="form-control form-control-sm"
                   placeholder="cc@example.com">
          </div>
          <div class="col-md-1">
            <label class="form-label small">Order</label>
            <input name="sort_order" type="number" class="form-control form-control-sm"
                   placeholder="0" value="0">
          </div>
          <div class="col-md-1">
            <button class="btn btn-primary btn-sm w-100">Add</button>
          </div>
        </div>
        <div class="mt-2">
          <label class="form-label small">Notes</label>
          <input name="notes" class="form-control form-control-sm"
                 placeholder="Optional notes">
        </div>
      </form>
    </div>
  </div>

  {# ── Supplier list ────────────────────────────────────────────────────── #}
  {% if suppliers %}
  <div class="card border-0 shadow-sm">
    <div class="card-body p-0">
      <table class="table table-sm table-hover mb-0 align-middle">
        <thead class="table-dark">
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th><i class="fas fa-envelope me-1"></i>Order Email</th>
            <th>CC</th>
            <th>Notes</th>
            <th class="text-center">Active</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for s in suppliers %}
          <tr class="{{ 'text-muted' if not s.is_active }}">
            <td class="font-monospace small">{{ s.supplier_code }}</td>
            <td>
              <form method="POST"
                    action="{{ url_for('admin_suppliers.supplier_update', supplier_id=s.id) }}"
                    class="d-flex gap-1 align-items-center flex-wrap">
                <input name="supplier_name" value="{{ s.supplier_name }}"
                       class="form-control form-control-sm" style="min-width:160px" required>
                <input type="email" name="email" value="{{ s.email or '' }}"
                       placeholder="orders@supplier.com"
                       class="form-control form-control-sm {% if not s.email %}border-warning{% endif %}"
                       style="min-width:190px">
                <input name="email_cc" value="{{ s.email_cc or '' }}"
                       placeholder="CC (optional)"
                       class="form-control form-control-sm" style="min-width:150px">
                <input name="notes" value="{{ s.notes or '' }}"
                       placeholder="Notes"
                       class="form-control form-control-sm" style="min-width:120px">
                <input name="sort_order" type="number" value="{{ s.sort_order or 0 }}"
                       class="form-control form-control-sm" style="width:60px">
                <button class="btn btn-sm btn-outline-primary">Save</button>
              </form>
            </td>
            <td>
              {% if s.email %}
                <span class="badge bg-success">{{ s.email }}</span>
              {% else %}
                <span class="badge bg-warning text-dark">No email</span>
              {% endif %}
            </td>
            <td class="small text-muted">{{ s.email_cc or '—' }}</td>
            <td class="small text-muted">{{ s.notes or '—' }}</td>
            <td class="text-center">
              <form method="POST"
                    action="{{ url_for('admin_suppliers.supplier_toggle', supplier_id=s.id) }}">
                {% if s.is_active %}
                  <button class="btn btn-sm btn-outline-success">✓ Active</button>
                {% else %}
                  <button class="btn btn-sm btn-outline-secondary">✗ Inactive</button>
                {% endif %}
              </form>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  <p class="text-muted small mt-2">
    {{ suppliers|length }} supplier(s) total.
    Suppliers with no email set are highlighted in amber —
    order emails cannot be sent until an address is added.
  </p>
  {% else %}
  <div class="alert alert-info">
    No suppliers yet. Click <strong>Import from DW</strong> to auto-populate
    from the data warehouse, then add email addresses.
  </div>
  {% endif %}

</div>
{% endblock %}
```

---

## CHANGE 6 — Add "Suppliers" to the Admin nav menu

**File:** `templates/base.html` (or wherever the Admin dropdown menu is defined)

Find the Admin dropdown menu items. It likely looks like:

```html
<li><a class="dropdown-item" href="...">Some Admin Item</a></li>
```

Add a new item inside the Admin dropdown:

```html
<li>
  <a class="dropdown-item" href="{{ url_for('admin_suppliers.supplier_list') }}">
    <i class="fas fa-truck me-2 text-muted"></i>Suppliers
  </a>
</li>
```

Place it near other data management items (e.g. near Box Types, Settings).

---

## Workflow

1. Go to **Admin → Suppliers**
2. If any suppliers exist in `ps_items_dw` but not here, a **warning banner** lists them
3. Click **+ Add** next to any listed supplier — it pre-fills the code and name in the Add form
4. Fill in the email and click **Add**
5. Each row shows an amber **"No email"** badge if no email is set yet
6. Type the email directly in the inline row and click **Save**
7. From that point, the Forecast and Replenishment email forms auto-fill the recipient

---

## Summary

| # | File | Change |
|---|---|---|
| 1–2 | Already in previous doc | `email`/`email_cc` on model + migration |
| 3 | `routes_admin_suppliers.py` (new) | Full CRUD + missing-from-DW check |
| 4 | `main.py` | Register blueprint |
| 5 | `templates/admin/suppliers.html` (new) | Inline-edit supplier list with email fields |
| 6 | `templates/base.html` | Add Suppliers link to Admin menu |
