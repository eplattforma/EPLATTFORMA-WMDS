"""Phase 4: admin/picker routes for the batch picking refactor.

Routes:
  - POST /picker/batch/claim/<batch_id>         — claim flow
  - GET  /admin/batch/orphaned-locks            — orphan reconciliation UI
  - POST /admin/batch/orphaned-locks/unlock-all — bulk unlock
  - GET/POST /admin/batch/drain-status          — drain workflow
  - POST /admin/batch/cancel/<batch_id>         — cancel (replaces hard-delete)

Templates are rendered with inline HTML to avoid template sprawl; admins
who want a richer view can extend the templates later. All routes are
permission-gated via ``services.permissions.require_permission``.
"""
import logging

from flask import Blueprint, flash, jsonify, redirect, render_template_string, request, url_for
from flask_login import current_user, login_required

from services import batch_status
from services.batch_picking import (
    BatchConflict,
    bulk_unlock_orphans,
    can_claim,
    cancel_batch,
    claim_batch,
    find_orphaned_locks,
)
from models import BatchPickingSession
from services.maintenance import drain
from services.permissions import require_permission

logger = logging.getLogger(__name__)

admin_batch_phase4_bp = Blueprint("admin_batch_phase4", __name__)


# ---------------------------------------------------------------------------
# Claim flow
# ---------------------------------------------------------------------------
@admin_batch_phase4_bp.route("/picker/batch/claim/<int:batch_id>", methods=["POST"])
@login_required
@require_permission("picking.claim_batch")
def claim_batch_route(batch_id):
    batch = BatchPickingSession.query.get_or_404(batch_id)
    ok, reason = can_claim(batch, current_user)
    if not ok:
        flash(reason, "danger")
        return redirect(request.referrer or url_for("batch.picker_batch_list"))
    try:
        result = claim_batch(batch_id, current_user.username)
        flash(
            f"Claimed batch #{batch_id}. Previous assignee: "
            f"{result['previous_assignee'] or '(unassigned)'}.",
            "success",
        )
    except ValueError as e:
        flash(str(e), "warning")
    return redirect(request.referrer or url_for("batch.picker_batch_list"))


# ---------------------------------------------------------------------------
# Cancel (the user-facing replacement for hard-delete)
# ---------------------------------------------------------------------------
@admin_batch_phase4_bp.route("/admin/batch/cancel/<int:batch_id>", methods=["POST"])
@login_required
@require_permission("picking.manage_batches")
def cancel_batch_route(batch_id):
    reason = (request.form.get("reason") or "").strip() or None
    try:
        result = cancel_batch(batch_id, current_user.username, reason=reason)
        flash(
            f"Batch #{batch_id} cancelled. Released {result['released_locks']} lock(s).",
            "success",
        )
    except ValueError as e:
        flash(str(e), "warning")
    except Exception as e:
        flash(f"Failed to cancel batch: {e}", "danger")
    return redirect(request.referrer or url_for("batch.batch_picking_manage"))


# ---------------------------------------------------------------------------
# Orphaned-locks UI
# ---------------------------------------------------------------------------
_ORPHANS_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="container py-3">
  <h2>Orphaned Locks</h2>
  <p class="text-muted">
    Items still locked by a batch that is missing or in a terminal state
    (Completed / Cancelled / Archived). Bulk-unlocking writes a
    <code>batch.orphan_unlock</code> activity-log entry.
  </p>
  {% if orphans %}
    <form method="POST" action="{{ url_for('admin_batch_phase4.unlock_orphans_route') }}"
          onsubmit="return confirm('Release all {{ orphans|length }} orphan lock(s)?');">
      <button class="btn btn-danger mb-2" type="submit">
        Unlock all {{ orphans|length }} orphan(s)
      </button>
    </form>
    <table class="table table-sm table-striped">
      <thead><tr>
        <th>Invoice</th><th>Item</th><th>Zone</th><th>Locked by batch id</th>
      </tr></thead>
      <tbody>
        {% for it in orphans[:500] %}
          <tr>
            <td>{{ it.invoice_no }}</td>
            <td>{{ it.item_code }}</td>
            <td>{{ it.zone }}</td>
            <td>{{ it.locked_by_batch_id }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    {% if orphans|length > 500 %}
      <p class="text-muted">Showing first 500 of {{ orphans|length }}.</p>
    {% endif %}
  {% else %}
    <div class="alert alert-success">No orphaned locks found.</div>
  {% endif %}
</div>
{% endblock %}
"""


@admin_batch_phase4_bp.route("/admin/batch/orphaned-locks", methods=["GET"])
@login_required
@require_permission("picking.manage_batches")
def orphaned_locks_route():
    if current_user.role != "admin":
        flash("Orphan reconciliation is admin-only.", "danger")
        return redirect(url_for("index"))
    orphans = find_orphaned_locks()
    try:
        from flask import render_template
        return render_template("admin_orphaned_locks.html", orphans=orphans)
    except Exception:
        return render_template_string(_ORPHANS_TEMPLATE, orphans=orphans)


@admin_batch_phase4_bp.route("/admin/batch/orphaned-locks/unlock-all", methods=["POST"])
@login_required
@require_permission("picking.manage_batches")
def unlock_orphans_route():
    if current_user.role != "admin":
        flash("Orphan reconciliation is admin-only.", "danger")
        return redirect(url_for("index"))
    n = bulk_unlock_orphans(current_user.username)
    flash(f"Released {n} orphan lock(s).", "success")
    return redirect(url_for("admin_batch_phase4.orphaned_locks_route"))


# ---------------------------------------------------------------------------
# Drain workflow
# ---------------------------------------------------------------------------
_DRAIN_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="container py-3">
  <h2>Drain Status</h2>
  <p>Current mode: <strong>{{ mode }}</strong></p>
  {% if mode == 'draining' %}
    <div class="alert alert-warning">{{ banner }}</div>
    <form method="POST" action="{{ url_for('admin_batch_phase4.drain_status_route') }}">
      <input type="hidden" name="mode" value="normal">
      <button class="btn btn-success">Resume normal mode</button>
    </form>
    <form method="POST" action="{{ url_for('admin_batch_phase4.drain_force_pause_route') }}" class="mt-2">
      <button class="btn btn-warning">Force-pause stuck batches now</button>
    </form>
  {% else %}
    <form method="POST" action="{{ url_for('admin_batch_phase4.drain_status_route') }}"
          onsubmit="return confirm('Switch to draining mode? New batch creation will be blocked for non-admins.');">
      <input type="hidden" name="mode" value="draining">
      <button class="btn btn-warning">Begin draining</button>
    </form>
  {% endif %}
  {% if last_pause %}
    <hr>
    <h5>Last force-pause result</h5>
    <pre class="bg-light p-2">{{ last_pause }}</pre>
  {% endif %}
</div>
{% endblock %}
"""


@admin_batch_phase4_bp.route("/admin/batch/drain-status", methods=["GET", "POST"])
@login_required
@require_permission("picking.manage_batches")
def drain_status_route():
    if current_user.role != "admin":
        flash("Drain mode can only be controlled by admins.", "danger")
        return redirect(url_for("batch.batch_picking_manage"))

    if request.method == "POST":
        new_mode = (request.form.get("mode") or "").strip().lower()
        try:
            drain.set_mode(new_mode, current_user.username)
            flash(f"maintenance_mode set to '{new_mode}'.", "success")
        except Exception as e:
            flash(f"Failed to change drain mode: {e}", "danger")
        return redirect(url_for("admin_batch_phase4.drain_status_route"))

    mode = drain.get_mode()
    banner = drain.get_drain_banner()
    return render_template_string(_DRAIN_TEMPLATE, mode=mode, banner=banner, last_pause=None)


@admin_batch_phase4_bp.route("/admin/batch/drain-status/force-pause", methods=["POST"])
@login_required
@require_permission("picking.manage_batches")
def drain_force_pause_route():
    if current_user.role != "admin":
        flash("Admins only.", "danger")
        return redirect(url_for("batch.batch_picking_manage"))
    summary = drain.force_pause_stuck_batches()
    flash(f"Force-pause complete: {summary}", "info")
    return redirect(url_for("admin_batch_phase4.drain_status_route"))


# ---------------------------------------------------------------------------
# JSON helper for tests / scripts
# ---------------------------------------------------------------------------
@admin_batch_phase4_bp.route("/admin/batch/orphaned-locks.json", methods=["GET"])
@login_required
@require_permission("picking.manage_batches")
def orphaned_locks_json():
    if current_user.role != "admin":
        return jsonify({"error": "admin only"}), 403
    orphans = find_orphaned_locks()
    return jsonify({
        "count": len(orphans),
        "items": [
            {
                "invoice_no": o.invoice_no,
                "item_code": o.item_code,
                "zone": o.zone,
                "locked_by_batch_id": o.locked_by_batch_id,
            }
            for o in orphans[:1000]
        ],
    })
