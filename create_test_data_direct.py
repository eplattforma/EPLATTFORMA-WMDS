#!/usr/bin/env python3
"""
Create Test Data Directly in Database
Adds test invoices and items directly to the database for testing purposes
"""

from app import app, db
from models import Invoice, InvoiceItem
from datetime import datetime
from timezone_utils import get_local_time

def create_test_invoices():
    """Create test invoices directly in the database"""
    
    with app.app_context():
        try:
            # Test Invoice 1 - Small order
            invoice1 = Invoice(
                invoice_no='TEST001',
                customer_name='Test Customer Alpha',
                status='not_started',
                routing=101,
                total_lines=3,
                total_qty=6,
                total_weight=2.5,
                total_exp_time=3.0
            )
            db.session.add(invoice1)
            
            # Items for TEST001
            items1 = [
                InvoiceItem(
                    invoice_no='TEST001',
                    item_code='TEST-ITEM-001',
                    item_name='Test Product Alpha',
                    location='MAIN-01-A01-L1-B1',
                    qty=2,
                    unit_type='PCS',
                    weight=0.5,
                    exp_time=0.8,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST001',
                    item_code='TEST-ITEM-002',
                    item_name='Test Product Beta',
                    location='MAIN-01-A02-L2-B3',
                    qty=1,
                    unit_type='PCS',
                    weight=1.2,
                    exp_time=1.0,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST001',
                    item_code='TEST-ITEM-003',
                    item_name='Test Product Gamma',
                    location='MAIN-02-A01-L1-B5',
                    qty=3,
                    unit_type='PCS',
                    weight=0.8,
                    exp_time=1.2,
                    is_picked=False
                )
            ]
            
            for item in items1:
                db.session.add(item)
            
            # Test Invoice 2 - Medium order
            invoice2 = Invoice(
                invoice_no='TEST002',
                customer_name='Test Customer Beta',
                status='not_started',
                routing=205,
                total_lines=4,
                total_qty=12,
                total_weight=7.6,
                total_exp_time=8.0
            )
            db.session.add(invoice2)
            
            # Items for TEST002
            items2 = [
                InvoiceItem(
                    invoice_no='TEST002',
                    item_code='TEST-ITEM-004',
                    item_name='Test Product Delta',
                    location='MAIN-01-A03-L3-B2',
                    qty=5,
                    unit_type='PCS',
                    weight=2.0,
                    exp_time=2.5,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST002',
                    item_code='TEST-ITEM-005',
                    item_name='Test Product Epsilon',
                    location='SENSITIVE-01-A01-L2-B1',
                    qty=2,
                    unit_type='BOX',
                    weight=3.5,
                    exp_time=3.0,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST002',
                    item_code='TEST-ITEM-006',
                    item_name='Test Product Zeta',
                    location='MAIN-03-A02-L1-B4',
                    qty=1,
                    unit_type='PCS',
                    weight=0.3,
                    exp_time=0.5,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST002',
                    item_code='TEST-ITEM-007',
                    item_name='Test Product Eta',
                    location='MAIN-01-A01-L4-B2',
                    qty=4,
                    unit_type='PCS',
                    weight=1.8,
                    exp_time=2.0,
                    is_picked=False
                )
            ]
            
            for item in items2:
                db.session.add(item)
            
            # Test Invoice 3 - Large order
            invoice3 = Invoice(
                invoice_no='TEST003',
                customer_name='Test Customer Gamma Corp',
                status='not_started',
                routing=312,
                total_lines=5,
                total_qty=28,
                total_weight=32.5,
                total_exp_time=21.2
            )
            db.session.add(invoice3)
            
            # Items for TEST003
            items3 = [
                InvoiceItem(
                    invoice_no='TEST003',
                    item_code='TEST-ITEM-008',
                    item_name='Test Product Theta',
                    location='MAIN-01-A01-L1-B1',
                    qty=10,
                    unit_type='PCS',
                    weight=5.0,
                    exp_time=4.0,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST003',
                    item_code='TEST-ITEM-009',
                    item_name='Test Product Iota',
                    location='MAIN-02-A03-L2-B3',
                    qty=7,
                    unit_type='BOX',
                    weight=12.5,
                    exp_time=6.0,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST003',
                    item_code='TEST-ITEM-010',
                    item_name='Test Product Kappa',
                    location='SENSITIVE-01-A02-L1-B2',
                    qty=3,
                    unit_type='PCS',
                    weight=2.2,
                    exp_time=3.5,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST003',
                    item_code='TEST-ITEM-011',
                    item_name='Test Product Lambda',
                    location='MAIN-03-A01-L3-B1',
                    qty=6,
                    unit_type='PCS',
                    weight=4.8,
                    exp_time=3.2,
                    is_picked=False
                ),
                InvoiceItem(
                    invoice_no='TEST003',
                    item_code='TEST-ITEM-012',
                    item_name='Test Product Mu',
                    location='MAIN-01-A04-L2-B5',
                    qty=2,
                    unit_type='BOX',
                    weight=8.0,
                    exp_time=4.5,
                    is_picked=False
                )
            ]
            
            for item in items3:
                db.session.add(item)
            
            # Commit all data
            db.session.commit()
            
            print("Test data created successfully!")
            print("\nTest invoices added:")
            print("- TEST001: 3 items, Test Customer Alpha (small order)")
            print("- TEST002: 4 items, Test Customer Beta (medium order)") 
            print("- TEST003: 5 items, Test Customer Gamma Corp (large order)")
            print("\nThese orders are now visible in your admin dashboard.")
            print("To delete later: Run 'python delete_test_data.py'")
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating test data: {str(e)}")
            return False
    
    return True

if __name__ == "__main__":
    create_test_invoices()