"""
Admin UI extensions for batch locking system
"""
from flask import render_template_string
from batch_locking_utils import get_locked_items_count

def get_batch_lock_status_html(batch_id):
    """
    Generate HTML status display for batch locking
    
    Args:
        batch_id: ID of the batch picking session
    
    Returns:
        HTML string showing lock status
    """
    locked_count = get_locked_items_count(batch_id)
    
    if locked_count > 0:
        return f'<span class="badge bg-warning"><i class="fas fa-lock me-1"></i>{locked_count} items locked</span>'
    else:
        return '<span class="badge bg-secondary"><i class="fas fa-unlock me-1"></i>No locks</span>'

def add_batch_lock_warning_to_template():
    """
    Template snippet for showing batch lock warnings
    """
    return """
    {% if locked_items %}
    <div class="alert alert-warning" role="alert">
        <i class="fas fa-exclamation-triangle me-2"></i>
        <strong>Items Locked by Batches:</strong>
        <ul class="mb-0 mt-2">
            {% for item, message in locked_items %}
            <li>{{ item.item_code }} - {{ message }}</li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
    """