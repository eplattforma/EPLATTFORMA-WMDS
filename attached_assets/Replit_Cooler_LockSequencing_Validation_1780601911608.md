# Cooler — Block lock-sequencing when items have no route stop

One change only, in `blueprints/cooler_picking.py`, inside the `lock_sequencing` route.

**Rule:** It should be impossible for a cooler item to be on a route without a delivery stop.
If it ever happens the data is broken and the lock must be **blocked** — not silently skipped — so the manager fixes the problem before proceeding.

---

## Find this exact block (the loop + commit, lines ~863–896)

```python
    stamped = 0
    skipped_no_stop = 0
    skipped_details = []
    for queue_id, inv_no, seq_no in rows:
        if seq_no is None:
            skipped_no_stop += 1
            skipped_details.append({
                "invoice_no": inv_no,
                "reason": "no active route_stop_invoice row",
            })
            continue
        db.session.execute(text(
            "UPDATE batch_pick_queue SET delivery_sequence = :seq, "
            "       updated_at = :now "
            "WHERE id = :id"
        ), {"id": queue_id, "seq": float(seq_no), "now": get_utc_now()})
        stamped += 1

    now = get_utc_now()
    db.session.execute(text(
        "UPDATE batch_picking_sessions "
        "SET sequence_locked_at = :now, sequence_locked_by = :who, "
        "    cooler_pack_mode = :mode, cooler_box_type_id = :btid, "
        "    last_activity_at = :now "
        "WHERE id = :sid"
    ), {"sid": session_id, "now": now, "who": _username(), "mode": pack_mode, "btid": box_type_id})

    _audit(
        "cooler.lock_sequencing",
        f"Locked cooler sequencing for route {route_id_int}: "
        f"stamped={stamped} skipped_no_stop={skipped_no_stop} "
        f"session_id={session_id}",
    )
    db.session.commit()

    # Default response is JSON so API/test clients keep working. The HTML
    # form on the cooler page sets a hidden ``_html_form=1`` marker so we
    # can return a flash + redirect instead. Sniffing ``Accept`` headers
    # is unreliable because Werkzeug's test client and most XHR callers
    # send ``*/*``.
    if not request.form.get("_html_form"):
        return jsonify({
            "ok": True,
            "route_id": route_id_int,
            "session_id": session_id,
            "stamped": stamped,
            "skipped_no_stop": skipped_no_stop,
            "skipped_details": skipped_details,
            "locked_at": now.isoformat(),
            "locked_by": _username(),
        })

    # HTML form POST — flash and redirect back to the picking screen
    if stamped:
        flash(f"Sequencing locked — {stamped} item(s) stamped with delivery order.", "success")
    else:
        flash("Sequencing locked (all items already had a sequence).", "info")
    if skipped_no_stop:
        flash(
            f"{skipped_no_stop} item(s) could not be sequenced (no active route stop): "
            + ", ".join(d['invoice_no'] for d in skipped_details),
            "warning",
        )
```

## Replace with this

```python
    # ── Pre-flight: block the lock if any item has no route stop ─────────────
    # Every cooler item on a route must have an active route_stop_invoice row.
    # If any are missing the route data is broken; force the manager to fix it
    # before locking rather than silently skipping items and producing an
    # incomplete pick list.
    missing_stop = [
        {"invoice_no": inv_no, "queue_id": queue_id}
        for queue_id, inv_no, seq_no in rows
        if seq_no is None
    ]
    if missing_stop:
        missing_invoices = ", ".join(d["invoice_no"] for d in missing_stop)
        msg = (
            f"Cannot lock — {len(missing_stop)} item(s) have no delivery stop assigned: "
            f"{missing_invoices}. "
            "Assign these invoices to a route stop first, then try again."
        )
        _audit(
            "cooler.lock_sequencing_blocked",
            f"Lock blocked for route {route_id_int}: "
            f"{len(missing_stop)} item(s) missing route stop — {missing_invoices}",
        )
        db.session.commit()  # persist the audit log entry
        if not request.form.get("_html_form"):
            return jsonify({
                "ok": False,
                "error": msg,
                "missing_stop": missing_stop,
            }), 422

        delivery_date = request.form.get("delivery_date", "").strip()
        if not delivery_date:
            date_row = db.session.execute(
                text("SELECT delivery_date FROM shipments WHERE id = :rid"),
                {"rid": route_id_int},
            ).fetchone()
            delivery_date = str(date_row[0]) if date_row and date_row[0] else ""
        flash(msg, "danger")
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id_int,
                                delivery_date=delivery_date))

    # All items have stops — stamp delivery_sequence for any not yet set
    stamped = 0
    for queue_id, inv_no, seq_no in rows:
        db.session.execute(text(
            "UPDATE batch_pick_queue SET delivery_sequence = :seq, "
            "       updated_at = :now "
            "WHERE id = :id"
        ), {"id": queue_id, "seq": float(seq_no), "now": get_utc_now()})
        stamped += 1

    now = get_utc_now()
    db.session.execute(text(
        "UPDATE batch_picking_sessions "
        "SET sequence_locked_at = :now, sequence_locked_by = :who, "
        "    cooler_pack_mode = :mode, cooler_box_type_id = :btid, "
        "    last_activity_at = :now "
        "WHERE id = :sid"
    ), {"sid": session_id, "now": now, "who": _username(), "mode": pack_mode, "btid": box_type_id})

    _audit(
        "cooler.lock_sequencing",
        f"Locked cooler sequencing for route {route_id_int}: "
        f"stamped={stamped} session_id={session_id}",
    )
    db.session.commit()

    if not request.form.get("_html_form"):
        return jsonify({
            "ok": True,
            "route_id": route_id_int,
            "session_id": session_id,
            "stamped": stamped,
            "locked_at": now.isoformat(),
            "locked_by": _username(),
        })

    # HTML form POST — flash and redirect back to the picking screen
    if stamped:
        flash(f"Sequencing locked — {stamped} item(s) stamped with delivery order.", "success")
    else:
        flash("Sequencing locked (all items already had a sequence).", "info")
```

---

## What this does

- **Before:** if an item has no route stop, `lock_sequencing` silently skips it, stamps a warning flash, and carries on. The missing item disappears from the pick list and is never picked.
- **After:** if ANY item has no route stop, the entire lock is **refused** with a clear `danger` flash listing the affected invoices. Nothing is stamped. The manager must fix the route assignment before the lock will proceed.

The audit log always records the block event so there is a trace even when the error occurs repeatedly.
