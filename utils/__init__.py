# Make utils directory a Python package
import logging
from datetime import datetime

def create_user(session, username, password, role):
    """
    Creates a new user in the database.
    
    Args:
        session: SQLAlchemy session
        username: User's username
        password: User's password (plain text for now)
        role: User's role ('admin', 'picker', 'warehouse_manager', or 'driver')
        
    Returns:
        Tuple (success, message)
    """
    # Import here to avoid circular imports
    from models import User
    
    # Validate role
    if role not in ['admin', 'picker', 'warehouse_manager', 'driver']:
        return False, "Role must be 'admin', 'picker', 'warehouse_manager', or 'driver'"
    
    # Check if user already exists
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        return False, f"User '{username}' already exists"
    
    # Create new user
    try:
        new_user = User(
            username=username,
            password=password,  # Note: In real-world, use password hashing
            role=role
        )
        
        session.add(new_user)
        session.commit()
        
        return True, f"User '{username}' created successfully"
    except Exception as e:
        logging.error(f"Error creating user: {str(e)}")
        session.rollback()
        return False, f"Error creating user: {str(e)}"
        
def calculate_invoice_totals(session, invoice_no):
    """
    Recalculates and updates the invoice totals based on its items.
    
    Args:
        session: SQLAlchemy session
        invoice_no: Invoice number to update
    """
    # Import here to avoid circular imports
    from models import Invoice, InvoiceItem
    
    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    invoice = Invoice.query.get(invoice_no)
    
    if not invoice:
        return
    
    # Calculate totals
    total_lines = len(items)
    
    # Handle potentially problematic values safely
    total_items = 0
    for item in items:
        try:
            if item.qty is not None:
                total_items += int(item.qty)
        except (ValueError, TypeError) as e:
            logging.warning(f"Error adding qty for {invoice_no}/{item.item_code}: {str(e)}")
    
    total_weight = 0
    for item in items:
        try:
            if item.line_weight is not None:
                total_weight += float(item.line_weight)
        except (ValueError, TypeError) as e:
            logging.warning(f"Error adding line_weight for {invoice_no}/{item.item_code}: {str(e)}")
    
    total_exp_time = 0
    for item in items:
        try:
            if item.exp_time is not None:
                total_exp_time += float(item.exp_time)
        except (ValueError, TypeError) as e:
            logging.warning(f"Error adding exp_time for {invoice_no}/{item.item_code}: {str(e)}")
    
    # Update invoice
    invoice.total_lines = total_lines
    invoice.total_items = total_items
    invoice.total_weight = total_weight
    invoice.total_exp_time = total_exp_time
    
    try:
        session.commit()
    except Exception as e:
        logging.error(f"Error updating invoice totals: {str(e)}")
        session.rollback()