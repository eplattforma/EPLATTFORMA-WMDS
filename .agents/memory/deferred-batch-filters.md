---
name: Deferred batch item filters
description: Deferred ("Send to Batch") sessions use sentinel values that every batch item filter must handle
---

## Rule
Deferred batch sessions use two sentinels: `zones='DEFERRED'` (not a real zone) and `pick_status='sent_to_batch'` on their items. Any query that filters batch items by real zone lists or by a hardcoded pick_status whitelist will silently return 0 rows for deferred sessions. Scope deferred sessions by `locked_by_batch_id` instead of zone, and include `'sent_to_batch'` in status whitelists.

**Why:** When `get_grouped_items()` returns empty, picking silently falls back to `rebuild_items_from_queue()`, which (before the fix) merged the same item across different customers' invoices — a hard-to-spot data bug, not an error.

**How to apply:** When adding any new batch-item query (counts, completion checks, reports), check it against a `session_type='deferred_route'` session. Regression tests live in `tests/test_deferred_batch.py` (per-customer separation tests at the bottom).
