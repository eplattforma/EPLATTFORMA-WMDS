# FIX-008 — Batch Reports Must Read Route/Stop from the Routes Module

## Priority: HIGH — Every standard batch now prints "NO-ROUTING" because the legacy `Invoice.routing` field died when routing moved to the Routes module.

## The Problem (verified against production data, 8 Jul 2026)

The batch report label resolves: batch route stop (only when `batch_session.route_id`
is set) → `Invoice.routing` → "NO-ROUTING". Standard zone batches have no
`route_id`, so they depend entirely on `Invoice.routing` — a field the import
used to fill but which is now dead:

| Month | Invoices | `routing` populated | Active route link (`route_stop_invoice`) |
|-------|----------|--------------------:|------------------------------------------:|
| 2026-04 | 578 | 568 (98%) | 501 |
| 2026-05 | 634 | 398 (63%) | 553 |
| 2026-06 | 684 |  49 (7%)  | 613 |
| 2026-07 | 191 |  15 (8%)  | 166 |

Example: IN10056387 / IN10056393 print "NO-ROUTING" but are stops **9** and
**13** of route **PAFOS THU2** (shipment 483).

Everything keyed on `Invoice.routing` is decaying the same way, not just the
label: report page ordering, Sequential batch invoice ordering at creation,
`rebuild_items_from_queue`'s Sequential sort, the pick-screen header
(`{{ item.routing }}` shows blank), and `batch_picking_summary` grouping.

## What Changes

### Change 1 — One helper: invoice → (route_name, stop_seq)

In `routes_batch.py`, extend `_build_stop_seq_lookup(batch_session)`. Keep the
existing route-bound branch; add a fallback for standard batches that looks up
each batch invoice's ACTIVE route link:

```python
def _build_stop_seq_lookup(batch_session):
    route_id = getattr(batch_session, 'route_id', None)
    if route_id:
        ...existing code, but also select Shipment.route_name...
        # value: {'seq': float, 'route_name': name}
    # Fallback: standard batch — invoices may still be on routes
    inv_nos = [bi.invoice_no for bi in batch_session.invoices]
    if not inv_nos:
        return {}
    rows = db.session.query(
        RouteStopInvoice.invoice_no, RouteStop.seq_no, Shipment.route_name,
    ).join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id
    ).join(Shipment, Shipment.id == RouteStop.shipment_id
    ).filter(
        RouteStopInvoice.invoice_no.in_(inv_nos),
        RouteStopInvoice.is_active.is_(True),
        RouteStop.deleted_at.is_(None),
        Shipment.status.notin_(['COMPLETED', 'CANCELLED']),
    ).all()
    out = {}
    for inv_no, seq, rname in rows:
        if inv_no not in out:          # first active link wins
            out[inv_no] = {'seq': float(seq) if seq is not None else None,
                           'route_name': rname or ''}
    return out
```

(Callers currently index the dict for a bare float — update the two report
routes and `get_routing_key` accordingly.)

### Change 2 — Label shows route + stop

`_routing_label_for_invoice`: when the lookup hits, return
`"{route_name} · STOP {n}"` (route name matters because one zone batch can span
several routes — a bare stop number is ambiguous). Keep the `Invoice.routing`
fallback for old data, then `NO-ROUTING`. In `batch_report.html` render the
route name on a smaller second line so the stop number stays big:

```html
<div class="routing-large">STOP {{ inv.stop_seq|int }}</div>
<div style="font-size:20px;font-weight:bold;">{{ inv.route_name }}</div>
```

(or keep the single combined label string — pick whichever reads better on
paper; the stop number must stay large for the loaders.)

### Change 3 — Report page ordering

`get_routing_key` in both report routes: sort by `(route_name, seq)` when the
lookup hits, so pages come out grouped per route in stop order; legacy
routing-desc fallback stays for old batches.

### Change 4 — Same source for the picking order (Sequential)

- Batch creation (`create_batch_atomic` and the invoice sort in
  `_enqueue_locked_items`' callers): invoices with an active route link order by
  (route_name, stop_seq) instead of the now-NULL routing float.
- `rebuild_items_from_queue` Sequential sort: same key (fall back to routing).
- Pick-screen header (`batch_picking_item.html` line ~243 `{{ item.routing }}`)
  and `batch_picking_summary` grouping: pass the same label
  (`STOP 9 · PAFOS THU2`) through the serialisers instead of raw `routing`.

### Change 5 — Decide the fate of `Invoice.routing`

Don't half-keep it. Either (a) stop displaying it anywhere and treat the Routes
module as the single source (recommended — it already covers ~90% of invoices),
or (b) have the route-planning code write the stop seq back into
`Invoice.routing`. Do NOT do both. Option (a) means unrouted invoices honestly
print "NO ROUTE YET", which is true and actionable.

## Schema Changes

None.

## Tests Required

| # | Scenario | Expected |
|---|----------|----------|
| RT1 | Standard zone batch, invoice on active route (stop 9, PAFOS THU2) | Report prints "STOP 9 / PAFOS THU2", not NO-ROUTING |
| RT2 | Invoice with legacy `routing` value, no route link | Legacy number still prints |
| RT3 | Invoice on no route at all | "NO-ROUTING" |
| RT4 | Batch spanning two routes | Pages grouped per route, stop order within each |
| RT5 | Invoice whose route is COMPLETED/CANCELLED, relinked to a new route | New route's stop wins |
| RT6 | Sequential batch creation with route-linked invoices | Invoice order follows stop sequence |
| RT7 | Pick screen header for route-linked invoice | Shows stop label, not blank |

## Verification

1. Reprint the batch from the screenshot (invoices IN10056387 / IN10056393) —
   pages must show STOP 9 and STOP 13 with PAFOS THU2, in that order.
2. Create a fresh batch over invoices from two different routes; check page
   grouping and the pick-screen header.
