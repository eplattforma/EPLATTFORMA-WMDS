-- Migration: Add unique constraint to receipt_log.reference_number
-- Date: 2025-10-12
-- Purpose: Ensure reference numbers are unique to prevent duplicates
-- Idempotent: Can be run multiple times safely

-- Add unique constraint on reference_number (idempotent with exception handling)
DO $$ 
BEGIN
    -- Try to add the constraint
    ALTER TABLE receipt_log ADD CONSTRAINT receipt_log_reference_number_key UNIQUE (reference_number);
    RAISE NOTICE 'Added UNIQUE constraint on receipt_log.reference_number';
EXCEPTION 
    WHEN duplicate_object THEN
        RAISE NOTICE 'UNIQUE constraint on receipt_log.reference_number already exists - skipping';
    WHEN undefined_table THEN
        RAISE EXCEPTION 'Table receipt_log does not exist. Run db.create_all() first.';
END $$;
