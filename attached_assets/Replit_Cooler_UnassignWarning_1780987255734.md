# Unassign from Route — Warning & Full Cleanup for Cooler-Picked Items

## The problem

When an invoice with already-picked cooler items is unassigned from a route,
the current `release_cooler_locks_for_invoice` function only cleans up
**pending** queue rows. Rows with `status = 'picked'` and their
`cooler_box_items` entries are left behind — creating orphaned data:
boxes still show items from an invoice no longer on the route, fill percentages
are wrong, and picked counts on the cooler session are inflated.

This brief fixes it in two parts:
1. **Frontend:** warn the user BEFORE unassigning and explain what will happen.
2. **Backend:** fully reverse cooler picks when the user confirms.

---

## Part A — Backend: new check endpoint

Add a new route to `routes_routes.py` (alongside the existing
`/unassign-from-route` route, around line 1080):

```python
@bp.route("/check-cooler-picks", methods=["POST"])
@login_required
def check_cooler_picks():
    """
    Check whether any of the given invoices have picked cooler items
    or cooler box assignments. Used by the frontend to decide whether
    to show a warning before unassigning from route.

    Expected JSON: {"invoice_nos": ["INV001", "INV002"]}
    Returns: {
        "ok": true,
        "affected": [
            {"invoice_no": "INV001", "picked_count": 3, "boxed_count": 2}
        ]
    }
    """
    from app import db
    from sqlalchemy import text

    data = request.get_json(force=True)
    invoice_nos = data.get("invoice_nos", [])
    if not invoice_nos:
        return jsonify({"ok": True, "affected": []})

    rows = db.session.execute(
        text(
            "SELECT bpq.invoice_no, "
            "       COUNT(*) AS picked_count, "
            "       COUNT(cbi.id) AS boxed_count "
            "FROM batch_pick_queue bpq "
            "LEFT JOIN cooler_box_items cbi ON cbi.queue_item_id = bpq.id "
            "WHERE bpq.invoice_no = ANY(:inv) "
            "  AND bpq.pick_zone_type = 'cooler' "
            "  AND bpq.status = 'picked' "
            "GROUP BY bpq.invoice_no"
        ),
        {"inv": invoice_nos},
    ).fetchall()

    affected = [
        {
            "invoice_no": r[0],
            "picked_count": int(r[1]),
            "boxed_count": int(r[2]),
        }
        for r in rows
        if r[1] > 0
    ]

    return jsonify({"ok": True, "affected": affected})
```

---

## Part B — Backend: extend `release_cooler_locks_for_invoice`

**File:** `services/cooler_route_extraction.py`, function
`release_cooler_locks_for_invoice` (line ~258).

Add a `full_reset=False` parameter. When `full_reset=True`, fully reverse all
cooler picks for this invoice: remove box assignments, reset picked status,
recalculate affected box fill levels, and cancel any boxes that end up empty.

Replace the existing function with this extended version:

```python
def release_cooler_locks_for_invoice(invoice_no, full_reset=False):
    """Release all cooler batch holds for an invoice being removed from a route.

    When full_reset=False (default / existing behaviour):
        - Deletes pending cooler queue rows
        - Clears batch locks on unpicked invoice items
        - Preserves picked rows and box assignments (audit trail)

    When full_reset=True (user confirmed unassign with warning):
        - Everything above PLUS:
        - Removes cooler_box_items for this invoice
        - Deletes picked batch_pick_queue rows for this invoice
        - Resets invoice_items.is_picked = FALSE for affected items
        - Recalculates fill on any boxes that lost items
        - Cancels any boxes that are now completely empty

    Returns dict with counters.
    """
    # 1) Drop pending cooler queue rows (always)
    res = db.session.execute(
        text(
            "DELETE FROM batch_pick_queue "
            "WHERE invoice_no = :inv "
            "  AND pick_zone_type = 'cooler' "
            "  AND status = 'pending'"
        ),
        {"inv": invoice_no},
    )
    queue_deleted = res.rowcount or 0

    # 2) Clear locks on unpicked InvoiceItems (always)
    res2 = db.session.execute(
        text(
            "UPDATE invoice_items "
            "SET locked_by_batch_id = NULL "
            "WHERE invoice_no = :inv "
            "  AND is_picked = FALSE "
            "  AND locked_by_batch_id IN ( "
            "    SELECT id FROM batch_picking_sessions "
            "    WHERE session_type = 'cooler_route' "
            "  )"
        ),
        {"inv": invoice_no},
    )
    items_unlocked = res2.rowcount or 0

    box_items_removed = 0
    picked_queue_deleted = 0
    items_unpicked = 0
    boxes_cancelled = 0

    if full_reset:
        # 3) Find which boxes contain items for this invoice (before deleting)
        affected_box_ids = db.session.execute(
            text(
                "SELECT DISTINCT cooler_box_id "
                "FROM cooler_box_items "
                "WHERE invoice_no = :inv"
            ),
            {"inv": invoice_no},
        ).scalars().all()

        # 4) Remove cooler_box_items rows for this invoice
        res3 = db.session.execute(
            text(
                "DELETE FROM cooler_box_items "
                "WHERE invoice_no = :inv"
            ),
            {"inv": invoice_no},
        )
        box_items_removed = res3.rowcount or 0

        # 5) Delete picked cooler queue rows for this invoice
        res4 = db.session.execute(
            text(
                "DELETE FROM batch_pick_queue "
                "WHERE invoice_no = :inv "
                "  AND pick_zone_type = 'cooler' "
                "  AND status = 'picked'"
            ),
            {"inv": invoice_no},
        )
        picked_queue_deleted = res4.rowcount or 0

        # 6) Reset is_picked on invoice_items that were picked in cooler context
        res5 = db.session.execute(
            text(
                "UPDATE invoice_items "
                "SET is_picked = FALSE, "
                "    locked_by_batch_id = NULL "
                "WHERE invoice_no = :inv "
                "  AND is_picked = TRUE "
                "  AND locked_by_batch_id IN ( "
                "        SELECT id FROM batch_picking_sessions "
                "        WHERE session_type = 'cooler_route' "
                "  )"
            ),
            {"inv": invoice_no},
        )
        items_unpicked = res5.rowcount or 0

        # 7) Recalculate fill on affected boxes; cancel any that are now empty
        for box_id in affected_box_ids:
            remaining = db.session.execute(
                text(
                    "SELECT COUNT(*), "
                    "       MIN(delivery_sequence), "
                    "       MAX(delivery_sequence) "
                    "FROM cooler_box_items "
                    "WHERE cooler_box_id = :bid"
                ),
                {"bid": box_id},
            ).fetchone()

            if not remaining or remaining[0] == 0:
                # Box is now empty — cancel it
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET status = 'cancelled' "
                        "WHERE id = :bid AND status = 'open'"
                    ),
                    {"bid": box_id},
                )
                boxes_cancelled += 1
            else:
                # Update stop sequence range based on remaining items
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, "
                        "    last_stop_sequence  = :ls "
                        "WHERE id = :bid"
                    ),
                    {
                        "fs": remaining[1],
                        "ls": remaining[2],
                        "bid": box_id,
                    },
                )

    return {
        "queue_deleted": queue_deleted,
        "items_unlocked": items_unlocked,
        "box_items_removed": box_items_removed,
        "picked_queue_deleted": picked_queue_deleted,
        "items_unpicked": items_unpicked,
        "boxes_cancelled": boxes_cancelled,
    }
```

---

## Part C — Backend: update `unassign_from_route`

**File:** `routes_routes.py`, function `unassign_from_route` (line ~1083).

Accept a `force_cooler_reset` flag in the JSON body and pass it through to
`release_cooler_locks_for_invoice`.

Find the line:
```python
release_cooler_locks_for_invoice(invoice.invoice_no)
```
Replace with:
```python
force_reset = bool(data.get("force_cooler_reset", False))
release_cooler_locks_for_invoice(invoice.invoice_no, full_reset=force_reset)
```

(The `data` variable is already available from `request.get_json(force=True)`
earlier in the same function.)

---

## Part D — Frontend: warning modal before unassigning

**File:** `templates/admin_dashboard.html`

Replace the existing `window.bulkUnassignFromRoute` function (around line 838)
with this version that checks first, shows a warning if needed, and only
proceeds after confirmation:

```javascript
window.bulkUnassignFromRoute = function() {
    var nos = window.getSelectedInvoiceNos ? window.getSelectedInvoiceNos() : [];
    if (!nos.length) { alert('No invoices selected.'); return; }

    var btn = document.getElementById('bulkUnassignRouteBtn');

    // Step 1: check for picked cooler items on any selected invoice
    fetch('{{ url_for("routes.check_cooler_picks") }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({invoice_nos: nos})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
        if (!d.ok) {
            alert('Could not check cooler picks: ' + (d.message || 'Unknown error'));
            return;
        }

        if (d.affected && d.affected.length > 0) {
            // Build warning message listing affected invoices
            var lines = d.affected.map(function(a) {
                return '• Invoice ' + a.invoice_no
                    + ': ' + a.picked_count + ' item(s) picked'
                    + (a.boxed_count > 0 ? ', ' + a.boxed_count + ' in a box' : '');
            });
            var msg = 'WARNING — Cooler items already picked\n\n'
                + 'The following invoices have cooler items that have been '
                + 'physically picked and/or placed in cooler boxes:\n\n'
                + lines.join('\n')
                + '\n\nConfirming will:\n'
                + '  • Remove these items from their cooler boxes\n'
                + '  • Reverse their picked status\n'
                + '  • Cancel any boxes that become empty\n\n'
                + 'This cannot be undone. Proceed?';

            if (!confirm(msg)) return;

            // User confirmed — unassign with full cooler reset
            _doUnassign(nos, btn, true);
        } else {
            // No cooler picks — simple confirmation and proceed
            if (!confirm('Unassign ' + nos.length + ' invoice(s) from their route(s)?')) return;
            _doUnassign(nos, btn, false);
        }
    })
    .catch(function(err) {
        alert('Network error checking cooler picks: ' + err);
    });
};

function _doUnassign(nos, btn, forceCoolerReset) {
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Unassigning...'; }
    fetch('{{ url_for("routes.unassign_from_route") }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({invoice_nos: nos, force_cooler_reset: forceCoolerReset})
    })
    .then(function(r) { return r.json().then(function(data) { return {ok: r.ok, data: data}; }); })
    .then(function(res) {
        if (res.ok && res.data && res.data.ok) { window.location.reload(); }
        else {
            alert('Unassign failed: ' + (res.data && res.data.message ? res.data.message : 'Unknown error'));
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-unlink me-1"></i>Unassign from route'; }
        }
    })
    .catch(function(err) {
        alert('Network error: ' + err);
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-unlink me-1"></i>Unassign from route'; }
    });
}
```

---

## End-to-end flow after this change

| Scenario | What happens |
|---|---|
| Unassign invoices with **no cooler picks** | Single "Unassign N invoice(s)?" confirm → proceeds exactly as today |
| Unassign invoices **with picked/boxed cooler items** | Warning modal lists affected invoices and explains what will be reversed → user cancels or confirms |
| User **cancels** the warning | Nothing changes — safe exit |
| User **confirms** the warning | Full cleanup: items removed from boxes, picks reversed, empty boxes cancelled, route recalculated |

---

## Testing checklist

- [ ] Select invoices with **no** cooler picks → only the simple confirm
      dialog appears (no cooler warning).
- [ ] Select invoices where some have picked cooler items → warning modal
      appears listing exactly those invoices with correct picked/boxed counts.
- [ ] Click Cancel on the warning → nothing changes, invoices remain on route.
- [ ] Click Confirm on the warning → invoices removed from route; cooler box
      items for those invoices deleted; `batch_pick_queue` picked rows deleted;
      `invoice_items.is_picked` reset to FALSE for affected items; any box that
      becomes empty is cancelled; boxes with remaining items have their
      stop-sequence ranges recalculated.
- [ ] After confirming: go to the cooler route screen and verify no ghost items
      from the unassigned invoices appear in any box.
- [ ] After confirming: verify fill percentages on remaining boxes are correct.
- [ ] Audit log entries are written for the cooler session covering the
      removals (use existing `_audit()` helper in cooler_picking.py pattern,
      or log within `release_cooler_locks_for_invoice`).
