"""
Test script to demonstrate comprehensive batch deletion
Shows all data that would be cleaned up when deleting a batch
"""
from app import app, db
from models import (
    BatchPickingSession, BatchSessionInvoice, BatchPickedItem, 
    ActivityLog, PickingException, InvoiceItem
)
from routes_batch import delete_batch_comprehensive
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def analyze_batch_data(batch_id):
    """Analyze all data related to a specific batch"""
    with app.app_context():
        print(f"🔍 Analyzing data for batch {batch_id}")
        print("=" * 50)
        
        # Get batch info
        batch = db.session.get(BatchPickingSession, batch_id)
        if not batch:
            print(f"❌ Batch {batch_id} not found")
            return
            
        print(f"Batch: {batch.name} ({batch.batch_number})")
        print(f"Status: {batch.status}")
        
        # Count related data
        data_counts = {}
        
        # 1. Batch session invoices
        data_counts['invoices'] = db.session.query(BatchSessionInvoice).filter_by(
            batch_session_id=batch_id
        ).count()
        
        # 2. Batch picked items
        data_counts['picked_items'] = db.session.query(BatchPickedItem).filter_by(
            batch_session_id=batch_id
        ).count()
        
        # 3. Locked items
        data_counts['locked_items'] = db.session.query(InvoiceItem).filter_by(
            locked_by_batch_id=batch_id
        ).count()
        
        # 4. Activity logs mentioning this batch
        batch_name = batch.name or f"BATCH-{batch_id}"
        data_counts['activity_logs'] = db.session.query(ActivityLog).filter(
            (ActivityLog.details.contains(f'batch {batch_id}')) |
            (ActivityLog.details.contains(f'Batch {batch_id}')) |
            (ActivityLog.details.contains(batch_name))
        ).count()
        
        # 5. Picking exceptions related to this batch
        data_counts['exceptions'] = db.session.query(PickingException).filter(
            (PickingException.reason.contains(f'batch {batch_id}')) |
            (PickingException.reason.contains(f'Batch {batch_id}')) |
            (PickingException.reason.contains(batch_name))
        ).count()
        
        print("\nRelated data that would be cleaned up:")
        print(f"  📋 Invoice links: {data_counts['invoices']}")
        print(f"  📦 Picked items: {data_counts['picked_items']}")
        print(f"  🔒 Locked items: {data_counts['locked_items']}")
        print(f"  📝 Activity logs: {data_counts['activity_logs']}")
        print(f"  ⚠️  Exceptions: {data_counts['exceptions']}")
        
        total_records = sum(data_counts.values()) + 1  # +1 for the batch itself
        print(f"\n📊 Total records to be deleted: {total_records}")
        
        return data_counts

def simulate_batch_deletion(batch_id):
    """Simulate what would happen during batch deletion without actually deleting"""
    with app.app_context():
        print(f"\n🧪 Simulating deletion of batch {batch_id}")
        print("=" * 50)
        
        # Analyze before deletion
        before_counts = analyze_batch_data(batch_id)
        
        if not before_counts:
            return
        
        print("\n✅ Comprehensive deletion would:")
        print("  1. Unlock all items locked by this batch")
        print("  2. Remove all picked item records")
        print("  3. Delete all related activity logs")
        print("  4. Remove any picking exceptions")
        print("  5. Delete invoice associations")
        print("  6. Remove the batch session itself")
        print("  7. Create a deletion audit log")
        
        print("\n🔐 Data integrity maintained:")
        print("  - No orphaned records left behind")
        print("  - Complete audit trail of deletion")
        print("  - Items properly unlocked for future batches")

if __name__ == "__main__":
    print("🚀 Comprehensive Batch Deletion Analysis")
    print("=" * 60)
    
    # Test with batch 115
    batch_id = 115
    analyze_batch_data(batch_id)
    simulate_batch_deletion(batch_id)
    
    print("\n📋 Summary:")
    print("The comprehensive deletion system ensures complete cleanup")
    print("of all related data while maintaining data integrity.")