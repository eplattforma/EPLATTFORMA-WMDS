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
}

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
