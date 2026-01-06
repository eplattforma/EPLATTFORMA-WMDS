-- Add line_id_365 column to purchase_order_lines table
-- This field stores the PS365 unique line identifier needed for order_pick_list API

ALTER TABLE purchase_order_lines 
ADD COLUMN IF NOT EXISTS line_id_365 VARCHAR(100);

-- Create index for better lookup performance
CREATE INDEX IF NOT EXISTS idx_purchase_order_lines_line_id_365 
ON purchase_order_lines(line_id_365);

-- Migration complete
