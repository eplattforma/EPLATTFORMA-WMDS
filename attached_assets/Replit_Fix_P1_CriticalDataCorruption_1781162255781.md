# WMDS Fix — Priority 1: Critical Data Corruption

These three bugs silently corrupt or destroy production data. Fix them in order, before any other work. Every code block below is exact, copy-pasteable find/replace text taken from the current codebase.

---

## Bug 1 — `unassign_from_route` deletes empty stops across the ENTIRE database

**File:** `routes_routes.py`
**Function:** `unassign_from_route` (route `POST /unassign-from-route`, function starts ~line 1121; the buggy block is ~lines 1153–1183)

After unassigning invoices, the cleanup query selects **every** `route_stop` row in the whole database that has zero linked `route_stop_invoice` rows — it is not filtered to the routes touched by this request. Any historical or unrelated route that legitimately has an empty stop (or whose stop became empty for any other reason) gets its stops deleted via `delete_stop()`, silently destroying stop data and sequence numbers on routes nobody asked to modify.

### Current code (find this exact block)

```python
    # Unassign each invoice from its route
    from sqlalchemy import delete
    from services.cooler_route_extraction import release_cooler_locks_for_invoice
    for invoice in invoices:
        # Always delete route_stop_invoice records (don't rely on stop_id being set)
        db.session.execute(
            delete(RouteStopInvoice).where(
                RouteStopInvoice.invoice_no == invoice.invoice_no
            )
        )
        invoice.route_id = None
        invoice.stop_id = None
        # Release cooler batch locks/queue rows for return-to-warehouse
        force_reset = bool(data.get("force_cooler_reset", False))
        release_cooler_locks_for_invoice(invoice.invoice_no, full_reset=force_reset)

    db.session.commit()
    
    # Clean up any empty stops after unassigning invoices
    empty_stops = db.session.execute(
        db.select(RouteStop.route_stop_id).outerjoin(
            RouteStopInvoice
        ).group_by(RouteStop.route_stop_id).having(
            db.func.count(RouteStopInvoice.invoice_no) == 0
        )
    ).scalars().all()
    
    for stop_id in empty_stops:
        from services import delete_stop
        delete_stop(stop_id)
```

### Replacement code

```python
    # Unassign each invoice from its route
    from sqlalchemy import delete
    from services.cooler_route_extraction import release_cooler_locks_for_invoice
    # Capture the affected route ids BEFORE clearing invoice.route_id so the
    # empty-stop cleanup below can be scoped to ONLY these routes.
    affected_route_ids = {
        invoice.route_id for invoice in invoices if invoice.route_id is not None
    }
    for invoice in invoices:
        # Always delete route_stop_invoice records (don't rely on stop_id being set)
        db.session.execute(
            delete(RouteStopInvoice).where(
                RouteStopInvoice.invoice_no == invoice.invoice_no
            )
        )
        invoice.route_id = None
        invoice.stop_id = None
        # Release cooler batch locks/queue rows for return-to-warehouse
        force_reset = bool(data.get("force_cooler_reset", False))
        release_cooler_locks_for_invoice(invoice.invoice_no, full_reset=force_reset)

    db.session.commit()
    
    # Clean up any empty stops after unassigning invoices — scoped to ONLY
    # the routes touched by this request. Never scan the whole database:
    # unrelated/historical routes may legitimately have empty stops and
    # must not be modified by this endpoint.
    empty_stops = []
    if affected_route_ids:
        empty_stops = db.session.execute(
            db.select(RouteStop.route_stop_id).outerjoin(
                RouteStopInvoice
            ).where(
                RouteStop.shipment_id.in_(affected_route_ids)
            ).group_by(RouteStop.route_stop_id).having(
                db.func.count(RouteStopInvoice.invoice_no) == 0
            )
        ).scalars().all()
    
    for stop_id in empty_stops:
        from services import delete_stop
        delete_stop(stop_id)
```

Note: `RouteStop` (table `route_stop`, defined in `models.py` ~line 1164) stores its route reference in the column `shipment_id` (FK to `shipments.id`), and `Invoice.route_id` also points to `shipments.id` — so `RouteStop.shipment_id.in_(affected_route_ids)` is the correct scoping filter.

### Testing checklist

- [ ] Create two routes (A and B), each with stops and assigned invoices. Manually empty one stop on route B (delete its `route_stop_invoice` rows directly in the DB). Unassign an invoice from route A via `POST /unassign-from-route` — verify route B's empty stop is NOT deleted.
- [ ] Unassign the last invoice from a stop on route A — verify that stop IS deleted and the rest of route A is intact.
- [ ] Unassign invoices spanning two routes in one request — verify empty-stop cleanup runs on both routes and nothing else.
- [ ] Call the endpoint with invoices that have `route_id = NULL` already — verify no error and no stop deletions occur (`affected_route_ids` is empty).

---

## Bug 2 — `cancel_batch` cooler box teardown matches zero rows: `cooler_session_id` is never populated

**Files:** `blueprints/cooler_picking.py` and `services/batch_picking.py`

`cancel_batch` (in `services/batch_picking.py`) cancels cooler boxes with `WHERE cooler_session_id = :sid`. But none of the box creation paths in `blueprints/cooler_picking.py` ever populate `cooler_boxes.cooler_session_id`:

- `box_create` (~line 1276): both INSERTs omit the column entirely.
- `pre_plan_boxes` (~line 1645): INSERT omits the column entirely.
- `confirm_box_plan` (~line 1421): INSERT includes the column but explicitly binds `"sid": None`.
- `pack_stop` (~line 3044): INSERT omits the column (endpoint is disabled with an early `return ... 410`, but the dead code should still be corrected).

Result: cancelling a cooler batch silently leaves all its boxes open — the teardown UPDATE matches zero rows.

### Fix 2.0 — Add a session-resolution helper to `blueprints/cooler_picking.py`

Insert a new helper immediately before `_is_cooler_route_pack_complete` (~line 178). It uses the same latest-session lookup pattern already used elsewhere in this file (e.g., ~line 1128).

**Find this exact text:**

```python
def _is_cooler_route_pack_complete(route_id, delivery_date):
```

**Replace with:**

```python
def _resolve_cooler_session_id(route_id):
    """Return the id of the LATEST cooler-route batch session for this
    route (legacy name patterns included), or None if no session exists.
    Mirrors the lookup used by the sequencing/lock endpoints."""
    try:
        rid = int(route_id)
    except (TypeError, ValueError):
        return None
    row = db.session.execute(text(
        "SELECT id FROM batch_picking_sessions "
        "WHERE session_type = 'cooler_route' "
        "  AND (route_id = :rid OR name = :legacy "
        "       OR name LIKE :legacy_prefix) "
        "ORDER BY created_at DESC LIMIT 1"
    ), {
        "rid": rid,
        "legacy": f"COOLER-ROUTE-{rid}",
        "legacy_prefix": f"COOLER-ROUTE-{rid}-%",
    }).fetchone()
    return row[0] if row is not None else None


def _is_cooler_route_pack_complete(route_id, delivery_date):
```

### Fix 2(a)-1 — `box_create` (`blueprints/cooler_picking.py`, ~lines 1303–1330)

**Find this exact block (the primary INSERT with RETURNING):**

```python
    now = get_utc_now()
    try:
        result = db.session.execute(
            text(
                "INSERT INTO cooler_boxes "
                "(route_id, delivery_date, box_no, status, created_by, created_at) "
                "VALUES (:rid, :dd, :bn, 'open', :who, :now) "
                "RETURNING id"
            ),
            {"rid": route_id, "dd": delivery_date, "bn": box_no,
             "who": _username(), "now": now},
        )
        new_id = result.scalar()
```

**Replace with:**

```python
    now = get_utc_now()
    cooler_session_id = _resolve_cooler_session_id(route_id)
    try:
        result = db.session.execute(
            text(
                "INSERT INTO cooler_boxes "
                "(route_id, delivery_date, box_no, status, created_by, created_at, "
                " cooler_session_id) "
                "VALUES (:rid, :dd, :bn, 'open', :who, :now, :sid) "
                "RETURNING id"
            ),
            {"rid": route_id, "dd": delivery_date, "bn": box_no,
             "who": _username(), "now": now, "sid": cooler_session_id},
        )
        new_id = result.scalar()
```

**Then find the SQLite fallback INSERT in the same function (~lines 1318–1330):**

```python
        try:
            db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, created_by, created_at) "
                    "VALUES (:rid, :dd, :bn, 'open', :who, :now)"
                ),
                {"rid": route_id, "dd": delivery_date, "bn": box_no,
                 "who": _username(), "now": now},
            )
```

**Replace with:**

```python
        try:
            db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, created_by, created_at, "
                    " cooler_session_id) "
                    "VALUES (:rid, :dd, :bn, 'open', :who, :now, :sid)"
                ),
                {"rid": route_id, "dd": delivery_date, "bn": box_no,
                 "who": _username(), "now": now, "sid": cooler_session_id},
            )
```

### Fix 2(a)-2 — `confirm_box_plan` (`blueprints/cooler_picking.py`, ~lines 1466–1496)

The INSERT already has the column; it just binds `None`. Resolve the session once before the loop and bind it.

**Find this exact block:**

```python
    now = get_utc_now()
    created = 0
    skipped = 0

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
```

**Replace with:**

```python
    now = get_utc_now()
    created = 0
    skipped = 0
    cooler_session_id = _resolve_cooler_session_id(route_id_int)

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
```

**Then find this exact line (~line 1494, the only occurrence of `"sid": None,` in the file):**

```python
                    "sid": None,
```

**Replace with:**

```python
                    "sid": cooler_session_id,
```

### Fix 2(a)-3 — `pre_plan_boxes` (`blueprints/cooler_picking.py`, ~lines 1697–1723)

**Find this exact block:**

```python
    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, first_stop_sequence, "
                    " last_stop_sequence, created_by, created_at, box_type_id, "
                    " fill_cm3, fill_weight_kg) "
                    "VALUES (:rid, :dd, :box_no, 'open', :fs, :ls, :who, :now, "
                    "        :btid, :fill, :weight) "
                    "RETURNING id"
                ),
                {
                    "rid": route_id_int, "dd": str(delivery_date),
                    "box_no": idx,
                    "fs": box["stop_min"], "ls": box["stop_max"],
                    "who": _username(), "now": now,
                    "btid": box["box_type_id"],
                    "fill": box["estimated_fill_cm3"],
                    "weight": box["estimated_weight_kg"],
                },
            ).fetchone()
```

**Replace with:**

```python
    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0
    cooler_session_id = _resolve_cooler_session_id(route_id_int)

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, first_stop_sequence, "
                    " last_stop_sequence, created_by, created_at, box_type_id, "
                    " fill_cm3, fill_weight_kg, cooler_session_id) "
                    "VALUES (:rid, :dd, :box_no, 'open', :fs, :ls, :who, :now, "
                    "        :btid, :fill, :weight, :sid) "
                    "RETURNING id"
                ),
                {
                    "rid": route_id_int, "dd": str(delivery_date),
                    "box_no": idx,
                    "fs": box["stop_min"], "ls": box["stop_max"],
                    "who": _username(), "now": now,
                    "btid": box["box_type_id"],
                    "fill": box["estimated_fill_cm3"],
                    "weight": box["estimated_weight_kg"],
                    "sid": cooler_session_id,
                },
            ).fetchone()
```

### Fix 2(a)-4 — `pack_stop` (`blueprints/cooler_picking.py`, ~lines 3104–3118)

This endpoint is disabled (`return jsonify({"error": "This endpoint has been disabled."}), 410` at the top), but correct the dead code so it cannot reintroduce the bug if re-enabled.

**Find this exact block:**

```python
    now = get_utc_now()
    result = db.session.execute(
        text(
            "INSERT INTO cooler_boxes "
            "(route_id, delivery_date, box_no, status, created_at, created_by) "
            "VALUES (:rid, :dd, :bno, 'open', :now, :who) RETURNING id"
        ),
        {
            "rid": route_id_int,
            "dd": delivery_date_str,
            "bno": new_box_no,
            "now": now,
            "who": _username(),
        },
    ).fetchone()
```

**Replace with:**

```python
    now = get_utc_now()
    result = db.session.execute(
        text(
            "INSERT INTO cooler_boxes "
            "(route_id, delivery_date, box_no, status, created_at, created_by, "
            " cooler_session_id) "
            "VALUES (:rid, :dd, :bno, 'open', :now, :who, :sid) RETURNING id"
        ),
        {
            "rid": route_id_int,
            "dd": delivery_date_str,
            "bno": new_box_no,
            "now": now,
            "who": _username(),
            "sid": _resolve_cooler_session_id(route_id_int),
        },
    ).fetchone()
```

### Fix 2(b) — `cancel_batch` teardown fallback (`services/batch_picking.py`, ~lines 572–583)

`BatchPickingSession` has a nullable `route_id` column (`models.py` ~line 278). Use it as a fallback match for legacy boxes created before fix 2(a), restricted to boxes whose `cooler_session_id` is NULL so boxes belonging to a different (sibling) session are never touched.

**Find this exact block:**

```python
        # Cooler-specific teardown: cancel any open boxes.
        if getattr(batch, 'session_type', None) == 'cooler_route':
            db.session.execute(
                text("""
                    UPDATE cooler_boxes
                    SET status = 'cancelled'
                    WHERE cooler_session_id = :sid
                      AND status NOT IN ('closed', 'loaded', 'delivered')
                """),
                {"sid": batch_id},
            )
```

**Replace with:**

```python
        # Cooler-specific teardown: cancel any open boxes.
        # Boxes are matched by cooler_session_id (populated at box creation),
        # with a fallback on route_id for legacy boxes created before
        # cooler_session_id was being set (those rows have it NULL).
        if getattr(batch, 'session_type', None) == 'cooler_route':
            _batch_route_id = getattr(batch, 'route_id', None)
            if _batch_route_id is not None:
                db.session.execute(
                    text("""
                        UPDATE cooler_boxes
                        SET status = 'cancelled'
                        WHERE (cooler_session_id = :sid
                               OR (cooler_session_id IS NULL
                                   AND route_id = :rid))
                          AND status NOT IN ('closed', 'loaded', 'delivered')
                    """),
                    {"sid": batch_id, "rid": _batch_route_id},
                )
            else:
                db.session.execute(
                    text("""
                        UPDATE cooler_boxes
                        SET status = 'cancelled'
                        WHERE cooler_session_id = :sid
                          AND status NOT IN ('closed', 'loaded', 'delivered')
                    """),
                    {"sid": batch_id},
                )
```

### Testing checklist

- [ ] Create a cooler route session, then create a box via each path (`box_create`, `pre_plan_boxes`, `confirm_box_plan`). Verify in the DB: `SELECT id, cooler_session_id FROM cooler_boxes` — every new row has a non-NULL `cooler_session_id` equal to the session id.
- [ ] Cancel the cooler batch via `cancel_batch`. Verify all open boxes for that session flip to `status = 'cancelled'`, and boxes with status `closed`, `loaded`, or `delivered` are untouched.
- [ ] Legacy fallback: manually set `cooler_session_id = NULL` on an open box for the session's route, cancel the batch, and verify that box is also cancelled (route_id fallback).
- [ ] Sibling-session safety: create two cooler sessions on the same route; the second session's boxes (with their own `cooler_session_id`) must NOT be cancelled when the first session is cancelled.
- [ ] Cancel a `standard` (non-cooler) batch and verify no `cooler_boxes` rows are modified.

---

## Bug 3 — `box_close` session-complete UPDATE marks ALL of the route's cooler sessions Completed

**File:** `blueprints/cooler_picking.py`
**Functions:** `_is_cooler_route_pack_complete` (~line 178) and `box_close` (~line 2143; the buggy UPDATE is ~lines 2298–2309)

When the last box on a route+date closes, `_is_cooler_route_pack_complete(route_id, delivery_date)` correctly checks completion scoped to route AND date — but the follow-up UPDATE is scoped only by `route_id`. It flips **every** non-terminal cooler session for that route to `Completed`, including sessions belonging to other delivery dates or sibling sessions for a different run, which then disappear from active work queues with unpicked items still outstanding.

### Current code (find this exact block in `box_close`)

```python
    if _is_cooler_route_pack_complete(box["route_id"], box["delivery_date"]):
        db.session.execute(
            text(
                "UPDATE batch_picking_sessions "
                "SET status = 'Completed', last_activity_at = :now "
                "WHERE session_type = 'cooler_route' "
                "  AND route_id = :rid "
                "  AND status NOT IN ('Completed', 'Cancelled', 'Archived')"
            ),
            {"rid": box["route_id"], "now": now},
        )
```

### Replacement code

This uses the box's own `cooler_session_id` (populated by Bug 2 fix) and falls back to `_resolve_cooler_session_id` (the helper added in Bug 2, Fix 2.0 — apply that first) for legacy boxes. The UPDATE is scoped to the single session id.

```python
    if _is_cooler_route_pack_complete(box["route_id"], box["delivery_date"]):
        # Scope the completion to ONE session: the session this box belongs
        # to. Never complete by route_id alone — that flips every
        # non-terminal cooler session on the route (other dates / sibling
        # runs) to Completed.
        _session_id = db.session.execute(
            text("SELECT cooler_session_id FROM cooler_boxes WHERE id = :bid"),
            {"bid": box_id},
        ).scalar()
        if _session_id is None:
            # Legacy box created before cooler_session_id was populated:
            # fall back to the latest cooler session for this route.
            _session_id = _resolve_cooler_session_id(box["route_id"])
        if _session_id is not None:
            db.session.execute(
                text(
                    "UPDATE batch_picking_sessions "
                    "SET status = 'Completed', last_activity_at = :now "
                    "WHERE id = :sid "
                    "  AND session_type = 'cooler_route' "
                    "  AND status NOT IN ('Completed', 'Cancelled', 'Archived')"
                ),
                {"sid": _session_id, "now": now},
            )
```

### Testing checklist

- [ ] Create two cooler sessions on the same route (e.g., two delivery dates, or a sibling `COOLER-ROUTE-<id>-2` session). Fully pick and close all boxes for session 1 — verify only session 1 becomes `Completed`; session 2 remains in its prior status.
- [ ] Close the last box of a complete route+date and verify its session flips to `Completed` exactly as before (no regression in the happy path).
- [ ] Close a box while other boxes on the same route+date are still open — verify no session is completed (`_is_cooler_route_pack_complete` returns False).
- [ ] Legacy box path: set a box's `cooler_session_id` to NULL, close it as the last box, and verify the latest cooler session for the route is the one completed (fallback works).
- [ ] Verify already `Cancelled` or `Archived` sessions are never resurrected or re-flipped by the UPDATE.

---

## Apply order

1. Bug 1 (`routes_routes.py`) — standalone.
2. Bug 2, Fix 2.0 first (adds `_resolve_cooler_session_id`, required by 2(a) and Bug 3), then 2(a)-1 through 2(a)-4, then 2(b).
3. Bug 3 — depends on the helper from Fix 2.0.
