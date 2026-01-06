-- ROUTE STOP MIGRATION (idempotent)
-- Creates route_stop and route_stop_invoice tables for delivery route management

-- ROUTE STOP (one visit in a route)
CREATE TABLE IF NOT EXISTS route_stop (
  route_stop_id   SERIAL PRIMARY KEY,
  shipment_id     INTEGER NOT NULL REFERENCES public.shipments(id) ON DELETE CASCADE,
  seq_no          INTEGER NOT NULL,
  stop_name       TEXT,
  stop_addr       TEXT,
  stop_city       TEXT,
  stop_postcode   TEXT,
  notes           TEXT,
  window_start    TIMESTAMP,
  window_end      TIMESTAMP,
  UNIQUE (shipment_id, seq_no)
);

CREATE INDEX IF NOT EXISTS idx_route_stop_shipment_seq ON route_stop (shipment_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_route_stop_shipment ON route_stop (shipment_id);

-- STOP ↔ INVOICES (many invoices per stop)
CREATE TABLE IF NOT EXISTS route_stop_invoice (
  route_stop_invoice_id SERIAL PRIMARY KEY,
  route_stop_id         INTEGER NOT NULL REFERENCES route_stop(route_stop_id) ON DELETE CASCADE,
  invoice_no            VARCHAR NOT NULL REFERENCES public.invoices(invoice_no) ON DELETE RESTRICT,
  status                VARCHAR,
  weight_kg             DOUBLE PRECISION,
  notes                 TEXT,
  UNIQUE (route_stop_id, invoice_no)
);

CREATE INDEX IF NOT EXISTS idx_rsi_stop ON route_stop_invoice (route_stop_id);
CREATE INDEX IF NOT EXISTS idx_rsi_invoice ON route_stop_invoice (invoice_no);
CREATE INDEX IF NOT EXISTS idx_rsi_status ON route_stop_invoice (status);

-- Helpful uniqueness on shipments (route header): one per driver per day
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_shipments_driver_day'
  ) THEN
    ALTER TABLE public.shipments
      ADD CONSTRAINT uq_shipments_driver_day UNIQUE (driver_name, delivery_date);
  END IF;
END$$;
