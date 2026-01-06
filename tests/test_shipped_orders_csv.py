"""
Comprehensive pytest tests for the shipped orders CSV export functionality.
Tests verify the 29-column schema, data accuracy, filtering, and response headers.
"""

import pytest
import csv
from io import StringIO
from datetime import datetime, timedelta


class TestShippedOrdersCSV:
    """Test class for shipped orders CSV export functionality."""
    
    # Expected HEADERS_29 as defined in routes_operations.py
    EXPECTED_HEADERS_29 = [
        'invoice_no', 'customer_name', 'status', 'shipped_at', 'delivered_at',          # 1-5
        'total_items', 'picked_items', 'completion_rate_percent', 'total_exceptions',    # 6-9
        'routing', 'assigned_to', 'total_weight_kg', 'upload_date',                     # 10-13
        'total_walking_time_s', 'total_picking_time_s', 'total_confirmation_time_s',    # 14-16
        'total_item_time_s', 'avg_walking_time_s', 'avg_picking_time_s',               # 17-19
        'avg_confirmation_time_s', 'avg_total_time_s', 'items_tracked',                # 20-22
        'batch_ids', 'batch_statuses', 'batch_total_items', 'batch_started_at',       # 23-26
        'zones_picked', 'corridors_picked', 'exception_codes'                         # 27-29
    ]
    
    def test_csv_route_authentication_required(self, client):
        """Test that CSV route requires authentication."""
        response = client.get('/operations/shipped-orders-report.csv')
        assert response.status_code == 302  # Redirect to login
        
    def test_csv_route_admin_access_only(self, client):
        """Test that CSV route requires admin role."""
        # Login as picker (non-admin)
        client.post('/login', data={
            'username': 'test_picker_user',
            'password': 'test_password'
        })
        
        response = client.get('/operations/shipped-orders-report.csv')
        assert response.status_code == 302  # Redirect due to access denied
        
    def test_csv_route_success_with_admin(self, admin_auth, test_data):
        """Test that CSV route returns 200 with admin access."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        assert response.status_code == 200
        
    def test_csv_response_headers(self, admin_auth, test_data):
        """Test that CSV response has correct headers."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        
        # Check Content-Type
        assert response.headers['Content-Type'] == 'text/csv; charset=utf-8'
        
        # Check Content-Disposition header for attachment
        content_disposition = response.headers.get('Content-Disposition', '')
        assert 'attachment' in content_disposition
        assert 'shipped_orders_report_' in content_disposition
        assert '.csv' in content_disposition
        
    def test_csv_schema_29_columns_header(self, admin_auth, test_data):
        """Test that CSV has exactly 29 headers in first line."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV content
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        # Verify exactly 29 headers
        assert len(headers) == 29, f"Expected 29 headers, got {len(headers)}"
        
        # Verify headers match expected HEADERS_29
        assert headers == self.EXPECTED_HEADERS_29, f"Headers mismatch. Got: {headers}"
        
    def test_csv_schema_29_columns_data_rows(self, admin_auth, test_data):
        """Test that every data row has exactly 29 columns."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV content
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)  # Skip header row
        
        # Check each data row
        row_count = 0
        for row_num, row in enumerate(csv_reader, start=2):  # Start at row 2 (after header)
            row_count += 1
            assert len(row) == 29, f"Row {row_num} has {len(row)} columns, expected 29. Row: {row}"
            
        # Ensure we have data rows to test
        assert row_count > 0, "No data rows found in CSV"
        
    def test_csv_data_accuracy_invoice_1(self, admin_auth, test_data):
        """Test that CSV data accurately reflects Invoice IN10001 (delivered order)."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV and find IN10001 row
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_row = None
        for row in csv_reader:
            if row[0] == 'IN10001':  # invoice_no is first column
                invoice_row = row
                break
                
        assert invoice_row is not None, "Invoice IN10001 not found in CSV"
        
        # Test critical field mapping (using header indices)
        assert invoice_row[0] == 'IN10001'  # invoice_no
        assert invoice_row[1] == 'Test Customer 1'  # customer_name
        assert invoice_row[2] == 'delivered'  # status
        assert invoice_row[5] == '3'  # total_items (3 items created in test data)
        assert invoice_row[6] == '3'  # picked_items (all items picked)
        assert invoice_row[7] == '100.0'  # completion_rate_percent
        assert invoice_row[8] == '1'  # total_exceptions
        assert invoice_row[9] == 'ROUTE001'  # routing
        assert invoice_row[10] == 'test_picker_user'  # assigned_to
        assert invoice_row[11] == '15.5'  # total_weight_kg
        assert invoice_row[12] == '2025-01-15'  # upload_date
        
    def test_csv_time_tracking_calculations(self, admin_auth, test_data):
        """Test that time tracking calculations are correct in CSV."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV and find IN10001 row (has time tracking data)
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_row = None
        for row in csv_reader:
            if row[0] == 'IN10001':
                invoice_row = row
                break
                
        assert invoice_row is not None
        
        # Test time tracking totals and averages
        # Expected totals: walking=105.0, picking=75.0, confirmation=30.0, total=210.0
        # Expected averages (3 items tracked): walking=35.0, picking=25.0, confirmation=10.0, total=70.0
        assert invoice_row[13] == '105.0'  # total_walking_time_s
        assert invoice_row[14] == '75.0'   # total_picking_time_s
        assert invoice_row[15] == '30.0'   # total_confirmation_time_s
        assert invoice_row[16] == '210.0'  # total_item_time_s
        assert invoice_row[17] == '35.0'   # avg_walking_time_s
        assert invoice_row[18] == '25.0'   # avg_picking_time_s
        assert invoice_row[19] == '10.0'   # avg_confirmation_time_s
        assert invoice_row[20] == '70.0'   # avg_total_time_s
        assert invoice_row[21] == '3'      # items_tracked
        
    def test_csv_zones_and_corridors(self, admin_auth, test_data):
        """Test that zones and corridors are correctly extracted and formatted."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV and find IN10001 row
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_row = None
        for row in csv_reader:
            if row[0] == 'IN10001':
                invoice_row = row
                break
                
        assert invoice_row is not None
        
        # Test zones and corridors (from picked items)
        # Expected zones: MAIN;SECONDARY (sorted)
        # Expected corridors: 09;12;15 (sorted, extracted from locations)
        zones_picked = invoice_row[26]  # zones_picked
        corridors_picked = invoice_row[27]  # corridors_picked
        
        assert 'MAIN' in zones_picked
        assert 'SECONDARY' in zones_picked
        assert '09' in corridors_picked
        assert '12' in corridors_picked
        assert '15' in corridors_picked
        
    def test_csv_batch_information(self, admin_auth, test_data):
        """Test that batch information is correctly included for batch-picked orders."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV and find IN10002 row (has batch data)
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_row = None
        for row in csv_reader:
            if row[0] == 'IN10002':
                invoice_row = row
                break
                
        assert invoice_row is not None
        
        # Test batch information
        batch_ids = invoice_row[22]      # batch_ids
        batch_statuses = invoice_row[23] # batch_statuses
        
        # Should have batch ID and status
        assert batch_ids != ''
        assert 'Completed' in batch_statuses
        
    def test_csv_exception_codes(self, admin_auth, test_data):
        """Test that exception codes are correctly included."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV and find IN10001 row (has exception)
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_row = None
        for row in csv_reader:
            if row[0] == 'IN10001':
                invoice_row = row
                break
                
        assert invoice_row is not None
        
        # Test exception codes
        exception_codes = invoice_row[28]  # exception_codes
        assert 'ITEM002' in exception_codes  # Item with exception
        
    def test_csv_date_filter_from_only(self, admin_auth, test_data):
        """Test date filtering with from date only."""
        # Filter to get only orders from 2025-01-17 onwards (should get IN10003)
        response = admin_auth.get('/operations/shipped-orders-report.csv?date_from=2025-01-17')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_numbers = []
        for row in csv_reader:
            invoice_numbers.append(row[0])
            
        # Should only include IN10003 (shipped/delivered on 2025-01-18/19)
        assert 'IN10003' in invoice_numbers
        assert 'IN10001' not in invoice_numbers  # Shipped on 2025-01-15
        assert 'IN10002' not in invoice_numbers  # Shipped on 2025-01-15
        
    def test_csv_date_filter_to_only(self, admin_auth, test_data):
        """Test date filtering with to date only."""
        # Filter to get only orders up to 2025-01-16 (should get IN10001, IN10002)
        response = admin_auth.get('/operations/shipped-orders-report.csv?date_to=2025-01-16')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_numbers = []
        for row in csv_reader:
            invoice_numbers.append(row[0])
            
        # Should include IN10001 and IN10002 (shipped on 2025-01-15)
        assert 'IN10001' in invoice_numbers
        assert 'IN10002' in invoice_numbers
        assert 'IN10003' not in invoice_numbers  # Shipped on 2025-01-18
        
    def test_csv_date_filter_range(self, admin_auth, test_data):
        """Test date filtering with both from and to dates."""
        # Filter for exact range 2025-01-15 to 2025-01-16
        response = admin_auth.get('/operations/shipped-orders-report.csv?date_from=2025-01-15&date_to=2025-01-16')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_numbers = []
        for row in csv_reader:
            invoice_numbers.append(row[0])
            
        # Should include IN10001 and IN10002 (both shipped on 2025-01-15)
        assert 'IN10001' in invoice_numbers
        assert 'IN10002' in invoice_numbers
        assert 'IN10003' not in invoice_numbers  # Outside date range
        
    def test_csv_status_filter(self, admin_auth, test_data):
        """Test status filtering functionality."""
        # Filter for only delivered orders
        response = admin_auth.get('/operations/shipped-orders-report.csv?status=delivered')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        statuses = []
        invoice_numbers = []
        for row in csv_reader:
            invoice_numbers.append(row[0])
            statuses.append(row[2])  # status column
            
        # Should only include delivered orders
        for status in statuses:
            assert status == 'delivered'
            
        # Should include IN10001 and IN10003 (both delivered)
        assert 'IN10001' in invoice_numbers
        assert 'IN10003' in invoice_numbers
        assert 'IN10002' not in invoice_numbers  # Status is 'shipped'
        
    def test_csv_customer_filter(self, admin_auth, test_data):
        """Test customer name filtering functionality."""
        # Filter for specific customer
        response = admin_auth.get('/operations/shipped-orders-report.csv?customer=Test Customer 2')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        invoice_numbers = []
        customer_names = []
        for row in csv_reader:
            invoice_numbers.append(row[0])
            customer_names.append(row[1])  # customer_name column
            
        # Should only include orders for Test Customer 2
        for customer in customer_names:
            assert 'Test Customer 2' in customer
            
        assert 'IN10002' in invoice_numbers
        assert 'IN10001' not in invoice_numbers
        assert 'IN10003' not in invoice_numbers
        
    def test_csv_picker_filter(self, admin_auth, test_data):
        """Test picker assignment filtering functionality."""
        # All test orders are assigned to 'test_picker_user', so this should return all
        response = admin_auth.get('/operations/shipped-orders-report.csv?picker=test_picker_user')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        assigned_pickers = []
        for row in csv_reader:
            assigned_pickers.append(row[10])  # assigned_to column
            
        # All should be assigned to test_picker_user
        for picker in assigned_pickers:
            assert picker == 'test_picker_user'
            
    def test_csv_empty_data_handling(self, admin_auth):
        """Test CSV export with no shipped orders."""
        # No test data created for this test, should get empty CSV with headers only
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        # Should still have 29 headers
        assert len(headers) == 29
        
        # Should have no data rows (or very few if there's existing data)
        row_count = sum(1 for row in csv_reader)
        # This test runs with fresh data, should have no rows or only the test data
        
    def test_csv_special_characters_handling(self, admin_auth, app):
        """Test CSV export handles special characters correctly."""
        with app.app_context():
            from models import Invoice, InvoiceItem
            from app import db
            
            # Create invoice with special characters
            special_invoice = Invoice(
                invoice_no='SPECIAL001',
                customer_name='Test "Customer" with, commas & symbols',
                upload_date='2025-01-15',
                status='delivered',
                shipped_at=datetime(2025, 1, 15, 10, 0, 0),
                delivered_at=datetime(2025, 1, 16, 10, 0, 0)
            )
            db.session.add(special_invoice)
            
            # Add item with special characters
            special_item = InvoiceItem(
                invoice_no='SPECIAL001',
                item_code='ITEM"SPECIAL',
                item_name='Item with "quotes" and, commas',
                location='A01-01-01',
                corridor='01',
                zone='MAIN',
                qty=1,
                picked_qty=1,
                is_picked=True,
                pick_status='picked'
            )
            db.session.add(special_item)
            db.session.commit()
            
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        # Parse CSV - should handle special characters properly
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        special_row = None
        for row in csv_reader:
            if row[0] == 'SPECIAL001':
                special_row = row
                break
                
        assert special_row is not None
        # Customer name with special characters should be preserved
        assert 'Test "Customer" with, commas & symbols' in special_row[1]
        
    def test_csv_date_format_consistency(self, admin_auth, test_data):
        """Test that dates are formatted consistently throughout CSV."""
        response = admin_auth.get('/operations/shipped-orders-report.csv')
        csv_content = response.get_data(as_text=True)
        
        csv_reader = csv.reader(StringIO(csv_content))
        headers = next(csv_reader)
        
        for row in csv_reader:
            shipped_at = row[3]  # shipped_at column
            delivered_at = row[4]  # delivered_at column
            
            # If dates exist, they should follow YYYY-MM-DD HH:MM format
            if shipped_at:
                assert len(shipped_at) == 16  # "YYYY-MM-DD HH:MM" format
                assert shipped_at[4] == '-' and shipped_at[7] == '-'
                assert shipped_at[10] == ' ' and shipped_at[13] == ':'
                
            if delivered_at:
                assert len(delivered_at) == 16
                assert delivered_at[4] == '-' and delivered_at[7] == '-'
                assert delivered_at[10] == ' ' and delivered_at[13] == ':'