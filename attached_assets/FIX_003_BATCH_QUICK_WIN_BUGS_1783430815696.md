# FIX-003 — Batch Picking Quick-Win Bug Fixes

## Priority: HIGH — Ten small, independent fixes; each is under an hour. No schema changes, no workflow changes.

Source: WMDS Batch & Picking Review (7 Jul 2026), items B1, B2, B6, B7, B8, B9, B12, B13, D2, D3, D4, P6.

---

## Change 1 — Broken redirect after batch creation (B1)

The endpoint `batch.batch_picking_view` was disabled (its `@batch_bp.route` decorator
is commented out at `routes_batch.py:1637`), but four callers still build URLs for it.
`url_for()` raises `BuildError`, so the batch is created and the user lands on an
error page.

In `routes_batch.py` replace all three redirects:

```python
# Lines 918, 1029, 1382 — replace:
return redirect(url_for('batch.batch_picking_view', batch_id=batch.id))
# with:
return redirect(url_for('batch.batch_picking_manage'))
```

In `main.py:270` (invoice batch badge helper) the same dead link is why batch badges
never render — the `except Exception: return ''` swallows the BuildError. Replace:

```python
link = url_for('batch.batch_picking_view', batch_id=b.id)
# with:
link = url_for('batch.batch_picking_manage')
```

Also fix the status filter in the same helper (`main.py:259`) — `'In Progress'` and
`'Assigned'` are never-used statuses (see FIX-004):

```python
BatchPickingSession.status.in_(['Created', 'Active', 'picking', 'Paused']),
```

Finally delete the commented-out `batch_picking_view` body (`routes_batch.py`
1636–1722) and `templates/batch_picking_view.html`, plus the two dead debug templates
that link to it: `templates/batch_debug.html` line 218 and
`templates/batch_verification_results.html` line 119 (both templates are unused — see
Change 10).

## Change 2 — `clear_batch_cache` name collision (B2)

`routes_batch.py` defines `clear_batch_cache` twice: a plain helper at line 97 and a
route handler at lines 1836–1857. The route handler, defined later, wins the module
name. Every internal call (`routes_batch.py` 368, 394, 3480) therefore runs the ROUTE
handler — flashing "Cache cleared for batch X…" to the picker mid-flow (e.g. every
time a Sequential order completes) and clearing the batch-start flag as a side effect.

Rename the route function only (no template or `url_for` references exist):

```python
@batch_bp.route('/picker/batch/clear_cache/<int:batch_id>')
@login_required
def clear_batch_cache_route(batch_id):   # was: clear_batch_cache
    ...
```

The helper at line 97 stays as-is; the three internal calls now hit the helper as
intended. The route body should call the helper instead of duplicating the pops.

## Change 3 — Assign-picker modal assigns the wrong batch (B6)

`templates/batch_picking_manage.html` lines 295–307: the modal rewrites the form URL
with `form.action.replace('/0', '/' + batchId)`. This works once. Open the modal for
batch A, cancel, open it for batch B — the action still points at A, so the picker is
assigned to the wrong batch.

Rebuild the action from a constant base every time:

```javascript
assignModal.addEventListener('show.bs.modal', function(event) {
    const button = event.relatedTarget;
    const batchId = button.getAttribute('data-batch-id');
    const batchName = button.getAttribute('data-batch-name');
    const form = document.getElementById('assignForm');
    form.action = "{{ url_for('batch.batch_picking_assign', batch_id=0) }}"
                      .replace(/0$/, batchId);
    document.getElementById('batch-name-display').textContent = batchName;
});
```

## Change 4 — Batch report always prints 0.0 kg (B7)

`templates/batch_print_reports.html` line 220 reads `invoice_data.invoice.weight_kg`,
which does not exist on `Invoice` (the field is `total_weight`; `weight_kg` lives on
`RouteStopInvoice`). Replace:

```html
{{ "%.1f"|format(invoice_data.invoice.total_weight or 0) }} kg
```

## Change 5 — Admin report never shows SKIPPED or skip reasons (B8)

`templates/batch_admin_print_report.html` lines 186–204 check
`item.pick_status == 'skipped'`, but the exception dicts built in
`batch_admin_print_report()` (`routes_batch.py` 4775–4845) never contain a
`pick_status` key — every row prints as EXCEPTION and skip reasons are lost.

In `routes_batch.py`, when building `exception_data` (both the shortage branch and
the not-allocated branch, ~lines 4780–4790), pull status from the InvoiceItem:

```python
# 'item' here is the InvoiceItem being iterated
exception_data['pick_status'] = ('skipped' if item.pick_status == 'skipped_pending'
                                 else 'exception')
exception_data['skip_reason'] = item.skip_reason or ''
```

And in the DB-exception fallback loop (~line 4796), widen the match — Sequential-mode
exception reasons never contain the string "Batch picking":

```python
picking_exceptions = PickingException.query.filter_by(
    invoice_no=invoice.invoice_no
).all()
# keep the existing locked_by_batch_id == batch_session.id scope check;
# it already prevents unrelated exceptions from leaking in.
```

## Change 6 — Admins cannot force-complete a stuck batch (B9)

`routes_batch.py:3700` requires `assigned_to == current_user.username` with no
admin bypass, unlike every other batch action. Replace:

```python
if (current_user.role not in ['admin', 'warehouse_manager']
        and batch_session.assigned_to != current_user.username):
    flash('You are not assigned to this batch', 'danger')
    return redirect(url_for('batch.picker_batch_list'))
```

## Change 7 — Delete `delete_batch_comprehensive` (B12)

`routes_batch.py` 3910–4018 deletes ActivityLog and PickingException rows by fuzzy
text match on the batch NAME (`details.contains(batch_name)`). A batch named "A1"
would delete every log line containing "A1". No route calls it (only
`test_comprehensive_batch_deletion.py` imports it). Delete the function and the test
file before anyone wires it back up. The audited paths (`cancel_batch`,
`delete_batch`) already cover deletion correctly.

## Change 8 — Delete the dead two-step confirm (D3)

No template posts to `batch.confirm_batch_item` — the pick screen's modal posts
directly to `complete_batch_confirm`. Delete:

- `routes_batch.py` 2856–2954 (`confirm_batch_item`)
- `templates/batch_picking_confirm.html`

## Change 9 — Delete the third (dead) issue path (D2) and dead banner (D4)

`templates/batch_picking_item.html`:

- Lines 543–600: the `#reportIssueModal` is opened by nothing, and its submit button
  duplicates the id `submitIssueBtn` used by the Exception modal, so it could never
  work. Delete the modal.
- Delete its backend `batch_report_issue` (`routes_batch.py` 3773–3908) — it
  duplicates allocation logic with different rules (marks short picks `picked`, not
  `exception`).
- Lines 116–139 and 162: the "Starting New Order" banner tests `item.is_new_order`
  and prints `item.invoice_position`; no serialiser ever sets either key, so the
  banner never shows and the badge renders "Order " (blank). Delete the markup (or,
  if the banner is wanted, set the keys in the Sequential serialiser — separate task).

Result: exactly two problem paths remain on the pick screen — Skip (collect later)
and Exception (unavailable/short) — which is the correct model.

## Change 10 — Manage page status column + delete wording (P6); template cleanup (D1)

`templates/batch_picking_manage.html` lines 90–96 only render badges for `Created`
and `In Progress` (never set) — Active/picking/Paused batches show a blank cell:

```html
{% if session.status == 'Created' %}<span class="badge bg-secondary">Created</span>
{% elif session.status == 'Active' %}<span class="badge bg-success">Assigned</span>
{% elif session.status == 'picking' %}<span class="badge bg-info">Picking</span>
{% elif session.status == 'Paused' %}<span class="badge bg-warning text-dark">Paused</span>
{% else %}<span class="badge bg-secondary">{{ session.status }}</span>{% endif %}
```

The trash button's confirm says "cannot be undone", but `delete_batch` routes
non-empty batches through cancel (locks released, audit kept). Change the confirm to:
`'Cancel this batch? Unpicked items are released back to normal picking. Audit history is kept.'`

Delete the six templates no route renders:
`picker_batch_list.html`, `batch_picking_simple.html`,
`batch_picking_filter_simple.html`, `batch_picking_create_with_filter.html`,
`batch_picking_debug.html`, `batch_verification_results.html`.

## Change 11 — Repo hygiene (B13)

- `cookies.txt` in the repo root contains a real session cookie. Delete it and
  **rotate `SESSION_SECRET`** (old cookies become invalid — pickers just log in again).
- Delete `routes.py.tmp`, `routes_batch_fixed.py` (1,276 lines, never imported),
  `picking_system_deployment.zip`.
- Add `*.tmp` and `cookies.txt` to `.gitignore`.

## Schema Changes

None.

## Tests Required

| # | Scenario | Expected |
|---|----------|----------|
| Q1 | Create batch via Simple page | Redirects to Manage page, no error |
| Q2 | Create batch with `use_db_backed_picking_queue=true` | Redirects to Manage page, no error |
| Q3 | Complete a Sequential order mid-batch | No "Cache cleared…" flash appears |
| Q4 | Open assign modal for batch A, cancel, open for batch B, assign | Batch B gets the picker |
| Q5 | Print picker batch report for invoice with known weight | Correct kg printed |
| Q6 | Batch with one skipped item → admin report | Row shows SKIPPED badge + reason |
| Q7 | Admin (not assigned) calls force_complete | Succeeds |
| Q8 | Manage page with batches in Active/picking/Paused | Status badges visible for all |

## Verification

1. Create a batch from each creation path — both must end on the Manage page.
2. As a picker, run a small Sequential batch to completion — confirm no stray
   "Cache cleared" messages.
3. Assign pickers to two different batches in one page visit — check both rows.
4. Print both batch reports for a batch containing a skipped item and a short pick —
   check weight, SKIPPED badge, and skip reason all appear.
5. `grep -rn "batch_picking_view\|confirm_batch_item\|batch_report_issue\|delete_batch_comprehensive"` returns no live references.
