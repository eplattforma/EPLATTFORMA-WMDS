"""
Utility functions for batch processing
"""
import logging
from datetime import datetime
from sqlalchemy import func, text
from app import db
from models import BatchPickingSession
from timezone_utils import get_local_time

logger = logging.getLogger(__name__)

def generate_batch_number():
    """
    Generate a unique batch number in the format BATCH-YYYYMMDD-###
    
    Returns:
        String: A unique batch number
    """
    today = get_local_time().strftime('%Y%m%d')
    base_format = f"BATCH-{today}-"
    
    try:
        # Using SQLAlchemy query
        result = db.session.query(
            func.max(
                func.cast(
                    func.substr(
                        BatchPickingSession.batch_number,
                        len(base_format) + 1,
                        3
                    ), 
                    db.Integer
                )
            )
        ).filter(
            BatchPickingSession.batch_number.like(f"{base_format}%")
        ).scalar()
        
        # Get the next sequence number
        if result is None:
            next_seq = 1
        else:
            next_seq = result + 1
            
        # Format with leading zeros
        batch_number = f"{base_format}{next_seq:03d}"
        
        # Make sure it's unique by checking if it already exists
        existing = db.session.query(BatchPickingSession).filter_by(
            batch_number=batch_number
        ).first()
        
        # If it somehow exists, increment and try again
        if existing:
            logger.warning(f"Batch number {batch_number} already exists, trying next sequence")
            next_seq += 1
            batch_number = f"{base_format}{next_seq:03d}"
        
        return batch_number
        
    except Exception as e:
        logger.error(f"Error generating batch number: {str(e)}")
        # Fallback to timestamp-based number
        timestamp = get_local_time().strftime('%Y%m%d-%H%M%S')
        return f"BATCH-{timestamp}"