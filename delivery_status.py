# delivery_status.py
"""
Canonical delivery status helper for consistent status handling across the system.
All invoice/RSI statuses should be lowercase and normalized using these utilities.
"""
from typing import Optional

# Map uppercase/legacy statuses to canonical lowercase values
STATUS_MAP = {
    "OUT_FOR_DELIVERY": "out_for_delivery",
    "DELIVERED": "delivered",
    "FAILED": "delivery_failed",
    "DELIVERY_FAILED": "delivery_failed",
    "RETURNED": "returned_to_warehouse",
    "RETURNED_TO_WAREHOUSE": "returned_to_warehouse",
    "SHIPPED": "shipped",
    "READY_FOR_DISPATCH": "ready_for_dispatch",
    "ASSIGNED": "ready_for_dispatch",  # Legacy status, maps to ready_for_dispatch
    # Retired warehouse status names (pre-canonical era). Old production rows
    # may still carry these; map them to their canonical equivalents.
    "IN PROGRESS": "picking",
    "IN_PROGRESS": "picking",
    "COMPLETED": "ready_for_dispatch",
    "READY FOR PACKING": "awaiting_packing",
    "READY_FOR_PACKING": "awaiting_packing",
}

# Legacy spellings as they appear verbatim in old rows, keyed by the canonical
# status they map to. Used to expand SQL IN-filters so legacy rows are not
# silently hidden from dashboards.
LEGACY_INVOICE_STATUS_ALIASES = {
    "picking": ["In Progress"],
    "ready_for_dispatch": ["Completed", "Assigned"],
    "awaiting_packing": ["Ready for Packing"],
}


def expand_legacy_aliases(statuses):
    """Return the given canonical status list plus any retired spellings that
    normalize to one of them, for use in SQL IN-filters.

    Case variants (Title Case, UPPERCASE, lowercase, and underscore forms)
    are included because SQL equality is case-sensitive on PostgreSQL while
    normalize_status() accepts any casing."""
    expanded = list(statuses)
    seen = set(expanded)
    for canonical in statuses:
        for alias in LEGACY_INVOICE_STATUS_ALIASES.get(canonical, []):
            for variant in (
                alias,
                alias.upper(),
                alias.lower(),
                alias.replace(' ', '_'),
                alias.upper().replace(' ', '_'),
                alias.lower().replace(' ', '_'),
            ):
                if variant not in seen:
                    seen.add(variant)
                    expanded.append(variant)
    return expanded


def heal_legacy_invoice_statuses(invoices):
    """Rewrite any retired status spellings (e.g. 'In Progress', 'Completed')
    on the given Invoice rows to their canonical lowercase equivalents so old
    production rows stop taking legacy branches. Commits only when a row was
    actually changed. Safe to call with an empty list."""
    from app import app, db  # lazy import to avoid circular imports
    from timezone_utils import utc_now_for_db
    healed = False
    for inv in invoices:
        canonical = normalize_status(inv.status)
        if canonical and canonical != inv.status:
            app.logger.info(
                f"Healing legacy invoice status on {inv.invoice_no}: "
                f"'{inv.status}' -> '{canonical}'"
            )
            inv.status = canonical
            inv.status_updated_at = utc_now_for_db()
            healed = True
    if healed:
        try:
            db.session.commit()
        except Exception as _heal_err:
            db.session.rollback()
            app.logger.warning(f"Legacy status heal commit failed: {_heal_err}")

# Terminal statuses that mean delivery is complete (one way or another)
TERMINAL_DELIVERY_STATUSES = {"delivered", "delivery_failed", "returned_to_warehouse"}

# All valid delivery statuses in order of progression
VALID_DELIVERY_STATUSES = [
    "not_started",
    "picking",
    "awaiting_batch_items",
    "awaiting_packing",
    "ready_for_dispatch",
    "shipped",
    "out_for_delivery",
    "delivered",
    "delivery_failed",
    "returned_to_warehouse",
]


def normalize_status(v: Optional[str]) -> Optional[str]:
    """
    Normalize a status string to its canonical lowercase form.
    
    Args:
        v: The status value to normalize (can be None)
        
    Returns:
        The normalized lowercase status, or None if input was None/empty
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    
    # Check exact match in map first
    if s in STATUS_MAP:
        return STATUS_MAP[s]
    
    # Check uppercase version
    up = s.upper()
    if up in STATUS_MAP:
        return STATUS_MAP[up]
    
    # Handle special case for "returned"
    low = s.lower()
    if low == "returned":
        return "returned_to_warehouse"
    
    # Default to lowercase version
    return low


def is_terminal_status(status: Optional[str]) -> bool:
    """
    Check if a status is a terminal delivery status.
    
    Args:
        status: The status to check
        
    Returns:
        True if the status is terminal (delivered, failed, or returned)
    """
    normalized = normalize_status(status)
    return normalized in TERMINAL_DELIVERY_STATUSES


def is_delivered(status: Optional[str]) -> bool:
    """Check if status represents successful delivery."""
    return normalize_status(status) == "delivered"


def is_failed(status: Optional[str]) -> bool:
    """Check if status represents failed delivery."""
    return normalize_status(status) == "delivery_failed"


def is_returned(status: Optional[str]) -> bool:
    """Check if status represents returned to warehouse."""
    return normalize_status(status) == "returned_to_warehouse"
