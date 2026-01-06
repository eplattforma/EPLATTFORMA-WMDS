"""
Order Status Constants for the Warehouse Management System
Defines the standardized 8-status order lifecycle
"""

# Order Status Constants
ORDER_STATUSES = {
    'not_started': {
        'value': 'not_started',
        'label': 'Not Started',
        'description': 'Order created, no action taken yet',
        'color': 'secondary',
        'icon': 'fas fa-clock',
        'sort_order': 1
    },

    'picking': {
        'value': 'picking',
        'label': 'Picking',
        'description': 'Order is currently being picked',
        'color': 'warning',
        'icon': 'fas fa-hand-holding',
        'sort_order': 2
    },
    'awaiting_batch_items': {
        'value': 'awaiting_batch_items',
        'label': 'Awaiting Batch Items',
        'description': 'Remaining items are locked by a batch picking session',
        'color': 'purple',
        'icon': 'fas fa-layer-group',
        'sort_order': 2.5
    },
    'awaiting_packing': {
        'value': 'awaiting_packing',
        'label': 'Awaiting Packing',
        'description': 'Picking complete, awaiting packing confirmation',
        'color': 'warning',
        'icon': 'fas fa-box-open',
        'sort_order': 2.7
    },
    'ready_for_dispatch': {
        'value': 'ready_for_dispatch',
        'label': 'Ready for Dispatch',
        'description': 'Picking complete, packed, and waiting for shipping',
        'color': 'info',
        'icon': 'fas fa-box',
        'sort_order': 3
    },
    'shipped': {
        'value': 'shipped',
        'label': 'Shipped',
        'description': 'Order handed off to the courier',
        'color': 'primary',
        'icon': 'fas fa-truck',
        'sort_order': 4
    },
    'out_for_delivery': {
        'value': 'out_for_delivery',
        'label': 'Out for Delivery',
        'description': 'Order is on the delivery route with driver',
        'color': 'info',
        'icon': 'fas fa-shipping-fast',
        'sort_order': 4.5
    },
    'delivered': {
        'value': 'delivered',
        'label': 'Delivered',
        'description': 'Customer confirmed delivery',
        'color': 'success',
        'icon': 'fas fa-check-circle',
        'sort_order': 5
    },
    'delivery_failed': {
        'value': 'delivery_failed',
        'label': 'Delivery Failed',
        'description': 'Courier unable to deliver',
        'color': 'danger',
        'icon': 'fas fa-exclamation-triangle',
        'sort_order': 6
    },
    'returned_to_warehouse': {
        'value': 'returned_to_warehouse',
        'label': 'Returned to Warehouse',
        'description': 'Order returned to warehouse (after failed delivery or return)',
        'color': 'dark',
        'icon': 'fas fa-undo',
        'sort_order': 7
    },
    'cancelled': {
        'value': 'cancelled',
        'label': 'Cancelled',
        'description': 'Order is cancelled after shipping or return (no reshipment)',
        'color': 'danger',
        'icon': 'fas fa-times-circle',
        'sort_order': 8
    }
}

# Status Transition Rules
STATUS_TRANSITIONS = {
    'not_started': ['picking'],
    'picking': ['awaiting_packing', 'awaiting_batch_items'],
    'awaiting_batch_items': ['picking', 'awaiting_packing'],
    'awaiting_packing': ['ready_for_dispatch'],
    'ready_for_dispatch': ['shipped'],
    'shipped': ['out_for_delivery', 'delivered', 'delivery_failed'],  # Can go to out_for_delivery when driver starts route
    'out_for_delivery': ['delivered', 'delivery_failed', 'returned_to_warehouse'],  # Driver actions
    'delivery_failed': ['returned_to_warehouse'],
    'delivered': ['returned_to_warehouse'],  # Customer return
    'returned_to_warehouse': ['cancelled', 'ready_for_dispatch'],  # Cancel or re-ship
    'cancelled': []  # Terminal status
}

# Status Groups for Reporting
STATUS_GROUPS = {
    'in_warehouse': ['not_started', 'picking', 'awaiting_batch_items', 'awaiting_packing', 'ready_for_dispatch', 'returned_to_warehouse'],
    'in_transit': ['shipped', 'out_for_delivery'],
    'completed': ['delivered'],
    'failed_or_cancelled': ['delivery_failed', 'cancelled']
}

# Get sorted status list
def get_sorted_statuses():
    """Get statuses sorted by their defined order"""
    return sorted(ORDER_STATUSES.values(), key=lambda x: x['sort_order'])

def get_status_info(status_value):
    """Get status information by value"""
    return ORDER_STATUSES.get(status_value)

def can_transition_to(from_status, to_status):
    """Check if status transition is allowed"""
    return to_status in STATUS_TRANSITIONS.get(from_status, [])

def get_allowed_transitions(from_status):
    """Get list of allowed status transitions from current status"""
    return STATUS_TRANSITIONS.get(from_status, [])

def get_status_badge_class(status_value):
    """Get Bootstrap badge class for status"""
    status_info = get_status_info(status_value)
    if status_info:
        return f"bg-{status_info['color']}"
    return "bg-secondary"

def get_status_icon(status_value):
    """Get icon class for status"""
    status_info = get_status_info(status_value)
    if status_info:
        return status_info['icon']
    return "fas fa-question"