# EP SmartGrowth — Cost Pipeline Fixes

Three targeted fixes to the Dropbox cost import and daily invoice cron:

1. **Write `cost_price_updated_at`** when a cost is changed by the Dropbox sync
2. **Surface unmatched item codes** as a visible warning in the Dropbox admin UI
3. **Persist cron failures to the database** so they are visible without Replit stdout access

---

## FIX 1 — Write `cost_price_updated_at` on cost update

**File:** `services/dropbox_service.py`

### Where to change

In `_cost_import_processor`, find the update loop:

```python
        for code in batch_codes:
            item = db.session.query(DwItem).get(code)
            if item:
                if item.cost_price != updates[code]:
                    item.cost_price = updates[code]
                    rows_updated += 1
```

**Replace with:**

```python
        for code in batch_codes:
            item = db.session.query(DwItem).get(code)
            if item:
                if item.cost_price != updates[code]:
                    item.cost_price = updates[code]
                    item.cost_price_updated_at = get_utc_now()
                    rows_updated += 1
```

> `get_utc_now()` is already imported at the top of `dropbox_service.py` from `timezone_utils`.

---

## FIX 2 — Surface unmatched item codes in the Dropbox admin UI

Currently, `unmatched_codes` are stored in `metadata_json` but never shown to the user. This fix adds a visible warning panel to the sync history page.

### Step 2a — `services/dropbox_service.py` — no change needed

The unmatched codes are already stored in `metadata_json['unmatched_codes']` and `metadata_json['unmatched_count']`. Nothing to change here.

### Step 2b — Dropbox admin template

**File:** whichever template renders the Dropbox sync history (likely `templates/admin/dropbox_sync.html` or similar — search for where `sync_history` is rendered).

Find the loop that renders sync history rows. Inside it (or as a detail panel that expands per row), add:

```html
{% set md = log.metadata_json or {} %}
{% set unmatched = md.get('unmatched_codes', []) %}
{% set unmatched_count = md.get('unmatched_count', 0) %}

{% if unmatched_count > 0 %}
<div class="alert alert-warning py-1 px-2 mt-1 mb-0 small">
  <strong>⚠ {{ unmatched_count }} unmatched item code(s)</strong> — cost not updated for these items
  (not found in ps_items_dw):
  <span class="font-monospace">{{ unmatched[:20] | join(', ') }}{% if unmatched_count > 20 %} … and {{ unmatched_count - 20 }} more{% endif %}</span>
</div>
{% endif %}
```

> This shows up inline under any sync run that had unmatched codes. It shows up to 20 codes and a count if there are more. If `unmatched_count` is 0 (or the key is absent for older runs), nothing is shown.

---

## FIX 3 — Persist daily invoice cron failures to the database

Currently `cron_daily_invoice_sync.py` only logs to stdout, which is invisible once the Replit deployment run ends. This fix writes failures (and success) to an existing or new `cron_run_log` table so they are always queryable.

### Step 3a — Add a `CronRunLog` model (if it doesn't already exist)

**File:** `models.py`

Add near the other log/audit models:

```python
class CronRunLog(db.Model):
    __tablename__ = 'cron_run_log'

    id = db.Column(db.Integer, primary_key=True)
    job_name = db.Column(db.String(100), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False)  # 'success' | 'failed'
    message = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<CronRunLog {self.job_name} {self.status} {self.started_at}>"
```

### Step 3b — Create the table

**File:** `main.py` (or wherever `db.create_all()` is called)

Ensure `CronRunLog` is imported so `db.create_all()` picks it up:

```python
from models import CronRunLog  # add alongside other model imports
```

### Step 3c — Update `datawarehouse_sync.py` — return full counts

Find the end of `sync_invoices_from_date`. The current return statement is:

```python
        return h_ins, h_upd
```

**Replace with:**

```python
        return {
            "headers_inserted": h_ins,
            "headers_updated": h_upd,
            "lines_inserted": l_ins,
            "lines_updated": l_upd,
            "stores_inserted": s_ins,
            "cashiers_inserted": u_ins,
        }
```

> **Important:** if any other call site unpacks the return value as `h_ins, h_upd = sync_invoices_from_date(...)`, update those too — change them to `result = sync_invoices_from_date(...)` and access `result["headers_inserted"]` etc. Search the codebase for `sync_invoices_from_date` to find all callers.

---

### Step 3d — Update `cron_daily_invoice_sync.py`

Replace the entire file with:

```python
"""
Daily invoice sync cron job.
Syncs today's invoices from PS365 at scheduled time.
Run via Replit Scheduled Deployment: python cron_daily_invoice_sync.py
"""
import os
import sys
import logging
from datetime import datetime

os.environ['TZ'] = 'Europe/Athens'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CRON] %(levelname)s %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def _write_cron_log(app, job_name, started_at, status, message=None):
    """Persist a cron run outcome to the database."""
    try:
        from app import db
        from models import CronRunLog
        from timezone_utils import get_utc_now
        with app.app_context():
            entry = CronRunLog(
                job_name=job_name,
                started_at=started_at,
                finished_at=get_utc_now(),
                status=status,
                message=message,
            )
            db.session.add(entry)
            db.session.commit()
    except Exception as log_err:
        logger.error(f"Could not write cron run log: {log_err}")


def main():
    logger.info("=" * 60)
    logger.info("DAILY INVOICE SYNC CRON - STARTED")
    logger.info("=" * 60)

    from app import app, db
    from datawarehouse_sync import sync_invoices_from_date
    from timezone_utils import get_utc_now

    JOB_NAME = 'daily_invoice_sync'
    started_at = get_utc_now()
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Syncing invoices for date: {today}")

    with app.app_context():
        try:
            result = sync_invoices_from_date(db.session, today, today)
            h = result.get("headers_inserted", 0)
            l = result.get("lines_inserted", 0)
            s = result.get("stores_inserted", 0)
            u = result.get("cashiers_inserted", 0)
            summary = (
                f"Synced {today} — "
                f"headers: {h}, lines: {l}, stores: {s}, cashiers: {u}"
            )
            logger.info(f"Daily invoice sync completed: {summary}")
            _write_cron_log(app, JOB_NAME, started_at, 'success', summary)
        except Exception as e:
            logger.error(f"Daily invoice sync FAILED: {e}", exc_info=True)
            _write_cron_log(app, JOB_NAME, started_at, 'failed', str(e)[:1000])
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("DAILY INVOICE SYNC CRON - FINISHED")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
```

### Step 3e — Add a simple admin view for cron run logs

**File:** any admin route file (e.g. `routes_admin.py` or similar)

Add a route to view recent cron runs:

```python
@app.route('/admin/cron-logs')
@login_required
@require_permission('admin')
def admin_cron_logs():
    from models import CronRunLog
    logs = CronRunLog.query.order_by(CronRunLog.started_at.desc()).limit(100).all()
    return render_template('admin/cron_logs.html', logs=logs)
```

**File:** `templates/admin/cron_logs.html` (new file)

```html
{% extends "base.html" %}
{% block content %}
<div class="container mt-4">
  <h4>Cron Run Log</h4>
  <table class="table table-sm table-striped">
    <thead>
      <tr>
        <th>Job</th>
        <th>Started</th>
        <th>Finished</th>
        <th>Status</th>
        <th>Message</th>
      </tr>
    </thead>
    <tbody>
      {% for log in logs %}
      <tr class="{{ 'table-danger' if log.status == 'failed' else '' }}">
        <td class="font-monospace small">{{ log.job_name }}</td>
        <td class="small">{{ log.started_at.strftime('%Y-%m-%d %H:%M') if log.started_at else '—' }}</td>
        <td class="small">{{ log.finished_at.strftime('%H:%M:%S') if log.finished_at else '—' }}</td>
        <td>
          {% if log.status == 'success' %}
            <span class="badge bg-success">success</span>
          {% else %}
            <span class="badge bg-danger">failed</span>
          {% endif %}
        </td>
        <td class="small text-muted">{{ log.message or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

Add a link to this page in the Admin nav menu alongside the other admin tools.

---

## Summary of changes

| Fix | File(s) changed |
|---|---|
| 1. `cost_price_updated_at` | `services/dropbox_service.py` — 1 line |
| 2. Unmatched codes warning | `templates/admin/dropbox_sync.html` — template only |
| 3. Cron failure logging | `models.py`, `main.py`, `cron_daily_invoice_sync.py`, new template + route |
