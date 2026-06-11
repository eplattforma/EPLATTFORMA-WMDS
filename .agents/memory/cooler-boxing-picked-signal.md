---
name: Cooler boxing keys off bpq.status='picked'
description: In cooler picking, batch_pick_queue.status='picked' (with no cooler_box_items row) is the SOLE signal that drives the whole boxing pipeline; a "not picked" outcome must never leave it 'picked'.
---

# Cooler boxing is driven entirely by bpq.status='picked'

A cooler item flows into the box plan / manifest based on ONE condition:
`batch_pick_queue.status='picked'` AND no `cooler_box_items` row yet. Every
downstream surface uses that same signal:

- route-completion "unboxed" check, route-status picked_unboxed counts/list
- Generate Box Plan candidate selection + confirm-plan pre-flight
- the manifest/label reads `cooler_box_items`, which is populated only when the
  plan is confirmed from those picked-unboxed candidates

**Why:** so any outcome that is NOT a real pick (reported unavailable /
zero-pick / exception) must set the queue row to a terminal non-'picked'
status (`'exception'`) and reconcile any pre-planned `cooler_box_items`
(`planned`→`exception`) — mirroring the canonical `queue_exception` path.
If it is left `'picked'`, the unavailable item gets boxed and shipped with the
required qty despite `picked_qty=0`, even though `invoice_item.pick_status`
correctly shows 'exception'.

**The trap:** `complete_batch_confirm` (Consolidated branch, which cooler
routes always use) originally mirrored EVERY line to 'picked' unconditionally
via `record_pick_to_queue` + a `cooler_box_items`→'picked' UPDATE, ignoring the
`is_exception` flag it had just used to set `invoice_item.pick_status`. The
invoice side was right; the queue/boxing side was wrong.

**How to apply:** whenever a cooler line's outcome is written, branch on
`is_exception` (defined as `bool(exception_reason) or picked_qty <= 0`): an
exception path sets bpq + cbi to 'exception'; only a genuine pick marks
'picked'. An exception on the last item in a box is fine — it makes
`cbi.status='planned'` count 0 so the box auto-closes with its remaining
picked items (matches box_close semantics: exception never blocks close).
