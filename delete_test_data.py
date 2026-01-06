#!/usr/bin/env python3
"""
Test Data Cleanup Script
Safely removes all data for invoices starting with "TEST"
"""

import sys
from app import app, db
from models import (
    Invoice, InvoiceItem, PickingException, BatchPickedItem, 
    ItemTimeTracking, ActivityLog, ShipmentOrder, BatchSessionInvoice,
    BatchPickingSession
)

def delete_test_data():
    """Delete all data for invoices that start with 'TEST'"""
    
    with app.app_context():
        try:
            # Find all test invoices
            test_invoices = Invoice.query.filter(Invoice.invoice_no.like('TEST%')).all()
            
            if not test_invoices:
                print("No test invoices found (invoices starting with 'TEST')")
                return
            
            print(f"Found {len(test_invoices)} test invoices:")
            for invoice in test_invoices:
                print(f"  - {invoice.invoice_no} ({invoice.customer_name})")
            
            # Auto-confirm deletion for automated execution
            print("\nProceeding with test data deletion...")
            
            deleted_counts = {
                'invoices': 0,
                'invoice_items': 0,
                'picking_exceptions': 0,
                'batch_picked_items': 0,
                'item_time_tracking': 0,
                'activity_logs': 0,
                'shipment_orders': 0,
                'batch_session_invoices': 0
            }
            
            # Delete related data for each test invoice
            for invoice in test_invoices:
                invoice_no = invoice.invoice_no
                print(f"Deleting data for {invoice_no}...")
                
                # Delete batch session invoice relationships first
                batch_session_invoices = BatchSessionInvoice.query.filter_by(invoice_no=invoice_no).all()
                for bsi in batch_session_invoices:
                    db.session.delete(bsi)
                    deleted_counts['batch_session_invoices'] += 1
                
                # Delete shipment orders
                shipment_orders = ShipmentOrder.query.filter_by(invoice_no=invoice_no).all()
                for so in shipment_orders:
                    db.session.delete(so)
                    deleted_counts['shipment_orders'] += 1
                
                # Delete activity logs
                activity_logs = ActivityLog.query.filter_by(invoice_no=invoice_no).all()
                for log in activity_logs:
                    db.session.delete(log)
                    deleted_counts['activity_logs'] += 1
                
                # Delete item time tracking
                time_tracking = ItemTimeTracking.query.filter_by(invoice_no=invoice_no).all()
                for tt in time_tracking:
                    db.session.delete(tt)
                    deleted_counts['item_time_tracking'] += 1
                
                # Delete batch picked items
                batch_items = BatchPickedItem.query.filter_by(invoice_no=invoice_no).all()
                for bi in batch_items:
                    db.session.delete(bi)
                    deleted_counts['batch_picked_items'] += 1
                
                # Delete picking exceptions
                exceptions = PickingException.query.filter_by(invoice_no=invoice_no).all()
                for exc in exceptions:
                    db.session.delete(exc)
                    deleted_counts['picking_exceptions'] += 1
                
                # Delete invoice items
                items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
                for item in items:
                    db.session.delete(item)
                    deleted_counts['invoice_items'] += 1
                
                # Delete the invoice itself
                db.session.delete(invoice)
                deleted_counts['invoices'] += 1
            
            # Commit all deletions
            db.session.commit()
            
            print("\n✅ Test data deletion completed successfully!")
            print("\nDeletion summary:")
            for table, count in deleted_counts.items():
                if count > 0:
                    print(f"  - {table}: {count} records deleted")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error during deletion: {str(e)}")
            return False
    
    return True

def list_test_data():
    """List all test invoices without deleting"""
    
    with app.app_context():
        test_invoices = Invoice.query.filter(Invoice.invoice_no.like('TEST%')).all()
        
        if not test_invoices:
            print("No test invoices found (invoices starting with 'TEST')")
            return
        
        print(f"Found {len(test_invoices)} test invoices:")
        print("-" * 80)
        print(f"{'Invoice No':<20} {'Customer':<30} {'Status':<20} {'Items':<10}")
        print("-" * 80)
        
        for invoice in test_invoices:
            items_count = len(invoice.items) if invoice.items else 0
            print(f"{invoice.invoice_no:<20} {invoice.customer_name:<30} {invoice.status:<20} {items_count:<10}")
        
        print("-" * 80)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_test_data()
    else:
        delete_test_data()