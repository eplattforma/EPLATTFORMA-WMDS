# FIX-006 — DB Queue as Single Source of Truth (retire the cookie cache)

## Priority: CRITICAL (structural) — Do after FIX-003/004. This removes the root cause of "batch changed mid-pick" bugs and deletes the largest source of complexity in the module.

Source: WMDS Batch & Picking Review (7 Jul 2026), items B3, B11, P1, P2, P4, P7.

---

## The Problem

The picker's item list is serialised into `session['batch_items_<id>']` — the Flask
session, which with no server-side store configured (`app.py`) is a **browser cookie
capped at ~4 KB**. Each serialised item is ~200–400 bytes, so a batch beyond roughly
15–20 items silently overflows: the browser drops the cookie, the next request finds
no cache, the list regenerates from live DB state, and the stored
`current_item_index` now points into a *different* list. Symptoms match the
historical bugs this module keeps patching ("CRUCIAL FIX: use a fixed list",
force-regeneration flags, fallback rebuilds in confirm/skip).

The infrastructure to fix this already exists and is the best code in the module:
`batch_pick_queue` + `services/batch_picking.py` (`create_batch_atomic`,
`record_pick_to_queue`, `rebuild_items_from_queue`) — currently behind
`use_db_backed_picking_queue` (default OFF), and even when ON, the queue is only
used as a resume fallback while the cookie cache remains primary.

Related flow duplication that should die with it:

- **Four creation paths**: Simple page (legacy), Filter page (legacy),
  and both again via the atomic service when the flag is ON.
- **Start vs Continue**: assignment sets status `Active`, so pickers only ever see
  "Continue" — `start_batch_picking` (which seeds time tracking) barely runs.
- `filter_invoices_for_batch` runs one COUNT query + one INFO log **per invoice**
  (`routes_batch.py` 1184–1232) — 200+ queries per page on a busy day.
- Hot paths log at WARNING with emoji markers (full invoice lists on every
  creation, per-invoice log lines) — noise that hides real warnings.

## What Changes

### Phase A — Turn the queue on and make it primary

1. **Flip the flag**: set `use_db_backed_picking_queue = true` (Settings). All new
   batches are created via `create_batch_atomic` (already race-safe with
   `FOR UPDATE`). Old in-flight batches keep working via `is_db_backed_batch()`
   per-batch dispatch — no migration needed; let legacy batches drain.

2. **Read the work list from the queue, not the cookie.** In
   `batch_picking_item`, for DB-backed batches, replace the
   `session[fixed_batch_key]` block with:

   ```python
   if _is_db_backed(batch_id):
       if batch_session.session_type == 'cooler_route':
           items = build_cooler_box_picking_items(batch_session)   # already queue-driven
       else:
           items = rebuild_items_from_queue(batch_id)              # pending rows only
       current_item = items[0] if items else None                  # queue IS the pointer
   ```

   Because picked/skipped/exception rows leave `pending`, **the first pending row is
   always the current item** — `current_item_index` becomes unnecessary for
   DB-backed batches. Progress display: `picked = COUNT(status='picked')`,
   `total = COUNT(*)` from the queue (one query).

3. **Confirm/skip/exception operate on the queue row**, not a list index:
   - confirm → existing `record_pick_to_queue` (+ InvoiceItem update, unchanged)
   - skip → `status='skipped_pending'` (already done for cooler; extend the same
     UPDATE to normal batches in `skip_batch_item`)
   - exception → `status='exception'` (mirror the cooler path in
     `complete_batch_confirm` for normal batches)
   - end-of-run skip recycle → the existing cooler recycle
     (`skipped_pending → pending`) works unchanged for normal batches; delete the
     zone-scoped variant.

4. **Ordering**: `rebuild_items_from_queue` already orders by `sequence_no` and
   applies Sequential routing sort. To preserve the admin-configurable walking
   order (`sort_batch_items`), write `sequence_no` **at creation time** in
   `create_batch_atomic` using the sorted order instead of enumeration order —
   one change at step 6 of the service (sort `free` with the same
   `sorting_utils` call before `enumerate`).

5. **Delete the cookie layer** for DB-backed batches: every
   `session['batch_items_...']` / `session['batch_start_...']` read/write, the
   `force_regenerate` logic, `clear_batch_cache` helper + route, and the fallback
   rebuild blocks in `confirm_batch_item`-successor, `complete_batch_confirm`,
   `skip_batch_item`. (Keep them only inside an `if not _is_db_backed(batch_id):`
   legacy guard until old batches drain, then delete outright.)

### Phase B — One creation flow

6. Route BOTH admin pages through the service unconditionally (drop the flag
   check), then delete the legacy bodies:
   - `batch_picking_create_simple` legacy branch (`routes_batch.py` 926–1034)
   - `batch_picking_create` legacy branch (`routes_batch.py` 1390–1576)
   - the now-unused sorted-invoice/lock/enqueue duplication and
     `_enqueue_locked_items` (creation always goes through the service;
     keep `_enqueue_locked_items` only for `add_invoices_to_batch`).
7. Retire the Simple page (`/admin/batch/simple` + `batch_picking_create.html`
   simple-mode) — the Filter page covers zone-only creation by selecting zones and
   nothing else. One way in, one code path.
8. Fix the filter page N+1 (P4): replace the per-invoice count loop
   (`routes_batch.py` 1184–1232) with one grouped query — the pattern already used
   in `add_invoices_to_batch` (lines 669–677) is correct; reuse it.

### Phase C — One "Open batch" action

9. In `picker_batch_list.html`, replace Start/Continue with a single **Open**
   button → `batch_picking_item`. Move the one thing `start_batch_picking` does
   that matters (create `OrderTimeBreakdown.picking_started`, set
   `status='picking'`) into `batch_picking_item` on first open:

   ```python
   if batch_session.status in ('Created', 'Active'):
       batch_session.status = 'picking'
       _seed_time_breakdowns(batch_session, current_user.username)  # extracted from start_batch_picking
       db.session.commit()
   ```

   Then delete `start_batch_picking`.
10. Fix first-item walking time (B11): anchor to the moment the batch entered
    `picking` (add `picking_started_at` timestamp column, or reuse
    `last_activity_at` set at that moment) instead of `created_at`
    (`routes_batch.py` 2830–2835).

### Phase D — Logging diet (P7)

11. Downgrade the emoji WARNING logs in creation/confirm/skip paths to DEBUG, and
    remove the per-invoice sorted-order dumps (`routes_batch.py` 979–982,
    1495–1499, 1340, 1427–1445, 1122–1133).

## Schema Changes

Optional: `batch_picking_sessions.picking_started_at TIMESTAMP NULL` (Phase C,
step 10). Everything else uses existing tables. `current_item_index` /
`current_invoice_index` become legacy-only; drop later.

## Rollout / Rollback

- Phase A is flag-gated per batch (`is_db_backed_batch`): flag OFF → new batches
  behave exactly as today. Roll back by flipping the flag; in-flight DB-backed
  batches still finish correctly because their queue rows exist.
- Phases B–D are code deletions; ship after Phase A has run clean for a week.
- `ROLLBACK_AND_FLAGS.md` already documents the flag — update it.

## Tests Required

| # | Scenario | Expected |
|---|----------|----------|
| T1 | 60-item batch (past cookie limit), pick 10, refresh browser | Item 11 shown, order unchanged |
| T2 | Same batch, continue on a different device | Same item 11, same order |
| T3 | Pick / skip / exception / recycle full cycle on a normal DB-backed batch | Queue statuses correct; skipped item returns at end |
| T4 | Sequential batch across 3 invoices | One invoice at a time, routing-desc order preserved |
| T5 | Consolidated batch | Grouped quantities match legacy behaviour |
| T6 | Cooler route batch | Box-first flow unchanged (already queue-driven) |
| T7 | Two admins create overlapping batches simultaneously | Exactly one wins; other gets BatchConflict message |
| T8 | Filter page with 200 eligible invoices | ≤ ~5 queries (log SQL count), page fast |
| T9 | Picker opens batch via Open button | status→picking, picking_started recorded once |
| T10 | Flag OFF | Legacy path byte-identical for new batches |

## Verification

1. Enable the flag in a test environment; run a real-size batch (50+ lines) on a
   handheld, killing the browser twice mid-run — sequence must never change.
2. Watch `batch_pick_queue` rows during the run: pending → picked/skipped_pending →
   recycle → picked; counts on the Manage page must match at every step.
3. After Phase B, `grep -n "batch_items_" routes_batch.py` returns only the legacy
   guard (Phase A) or nothing (after cleanup).
