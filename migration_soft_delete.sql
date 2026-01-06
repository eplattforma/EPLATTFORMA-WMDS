-- ============================================================================
-- SOFT DELETE & ACTIVATABLE COLUMNS MIGRATION
-- ============================================================================
-- This migration adds soft delete and activatable columns to critical tables
-- to prevent data inconsistency from hard deletes.
--
-- Run this migration ONCE on your database:
--   psql $DATABASE_URL < migration_soft_delete.sql
--
-- Or execute via Flask shell:
--   from app import app, db
--   with app.app_context():
--       db.session.execute(open('migration_soft_delete.sql').read())
--       db.session.commit()
-- ============================================================================

BEGIN;

-- ============================================================================
-- USER TABLE - Add Activatable Columns
-- ============================================================================
-- Users should NEVER be hard-deleted (25+ FK references)
-- Instead, disable them with is_active=false

DO $$
BEGIN
    -- Add is_active column if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='users' AND column_name='is_active') THEN
        ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true;
        RAISE NOTICE 'Added is_active to users table';
    END IF;
    
    -- Add disabled_at column if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='users' AND column_name='disabled_at') THEN
        ALTER TABLE users ADD COLUMN disabled_at TIMESTAMP NULL;
        RAISE NOTICE 'Added disabled_at to users table';
    END IF;
    
    -- Add disabled_reason column if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='users' AND column_name='disabled_reason') THEN
        ALTER TABLE users ADD COLUMN disabled_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added disabled_reason to users table';
    END IF;
END $$;

-- ============================================================================
-- INVOICE TABLE - Add Soft Delete Columns
-- ============================================================================
-- Invoices cannot be deleted if they have:
-- - DeliveryDiscrepancies, CODReceipts, PODRecords, ActivityLogs, DeliveryEvents

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='invoices' AND column_name='deleted_at') THEN
        ALTER TABLE invoices ADD COLUMN deleted_at TIMESTAMP NULL;
        RAISE NOTICE 'Added deleted_at to invoices table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='invoices' AND column_name='deleted_by') THEN
        ALTER TABLE invoices ADD COLUMN deleted_by VARCHAR(64) NULL;
        RAISE NOTICE 'Added deleted_by to invoices table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='invoices' AND column_name='delete_reason') THEN
        ALTER TABLE invoices ADD COLUMN delete_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added delete_reason to invoices table';
    END IF;
END $$;

-- ============================================================================
-- SHIPMENTS TABLE - Add Soft Delete Columns
-- ============================================================================
-- Routes/Shipments cannot be deleted if they have:
-- - RouteStops, DeliveryEvents, CODReceipts, PODRecords, assigned invoices

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='shipments' AND column_name='deleted_at') THEN
        ALTER TABLE shipments ADD COLUMN deleted_at TIMESTAMP NULL;
        RAISE NOTICE 'Added deleted_at to shipments table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='shipments' AND column_name='deleted_by') THEN
        ALTER TABLE shipments ADD COLUMN deleted_by VARCHAR(64) NULL;
        RAISE NOTICE 'Added deleted_by to shipments table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='shipments' AND column_name='delete_reason') THEN
        ALTER TABLE shipments ADD COLUMN delete_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added delete_reason to shipments table';
    END IF;
END $$;

-- ============================================================================
-- ROUTE_STOP TABLE - Add Soft Delete Columns
-- ============================================================================
-- Route stops cannot be deleted if they have:
-- - Assigned invoices, PODRecords, DeliveryEvents

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='route_stop' AND column_name='deleted_at') THEN
        ALTER TABLE route_stop ADD COLUMN deleted_at TIMESTAMP NULL;
        RAISE NOTICE 'Added deleted_at to route_stop table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='route_stop' AND column_name='deleted_by') THEN
        ALTER TABLE route_stop ADD COLUMN deleted_by VARCHAR(64) NULL;
        RAISE NOTICE 'Added deleted_by to route_stop table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='route_stop' AND column_name='delete_reason') THEN
        ALTER TABLE route_stop ADD COLUMN delete_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added delete_reason to route_stop table';
    END IF;
END $$;

-- ============================================================================
-- BATCH_PICKING_SESSIONS TABLE - Add Soft Delete Columns
-- ============================================================================
-- Batch sessions cannot be deleted if they have:
-- - Locked items, activity logs

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='batch_picking_sessions' AND column_name='deleted_at') THEN
        ALTER TABLE batch_picking_sessions ADD COLUMN deleted_at TIMESTAMP NULL;
        RAISE NOTICE 'Added deleted_at to batch_picking_sessions table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='batch_picking_sessions' AND column_name='deleted_by') THEN
        ALTER TABLE batch_picking_sessions ADD COLUMN deleted_by VARCHAR(64) NULL;
        RAISE NOTICE 'Added deleted_by to batch_picking_sessions table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='batch_picking_sessions' AND column_name='delete_reason') THEN
        ALTER TABLE batch_picking_sessions ADD COLUMN delete_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added delete_reason to batch_picking_sessions table';
    END IF;
END $$;

-- ============================================================================
-- PS_CUSTOMERS TABLE - Add Soft Delete & Activatable Columns
-- ============================================================================
-- PSCustomers cannot be deleted if referenced by:
-- - Invoices, CreditTerms, PaymentRecords

DO $$
BEGIN
    -- Soft delete columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='ps_customers' AND column_name='deleted_at') THEN
        ALTER TABLE ps_customers ADD COLUMN deleted_at TIMESTAMP NULL;
        RAISE NOTICE 'Added deleted_at to ps_customers table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='ps_customers' AND column_name='deleted_by') THEN
        ALTER TABLE ps_customers ADD COLUMN deleted_by VARCHAR(64) NULL;
        RAISE NOTICE 'Added deleted_by to ps_customers table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='ps_customers' AND column_name='delete_reason') THEN
        ALTER TABLE ps_customers ADD COLUMN delete_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added delete_reason to ps_customers table';
    END IF;
    
    -- Activatable columns (PSCustomer already has 'active' field, adding additional fields)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='ps_customers' AND column_name='is_active') THEN
        -- Sync is_active with existing active field
        ALTER TABLE ps_customers ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true;
        UPDATE ps_customers SET is_active = active WHERE active IS NOT NULL;
        RAISE NOTICE 'Added is_active to ps_customers table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='ps_customers' AND column_name='disabled_at') THEN
        ALTER TABLE ps_customers ADD COLUMN disabled_at TIMESTAMP NULL;
        RAISE NOTICE 'Added disabled_at to ps_customers table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='ps_customers' AND column_name='disabled_reason') THEN
        ALTER TABLE ps_customers ADD COLUMN disabled_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added disabled_reason to ps_customers table';
    END IF;
END $$;

-- ============================================================================
-- PURCHASE_ORDERS TABLE - Add Soft Delete Columns
-- ============================================================================
-- Purchase orders cannot be deleted if they have:
-- - ReceivingSessions, ReceivingLines

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='purchase_orders' AND column_name='deleted_at') THEN
        ALTER TABLE purchase_orders ADD COLUMN deleted_at TIMESTAMP NULL;
        RAISE NOTICE 'Added deleted_at to purchase_orders table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='purchase_orders' AND column_name='deleted_by') THEN
        ALTER TABLE purchase_orders ADD COLUMN deleted_by VARCHAR(64) NULL;
        RAISE NOTICE 'Added deleted_by to purchase_orders table';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='purchase_orders' AND column_name='delete_reason') THEN
        ALTER TABLE purchase_orders ADD COLUMN delete_reason VARCHAR(255) NULL;
        RAISE NOTICE 'Added delete_reason to purchase_orders table';
    END IF;
END $$;

-- ============================================================================
-- CREATE INDEXES FOR PERFORMANCE
-- ============================================================================
-- Add indexes on frequently queried soft-delete columns

CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);
CREATE INDEX IF NOT EXISTS idx_invoices_deleted_at ON invoices(deleted_at);
CREATE INDEX IF NOT EXISTS idx_shipments_deleted_at ON shipments(deleted_at);
CREATE INDEX IF NOT EXISTS idx_route_stop_deleted_at ON route_stop(deleted_at);
CREATE INDEX IF NOT EXISTS idx_batch_sessions_deleted_at ON batch_picking_sessions(deleted_at);
CREATE INDEX IF NOT EXISTS idx_ps_customers_deleted_at ON ps_customers(deleted_at);
CREATE INDEX IF NOT EXISTS idx_ps_customers_is_active ON ps_customers(is_active);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_deleted_at ON purchase_orders(deleted_at);

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES
-- ============================================================================
-- Run these queries to verify the migration succeeded:

-- SELECT column_name, data_type, is_nullable 
-- FROM information_schema.columns 
-- WHERE table_name IN ('users', 'invoices', 'shipments', 'route_stop', 'batch_picking_sessions', 'ps_customers', 'purchase_orders')
-- AND column_name IN ('is_active', 'disabled_at', 'disabled_reason', 'deleted_at', 'deleted_by', 'delete_reason')
-- ORDER BY table_name, column_name;
