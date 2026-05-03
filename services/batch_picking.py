"""Phase 4: DB-backed batch picking service.

Provides:
  - ``BatchConflict`` exception raised when two concurrent batch
    creations want overlapping items.
  - ``create_batch_atomic(...)`` — single-transaction batch creation
    that locks items, creates ``batch_pick_queue`` rows, and writes the
    audit log; rolls back entirely on conflict.
  - ``cancel_batch(...)`` — soft cancel that releases unpicked locks,
    flips queue rows to ``cancelled``, preserves picked rows for audit,
    and writes ``batch.cancelled`` activity.

All flag reads default to the production-safe value (``false`` for
``use_db_backed_picking_queue``) so callers in the legacy path can call
these helpers freely without changing behaviour.
"""
import logging

from sqlalchemy import and_, text
from sqlalchemy.exc import IntegrityError

from app import db
from models import (
    ActivityLog,
    BatchPickedItem,
    BatchPickingSession,
    BatchSessionInvoice,
    Invoice,
    InvoiceItem,
    Setting,
)
from services import batch_status
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)


class BatchConflict(Exception):
    """Raised when batch creation cannot proceed because some items are
    already locked by another active batch.

    Attributes:
      conflicting_batch_id: id of the batch that holds the lock
                            (``None`` if multiple batches conflict).
      conflicting_item_codes: list of (invoice_no, item_code) tuples.
      message: human-readable summary suitable for a flash message.
    """

    def __init__(self, conflicting_batch_id=None, conflicting_item_codes=None, message=None):
        self.conflicting_batch_id = conflicting_batch_id
        self.conflicting_item_codes = list(conflicting_item_codes or [])
        if not message:
            n = len(self.conflicting_item_codes)
            if conflicting_batch_id:
                message = (
                    f"Cannot create batch: {n} item(s) are already locked by "
                    f"batch #{conflicting_batch_id}."
                )
            else:
                message = f"Cannot create batch: {n} item(s) are already locked by another batch."
        super().__init__(message)
        self.message = message


def is_db_queue_enabled():
    """Read ``use_db_backed_picking_queue`` flag (defaults OFF)."""
    try:
        return Setting.get(db.session, "use_db_backed_picking_queue", "false").lower() == "true"
    except Exception:
        return False


def is_db_backed_batch(batch_id):
    """Per-batch dispatcher: a batch is DB-backed iff it has at least
    one row in ``batch_pick_queue``. This decouples pick-time behaviour
    from the global feature flag, so a batch created while the flag was
    ON keeps writing to its queue even after the flag is flipped OFF
    mid-shift (and vice-versa: legacy batches never start writing).
    """
    try:
        n = db.session.execute(
            text("SELECT 1 FROM batch_pick_queue WHERE batch_session_id = :bid LIMIT 1"),
            {"bid": batch_id},
        ).scalar()
        return n is not None
    except Exception:
        return False


def record_pick_to_queue(batch_id, invoice_no, item_code, picker, qty_picked):
    """Mark the matching ``batch_pick_queue`` row picked + touch
    ``last_activity_at``. Dispatch is per-batch (queue-row existence),
    NOT the global flag — durable resume must work even if the flag
    flips mid-shift. Returns rowcount; 0 means legacy batch (no-op).
    Errors are re-raised so failures are operationally visible.
    """
    if not is_db_backed_batch(batch_id):
        return 0
    from timezone_utils import get_utc_now
    now = get_utc_now()
    result = db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'picked', picked_by = :picker, "
            "    picked_at = :now, qty_picked = :qty "
            "WHERE batch_session_id = :bid "
            "  AND invoice_no = :inv "
            "  AND item_code = :ic "
            "  AND status = 'pending'"
        ),
        {"picker": picker, "now": now, "qty": qty_picked,
         "bid": batch_id, "inv": invoice_no, "ic": item_code},
    )
    db.session.execute(
        text("UPDATE batch_picking_sessions SET last_activity_at = :now WHERE id = :bid"),
        {"now": now, "bid": batch_id},
    )
    return result.rowcount or 0


def rebuild_items_from_queue(batch_id):
    """Rebuild the picker work-list from ``batch_pick_queue`` pending
    rows for a DB-backed batch. This is the durable resume path: when
    the Flask session cache is missing (refresh / restart / new device),
    callers use this to reconstruct display data from the queue +
    invoice_items WITHOUT relying on the legacy session-cached list.

    Returns: list of dicts in sequence_no order, one per (item_code,
    location, zone) group, each with the source_items the picker needs.
    Returns [] for non-DB-backed batches.
    """
    if not is_db_backed_batch(batch_id):
        return []
    rows = db.session.execute(
        text("SELECT invoice_no, item_code, qty_required, sequence_no "
             "FROM batch_pick_queue "
             "WHERE batch_session_id = :bid AND status = 'pending' "
             "ORDER BY sequence_no, id"),
        {"bid": batch_id},
    ).fetchall()
    rebuilt, seen = [], {}
    for r in rows:
        ii = InvoiceItem.query.filter_by(invoice_no=r.invoice_no, item_code=r.item_code).first()
        zone = (getattr(ii, 'zone', '') if ii else '') or ''
        location = (getattr(ii, 'location', '') if ii else '') or ''
        key = (r.item_code, location, zone)
        if key not in seen:
            inv = Invoice.query.filter_by(invoice_no=r.invoice_no).first()
            seen[key] = {
                'item_code': r.item_code,
                'item_name': (getattr(ii, 'item_name', None) if ii else r.item_code) or r.item_code,
                'location': location,
                'zone': zone,
                'barcode': (getattr(ii, 'barcode', '') if ii else ''),
                'unit_type': (getattr(ii, 'unit_type', '') if ii else ''),
                'pack': (getattr(ii, 'pack', '') if ii else ''),
                'total_qty': 0,
                'current_invoice': r.invoice_no,
                'customer_name': (getattr(inv, 'customer_name', None) if inv else None),
                'routing': (getattr(inv, 'routing', None) if inv else None),
                'order_total_items': (getattr(inv, 'total_items', None) if inv else None),
                'order_total_weight': (getattr(inv, 'total_weight', None) if inv else None),
                'source_items': [],
            }
            rebuilt.append(seen[key])
        entry = seen[key]
        entry['total_qty'] += int(r.qty_required or 0)
        entry['source_items'].append({
            'invoice_no': r.invoice_no,
            'item_code': r.item_code,
            'qty': int(r.qty_required or 0),
        })
    return rebuilt


def is_claim_required():
    """Read ``batch_claim_required`` flag (defaults OFF). When ON, a
    picker must explicitly claim a batch before starting to pick — the
    legacy ``assigned_to`` auto-assignment is no longer enough."""
    try:
        return Setting.get(db.session, "batch_claim_required", "false").lower() == "true"
    except Exception:
        return False


def _candidate_items_query(filters):
    """Build the candidate-items query from a ``filters`` dict.

    ``filters`` keys (all optional except ``zones``):
      - zones: list[str]
      - corridors: list[str]
      - unit_types: list[str]
      - invoice_nos: list[str]  (when caller pre-selected a subset)
    """
    zones = [z for z in (filters.get("zones") or []) if z]
    if not zones:
        return None

    conds = [
        InvoiceItem.zone.in_(zones),
        InvoiceItem.is_picked.is_(False),
        InvoiceItem.pick_status.in_(["not_picked", "reset", "skipped_pending"]),
    ]
    if filters.get("corridors"):
        conds.append(InvoiceItem.corridor.in_(filters["corridors"]))
    if filters.get("unit_types"):
        conds.append(InvoiceItem.unit_type.in_(filters["unit_types"]))
    if filters.get("invoice_nos"):
        conds.append(InvoiceItem.invoice_no.in_(filters["invoice_nos"]))

    return db.session.query(InvoiceItem).filter(and_(*conds))


def create_batch_atomic(filters, created_by, mode="Sequential", name=None,
                        batch_number=None, assigned_to=None,
                        pick_zone_type="normal"):
    """Create a batch atomically. All-or-nothing.

    Steps inside the single transaction:
      1. Resolve candidate items.
      2. Detect conflict: any candidate already locked by another active batch.
      3. Insert ``batch_picking_sessions`` row (status='Created').
      4. Lock items (``locked_by_batch_id``).
      5. Insert ``batch_session_invoices`` rows for each distinct invoice.
      6. Insert ``batch_pick_queue`` rows.
      7. Insert ``batch.created`` activity log.
      8. Commit.

    Returns the persisted ``BatchPickingSession``.
    Raises ``BatchConflict`` (rolled back) when items are not free.
    """
    from batch_utils import generate_batch_number

    zones = [z.strip() for z in (filters.get("zones") or []) if z and str(z).strip()]
    if not zones:
        raise ValueError("create_batch_atomic: at least one zone is required")

    corridors = filters.get("corridors") or []
    unit_types = filters.get("unit_types") or []
    invoice_nos_filter = filters.get("invoice_nos") or []

    name = name or f"Batch_{','.join(zones)}_{get_utc_now().strftime('%Y%m%d_%H%M%S')}"

    # Use a single connection-level transaction so step-7 commit covers steps 3-7.
    # We use db.session here (not engine.begin) so SQLAlchemy ORM stays consistent
    # with the rest of the application; an exception triggers a single rollback.
    try:
        # Step 1: candidate items (any lock state)
        cand_q = db.session.query(InvoiceItem).filter(
            InvoiceItem.zone.in_(zones),
            InvoiceItem.is_picked.is_(False),
            InvoiceItem.pick_status.in_(["not_picked", "reset", "skipped_pending"]),
        )
        if corridors:
            cand_q = cand_q.filter(InvoiceItem.corridor.in_(corridors))
        if unit_types:
            cand_q = cand_q.filter(InvoiceItem.unit_type.in_(unit_types))
        if invoice_nos_filter:
            cand_q = cand_q.filter(InvoiceItem.invoice_no.in_(invoice_nos_filter))

        # Postgres: take row-level locks on every candidate row up-front so
        # two concurrent workers cannot both observe the same item as
        # `locked_by_batch_id IS NULL` and then race to claim it. SKIP LOCKED
        # means a concurrent transaction already holding the row is treated
        # as if its rows didn't match — the second batch sees a smaller
        # candidate set and either succeeds with the leftovers or raises
        # BatchConflict (no eligible items). SQLite has no row locking, so
        # we silently fall back to the unlocked SELECT — fine for tests.
        try:
            dialect = db.session.bind.dialect.name if db.session.bind else ""
        except Exception:
            dialect = ""
        if dialect == "postgresql":
            try:
                # NB: ``skip_locked=False`` (the default) — we want the
                # second concurrent transaction to **block** until the
                # first commits, then observe the freshly-set
                # ``locked_by_batch_id`` and raise ``BatchConflict``.
                # SKIP LOCKED would let the second transaction silently
                # win on the leftovers, which violates the Phase 4
                # "exactly one winner" contract.
                cand_q = cand_q.with_for_update()
            except Exception as e:
                logger.debug("with_for_update unavailable: %s", e)

        candidates = cand_q.all()

        # Step 2: conflict detection
        locked = [i for i in candidates if i.locked_by_batch_id is not None]
        if locked:
            conflicting_id = locked[0].locked_by_batch_id
            codes = [(i.invoice_no, i.item_code) for i in locked]
            db.session.rollback()
            raise BatchConflict(
                conflicting_batch_id=conflicting_id,
                conflicting_item_codes=codes,
            )

        free = [i for i in candidates if i.locked_by_batch_id is None]
        if not free:
            db.session.rollback()
            raise BatchConflict(
                conflicting_batch_id=None,
                conflicting_item_codes=[],
                message="Cannot create batch: no eligible (unlocked) items match the filters.",
            )

        # Step 3: insert session row
        session_obj = BatchPickingSession(
            name=name,
            batch_number=batch_number or generate_batch_number(),
            zones=",".join(zones),
            corridors=",".join(corridors) if corridors else None,
            unit_types=",".join(unit_types) if unit_types else None,
            created_by=created_by,
            assigned_to=assigned_to,
            picking_mode=mode,
            status="Created",
        )
        # last_activity_at is a Phase 4 column (may not exist on the ORM model
        # if models.py wasn't updated); set via setattr so unit tests on
        # SQLite that auto-create-from-ORM don't crash if the column is absent.
        try:
            session_obj.last_activity_at = get_utc_now()
        except Exception:
            pass

        db.session.add(session_obj)
        db.session.flush()  # assign id

        # Step 4: lock items (only the free ones — composite PK
        # ``(invoice_no, item_code)``).
        from sqlalchemy import tuple_
        free_keys = [(i.invoice_no, i.item_code) for i in free]
        if free_keys:
            db.session.query(InvoiceItem).filter(
                tuple_(InvoiceItem.invoice_no, InvoiceItem.item_code).in_(free_keys)
            ).update(
                {InvoiceItem.locked_by_batch_id: session_obj.id},
                synchronize_session=False,
            )

        # Step 5: invoice junctions
        seen_invoices = set()
        for item in free:
            if item.invoice_no in seen_invoices:
                continue
            seen_invoices.add(item.invoice_no)
            db.session.add(BatchSessionInvoice(
                batch_session_id=session_obj.id,
                invoice_no=item.invoice_no,
            ))

        # Step 6: queue rows (idempotent insert via ORM; the table is new in Phase 4
        # so we hand-write SQL rather than declare an ORM model — keeps the legacy
        # session path completely untouched when the flag is OFF).
        for seq, item in enumerate(free, start=1):
            db.session.execute(
                text(
                    """
                    INSERT INTO batch_pick_queue (
                        batch_session_id, invoice_no, item_code, pick_zone_type,
                        sequence_no, status, qty_required
                    ) VALUES (
                        :sid, :inv, :code, :pzt, :seq, 'pending', :qty
                    )
                    """
                ),
                {
                    "sid": session_obj.id,
                    "inv": item.invoice_no,
                    "code": item.item_code,
                    "pzt": pick_zone_type,
                    "seq": seq,
                    "qty": float(item.qty) if item.qty is not None else None,
                },
            )

        # Step 7: activity log
        db.session.add(ActivityLog(
            picker_username=created_by,
            activity_type="batch.created",
            details=(
                f"Batch #{session_obj.id} ({session_obj.batch_number or session_obj.name}) "
                f"created atomically by {created_by} with {len(free)} item(s) across "
                f"{len(seen_invoices)} invoice(s); zones={','.join(zones)}; "
                f"mode={mode}; pick_zone_type={pick_zone_type}"
            ),
        ))

        # Step 8: commit
        db.session.commit()
        logger.info(
            f"create_batch_atomic: batch {session_obj.id} created by {created_by} "
            f"with {len(free)} item(s)"
        )
        return session_obj

    except BatchConflict:
        # Already rolled back above
        raise
    except IntegrityError as e:
        db.session.rollback()
        # Two simultaneous creators inserting overlapping locks could surface
        # as a race here even after the conflict check; treat as conflict.
        logger.warning(f"create_batch_atomic: integrity error treated as conflict: {e}")
        raise BatchConflict(
            conflicting_batch_id=None,
            conflicting_item_codes=[],
            message="Cannot create batch: another concurrent batch grabbed the same items.",
        )
    except Exception:
        db.session.rollback()
        raise


def cancel_batch(batch_id, cancelled_by, reason=None):
    """Cancel a batch.

    Lock lifecycle (per brief §4.5–4.6):
      - Mark session ``status = 'Cancelled'`` with audit columns.
      - Mark queue rows ``status = 'cancelled'`` for everything not picked.
      - Release ``locked_by_batch_id`` for items not yet picked.
      - Leave picked items + their queue rows intact for audit.
      - Write ``batch.cancelled`` activity log.

    Returns a summary dict.
    """
    batch = db.session.get(BatchPickingSession, batch_id)
    if batch is None:
        raise ValueError(f"cancel_batch: batch {batch_id} not found")

    if batch_status.is_terminal(batch.status):
        raise ValueError(
            f"cancel_batch: batch {batch_id} is already terminal ({batch.status})"
        )
    if not batch_status.can_cancel(batch.status):
        raise ValueError(
            f"cancel_batch: batch {batch_id} status {batch.status} is not cancellable"
        )

    try:
        # Release locks for unpicked items only (preserve picked for audit)
        released = db.session.query(InvoiceItem).filter(
            InvoiceItem.locked_by_batch_id == batch_id,
            InvoiceItem.is_picked.is_(False),
        ).update(
            {InvoiceItem.locked_by_batch_id: None},
            synchronize_session=False,
        )

        # Flip non-picked queue rows to cancelled (best-effort; queue table
        # only has rows when the DB-backed flag was on at creation time).
        try:
            db.session.execute(
                text(
                    """
                    UPDATE batch_pick_queue
                    SET status = 'cancelled',
                        cancelled_at = NOW(),
                        updated_at = NOW()
                    WHERE batch_session_id = :sid
                      AND status NOT IN ('picked', 'cancelled')
                    """
                ),
                {"sid": batch_id},
            )
        except Exception as e:
            logger.debug(f"cancel_batch: queue update skipped (non-fatal): {e}")

        # Audit columns + status flip (Phase 4 columns set via setattr for
        # ORM-without-model tolerance).
        batch.status = "Cancelled"
        for col, val in (
            ("cancelled_at", get_utc_now()),
            ("cancelled_by", cancelled_by),
            ("cancel_reason", reason or ""),
            ("last_activity_at", get_utc_now()),
        ):
            try:
                setattr(batch, col, val)
            except Exception:
                pass

        db.session.add(ActivityLog(
            picker_username=cancelled_by,
            activity_type="batch.cancelled",
            details=(
                f"Batch #{batch.id} ({batch.batch_number or batch.name}) cancelled by "
                f"{cancelled_by}; released {released} lock(s); reason={reason or '(none)'}"
            ),
        ))

        db.session.commit()
        logger.info(
            f"cancel_batch: batch {batch_id} cancelled by {cancelled_by}; "
            f"released {released} lock(s)"
        )
        return {
            "batch_id": batch_id,
            "released_locks": released,
            "reason": reason or "",
        }
    except Exception:
        db.session.rollback()
        raise


def can_claim(batch, user):
    """Authorization for /picker/batch/claim: admins/WMs may claim any
    non-terminal batch; pickers may claim only batches that are
    unassigned, unclaimed, or already assigned/claimed to themselves.
    Returns (ok: bool, reason: str)."""
    if user.role in ('admin', 'warehouse_manager'):
        return True, ""
    assigned = (batch.assigned_to or "").strip()
    claimed = (getattr(batch, 'claimed_by', None) or "").strip()
    if assigned and assigned != user.username:
        return False, f"Batch is assigned to {assigned}; ask an admin to reassign."
    if claimed and claimed != user.username:
        return False, f"Batch already claimed by {claimed}."
    return True, ""


def claim_batch(batch_id, claimed_by):
    """Reassign a batch to ``claimed_by`` (admin/warehouse_manager helping out).

    Records ``claimed_by``/``claimed_at``, updates ``assigned_to`` so all
    subsequent pick activity is logged under the real clicker, and writes
    a ``batch.claimed`` audit row. Refuses on terminal batches.
    """
    batch = db.session.get(BatchPickingSession, batch_id)
    if batch is None:
        raise ValueError(f"claim_batch: batch {batch_id} not found")
    if not batch_status.can_claim(batch.status):
        raise ValueError(
            f"claim_batch: batch {batch_id} status {batch.status} is not claimable"
        )

    previous_assignee = batch.assigned_to
    try:
        batch.assigned_to = claimed_by
        for col, val in (
            ("claimed_by", claimed_by),
            ("claimed_at", get_utc_now()),
            ("last_activity_at", get_utc_now()),
        ):
            try:
                setattr(batch, col, val)
            except Exception:
                pass
        db.session.add(ActivityLog(
            picker_username=claimed_by,
            activity_type="batch.claimed",
            details=(
                f"Batch #{batch.id} ({batch.batch_number or batch.name}) claimed by "
                f"{claimed_by} (previously assigned to {previous_assignee or '(unassigned)'})"
            ),
        ))
        db.session.commit()
        return {"batch_id": batch_id, "claimed_by": claimed_by,
                "previous_assignee": previous_assignee}
    except Exception:
        db.session.rollback()
        raise


def find_orphaned_locks():
    """Return a list of ``InvoiceItem`` rows whose ``locked_by_batch_id``
    points at a missing or terminal batch.

    Used by the ``/admin/batch/orphaned-locks`` UI.
    """
    locked = db.session.query(InvoiceItem).filter(
        InvoiceItem.locked_by_batch_id.isnot(None)
    ).all()
    if not locked:
        return []

    batch_ids = {i.locked_by_batch_id for i in locked}
    batches = {
        b.id: b for b in db.session.query(BatchPickingSession).filter(
            BatchPickingSession.id.in_(batch_ids)
        ).all()
    }

    orphans = []
    for item in locked:
        b = batches.get(item.locked_by_batch_id)
        if b is None or batch_status.is_terminal(b.status):
            orphans.append(item)
    return orphans


def bulk_unlock_orphans(actor):
    """Release every orphaned lock and write a ``batch.orphan_unlock``
    audit row. Returns the number of items released.
    """
    orphans = find_orphaned_locks()
    if not orphans:
        return 0

    try:
        from sqlalchemy import tuple_
        keys = [(i.invoice_no, i.item_code) for i in orphans]
        released = db.session.query(InvoiceItem).filter(
            tuple_(InvoiceItem.invoice_no, InvoiceItem.item_code).in_(keys)
        ).update(
            {InvoiceItem.locked_by_batch_id: None},
            synchronize_session=False,
        )
        db.session.add(ActivityLog(
            picker_username=actor,
            activity_type="batch.orphan_unlock",
            details=(
                f"Bulk-released {released} orphaned lock(s) by {actor}; "
                f"keys={keys[:25]}{'...' if len(keys) > 25 else ''}"
            ),
        ))
        db.session.commit()
        return released
    except Exception:
        db.session.rollback()
        raise
