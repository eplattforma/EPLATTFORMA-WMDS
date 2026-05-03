"""Phase 4: Centralised status helpers for ``BatchPickingSession``.

Production status values are stored in **Title Case** (``Created``,
``In Progress``, ``Paused``, ``Completed``) plus the new Phase 4
terminal states (``Cancelled``, ``Archived``). Comparisons here are
case-insensitive so legacy lowercase values like ``picking`` continue to
work.

Helpers replace scattered ``status in (...)`` checks across
``routes_batch.py`` / ``routes_batch_fixed.py`` without changing the
actual stored string values.
"""

# Canonical sets (case-insensitive comparison via _norm() below).
ACTIVE = {"created", "in progress", "picking", "active", "paused"}
TERMINAL = {"completed", "cancelled", "archived"}
EDITABLE = {"created"}
CANCELLABLE = {"created", "in progress", "picking", "active", "paused"}
CLAIMABLE = {"created", "in progress", "picking", "active", "paused"}


def _norm(status):
    if status is None:
        return ""
    return str(status).strip().lower()


def is_active(status):
    """Return True for any non-terminal (in-flight) batch state."""
    return _norm(status) in ACTIVE


def is_terminal(status):
    """Return True if the batch can no longer be worked
    (Completed/Cancelled/Archived)."""
    return _norm(status) in TERMINAL


def can_edit(status):
    """Edits to zones/criteria are only allowed before picking starts."""
    return _norm(status) in EDITABLE


def can_cancel(status):
    """Cancel is allowed for any active batch; terminal batches refuse."""
    return _norm(status) in CANCELLABLE


def can_claim(status):
    """Claimable while in-flight; terminal batches refuse a claim."""
    return _norm(status) in CLAIMABLE
