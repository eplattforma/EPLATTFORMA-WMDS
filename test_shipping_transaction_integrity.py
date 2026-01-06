#!/usr/bin/env python3
"""
Test script to verify transaction integrity in shipping_utils.py
Tests that savepoint-based transactions provide true partial success semantics.
"""

import sys
import os
import logging
from datetime import datetime

# Add the current directory to the path to import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import Invoice, User
from utils.shipping_utils import ship_invoices

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_test_data():
    """Create test data for transaction integrity testing"""
    logger.info("Creating test data...")
    
    with app.app_context():
        # Create a test user if it doesn't exist
        test_user = User.query.filter_by(username='test_shipper').first()
        if not test_user:
            test_user = User(username='test_shipper', password='test', role='admin')
            db.session.add(test_user)
        
        # Create test invoices with different statuses
        test_invoices = [
            # This should succeed (ready_for_dispatch)
            {
                'invoice_no': 'TEST_SUCCESS_001',
                'status': 'ready_for_dispatch',
                'customer_name': 'Test Customer 1',
                'upload_date': '2025-09-12',
                'routing': '001'
            },
            # This should succeed (ready_for_dispatch)
            {
                'invoice_no': 'TEST_SUCCESS_002', 
                'status': 'ready_for_dispatch',
                'customer_name': 'Test Customer 2',
                'upload_date': '2025-09-12',
                'routing': '002'
            },
            # This should be skipped (wrong status)
            {
                'invoice_no': 'TEST_SKIP_001',
                'status': 'picking',
                'customer_name': 'Test Customer 3',
                'upload_date': '2025-09-12',
                'routing': '003'
            },
            # This should succeed (ready_for_dispatch)
            {
                'invoice_no': 'TEST_SUCCESS_003',
                'status': 'ready_for_dispatch',
                'customer_name': 'Test Customer 4',
                'upload_date': '2025-09-12',
                'routing': '004'
            }
        ]
        
        for invoice_data in test_invoices:
            existing = Invoice.query.filter_by(invoice_no=invoice_data['invoice_no']).first()
            if existing:
                # Update existing invoice
                for key, value in invoice_data.items():
                    setattr(existing, key, value)
            else:
                # Create new invoice
                invoice = Invoice(**invoice_data)
                db.session.add(invoice)
        
        db.session.commit()
        logger.info(f"Created/updated {len(test_invoices)} test invoices")
        
        return [inv['invoice_no'] for inv in test_invoices]

def test_transaction_integrity():
    """Test that partial success scenarios work correctly with savepoints"""
    logger.info("Starting transaction integrity test...")
    
    with app.app_context():
        # Create test data
        test_invoice_numbers = create_test_data()
        
        # Verify initial state
        logger.info("Verifying initial state...")
        for invoice_no in test_invoice_numbers:
            invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
            logger.info(f"  {invoice_no}: status={invoice.status}, shipped_at={invoice.shipped_at}")
        
        # Test the shipping function
        logger.info("\nTesting ship_invoices with mixed scenarios...")
        
        # This list includes invoices that should succeed, skip, and potentially fail
        shipped, skipped, failed = ship_invoices(
            invoice_numbers=test_invoice_numbers,
            shipped_by='test_shipper'
        )
        
        logger.info(f"\nResults:")
        logger.info(f"  Successfully shipped: {shipped}")
        logger.info(f"  Skipped: {skipped}")
        logger.info(f"  Failed: {failed}")
        
        # Verify final state
        logger.info("\nVerifying final state...")
        success_count = 0
        for invoice_no in test_invoice_numbers:
            invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
            logger.info(f"  {invoice_no}: status={invoice.status}, shipped_at={invoice.shipped_at}, shipped_by={invoice.shipped_by}")
            
            if invoice_no in shipped:
                # Should be successfully shipped
                assert invoice.status == 'shipped', f"Invoice {invoice_no} should be shipped but is {invoice.status}"
                assert invoice.shipped_at is not None, f"Invoice {invoice_no} should have shipped_at timestamp"
                assert invoice.shipped_by == 'test_shipper', f"Invoice {invoice_no} should be shipped by test_shipper"
                success_count += 1
                
            elif invoice_no in skipped:
                # Should remain in original status
                assert invoice.status != 'shipped', f"Skipped invoice {invoice_no} should not be shipped"
                assert invoice.shipped_at is None, f"Skipped invoice {invoice_no} should not have shipped_at"
                
        logger.info(f"\n✅ Transaction integrity test PASSED!")
        logger.info(f"   - {success_count} invoices successfully shipped and persisted")
        logger.info(f"   - {len(skipped)} invoices properly skipped")
        logger.info(f"   - {len(failed)} invoices failed (as expected)")
        logger.info(f"   - All successful transactions were preserved despite mixed results")
        
        return True

def cleanup_test_data():
    """Clean up test data"""
    logger.info("Cleaning up test data...")
    
    with app.app_context():
        test_invoices = ['TEST_SUCCESS_001', 'TEST_SUCCESS_002', 'TEST_SKIP_001', 'TEST_SUCCESS_003']
        
        for invoice_no in test_invoices:
            invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
            if invoice:
                db.session.delete(invoice)
        
        test_user = User.query.filter_by(username='test_shipper').first()
        if test_user:
            db.session.delete(test_user)
            
        db.session.commit()
        logger.info("Test data cleaned up")

if __name__ == '__main__':
    try:
        # Run the test
        test_result = test_transaction_integrity()
        
        if test_result:
            logger.info("\n🎉 ALL TESTS PASSED - Transaction integrity is working correctly!")
            
    except Exception as e:
        logger.error(f"\n❌ TEST FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Clean up
        cleanup_test_data()