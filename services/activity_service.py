"""PostgreSQL data access for the gapless activity timeline.

The database owns the timeline invariants.  This module intentionally only
calls the stored procedures created by the activity-tracking migration.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Iterator, Optional, Sequence


PICKER_STATES = (
    "picking", "break", "restock", "assist", "repacking",
    "awaiting_order", "unassigned", "offline",
)
DECLARABLE_STATES = ("picking", "break", "restock", "assist", "repacking")
SUPERVISOR_ROLES = frozenset({"admin", "warehouse_manager"})


class ActivityError(Exception):
    status = 500
    code = "ACTIVITY_ERROR"


class TrackingDisabled(ActivityError):
    status = 403
    code = "TRACKING_DISABLED"


class StaleSegment(ActivityError):
    status = 409
    code = "STALE_SEGMENT"


class InvalidState(ActivityError):
    status = 400
    code = "INVALID_STATE"


class ShiftNotOwned(ActivityError):
    status = 403
    code = "SHIFT_NOT_OWNED"


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
        def iso(value: Any) -> Any:
            return value.isoformat() if hasattr(value, "isoformat") else value

        return {
            "shift_id": self.shift_id,
            "picker_username": self.picker_username,
            "check_in_time": iso(self.check_in_time),
            "open_segment_id": self.open_segment_id,
            "open_state": self.open_state,
            "open_started_at": iso(self.open_started_at),
        }


class ActivityService:
    """Thin, DB-API based adapter around the activity tracking SQL functions."""

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
    _UNRESOLVED_FOR_SHIFT = """
        SELECT id, started_at, ended_at, ROUND(duration_sec / 60.0, 1) AS minutes,
               resolution
          FROM picker_segment
         WHERE shift_id = %s AND resolution IN ('pending', 'declined')
         ORDER BY started_at"""

    def __init__(
        self,
        connection_factory: Callable[[], ContextManager[Any]],
        cursor_factory: Optional[Callable[..., Any]] = None,
    ):
        self._connection = connection_factory
        self._cursor_factory = cursor_factory

    @contextmanager
    def _cursor(self, commit: bool = False) -> Iterator[Any]:
        with self._connection() as conn:
            cur = (
                conn.cursor(cursor_factory=self._cursor_factory)
                if self._cursor_factory
                else conn.cursor()
            )
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
            return cur.fetchone() if cur.description else None

    @staticmethod
    def _is_stale(exc: Exception) -> bool:
        return getattr(exc, "pgcode", None) == "40001" or "stale_segment" in str(exc)

    @staticmethod
    def _require_declarable(state: Any) -> str:
        if state not in DECLARABLE_STATES:
            raise InvalidState(
                "state must be one of: " + ", ".join(DECLARABLE_STATES)
            )
        return state

    def schema_available(self) -> bool:
        row = self._one(
            "SELECT to_regprocedure('picker_tracking_enabled(character varying)') IS NOT NULL AS ok"
        )
        return bool(row and row["ok"])

    def tracking_enabled(self, username: str) -> bool:
        row = self._one("SELECT picker_tracking_enabled(%s) AS ok", (username,))
        return bool(row and row["ok"])

    def _require_enabled(self, username: str) -> None:
        if not self.tracking_enabled(username):
            raise TrackingDisabled("Activity tracking is not enabled for this user")

    def _require_shift_owner(self, shift_id: int, username: str) -> None:
        row = self._one(
            "SELECT 1 AS ok FROM shifts WHERE id = %s AND picker_username = %s",
            (shift_id, username),
        )
        if not row:
            raise ShiftNotOwned("This shift does not belong to the current user")

    def _require_segment_owner(self, segment_id: int, username: str) -> None:
        row = self._one(
            "SELECT 1 AS ok FROM picker_segment WHERE id = %s AND picker_username = %s",
            (segment_id, username),
        )
        if not row:
            raise ShiftNotOwned("This timeline segment does not belong to the current user")

    def check_in(self, username: str, coordinates: Optional[str] = None) -> OpenShift:
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
                       VALUES (%s, (now() AT TIME ZONE 'utc'), %s, 'active')
                       RETURNING id""",
                    (username, coordinates),
                )
                shift_id = cur.fetchone()["id"]

            cur.execute("SELECT picker_shift_ensure_open(%s)", (shift_id,))
            cur.execute(self._OPEN_SHIFT_BY_ID, (shift_id,))
            return OpenShift.from_row(cur.fetchone())  # type: ignore[return-value]

    def check_out(
        self, shift_id: int, username: str, coordinates: Optional[str] = None
    ) -> dict:
        # A user must always be able to close their own already-open timeline.
        # This remains true if an administrator has just disabled the master
        # switch as part of a rollback.
        self._require_shift_owner(shift_id, username)
        with self._cursor(commit=True) as cur:
            cur.execute(
                "SELECT picker_shift_close(%s, (now() AT TIME ZONE 'utc'), 'check_out')",
                (shift_id,),
            )
            if coordinates:
                cur.execute(
                    "UPDATE shifts SET check_out_coordinates = %s WHERE id = %s",
                    (coordinates, shift_id),
                )
            cur.execute(self._UNRESOLVED_FOR_SHIFT, (shift_id,))
            unresolved = list(cur.fetchall())
            if unresolved:
                cur.execute(
                    "UPDATE shifts SET status = 'pending_review' WHERE id = %s",
                    (shift_id,),
                )
            return {
                "shift_id": shift_id,
                "unresolved": unresolved,
                "shift_status": "pending_review" if unresolved else "completed",
            }

    def current_shift(self, username: str) -> Optional[OpenShift]:
        return OpenShift.from_row(self._one(self._OPEN_SHIFT_BY_USER, (username,)))

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
        self._require_enabled(username)
        self._require_shift_owner(shift_id, username)
        try:
            row = self._exec(
                """SELECT picker_transition(
                       %s, %s::picker_state, (now() AT TIME ZONE 'utc'),
                       %s, %s, %s::uuid, %s
                   ) AS id""",
                (
                    shift_id,
                    new_state,
                    opened_by,
                    close_reason,
                    action_id,
                    expected_open_segment_id,
                ),
            )
        except Exception as exc:
            if self._is_stale(exc):
                raise StaleSegment("Segment already closed — re-sync") from exc
            raise
        return row["id"]  # type: ignore[index]

    def declare(
        self,
        shift_id: int,
        username: str,
        state: str,
        action_id: str,
        expected_open_segment_id: Optional[int] = None,
    ) -> int:
        return self.transition(
            shift_id,
            username,
            self._require_declarable(state),
            action_id,
            expected_open_segment_id=expected_open_segment_id,
        )

    def packing_complete(self, shift_id: int, username: str, action_id: str) -> int:
        return self.transition(
            shift_id,
            username,
            "unassigned",
            action_id,
            opened_by="system",
            close_reason="packing_complete",
        )

    def awaiting_order(self, shift_id: int, username: str, action_id: str) -> int:
        return self.transition(
            shift_id, username, "awaiting_order", action_id, opened_by="system"
        )

    def heartbeat(self, shift_id: int, username: str) -> None:
        self._require_enabled(username)
        self._require_shift_owner(shift_id, username)
        self._exec(
            """UPDATE shifts SET last_heartbeat_at = (now() AT TIME ZONE 'utc')
                 WHERE id = %s""",
            (shift_id,),
        )

    def unresolved_for_shift(self, shift_id: int, username: str) -> list[dict]:
        self._require_shift_owner(shift_id, username)
        return self._all(self._UNRESOLVED_FOR_SHIFT, (shift_id,))

    def classify(self, segment_id: int, state: str, username: str) -> None:
        self._require_segment_owner(segment_id, username)
        self._exec(
            "SELECT picker_classify_segment(%s, %s::picker_state, %s)",
            (segment_id, self._require_declarable(state), username),
        )

    def decline(self, segment_id: int, username: str) -> None:
        self._require_segment_owner(segment_id, username)
        self._exec("SELECT picker_decline_segment(%s, %s)", (segment_id, username))

    def supervisor_resolve(
        self, segment_id: int, state: Optional[str], username: str, note: Optional[str]
    ) -> None:
        if state is not None:
            self._require_declarable(state)
        self._exec(
            "SELECT picker_supervisor_resolve(%s, %s::picker_state, %s, %s)",
            (segment_id, state, username, note),
        )

    def review_queue(self) -> list[dict]:
        return self._all("SELECT * FROM vw_supervisor_review_queue")

    def closure_blockers(self) -> list[dict]:
        return self._all("SELECT * FROM vw_shift_closure_blockers")

    def roster(self) -> list[dict]:
        return self._all("SELECT * FROM vw_tracking_roster")

    def set_tracking(self, username: str, enabled: bool, changed_by: str) -> None:
        self._exec(
            """UPDATE users SET track_activity = %s, track_activity_set_by = %s
                 WHERE username = %s""",
            (enabled, changed_by, username),
        )

    def master_switch(self, enabled: bool) -> None:
        with self._cursor(commit=True) as cur:
            cur.execute(
                "UPDATE settings SET value = %s WHERE key = 'activity_mode.enabled'",
                ("true" if enabled else "false",),
            )
            if not enabled:
                # Disabling is a safe rollback, not an opportunity to leave
                # dangling segments.  Only users opted into this feature are
                # closed; legacy shifts remain untouched.
                cur.execute(
                    """SELECT s.id
                         FROM shifts s
                         JOIN users u ON u.username = s.picker_username
                        WHERE s.check_out_time IS NULL AND u.track_activity"""
                )
                for row in cur.fetchall():
                    cur.execute(
                        """SELECT picker_shift_close(
                               %s, (now() AT TIME ZONE 'utc'), 'admin'
                           )""",
                        (row["id"],),
                    )

    def master_switch_state(self) -> bool:
        row = self._one(
            "SELECT value FROM settings WHERE key = 'activity_mode.enabled'"
        )
        return bool(row and str(row["value"]).lower() == "true")

    def integrity_check(self) -> dict:
        timeline = self._all("SELECT * FROM vw_shift_timeline_integrity")
        reconciliation = self._all("SELECT * FROM vw_accounting_reconciliation")
        return {
            "healthy": not timeline and not reconciliation,
            "timeline_violations": timeline,
            "reconciliation_breaks": reconciliation,
        }

    def reap_stale_shifts(self) -> list[dict]:
        with self._cursor(commit=True) as cur:
            cur.execute("SELECT * FROM picker_reap_stale_shifts()")
            return list(cur.fetchall())

    def auto_write_off(self) -> int:
        row = self._exec("SELECT picker_auto_writeoff_stale() AS n")
        return int(row["n"])  # type: ignore[index]