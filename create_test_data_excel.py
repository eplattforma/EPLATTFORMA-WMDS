#!/usr/bin/env python3
"""
Create Test Data Excel File
Generates an Excel file with test invoice data that can be imported through the admin interface
"""

import pandas as pd
from datetime import datetime

def create_test_excel():
    """Create an Excel file with test invoice data"""
    
    # Sample test data - you can modify this
    test_data = [
        # Test Invoice 1 - Small order
        {
            'Invoice': 'TEST001',
            'Customer': 'Test Customer Alpha',
            'Item Code': 'TEST-ITEM-001',
            'Item Name': 'Test Product Alpha',
            'Location': 'MAIN-01-A01-L1-B1',
            'Qty': 2,
            'Unit Type': 'PCS',
            'Weight': 0.5,
            'Expected Time': 0.8
        },
        {
            'Invoice': 'TEST001',
            'Customer': 'Test Customer Alpha',
            'Item Code': 'TEST-ITEM-002',
            'Item Name': 'Test Product Beta',
            'Location': 'MAIN-01-A02-L2-B3',
            'Qty': 1,
            'Unit Type': 'PCS',
            'Weight': 1.2,
            'Expected Time': 1.0
        },
        {
            'Invoice': 'TEST001',
            'Customer': 'Test Customer Alpha',
            'Item Code': 'TEST-ITEM-003',
            'Item Name': 'Test Product Gamma',
            'Location': 'MAIN-02-A01-L1-B5',
            'Qty': 3,
            'Unit Type': 'PCS',
            'Weight': 0.8,
            'Expected Time': 1.2
        },
        
        # Test Invoice 2 - Medium order
        {
            'Invoice': 'TEST002',
            'Customer': 'Test Customer Beta',
            'Item Code': 'TEST-ITEM-004',
            'Item Name': 'Test Product Delta',
            'Location': 'MAIN-01-A03-L3-B2',
            'Qty': 5,
            'Unit Type': 'PCS',
            'Weight': 2.0,
            'Expected Time': 2.5
        },
        {
            'Invoice': 'TEST002',
            'Customer': 'Test Customer Beta',
            'Item Code': 'TEST-ITEM-005',
            'Item Name': 'Test Product Epsilon',
            'Location': 'SENSITIVE-01-A01-L2-B1',
            'Qty': 2,
            'Unit Type': 'BOX',
            'Weight': 3.5,
            'Expected Time': 3.0
        },
        {
            'Invoice': 'TEST002',
            'Customer': 'Test Customer Beta',
            'Item Code': 'TEST-ITEM-006',
            'Item Name': 'Test Product Zeta',
            'Location': 'MAIN-03-A02-L1-B4',
            'Qty': 1,
            'Unit Type': 'PCS',
            'Weight': 0.3,
            'Expected Time': 0.5
        },
        {
            'Invoice': 'TEST002',
            'Customer': 'Test Customer Beta',
            'Item Code': 'TEST-ITEM-007',
            'Item Name': 'Test Product Eta',
            'Location': 'MAIN-01-A01-L4-B2',
            'Qty': 4,
            'Unit Type': 'PCS',
            'Weight': 1.8,
            'Expected Time': 2.0
        },
        
        # Test Invoice 3 - Large order with multiple zones
        {
            'Invoice': 'TEST003',
            'Customer': 'Test Customer Gamma Corp',
            'Item Code': 'TEST-ITEM-008',
            'Item Name': 'Test Product Theta',
            'Location': 'MAIN-01-A01-L1-B1',
            'Qty': 10,
            'Unit Type': 'PCS',
            'Weight': 5.0,
            'Expected Time': 4.0
        },
        {
            'Invoice': 'TEST003',
            'Customer': 'Test Customer Gamma Corp',
            'Item Code': 'TEST-ITEM-009',
            'Item Name': 'Test Product Iota',
            'Location': 'MAIN-02-A03-L2-B3',
            'Qty': 7,
            'Unit Type': 'BOX',
            'Weight': 12.5,
            'Expected Time': 6.0
        },
        {
            'Invoice': 'TEST003',
            'Customer': 'Test Customer Gamma Corp',
            'Item Code': 'TEST-ITEM-010',
            'Item Name': 'Test Product Kappa',
            'Location': 'SENSITIVE-01-A02-L1-B2',
            'Qty': 3,
            'Unit Type': 'PCS',
            'Weight': 2.2,
            'Expected Time': 3.5
        },
        {
            'Invoice': 'TEST003',
            'Customer': 'Test Customer Gamma Corp',
            'Item Code': 'TEST-ITEM-011',
            'Item Name': 'Test Product Lambda',
            'Location': 'MAIN-03-A01-L3-B1',
            'Qty': 6,
            'Unit Type': 'PCS',
            'Weight': 4.8,
            'Expected Time': 3.2
        },
        {
            'Invoice': 'TEST003',
            'Customer': 'Test Customer Gamma Corp',
            'Item Code': 'TEST-ITEM-012',
            'Item Name': 'Test Product Mu',
            'Location': 'MAIN-01-A04-L2-B5',
            'Qty': 2,
            'Unit Type': 'BOX',
            'Weight': 8.0,
            'Expected Time': 4.5
        }
    ]
    
    # Create DataFrame
    df = pd.DataFrame(test_data)
    
    # Save to Excel file
    filename = f'test_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    df.to_excel(filename, index=False)
    
    print(f"Test data Excel file created: {filename}")
    print(f"Contains {len(df)} items across {df['Invoice'].nunique()} test invoices:")
    
    # Show summary
    for invoice in df['Invoice'].unique():
        items = df[df['Invoice'] == invoice]
        total_qty = items['Qty'].sum()
        total_weight = items['Weight'].sum()
        total_time = items['Expected Time'].sum()
        print(f"  - {invoice}: {len(items)} items, {total_qty} qty, {total_weight}kg, {total_time}min")
    
    print(f"\nTo import: Go to Admin Dashboard → Import Data → Upload {filename}")
    print("To delete later: Run 'python delete_test_data.py'")
    
    return filename

if __name__ == "__main__":
    create_test_excel()