"""
Test configuration and fixtures for pytest tests.
Provides isolated test environment with in-memory SQLite database.
"""

import pytest
import os
import tempfile
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash


@pytest.fixture(scope='function')
def app():
    """Create a test Flask app with isolated SQLite database."""
    # Import after setting test environment
    os.environ['SESSION_SECRET'] = 'test-secret-key-for-testing'
    
    # Use in-memory SQLite database for tests - no file permissions issues
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    
    # Now import the app after setting environment
    from app import app, db
    
    # Configure the app for testing
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    # Override engine options that are incompatible with SQLite
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'echo': False
    }
    
    # Import all routes to ensure they're registered
    import routes  # Main routes
    import routes_operations  # CSV export routes
    import routes_batch  # Other routes
    
    # Create the database and tables
    with app.app_context():
        db.create_all()
        
        # Create test users with unique names to avoid conflicts
        from models import User
        
        # Check if users already exist before creating
        existing_admin = User.query.filter_by(username='test_admin_user').first()
        existing_picker = User.query.filter_by(username='test_picker_user').first()
        
        if not existing_admin:
            admin_user = User(
                username='test_admin_user',
                password=generate_password_hash('test_password'),
                role='admin'
            )
            db.session.add(admin_user)
        
        if not existing_picker:
            picker_user = User(
                username='test_picker_user', 
                password=generate_password_hash('test_password'),
                role='picker'
            )
            db.session.add(picker_user)
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            # Users might already exist from app initialization, which is fine
            pass
    
    yield app
    
    # No cleanup needed for in-memory database


@pytest.fixture(scope='function')
def client(app):
    """Create a test client for the Flask app."""
    return app.test_client()


@pytest.fixture(scope='function')
def admin_auth(client):
    """Login as admin user and return client with auth."""
    # Login as admin
    response = client.post('/login', data={
        'username': 'test_admin_user',
        'password': 'test_password'
    })
    assert response.status_code == 302  # Redirect after successful login
    return client


@pytest.fixture(scope='function')
def test_data(app):
    """Create comprehensive test data for CSV export testing."""
    with app.app_context():
        from models import (
            Invoice, InvoiceItem, PickingException, 
            ItemTimeTracking, BatchPickingSession, 
            BatchSessionInvoice, BatchPickedItem
        )
        from app import db
        
        # Create test invoices with different statuses
        base_date = datetime(2025, 1, 15, 10, 0, 0)
        
        # Invoice 1: Delivered order with complete data
        invoice1 = Invoice(
            invoice_no='IN10001',
            routing='ROUTE001',
            customer_name='Test Customer 1',
            upload_date='2025-01-15',
            assigned_to='test_picker_user',
            total_lines=3,
            total_items=10,
            total_weight=15.5,
            status='delivered',
            shipped_at=base_date,
            delivered_at=base_date + timedelta(days=1),
            shipped_by='test_admin_user'
        )
        
        # Invoice 2: Shipped order with batch info
        invoice2 = Invoice(
            invoice_no='IN10002',
            routing='ROUTE002', 
            customer_name='Test Customer 2',
            upload_date='2025-01-16',
            assigned_to='test_picker_user',
            total_lines=2,
            total_items=5,
            total_weight=8.3,
            status='shipped',
            shipped_at=base_date + timedelta(hours=2),
            shipped_by='test_admin_user'
        )
        
        # Invoice 3: Delivered order for date filtering tests
        invoice3 = Invoice(
            invoice_no='IN10003',
            routing='ROUTE003',
            customer_name='Test Customer 3', 
            upload_date='2025-01-17',
            assigned_to='test_picker_user',
            total_lines=1,
            total_items=2,
            total_weight=3.2,
            status='delivered',
            shipped_at=base_date + timedelta(days=3),
            delivered_at=base_date + timedelta(days=4),
            shipped_by='test_admin_user'
        )
        
        db.session.add_all([invoice1, invoice2, invoice3])
        
        # Create invoice items for Invoice 1
        items1 = [
            InvoiceItem(
                invoice_no='IN10001',
                item_code='ITEM001',
                location='A12-01-01',
                corridor='12',
                zone='MAIN',
                item_weight=2.5,
                item_name='Test Item 1',
                unit_type='PIECES',
                qty=4,
                line_weight=10.0,
                picked_qty=4,
                is_picked=True,
                pick_status='picked'
            ),
            InvoiceItem(
                invoice_no='IN10001',
                item_code='ITEM002',
                location='B15-02-01',
                corridor='15',
                zone='SECONDARY',
                item_weight=1.5,
                item_name='Test Item 2',
                unit_type='BOXES',
                qty=3,
                line_weight=4.5,
                picked_qty=2,
                is_picked=True,
                pick_status='picked'
            ),
            InvoiceItem(
                invoice_no='IN10001',
                item_code='ITEM003',
                location='C09-01-01',
                corridor='09',
                zone='MAIN', 
                item_weight=0.5,
                item_name='Test Item 3',
                unit_type='PIECES',
                qty=3,
                line_weight=1.0,
                picked_qty=3,
                is_picked=True,
                pick_status='picked'
            )
        ]
        
        # Create invoice items for Invoice 2
        items2 = [
            InvoiceItem(
                invoice_no='IN10002',
                item_code='ITEM004',
                location='A10-01-01',
                corridor='10',
                zone='MAIN',
                item_weight=3.0,
                item_name='Test Item 4',
                unit_type='PIECES',
                qty=3,
                line_weight=9.0,
                picked_qty=3,
                is_picked=True,
                pick_status='picked'
            ),
            InvoiceItem(
                invoice_no='IN10002',
                item_code='ITEM005',
                location='B12-01-01',
                corridor='12',
                zone='SECONDARY',
                item_weight=1.0,
                item_name='Test Item 5',
                unit_type='BOXES',
                qty=2,
                line_weight=2.0,
                picked_qty=1,
                is_picked=True,
                pick_status='picked'
            )
        ]
        
        # Create simple items for Invoice 3
        items3 = [
            InvoiceItem(
                invoice_no='IN10003',
                item_code='ITEM006',
                location='A11-01-01',
                corridor='11',
                zone='MAIN',
                item_weight=1.6,
                item_name='Test Item 6',
                unit_type='PIECES',
                qty=2,
                line_weight=3.2,
                picked_qty=2,
                is_picked=True,
                pick_status='picked'
            )
        ]
        
        db.session.add_all(items1 + items2 + items3)
        
        # Create picking exceptions for Invoice 1
        exception1 = PickingException(
            invoice_no='IN10001',
            item_code='ITEM002',
            expected_qty=3,
            picked_qty=2,
            picker_username='test_picker_user',
            timestamp=base_date + timedelta(minutes=30),
            reason='Damaged item found'
        )
        db.session.add(exception1)
        
        # Create time tracking data for Invoice 1
        time_tracking1 = [
            ItemTimeTracking(
                invoice_no='IN10001',
                item_code='ITEM001',
                picker_username='test_picker_user',
                walking_time=45.0,
                picking_time=30.0,
                confirmation_time=15.0,
                total_item_time=90.0,
                item_started=base_date + timedelta(minutes=5),
                item_completed=base_date + timedelta(minutes=6, seconds=30)
            ),
            ItemTimeTracking(
                invoice_no='IN10001',
                item_code='ITEM002',
                picker_username='test_picker_user',
                walking_time=35.0,
                picking_time=25.0,
                confirmation_time=10.0,
                total_item_time=70.0,
                item_started=base_date + timedelta(minutes=10),
                item_completed=base_date + timedelta(minutes=11, seconds=10)
            ),
            ItemTimeTracking(
                invoice_no='IN10001',
                item_code='ITEM003',
                picker_username='test_picker_user',
                walking_time=25.0,
                picking_time=20.0,
                confirmation_time=5.0,
                total_item_time=50.0,
                item_started=base_date + timedelta(minutes=15),
                item_completed=base_date + timedelta(minutes=15, seconds=50)
            )
        ]
        
        # Create time tracking data for Invoice 2
        time_tracking2 = [
            ItemTimeTracking(
                invoice_no='IN10002',
                item_code='ITEM004',
                picker_username='test_picker_user',
                walking_time=40.0,
                picking_time=35.0,
                confirmation_time=20.0,
                total_item_time=95.0,
                item_started=base_date + timedelta(hours=2, minutes=5),
                item_completed=base_date + timedelta(hours=2, minutes=6, seconds=35)
            ),
            ItemTimeTracking(
                invoice_no='IN10002', 
                item_code='ITEM005',
                picker_username='test_picker_user',
                walking_time=30.0,
                picking_time=15.0,
                confirmation_time=10.0,
                total_item_time=55.0,
                item_started=base_date + timedelta(hours=2, minutes=10),
                item_completed=base_date + timedelta(hours=2, minutes=10, seconds=55)
            )
        ]
        
        db.session.add_all(time_tracking1 + time_tracking2)
        
        # Create batch picking session for Invoice 2
        batch_session = BatchPickingSession(
            name='Test Batch 1',
            batch_number='BATCH-20250115-001',
            zones='MAIN,SECONDARY',
            corridors='10,12',
            created_at=base_date - timedelta(hours=1),
            created_by='test_admin_user',
            assigned_to='test_picker_user',
            status='Completed',
            picking_mode='Sequential'
        )
        db.session.add(batch_session)
        db.session.flush()  # Get the ID
        
        # Create batch session invoice relationship
        batch_invoice = BatchSessionInvoice(
            batch_session_id=batch_session.id,
            invoice_no='IN10002'
        )
        db.session.add(batch_invoice)
        
        # Create batch picked items for Invoice 2
        batch_items = [
            BatchPickedItem(
                batch_session_id=batch_session.id,
                invoice_no='IN10002',
                item_code='ITEM004',
                picked_qty=3
            ),
            BatchPickedItem(
                batch_session_id=batch_session.id,
                invoice_no='IN10002',
                item_code='ITEM005',
                picked_qty=1
            )
        ]
        db.session.add_all(batch_items)
        
        db.session.commit()
        
        return {
            'invoices': [invoice1, invoice2, invoice3],
            'items': items1 + items2 + items3,
            'exceptions': [exception1],
            'time_tracking': time_tracking1 + time_tracking2,
            'batch_session': batch_session,
            'batch_items': batch_items
        }