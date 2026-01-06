"""
Comprehensive test script for batch locking system
Tests the exact scenario where batch conflicts should be detected and prevented
"""
from app import app, db
from models import InvoiceItem, BatchPickingSession, BatchSessionInvoice
from batch_locking_utils import check_batch_conflicts, lock_items_for_batch
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_batch_conflict_detection():
    """Test the batch conflict detection system"""
    with app.app_context():
        print("🔍 Testing Batch Conflict Detection System")
        print("=" * 50)
        
        # Get current state of batch 115
        batch_115 = db.session.get(BatchPickingSession, 115)
        if not batch_115:
            print("❌ Batch 115 not found")
            return
            
        print(f"✅ Found batch 115: {batch_115.name}")
        print(f"   Zones: {batch_115.zones}")
        print(f"   Corridors: {batch_115.corridors}")
        
        # Get invoice for batch 115
        batch_115_invoices = db.session.query(BatchSessionInvoice).filter(
            BatchSessionInvoice.batch_session_id == 115
        ).all()
        
        print(f"   Invoices: {[bi.invoice_no for bi in batch_115_invoices]}")
        
        # Check locked items for batch 115
        locked_items = db.session.query(InvoiceItem).filter(
            InvoiceItem.locked_by_batch_id == 115,
            InvoiceItem.is_picked == False
        ).all()
        
        print(f"   Locked items: {len(locked_items)}")
        for item in locked_items:
            print(f"     - {item.invoice_no}: {item.item_code} (Zone: {item.zone}, Corridor: {item.corridor})")
        
        # Test 1: Check conflicts with same criteria as batch 115
        print("\n🧪 Test 1: Checking conflicts with identical criteria")
        zones_list = batch_115.zones.split(',')
        corridors_list = [batch_115.corridors] if batch_115.corridors else None
        invoice_nos = [bi.invoice_no for bi in batch_115_invoices]
        
        conflicts = check_batch_conflicts(
            zones_list=zones_list,
            corridors_list=corridors_list,
            invoice_nos=invoice_nos
        )
        
        print(f"   Zones tested: {zones_list}")
        print(f"   Corridors tested: {corridors_list}")
        print(f"   Invoices tested: {invoice_nos}")
        print(f"   Conflicts found: {conflicts}")
        
        if conflicts['has_conflicts']:
            print("   ✅ PASS: System correctly detected conflicts")
            for conflict in conflicts['conflicts']:
                print(f"     - Conflict with {conflict['batch_name']}: {len(conflict['items'])} items")
        else:
            print("   ❌ FAIL: System should have detected conflicts but didn't")
        
        # Test 2: Check conflicts with overlapping corridors
        print("\n🧪 Test 2: Checking conflicts with overlapping corridors")
        test_corridors = ['30', '31']  # This should conflict with batch 115's corridor '30'
        
        conflicts_overlap = check_batch_conflicts(
            zones_list=zones_list,
            corridors_list=test_corridors,
            invoice_nos=invoice_nos
        )
        
        print(f"   Corridors tested: {test_corridors}")
        print(f"   Conflicts found: {conflicts_overlap}")
        
        if conflicts_overlap['has_conflicts']:
            print("   ✅ PASS: System correctly detected corridor conflicts")
        else:
            print("   ❌ FAIL: System should have detected corridor conflicts")
        
        # Test 3: Check raw SQL query
        print("\n🧪 Test 3: Raw SQL verification")
        from sqlalchemy import text
        raw_conflicts = db.session.execute(text("""
            SELECT 
                ii.invoice_no,
                ii.item_code,
                ii.zone,
                ii.corridor,
                ii.locked_by_batch_id
            FROM invoice_items ii
            WHERE ii.zone IN ('48h CAVA','MAIN','PACK','SENSITIVE','SNACKS')
            AND ii.corridor IN ('30', '31')
            AND ii.invoice_no IN ('TEST048903')
            AND ii.is_picked = false
            AND ii.pick_status IN ('not_picked', 'reset', 'skipped_pending')
            AND ii.locked_by_batch_id IS NOT NULL
        """)).fetchall()
        
        print(f"   Raw conflicts found: {len(raw_conflicts)}")
        for conflict in raw_conflicts:
            print(f"     - {conflict[0]}: {conflict[1]} (Zone: {conflict[2]}, Corridor: {conflict[3]}, Locked by: {conflict[4]})")
        
        return conflicts

def test_batch_creation_prevention():
    """Test that batch creation is actually prevented"""
    with app.app_context():
        print("\n🔍 Testing Batch Creation Prevention")
        print("=" * 50)
        
        # Simulate the exact parameters that created batch 117
        zones_list = ['48h CAVA', 'MAIN', 'PACK', 'SENSITIVE', 'SNACKS']
        corridors_list = ['30', '31']
        invoice_nos = ['TEST048903']
        
        print(f"Simulating batch creation with:")
        print(f"   Zones: {zones_list}")
        print(f"   Corridors: {corridors_list}")
        print(f"   Invoices: {invoice_nos}")
        
        conflicts = check_batch_conflicts(
            zones_list=zones_list,
            corridors_list=corridors_list,
            invoice_nos=invoice_nos
        )
        
        print(f"   Conflict check result: {conflicts}")
        
        if conflicts['has_conflicts']:
            print("   ✅ PASS: Batch creation would be blocked")
            return True
        else:
            print("   ❌ FAIL: Batch creation would NOT be blocked")
            return False

if __name__ == "__main__":
    print("🚀 Starting Comprehensive Batch Locking Tests")
    print("=" * 60)
    
    conflicts = test_batch_conflict_detection()
    prevention_works = test_batch_creation_prevention()
    
    print("\n📊 SUMMARY")
    print("=" * 20)
    if conflicts and conflicts.get('has_conflicts') and prevention_works:
        print("✅ Batch locking system is working correctly")
    else:
        print("❌ Batch locking system has issues that need fixing")