---
name: VPACK pieces convention
description: Virtual-pack items must always be instructed/validated in pieces, not pack units.
---
Rule: any picking UI or quantity check for VPACK (virtual pack) items must work in PIECES — qty × DwItem.number_of_pieces (only when attribute_1_code_365 == 'VPACK'). `InvoiceItem.display_qty` / `display_unit_type` are the authority; batch code shares this via `services/batch_picking.pieces_required_for_source` / `apply_vpack_display`.

**Why:** queue/import snapshots (`expected_pick_pieces`, `batch_pick_queue.qty_required`) can hold stale pack units when the DW item was synced after invoice import — the live DwItem lookup is what normal picking trusts, so all screens must match it or pickers see "Pick 4 VIRTUAL PACK" instead of "Pick 12 Pieces" and false quantity exceptions fire.

**How to apply:** when adding any new pick/confirm/exception surface, derive expected quantity from display_qty (pieces), record picked_qty in pieces, and never compare picker input against raw queue qty for VPACK lines.
