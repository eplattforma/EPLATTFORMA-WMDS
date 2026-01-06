-- Migration: Add item tracking requirement fields to purchase_order_lines
-- Date: 2025-11-03
-- Description: Adds columns to track which items require expiration dates, lot numbers, or serial numbers

-- Add tracking requirement columns to purchase_order_lines
ALTER TABLE purchase_order_lines 
ADD COLUMN IF NOT EXISTS item_has_expiration_date BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS item_has_lot_number BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS item_has_serial_number BOOLEAN NOT NULL DEFAULT FALSE;

-- Add comments for documentation
COMMENT ON COLUMN purchase_order_lines.item_has_expiration_date IS 'Whether this item requires expiration date tracking (from PS365)';
COMMENT ON COLUMN purchase_order_lines.item_has_lot_number IS 'Whether this item requires lot number tracking (from PS365)';
COMMENT ON COLUMN purchase_order_lines.item_has_serial_number IS 'Whether this item requires serial number tracking (from PS365)';

-- Note: No index needed on these boolean fields as they are not used for filtering/searching
