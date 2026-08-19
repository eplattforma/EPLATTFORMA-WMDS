"""
activity_service.py — data layer for the gapless picker timeline (Flask/PostgreSQL)

Requires migrations 010 + 011.

DRIVER-AGNOSTIC BY DESIGN
-------------------------
This class takes a `connection_factory`: a context manager yielding a DB-API
connection whose cursors return dict-like rows. Pick the adapter matching your
app (see ADAPTERS at the bottom of this file) and pass it in. Nothing else in
this module cares which driver you use.

WHY THIS FILE IS THIN
---------------------
Every invariant — no gaps, no overlaps, idempotency, the grace window, the
resolution machine — lives in PostgreSQL functions, not here. That is deliberate:
a Python bug cannot create a timeline gap, because Python is never trusted to
compute segment boundaries. Each method below is a single SQL call.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Iterator, Optional, Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Every state the timeline can hold.
PICKER_STATES = (
    "picking", "break", "restock", "assist", "repacking",
    "awaiting_order", "unassigned", "offline",
)

#: States a picker may CHOOSE. Deliberately excludes 'unassigned', 'offline' and
#: 'awaiting_order' — those are system-assigned. Validate client input against
#: this, never against PICKER_STATES, or a client could mark itself offline.
DECLARABLE_STATES = ("picking", "break", "restock", "assist", "repacking")

SUPERVISOR_ROLES = frozenset({"admin", "warehouse_manager"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ActivityError(Exception):
    """Base. Carries an HTTP status so the blueprint can map it directly."""
    status = 500
    code = "ACTIVITY_ERROR"


class TrackingDisabled(ActivityError):
    status = 403
    code = "TRACKING_DISABLED"


class StaleSegment(ActivityError):
    """The client acted on a segment the server already closed (usually the
    reaper got there first after a device went quiet). The client must re-sync
    via current_shift() rather than retry."""
    status = 409
    code = "STALE_SEGMENT"


class InvalidState(ActivityError):
    status = 400
    code = "INVALID_STATE"


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

@dataclass
class OpenShift:
    shift_id: int
    picker_username: str
    check_in_time: Any
    open_segment_id: Optional[int]
    open_state: Optional[str]
    open_started_at: Optional[Any]

    @classmethod
    def from_row(cls, row: Optional[dict]) -> Optional["OpenShift"]:
        if not row:
            return None
        return cls(
            shift_id=row["shift_id"],
            picker_username=row["picker_username"],
            check_in_time=row["check_in_time"],
            open_segment_id=row["open_segment_id"],
            open_state=row["open_state"],
            open_started_at=row["open_started_at"],
        )

    def to_dict(self) -> dict:
        def iso(v):
            return v.isoformat() if hasattr(v, "isoformat") else v
        return {
            "shift_id": self.shift_id,
            "picker_username": self.picker_username,
            "check_in_time": iso(self.check_in_time),
            "open_segment_id": self.open_segment_id,
            "open_state": self.open_state,
            "open_started_at": iso(self.open_started_at),
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ActivityService:
    def __init__(self, connection_factory: Callable[[], ContextManager[Any]]):
        self._connection = connection_factory

    # -- plumbing ----------------------------------------------------------

    @contextmanager
    def _cursor(self, commit: bool = False) -> Iterator[Any]:
        with self._connection() as conn:
            cur = conn.cursor()
            try:
                yield cur
                if commit:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    def _one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        with self._cursor(commit=True) as cur:
            cur.execute(sql, params)
            if cur.description:
                return cur.fetchone()
            return None

    @staticmethod
    def _is_stale(exc: Exception) -> bool:
        # picker_transition raises SQLSTATE 55000 (serialization_failure) on a
        # stale expected_open_segment_id.
        return getattr(exc, "pgcode", None) == "55000" or "stale_segment" in str(exc)

    # -- the gate ----------------------------------------------------------

    def tracking_enabled(self, username: str) -> bool:
        """THE gate. Never check `role` for tracking decisions anywhere in the
        app — call this. Role does not predict who is tracked: 9 users record
        shifts across 3 roles, only 5 of them are role='picker'."""
        row = self._one("SELECT picker_tracking_enabled(%s) AS ok", (username,))
        return bool(row and row["ok"])

    def _require_enabled(self, username: str) -> None:
        if not self.tracking_enabled(username):
            raise TrackingDisabled(f"Activity tracking is not enabled for {username}")

    @staticmethod
    def _require_declarable(state: Any) -> str:
        if state not in DECLARABLE_STATES:
            raise InvalidState(
                "state must be one of: " + ", ".join(DECLARABLE_STATES))
        return state

    # -- shift lifecycle ---------------------------------------------------

    def check_in(self, username: str, coordinates: Optional[str] = None) -> OpenShift:
        """Creates the shift AND opens the first segment in one transaction, so
        the timeline is covered from check_in_time with no window in which time
        is unowned. Re-calling while a shift is open is a no-op (idempotent).

        The client must immediately prompt for an activity; anything chosen
        within the grace window relabels this segment back to check-in."""
        self._require_enabled(username)
        with self._cursor(commit=True) as cur:
            cur.execute(
                """SELECT id FROM shifts
                    WHERE picker_username = %s AND check_out_time IS NULL
                    ORDER BY check_in_time DESC LIMIT 1""",
                (username,),
            )
            existing = cur.fetchone()
            if existing:
                shift_id = existing["id"]
            else:
                cur.execute(
                    """INSERT INTO shifts
                         (picker_username, check_in_time, check_in_coordinates, status)
                       VALUES (%s, (now() AT TIME ZONE 'utc'), %s, 'active') RETURNING id""",
                    (username, coordinates),
                )
                shift_id = cur.fetchone()["id"]

            # Idempotent, and required rather than optional: a shift that already
            # existed before this feature shipped has no segments, so its first
            # segment must start at check_in_time, not (now() AT TIME ZONE 'utc'). ensure_open handles
            # new shifts, pre-migration shifts, and resumed timelines alike.
            cur.execute("SELECT picker_shift_ensure_open(%s)", (shift_id,))

            cur.execute(self._OPEN_SHIFT_BY_ID, (shift_id,))
            return OpenShift.from_row(cur.fetchone())  # type: ignore[return-value]

    def check_out(self, shift_id: int, coordinates: Optional[str] = None) -> dict:
        """Closes the open segment at check_out_time — nothing is left dangling.

        Returns blocks still needing a label. The picker leaves regardless; it is
        the RECORD that stays open (shift status -> 'pending_review')."""
        with self._cursor(commit=True) as cur:
            cur.execute(
                "SELECT picker_shift_close(%s, (now() AT TIME ZONE 'utc'), 'check_out')",
                (shift_id,),
            )
            if coordinates:
                cur.execute(
                    "UPDATE shifts SET check_out_coordinates=%s WHERE id=%s",
                    (coordinates, shift_id),
                )
            cur.execute(self._UNRESOLVED_FOR_SHIFT, (shift_id,))
            unresolved = list(cur.fetchall())
            if unresolved:
                cur.execute(
                    "UPDATE shifts SET status='pending_review' WHERE id=%s", (shift_id,))
            return {
                "shift_id": shift_id,
                "unresolved": unresolved,
                "shift_status": "pending_review" if unresolved else "completed",
            }

    _OPEN_SHIFT_SELECT = """
        SELECT s.id AS shift_id, s.picker_username, s.check_in_time,
               g.id AS open_segment_id, g.state AS open_state,
               g.started_at AS open_started_at
          FROM shifts s
          LEFT JOIN picker_segment g ON g.shift_id = s.id AND g.ended_at IS NULL
    """
    _OPEN_SHIFT_BY_ID = _OPEN_SHIFT_SELECT + " WHERE s.id = %s"
    _OPEN_SHIFT_BY_USER = _OPEN_SHIFT_SELECT + """
         WHERE s.picker_username = %s AND s.check_out_time IS NULL
         ORDER BY s.check_in_time DESC LIMIT 1"""

    def current_shift(self, username: str) -> Optional[OpenShift]:
        """Client calls this on load, and after any 409, to re-sync."""
        return OpenShift.from_row(self._one(self._OPEN_SHIFT_BY_USER, (username,)))

    # -- transitions -------------------------------------------------------

    def transition(
        self,
        shift_id: int,
        username: str,
        new_state: str,
        action_id: str,
        opened_by: str = "picker",
        close_reason: str = "declared",
        expected_open_segment_id: Optional[int] = None,
    ) -> int:
        """The ONLY way to change state. Closes the open segment and opens the
        new one at the same instant, in one transaction — a gap is not
        expressible.

        action_id: client-generated UUID. Replaying it returns the original
          segment and creates nothing. Generate it once per user gesture and
          reuse it on retry; a fresh UUID per retry defeats the protection.
        expected_open_segment_id: optimistic lock -> StaleSegment on mismatch.
        """
        self._require_enabled(username)
        try:
            row = self._exec(
                """SELECT picker_transition(
                       %s, %s::picker_state, (now() AT TIME ZONE 'utc'), %s, %s, %s::uuid, %s
                   ) AS id""",
                (shift_id, new_state, opened_by, close_reason,
                 action_id, expected_open_segment_id),
            )
        except Exception as exc:
            if self._is_stale(exc):
                raise StaleSegment("Segment already closed — re-sync") from exc
            raise
        return row["id"]  # type: ignore[index]

    def declare(self, shift_id: int, username: str, state: str, action_id: str,
                expected_open_segment_id: Optional[int] = None) -> int:
        """Picker-initiated transition. Validates against DECLARABLE_STATES."""
        return self.transition(
            shift_id, username, self._require_declarable(state), action_id,
            opened_by="picker", close_reason="declared",
            expected_open_segment_id=expected_open_segment_id,
        )

    def packing_complete(self, shift_id: int, username: str, action_id: str) -> int:
        """Order finished. Switches to 'unassigned' server-side so no time is
        unowned while the picker decides. The modal then labels it."""
        return self.transition(
            shift_id, username, "unassigned", action_id,
            opened_by="system", close_reason="packing_complete",
        )

    def awaiting_order(self, shift_id: int, username: str, action_id: str) -> int:
        """Picker asked for work and the queue was empty. Attributed to
        Planning — never scored against the picker."""
        return self.transition(
            shift_id, username, "awaiting_order", action_id, opened_by="system",
        )

    def heartbeat(self, shift_id: int) -> None:
        """Every ~30s from the client. Lets the reaper close a dead device's
        segment at last_heartbeat rather than at reaper-run time — the
        difference between recording 15 minutes unknown and 4 hours of phantom
        break."""
        self._exec(
            "UPDATE shifts SET last_heartbeat_at = (now() AT TIME ZONE 'utc') WHERE id = %s",
            (shift_id,),
        )

    # -- resolution --------------------------------------------------------

    _UNRESOLVED_FOR_SHIFT = """
        SELECT id, started_at, ended_at,
               ROUND(duration_sec/60.0, 1) AS minutes, resolution
          FROM picker_segment
         WHERE shift_id = %s AND resolution IN ('pending','declined')
         ORDER BY started_at"""

    def unresolved_for_shift(self, shift_id: int) -> list[dict]:
        return self._all(self._UNRESOLVED_FOR_SHIFT, (shift_id,))

    def classify(self, segment_id: int, state: str, by: str) -> None:
        self._exec(
            "SELECT picker_classify_segment(%s, %s::picker_state, %s)",
            (segment_id, self._require_declarable(state), by),
        )

    def decline(self, segment_id: int, by: str) -> None:
        """Picker cannot recall. Honest — forcing a label produces fiction. It
        ESCALATES rather than closing the block."""
        self._exec("SELECT picker_decline_segment(%s, %s)", (segment_id, by))

    def supervisor_resolve(self, segment_id: int, state: Optional[str],
                           by: str, note: Optional[str] = None) -> None:
        """state=None is an explicit write-off (still counts as unassigned —
        resolved, not erased)."""
        if state is not None:
            self._require_declarable(state)
        self._exec(
            "SELECT picker_supervisor_resolve(%s, %s::picker_state, %s, %s)",
            (segment_id, state, by, note),
        )

    def review_queue(self) -> list[dict]:
        return self._all("SELECT * FROM vw_supervisor_review_queue")

    def closure_blockers(self) -> list[dict]:
        return self._all("SELECT * FROM vw_shift_closure_blockers")

    # -- admin: per-user roster -------------------------------------------

    def roster(self) -> list[dict]:
        return self._all("SELECT * FROM vw_tracking_roster")

    def set_tracking(self, username: str, enabled: bool, admin_username: str) -> None:
        """Turning tracking OFF closes any open shift cleanly (DB trigger).
        Turning it ON takes effect at the next check-in — elapsed time cannot be
        retroactively covered. Every change lands in user_tracking_audit."""
        self._exec(
            """UPDATE users
                  SET track_activity = %s, track_activity_set_by = %s
                WHERE username = %s""",
            (enabled, admin_username, username),
        )

    def master_switch(self, enabled: bool) -> None:
        self._exec(
            "UPDATE settings SET value = %s WHERE key = 'activity_mode.enabled'",
            ("true" if enabled else "false",),
        )

    def master_switch_state(self) -> bool:
        row = self._one(
            "SELECT value FROM settings WHERE key = 'activity_mode.enabled'")
        return bool(row) and str(row["value"]).lower() == "true"

    # -- health & cron -----------------------------------------------------

    def integrity_check(self) -> dict:
        """Wire to monitoring. Both lists should be empty forever; a non-empty
        result means an invariant broke upstream."""
        gaps = self._all("SELECT * FROM vw_shift_timeline_integrity")
        recon = self._all("SELECT * FROM vw_accounting_reconciliation")
        return {
            "healthy": not gaps and not recon,
            "timeline_violations": gaps,
            "reconciliation_breaks": recon,
        }

    def reap_stale_shifts(self) -> list[dict]:
        """Cron: every 5 minutes. Crash / never-checked-out recovery."""
        with self._cursor(commit=True) as cur:
            cur.execute("SELECT * FROM picker_reap_stale_shifts()")
            return list(cur.fetchall())

    def auto_write_off(self) -> int:
        """Cron: daily. 7-day backstop so reporting is never blocked forever."""
        row = self._exec("SELECT picker_auto_writeoff_stale() AS n")
        return row["n"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# ADAPTERS — pick the one matching your app and pass it to ActivityService
# ---------------------------------------------------------------------------
#
# The service needs dict-like rows. Each adapter below configures that.
#
# --- psycopg2 (most common in Flask) ---------------------------------------
#
#   import psycopg2
#   from psycopg2.extras import RealDictCursor
#   from psycopg2.pool import ThreadedConnectionPool
#   from contextlib import contextmanager
#
#   pool = ThreadedConnectionPool(1, 10, DATABASE_URL,
#                                 cursor_factory=RealDictCursor)
#
#   @contextmanager
#   def get_connection():
#       conn = pool.getconn()
#       try:
#           yield conn
#       finally:
#           pool.putconn(conn)
#
#   activity = ActivityService(get_connection)
#
# --- psycopg 3 --------------------------------------------------------------
#
#   from psycopg_pool import ConnectionPool
#   from psycopg.rows import dict_row
#
#   pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row})
#   activity = ActivityService(pool.connection)
#
# --- Flask-SQLAlchemy -------------------------------------------------------
#
#   from contextlib import contextmanager
#
#   @contextmanager
#   def get_connection():
#       conn = db.engine.raw_connection()      # underlying DB-API connection
#       try:
#           yield conn
#       finally:
#           conn.close()
#
#   # Requires the engine be created with dict rows, e.g.
#   #   create_engine(URL, connect_args={"cursor_factory": RealDictCursor})
#
# NOTE ON NEON: use the pooled connection string and keep pool sizes modest;
# Neon closes idle connections, so a long-lived unpooled global connection will
# eventually fail. All methods here are short-lived and pool-friendly.
# ---------------------------------------------------------------------------
