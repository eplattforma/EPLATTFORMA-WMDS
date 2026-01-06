# Admin UI Snippets — Add ETC controls to `/admin/oi/dashboard`

These snippets assume:
- You already have an admin section and a dashboard route `/admin/oi/dashboard`.
- You have the `Setting` model available.
- You want **admin-only** actions (no public APIs).

---

## 1) Add an “Order Time Estimation (ETC)” card to the dashboard (Jinja)

Paste into your existing dashboard template (example: `templates/admin/oi/dashboard.html`).

```html
<div class="card mt-3">
  <div class="card-body">
    <div class="d-flex justify-content-between align-items-start">
      <div>
        <h5 class="card-title">Order Time Estimation (ETC)</h5>
        <p class="card-text mb-1">
          Uses OI attributes from <code>ps_items_dw</code> and warehouse location rules (corridors, ladder, stairs) to estimate minutes per order.
        </p>
        <small class="text-muted">
          Parameter set: <code>{{ etc_params_version }}</code> |
          Summer Mode: <strong>{{ 'ON' if summer_mode else 'OFF' }}</strong>
        </small>
      </div>

      <div class="text-end">
        <form method="post" action="{{ url_for('admin_oi.toggle_summer_mode') }}" class="d-inline">
          <input type="hidden" name="next" value="{{ request.path }}">
          <button class="btn btn-sm btn-outline-primary" type="submit">
            Toggle Summer Mode
          </button>
        </form>

        <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="modal" data-bs-target="#etcParamsModal">
          Edit ETC Parameters
        </button>
      </div>
    </div>

    <hr>

    <!-- Recalculate for one invoice -->
    <form method="post" action="{{ url_for('admin_oi.recalc_invoice_etc') }}" class="row g-2 align-items-end">
      <div class="col-auto">
        <label class="form-label mb-0">Invoice No</label>
        <input name="invoice_no" class="form-control form-control-sm" placeholder="IN10052417" required>
      </div>
      <div class="col-auto">
        <button class="btn btn-sm btn-success" type="submit">Recalculate ETC</button>
      </div>
    </form>

    <div class="mt-2">
      <form method="post" action="{{ url_for('admin_oi.recalc_open_invoices_etc') }}" class="d-inline">
        <button class="btn btn-sm btn-warning" type="submit">Recalculate ETC for Open Invoices</button>
      </form>
      <small class="text-muted ms-2">Open = not_started / picking (adjust in code).</small>
    </div>
  </div>
</div>

<!-- ETC Parameters Modal -->
<div class="modal fade" id="etcParamsModal" tabindex="-1" aria-labelledby="etcParamsModalLabel" aria-hidden="true">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <form method="post" action="{{ url_for('admin_oi.save_etc_params') }}">
        <div class="modal-header">
          <h5 class="modal-title" id="etcParamsModalLabel">ETC Parameters (JSON)</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>

        <div class="modal-body">
          <p class="text-muted">
            Edit JSON carefully. Changes apply immediately to future estimates. You can recalibrate weekly using your tracked times.
          </p>
          <textarea name="params_json" class="form-control" rows="18" style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;">{{ etc_params_json }}</textarea>
        </div>

        <div class="modal-footer">
          <button class="btn btn-secondary" type="button" data-bs-dismiss="modal">Cancel</button>
          <button class="btn btn-primary" type="submit">Save</button>
        </div>
      </form>
    </div>
  </div>
</div>
```

---

## 2) Pass variables into the dashboard view (Flask)

Inside your existing `/admin/oi/dashboard` route:

```python
from models import Setting

params = Setting.get_json(db.session, "oi_time_params_v1", default={})
etc_params_json = json.dumps(params, indent=2, ensure_ascii=False)
etc_params_version = params.get("version", "v1")
summer_mode = (Setting.get(db.session, "summer_mode", "false") or "false").lower() in ("true","1","yes","on")

return render_template(
    "admin/oi/dashboard.html",
    etc_params_json=etc_params_json,
    etc_params_version=etc_params_version,
    summer_mode=summer_mode,
    # ... your existing vars
)
```

---

## 3) Admin routes (Flask) — save params + toggle summer mode + recalc

Create a file like `routes_admin_oi_time.py` (or merge into your existing admin OI routes):

```python
import json
from flask import Blueprint, request, redirect, url_for, flash
from app import db
from models import Setting, Invoice
from services.oi_time_estimator import estimate_and_persist_invoice_time

admin_oi = Blueprint("admin_oi", __name__)

@admin_oi.post("/admin/oi/toggle-summer-mode")
def toggle_summer_mode():
    current = (Setting.get(db.session, "summer_mode", "false") or "false").lower() in ("true","1","yes","on")
    Setting.set(db.session, "summer_mode", "false" if current else "true")
    flash(f"Summer Mode is now {'OFF' if current else 'ON'}", "success")
    return redirect(request.form.get("next") or url_for("admin_oi.dashboard"))

@admin_oi.post("/admin/oi/save-etc-params")
def save_etc_params():
    raw = request.form.get("params_json", "").strip()
    try:
        parsed = json.loads(raw)
    except Exception as e:
        flash(f"Invalid JSON: {e}", "danger")
        return redirect(url_for("admin_oi.dashboard"))

    # Minimal validation
    if "travel" not in parsed or "pick" not in parsed or "pack" not in parsed:
        flash("Missing required keys: travel/pick/pack", "danger")
        return redirect(url_for("admin_oi.dashboard"))

    Setting.set_json(db.session, "oi_time_params_v1", parsed)
    flash("ETC parameters saved.", "success")
    return redirect(url_for("admin_oi.dashboard"))

@admin_oi.post("/admin/oi/recalc-invoice-etc")
def recalc_invoice_etc():
    invoice_no = request.form.get("invoice_no")
    if not invoice_no:
        flash("Missing invoice number.", "danger")
        return redirect(url_for("admin_oi.dashboard"))

    try:
        estimate_and_persist_invoice_time(invoice_no)
        flash(f"ETC recalculated for {invoice_no}.", "success")
    except Exception as e:
        flash(f"Failed: {e}", "danger")

    return redirect(url_for("admin_oi.dashboard"))

@admin_oi.post("/admin/oi/recalc-open-invoices-etc")
def recalc_open_invoices_etc():
    open_statuses = ["not_started", "picking"]
    invoices = db.session.query(Invoice).filter(Invoice.status.in_(open_statuses)).all()

    ok, fail = 0, 0
    for inv in invoices:
        try:
            estimate_and_persist_invoice_time(inv.invoice_no)
            ok += 1
        except:
            fail += 1

    flash(f"ETC recalculated: {ok} ok, {fail} failed.", "success" if fail == 0 else "warning")
    return redirect(url_for("admin_oi.dashboard"))
```

Register blueprint:
```python
app.register_blueprint(admin_oi)
```

---

## 4) Add the service module
Copy `services/oi_time_estimator.py` from this package into your Replit project under `services/`.

---

## 5) Create the settings records
- Insert JSON from `oi_time_params_v1.json` into settings key `oi_time_params_v1`
- Set `summer_mode` to `false` initially

You can do this from an admin-only CLI script or a one-time admin page.

