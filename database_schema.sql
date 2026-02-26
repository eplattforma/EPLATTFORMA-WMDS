--
-- PostgreSQL database dump
--

-- Dumped from database version 16.11 (df20cf9)
-- Dumped by pg_dump version 16.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: _system; Type: SCHEMA; Schema: -; Owner: neondb_owner
--

CREATE SCHEMA _system;


ALTER SCHEMA _system OWNER TO neondb_owner;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: replit_database_migrations_v1; Type: TABLE; Schema: _system; Owner: neondb_owner
--

CREATE TABLE _system.replit_database_migrations_v1 (
    id bigint NOT NULL,
    build_id text NOT NULL,
    deployment_id text NOT NULL,
    statement_count bigint NOT NULL,
    applied_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE _system.replit_database_migrations_v1 OWNER TO neondb_owner;

--
-- Name: replit_database_migrations_v1_id_seq; Type: SEQUENCE; Schema: _system; Owner: neondb_owner
--

CREATE SEQUENCE _system.replit_database_migrations_v1_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE _system.replit_database_migrations_v1_id_seq OWNER TO neondb_owner;

--
-- Name: replit_database_migrations_v1_id_seq; Type: SEQUENCE OWNED BY; Schema: _system; Owner: neondb_owner
--

ALTER SEQUENCE _system.replit_database_migrations_v1_id_seq OWNED BY _system.replit_database_migrations_v1.id;


--
-- Name: activity_logs; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.activity_logs (
    id integer NOT NULL,
    picker_username character varying(64),
    "timestamp" timestamp without time zone,
    activity_type character varying(50),
    invoice_no character varying(50),
    item_code character varying(50),
    details text
);


ALTER TABLE public.activity_logs OWNER TO neondb_owner;

--
-- Name: activity_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.activity_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.activity_logs_id_seq OWNER TO neondb_owner;

--
-- Name: activity_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.activity_logs_id_seq OWNED BY public.activity_logs.id;


--
-- Name: batch_picked_items; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.batch_picked_items (
    id integer NOT NULL,
    batch_session_id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    item_code character varying(50) NOT NULL,
    picked_qty integer NOT NULL,
    "timestamp" timestamp without time zone
);


ALTER TABLE public.batch_picked_items OWNER TO neondb_owner;

--
-- Name: batch_picked_items_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.batch_picked_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batch_picked_items_id_seq OWNER TO neondb_owner;

--
-- Name: batch_picked_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.batch_picked_items_id_seq OWNED BY public.batch_picked_items.id;


--
-- Name: batch_picking_sessions; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.batch_picking_sessions (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    zones character varying(500) NOT NULL,
    created_at timestamp without time zone,
    created_by character varying(64) NOT NULL,
    assigned_to character varying(64),
    status character varying(20),
    current_item_index integer,
    picking_mode character varying(20) DEFAULT 'Sequential'::character varying,
    current_invoice_index integer DEFAULT 0,
    batch_number character varying(20),
    corridors character varying(500),
    unit_types character varying(500) DEFAULT NULL::character varying,
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255)
);


ALTER TABLE public.batch_picking_sessions OWNER TO neondb_owner;

--
-- Name: batch_picking_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.batch_picking_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batch_picking_sessions_id_seq OWNER TO neondb_owner;

--
-- Name: batch_picking_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.batch_picking_sessions_id_seq OWNED BY public.batch_picking_sessions.id;


--
-- Name: batch_session_invoices; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.batch_session_invoices (
    batch_session_id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    is_completed boolean DEFAULT false
);


ALTER TABLE public.batch_session_invoices OWNER TO neondb_owner;

--
-- Name: cod_receipts; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.cod_receipts (
    id integer NOT NULL,
    route_id integer NOT NULL,
    route_stop_id integer NOT NULL,
    driver_username character varying(64) NOT NULL,
    invoice_nos json NOT NULL,
    expected_amount numeric(12,2) NOT NULL,
    received_amount numeric(12,2) NOT NULL,
    variance numeric(12,2),
    payment_method character varying(20) NOT NULL,
    note text,
    ps365_receipt_id character varying(128),
    ps365_synced_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    cheque_number character varying(50),
    cheque_date date
);


ALTER TABLE public.cod_receipts OWNER TO neondb_owner;

--
-- Name: cod_receipts_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.cod_receipts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.cod_receipts_id_seq OWNER TO neondb_owner;

--
-- Name: cod_receipts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.cod_receipts_id_seq OWNED BY public.cod_receipts.id;


--
-- Name: credit_terms; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.credit_terms (
    id integer NOT NULL,
    customer_code character varying(50) NOT NULL,
    terms_code character varying(50) NOT NULL,
    due_days integer NOT NULL,
    is_credit boolean NOT NULL,
    credit_limit numeric(12,2),
    allow_cash boolean,
    allow_card_pos boolean,
    allow_bank_transfer boolean,
    allow_cheque boolean,
    cheque_days_allowed integer,
    min_cash_allowed integer,
    max_cash_allowed integer,
    notes_for_driver text,
    valid_from date,
    valid_to date
);


ALTER TABLE public.credit_terms OWNER TO neondb_owner;

--
-- Name: credit_terms_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.credit_terms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.credit_terms_id_seq OWNER TO neondb_owner;

--
-- Name: credit_terms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.credit_terms_id_seq OWNED BY public.credit_terms.id;


--
-- Name: delivery_discrepancies; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.delivery_discrepancies (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    item_code_expected character varying(50) NOT NULL,
    item_name character varying(200),
    qty_expected integer NOT NULL,
    qty_actual numeric(10,2),
    discrepancy_type character varying(50) NOT NULL,
    reported_by character varying(64) NOT NULL,
    reported_at timestamp without time zone NOT NULL,
    reported_source character varying(50),
    status character varying(20) NOT NULL,
    validated_by character varying(64),
    validated_at timestamp without time zone,
    resolved_by character varying(64),
    resolved_at timestamp without time zone,
    resolution_action character varying(50),
    note text,
    photo_paths text,
    picker_username character varying(64),
    picked_at timestamp without time zone,
    delivery_date date,
    shelf_code_365 character varying(50),
    location character varying(100),
    is_validated boolean DEFAULT false NOT NULL,
    is_resolved boolean DEFAULT false NOT NULL,
    actual_item_id integer,
    actual_item_code text,
    actual_item_name text,
    actual_qty numeric(12,3),
    actual_barcode text
);


ALTER TABLE public.delivery_discrepancies OWNER TO neondb_owner;

--
-- Name: delivery_discrepancies_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.delivery_discrepancies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.delivery_discrepancies_id_seq OWNER TO neondb_owner;

--
-- Name: delivery_discrepancies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.delivery_discrepancies_id_seq OWNED BY public.delivery_discrepancies.id;


--
-- Name: delivery_discrepancy_events; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.delivery_discrepancy_events (
    id integer NOT NULL,
    discrepancy_id integer NOT NULL,
    event_type character varying(50) NOT NULL,
    actor character varying(64) NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    note text,
    old_value text,
    new_value text
);


ALTER TABLE public.delivery_discrepancy_events OWNER TO neondb_owner;

--
-- Name: delivery_discrepancy_events_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.delivery_discrepancy_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.delivery_discrepancy_events_id_seq OWNER TO neondb_owner;

--
-- Name: delivery_discrepancy_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.delivery_discrepancy_events_id_seq OWNED BY public.delivery_discrepancy_events.id;


--
-- Name: delivery_events; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.delivery_events (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    action character varying(30) NOT NULL,
    actor character varying(64) NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    reason text
);


ALTER TABLE public.delivery_events OWNER TO neondb_owner;

--
-- Name: delivery_events_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.delivery_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.delivery_events_id_seq OWNER TO neondb_owner;

--
-- Name: delivery_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.delivery_events_id_seq OWNED BY public.delivery_events.id;


--
-- Name: delivery_lines; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.delivery_lines (
    id integer NOT NULL,
    route_id integer NOT NULL,
    route_stop_id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    item_code character varying(50) NOT NULL,
    qty_ordered numeric(10,2) NOT NULL,
    qty_delivered numeric(10,2) NOT NULL,
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.delivery_lines OWNER TO neondb_owner;

--
-- Name: delivery_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.delivery_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.delivery_lines_id_seq OWNER TO neondb_owner;

--
-- Name: delivery_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.delivery_lines_id_seq OWNED BY public.delivery_lines.id;


--
-- Name: discrepancy_types; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.discrepancy_types (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    display_name character varying(100) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL
);


ALTER TABLE public.discrepancy_types OWNER TO neondb_owner;

--
-- Name: discrepancy_types_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.discrepancy_types_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.discrepancy_types_id_seq OWNER TO neondb_owner;

--
-- Name: discrepancy_types_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.discrepancy_types_id_seq OWNED BY public.discrepancy_types.id;


--
-- Name: dw_attribute1; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_attribute1 (
    attribute_1_code_365 character varying(64) NOT NULL,
    attribute_1_name character varying(255) NOT NULL,
    attribute_1_secondary_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_attribute1 OWNER TO neondb_owner;

--
-- Name: dw_attribute2; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_attribute2 (
    attribute_2_code_365 character varying(64) NOT NULL,
    attribute_2_name character varying(255) NOT NULL,
    attribute_2_secondary_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_attribute2 OWNER TO neondb_owner;

--
-- Name: dw_attribute3; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_attribute3 (
    attribute_3_code_365 character varying(64) NOT NULL,
    attribute_3_name character varying(255) NOT NULL,
    attribute_3_secondary_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_attribute3 OWNER TO neondb_owner;

--
-- Name: dw_attribute4; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_attribute4 (
    attribute_4_code_365 character varying(64) NOT NULL,
    attribute_4_name character varying(255) NOT NULL,
    attribute_4_secondary_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_attribute4 OWNER TO neondb_owner;

--
-- Name: dw_attribute5; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_attribute5 (
    attribute_5_code_365 character varying(64) NOT NULL,
    attribute_5_name character varying(255) NOT NULL,
    attribute_5_secondary_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_attribute5 OWNER TO neondb_owner;

--
-- Name: dw_attribute6; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_attribute6 (
    attribute_6_code_365 character varying(64) NOT NULL,
    attribute_6_name character varying(255) NOT NULL,
    attribute_6_secondary_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_attribute6 OWNER TO neondb_owner;

--
-- Name: dw_brands; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_brands (
    brand_code_365 character varying(64) NOT NULL,
    brand_name character varying(255) NOT NULL,
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_brands OWNER TO neondb_owner;

--
-- Name: dw_cashier; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_cashier (
    user_code_365 character varying(64) NOT NULL,
    user_name character varying(255),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_cashier OWNER TO neondb_owner;

--
-- Name: dw_category_penetration; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_category_penetration (
    id integer NOT NULL,
    customer_code_365 character varying NOT NULL,
    category_code character varying NOT NULL,
    total_spend numeric(12,2) NOT NULL,
    has_category integer NOT NULL
);


ALTER TABLE public.dw_category_penetration OWNER TO neondb_owner;

--
-- Name: dw_category_penetration_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.dw_category_penetration_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dw_category_penetration_id_seq OWNER TO neondb_owner;

--
-- Name: dw_category_penetration_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.dw_category_penetration_id_seq OWNED BY public.dw_category_penetration.id;


--
-- Name: dw_churn_risk; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_churn_risk (
    id integer NOT NULL,
    customer_code_365 character varying NOT NULL,
    category_code character varying NOT NULL,
    recent_spend numeric(14,2) NOT NULL,
    prev_spend numeric(14,2) NOT NULL,
    spend_ratio double precision NOT NULL,
    drop_pct double precision NOT NULL,
    churn_flag integer NOT NULL
);


ALTER TABLE public.dw_churn_risk OWNER TO neondb_owner;

--
-- Name: dw_churn_risk_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.dw_churn_risk_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dw_churn_risk_id_seq OWNER TO neondb_owner;

--
-- Name: dw_churn_risk_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.dw_churn_risk_id_seq OWNED BY public.dw_churn_risk.id;


--
-- Name: dw_invoice_header; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_invoice_header (
    invoice_no_365 character varying(64) NOT NULL,
    invoice_type character varying(64) NOT NULL,
    invoice_date_utc0 date NOT NULL,
    customer_code_365 character varying(64),
    store_code_365 character varying(64),
    user_code_365 character varying(64),
    total_sub numeric(18,4),
    total_discount numeric(18,4),
    total_vat numeric(18,4),
    total_grand numeric(18,4),
    points_earned numeric(18,2),
    points_redeemed numeric(18,2),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_invoice_header OWNER TO neondb_owner;

--
-- Name: dw_invoice_line; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_invoice_line (
    id integer NOT NULL,
    invoice_no_365 character varying(64) NOT NULL,
    line_number integer NOT NULL,
    item_code_365 character varying(64),
    quantity numeric(18,4),
    price_excl numeric(18,4),
    price_incl numeric(18,4),
    discount_percent numeric(18,4),
    vat_code_365 character varying(20),
    vat_percent numeric(6,4),
    line_total_excl numeric(18,4),
    line_total_discount numeric(18,4),
    line_total_vat numeric(18,4),
    line_total_incl numeric(18,4),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_invoice_line OWNER TO neondb_owner;

--
-- Name: dw_invoice_line_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.dw_invoice_line_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dw_invoice_line_id_seq OWNER TO neondb_owner;

--
-- Name: dw_invoice_line_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.dw_invoice_line_id_seq OWNED BY public.dw_invoice_line.id;


--
-- Name: dw_item_categories; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_item_categories (
    category_code_365 character varying(64) NOT NULL,
    category_name character varying(255) NOT NULL,
    parent_category_code character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_item_categories OWNER TO neondb_owner;

--
-- Name: dw_reco_basket; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_reco_basket (
    id integer NOT NULL,
    from_item_code character varying NOT NULL,
    to_item_code character varying NOT NULL,
    support double precision NOT NULL,
    confidence double precision NOT NULL,
    lift double precision
);


ALTER TABLE public.dw_reco_basket OWNER TO neondb_owner;

--
-- Name: dw_reco_basket_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.dw_reco_basket_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dw_reco_basket_id_seq OWNER TO neondb_owner;

--
-- Name: dw_reco_basket_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.dw_reco_basket_id_seq OWNED BY public.dw_reco_basket.id;


--
-- Name: dw_seasons; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_seasons (
    season_code_365 character varying(64) NOT NULL,
    season_name character varying(255) NOT NULL,
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_seasons OWNER TO neondb_owner;

--
-- Name: dw_share_of_wallet; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_share_of_wallet (
    id integer NOT NULL,
    customer_code_365 character varying NOT NULL,
    actual_spend numeric(14,2) NOT NULL,
    avg_spend numeric(14,2) NOT NULL,
    opportunity_gap numeric(14,2) NOT NULL
);


ALTER TABLE public.dw_share_of_wallet OWNER TO neondb_owner;

--
-- Name: dw_share_of_wallet_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.dw_share_of_wallet_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dw_share_of_wallet_id_seq OWNER TO neondb_owner;

--
-- Name: dw_share_of_wallet_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.dw_share_of_wallet_id_seq OWNED BY public.dw_share_of_wallet.id;


--
-- Name: dw_store; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.dw_store (
    store_code_365 character varying(64) NOT NULL,
    store_name character varying(255),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL
);


ALTER TABLE public.dw_store OWNER TO neondb_owner;

--
-- Name: idle_periods; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.idle_periods (
    id integer NOT NULL,
    shift_id integer NOT NULL,
    start_time timestamp without time zone NOT NULL,
    end_time timestamp without time zone,
    duration_minutes integer,
    is_break boolean,
    break_reason character varying(200)
);


ALTER TABLE public.idle_periods OWNER TO neondb_owner;

--
-- Name: idle_periods_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.idle_periods_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.idle_periods_id_seq OWNER TO neondb_owner;

--
-- Name: idle_periods_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.idle_periods_id_seq OWNED BY public.idle_periods.id;


--
-- Name: invoice_delivery_events; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.invoice_delivery_events (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    action character varying(30) NOT NULL,
    actor character varying(64) NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    reason text
);


ALTER TABLE public.invoice_delivery_events OWNER TO neondb_owner;

--
-- Name: invoice_delivery_events_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.invoice_delivery_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.invoice_delivery_events_id_seq OWNER TO neondb_owner;

--
-- Name: invoice_delivery_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.invoice_delivery_events_id_seq OWNED BY public.invoice_delivery_events.id;


--
-- Name: invoice_items; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.invoice_items (
    invoice_no character varying(50) NOT NULL,
    item_code character varying(50) NOT NULL,
    location character varying(100),
    barcode character varying(100),
    zone character varying(50),
    item_weight double precision,
    item_name character varying(200),
    unit_type character varying(50),
    pack character varying(50),
    qty integer,
    line_weight double precision,
    exp_time double precision,
    picked_qty integer,
    is_picked boolean,
    pick_status character varying(20) DEFAULT 'not_picked'::character varying,
    reset_by character varying(64),
    reset_timestamp timestamp without time zone,
    reset_note character varying(500),
    skip_reason text,
    skip_timestamp timestamp without time zone,
    skip_count integer DEFAULT 0,
    corridor character varying(10),
    locked_by_batch_id integer,
    pieces_per_unit_snapshot integer,
    expected_pick_pieces integer
);


ALTER TABLE public.invoice_items OWNER TO neondb_owner;

--
-- Name: invoice_post_delivery_cases; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.invoice_post_delivery_cases (
    id bigint NOT NULL,
    invoice_no character varying(50) NOT NULL,
    route_id bigint,
    route_stop_id bigint,
    status character varying(50) DEFAULT 'OPEN'::character varying NOT NULL,
    reason text,
    notes text,
    created_by character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.invoice_post_delivery_cases OWNER TO neondb_owner;

--
-- Name: invoice_post_delivery_cases_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.invoice_post_delivery_cases_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.invoice_post_delivery_cases_id_seq OWNER TO neondb_owner;

--
-- Name: invoice_post_delivery_cases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.invoice_post_delivery_cases_id_seq OWNED BY public.invoice_post_delivery_cases.id;


--
-- Name: invoice_route_history; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.invoice_route_history (
    id bigint NOT NULL,
    invoice_no character varying(50) NOT NULL,
    route_id bigint,
    route_stop_id bigint,
    action character varying(100) NOT NULL,
    reason text,
    notes text,
    actor_username character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.invoice_route_history OWNER TO neondb_owner;

--
-- Name: invoice_route_history_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.invoice_route_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.invoice_route_history_id_seq OWNER TO neondb_owner;

--
-- Name: invoice_route_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.invoice_route_history_id_seq OWNED BY public.invoice_route_history.id;


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.invoices (
    invoice_no character varying(50) NOT NULL,
    routing character varying(100),
    customer_name character varying(200),
    upload_date character varying(10) NOT NULL,
    assigned_to character varying(64),
    total_lines integer,
    total_items integer,
    total_weight double precision,
    total_exp_time double precision,
    status character varying(30) DEFAULT 'not_started'::character varying,
    current_item_index integer,
    packing_complete_time timestamp without time zone,
    picking_complete_time timestamp without time zone,
    status_updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    shipped_at timestamp without time zone,
    shipped_by character varying(64),
    delivered_at timestamp without time zone,
    undelivered_reason text,
    customer_code character varying(50),
    route_id integer,
    stop_id integer,
    total_grand numeric(12,2),
    total_sub numeric(12,2),
    total_vat numeric(12,2),
    ps365_synced_at timestamp without time zone,
    customer_code_365 character varying(50),
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255)
);


ALTER TABLE public.invoices OWNER TO neondb_owner;

--
-- Name: item_time_tracking; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.item_time_tracking (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    item_code character varying(50) NOT NULL,
    picker_username character varying(64) NOT NULL,
    item_started timestamp without time zone,
    item_completed timestamp without time zone,
    walking_to_location double precision,
    time_at_location double precision,
    location character varying(100),
    zone character varying(50),
    quantity_picked integer,
    created_at timestamp without time zone,
    walking_time double precision DEFAULT 0.0,
    picking_time double precision DEFAULT 0.0,
    confirmation_time double precision DEFAULT 0.0,
    total_item_time double precision DEFAULT 0.0,
    corridor character varying(50),
    shelf character varying(50),
    level character varying(50),
    bin_location character varying(50),
    quantity_expected integer DEFAULT 0,
    item_weight double precision,
    item_name character varying(200),
    unit_type character varying(50),
    expected_time double precision DEFAULT 0.0,
    efficiency_ratio double precision DEFAULT 0.0,
    previous_location character varying(100),
    order_sequence integer DEFAULT 0,
    time_of_day character varying(10),
    day_of_week character varying(10),
    picked_correctly boolean DEFAULT true,
    was_skipped boolean DEFAULT false,
    skip_reason character varying(200),
    peak_hours boolean DEFAULT false,
    concurrent_pickers integer DEFAULT 1,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.item_time_tracking OWNER TO neondb_owner;

--
-- Name: item_time_tracking_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.item_time_tracking_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.item_time_tracking_id_seq OWNER TO neondb_owner;

--
-- Name: item_time_tracking_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.item_time_tracking_id_seq OWNED BY public.item_time_tracking.id;


--
-- Name: oi_estimate_lines; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.oi_estimate_lines (
    id integer NOT NULL,
    run_id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    invoice_item_id integer,
    item_code character varying(100),
    location character varying(100),
    unit_type_normalized character varying(50),
    qty double precision,
    estimated_pick_seconds double precision,
    estimated_walk_seconds double precision,
    estimated_total_seconds double precision,
    breakdown_json text
);


ALTER TABLE public.oi_estimate_lines OWNER TO neondb_owner;

--
-- Name: oi_estimate_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.oi_estimate_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.oi_estimate_lines_id_seq OWNER TO neondb_owner;

--
-- Name: oi_estimate_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.oi_estimate_lines_id_seq OWNED BY public.oi_estimate_lines.id;


--
-- Name: oi_estimate_runs; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.oi_estimate_runs (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    estimator_version character varying(50) NOT NULL,
    params_revision integer NOT NULL,
    params_snapshot_json text,
    estimated_total_seconds double precision,
    estimated_pick_seconds double precision,
    estimated_travel_seconds double precision,
    breakdown_json text,
    reason character varying(100),
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.oi_estimate_runs OWNER TO neondb_owner;

--
-- Name: oi_estimate_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.oi_estimate_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.oi_estimate_runs_id_seq OWNER TO neondb_owner;

--
-- Name: oi_estimate_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.oi_estimate_runs_id_seq OWNED BY public.oi_estimate_runs.id;


--
-- Name: order_time_breakdown; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.order_time_breakdown (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    picker_username character varying(64) NOT NULL,
    picking_started timestamp without time zone,
    picking_completed timestamp without time zone,
    packing_started timestamp without time zone,
    packing_completed timestamp without time zone,
    total_walking_time double precision,
    total_picking_time double precision,
    total_packing_time double precision,
    total_items_picked integer,
    total_locations_visited integer,
    average_time_per_item double precision,
    created_at timestamp without time zone,
    updated_at timestamp without time zone
);


ALTER TABLE public.order_time_breakdown OWNER TO neondb_owner;

--
-- Name: order_time_breakdown_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.order_time_breakdown_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.order_time_breakdown_id_seq OWNER TO neondb_owner;

--
-- Name: order_time_breakdown_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.order_time_breakdown_id_seq OWNED BY public.order_time_breakdown.id;


--
-- Name: payment_customers; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.payment_customers (
    id integer NOT NULL,
    code character varying(50) NOT NULL,
    name character varying(255) NOT NULL,
    "group" character varying(100)
);


ALTER TABLE public.payment_customers OWNER TO neondb_owner;

--
-- Name: payment_customers_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.payment_customers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payment_customers_id_seq OWNER TO neondb_owner;

--
-- Name: payment_customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.payment_customers_id_seq OWNED BY public.payment_customers.id;


--
-- Name: picking_exceptions; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.picking_exceptions (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    item_code character varying(50) NOT NULL,
    expected_qty integer NOT NULL,
    picked_qty integer NOT NULL,
    picker_username character varying(64) NOT NULL,
    "timestamp" timestamp without time zone,
    reason character varying(500)
);


ALTER TABLE public.picking_exceptions OWNER TO neondb_owner;

--
-- Name: picking_exceptions_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.picking_exceptions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.picking_exceptions_id_seq OWNER TO neondb_owner;

--
-- Name: picking_exceptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.picking_exceptions_id_seq OWNED BY public.picking_exceptions.id;


--
-- Name: pod_records; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.pod_records (
    id integer NOT NULL,
    route_id integer NOT NULL,
    route_stop_id integer NOT NULL,
    invoice_nos json NOT NULL,
    has_physical_signed_invoice boolean,
    receiver_name character varying(200),
    receiver_relationship character varying(100),
    photo_paths json,
    gps_lat numeric(10,8),
    gps_lng numeric(11,8),
    collected_at timestamp without time zone NOT NULL,
    collected_by character varying(64) NOT NULL,
    notes text
);


ALTER TABLE public.pod_records OWNER TO neondb_owner;

--
-- Name: pod_records_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.pod_records_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pod_records_id_seq OWNER TO neondb_owner;

--
-- Name: pod_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.pod_records_id_seq OWNED BY public.pod_records.id;


--
-- Name: ps365_reserved_stock_777; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.ps365_reserved_stock_777 (
    item_code_365 character varying(64) NOT NULL,
    item_name character varying(255) NOT NULL,
    season_name character varying(128),
    number_of_pieces integer,
    number_field_5_value integer,
    store_code_365 character varying(16) NOT NULL,
    stock numeric(18,4) NOT NULL,
    stock_reserved numeric(18,4) NOT NULL,
    stock_ordered numeric(18,4) NOT NULL,
    available_stock numeric(18,4) NOT NULL,
    synced_at timestamp without time zone NOT NULL,
    supplier_item_code character varying(255),
    barcode character varying(100)
);


ALTER TABLE public.ps365_reserved_stock_777 OWNER TO neondb_owner;

--
-- Name: ps_customers; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.ps_customers (
    customer_code_365 character varying(50) NOT NULL,
    customer_code_secondary text,
    is_company boolean,
    company_name text,
    store_code_365 text,
    active boolean NOT NULL,
    tel_1 text,
    mobile text,
    sms text,
    website text,
    category_code_1_365 text,
    category_1_name text,
    category_code_2_365 text,
    category_2_name text,
    company_activity_code_365 text,
    company_activity_name text,
    credit_limit_amount double precision,
    vat_registration_number text,
    address_line_1 text,
    address_line_2 text,
    address_line_3 text,
    postal_code text,
    town text,
    contact_last_name text,
    contact_first_name text,
    agent_code_365 text,
    agent_name text,
    last_synced_at timestamp without time zone,
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    disabled_at timestamp without time zone,
    disabled_reason character varying(255),
    latitude double precision,
    longitude double precision
);


ALTER TABLE public.ps_customers OWNER TO neondb_owner;

--
-- Name: ps_items_dw; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.ps_items_dw (
    item_code_365 character varying(64) NOT NULL,
    item_name character varying(255) NOT NULL,
    active boolean NOT NULL,
    category_code_365 character varying(64),
    brand_code_365 character varying(64),
    season_code_365 character varying(64),
    attribute_6_code_365 character varying(64),
    attr_hash character varying(32) NOT NULL,
    last_sync_at timestamp without time zone NOT NULL,
    attribute_1_code_365 character varying(64),
    attribute_2_code_365 character varying(64),
    attribute_3_code_365 character varying(64),
    attribute_4_code_365 character varying(64),
    attribute_5_code_365 character varying(64),
    item_length numeric(10,3),
    item_width numeric(10,3),
    item_height numeric(10,3),
    item_weight numeric(10,3),
    number_of_pieces integer,
    selling_qty numeric(10,3),
    wms_zone character varying(50),
    wms_unit_type character varying(50),
    wms_fragility character varying(20),
    wms_stackability character varying(20),
    wms_temperature_sensitivity character varying(30),
    wms_pressure_sensitivity character varying(20),
    wms_shape_type character varying(30),
    wms_spill_risk boolean,
    wms_pick_difficulty integer,
    wms_shelf_height character varying(20),
    wms_box_fit_rule character varying(30),
    wms_class_confidence integer,
    wms_class_source character varying(30),
    wms_class_notes text,
    wms_classified_at timestamp without time zone,
    wms_class_evidence text,
    barcode character varying(100),
    supplier_item_code character varying(255),
    min_order_qty integer
);


ALTER TABLE public.ps_items_dw OWNER TO neondb_owner;

--
-- Name: purchase_order_lines; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.purchase_order_lines (
    id integer NOT NULL,
    purchase_order_id integer NOT NULL,
    line_number integer NOT NULL,
    item_code_365 character varying(100) NOT NULL,
    item_name character varying(500),
    line_quantity numeric(12,4),
    line_price_excl_vat numeric(12,2),
    line_total_sub numeric(12,2),
    line_total_discount numeric(12,2),
    line_total_discount_percentage numeric(5,2),
    line_vat_code_365 character varying(50),
    line_total_vat numeric(12,2),
    line_total_vat_percentage numeric(5,2),
    line_total_grand numeric(12,2),
    shelf_locations text,
    item_has_expiration_date boolean DEFAULT false NOT NULL,
    item_has_lot_number boolean DEFAULT false NOT NULL,
    item_has_serial_number boolean DEFAULT false NOT NULL,
    line_id_365 character varying(100),
    item_barcode character varying(100),
    unit_type character varying(50),
    pieces_per_unit integer,
    supplier_item_code character varying(255),
    stock_qty numeric(12,4),
    stock_reserved_qty numeric(12,4),
    stock_ordered_qty numeric(12,4),
    available_qty numeric(12,4),
    stock_synced_at timestamp with time zone
);


ALTER TABLE public.purchase_order_lines OWNER TO neondb_owner;

--
-- Name: purchase_order_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.purchase_order_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.purchase_order_lines_id_seq OWNER TO neondb_owner;

--
-- Name: purchase_order_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.purchase_order_lines_id_seq OWNED BY public.purchase_order_lines.id;


--
-- Name: purchase_orders; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.purchase_orders (
    id integer NOT NULL,
    code_365 character varying(100),
    shopping_cart_code character varying(100),
    supplier_code character varying(100),
    status_code character varying(50),
    status_name character varying(100),
    order_date_local character varying(50),
    order_date_utc0 character varying(50),
    comments text,
    total_sub numeric(12,2),
    total_discount numeric(12,2),
    total_vat numeric(12,2),
    total_grand numeric(12,2),
    downloaded_at timestamp without time zone NOT NULL,
    downloaded_by character varying(64),
    supplier_name character varying(200),
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255),
    is_archived boolean DEFAULT false NOT NULL,
    archived_at timestamp without time zone,
    archived_by character varying(64),
    description text
);


ALTER TABLE public.purchase_orders OWNER TO neondb_owner;

--
-- Name: purchase_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.purchase_orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.purchase_orders_id_seq OWNER TO neondb_owner;

--
-- Name: purchase_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.purchase_orders_id_seq OWNED BY public.purchase_orders.id;


--
-- Name: receipt_log; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.receipt_log (
    id integer NOT NULL,
    reference_number character varying(32) NOT NULL,
    customer_code_365 character varying(32) NOT NULL,
    amount numeric(12,2) NOT NULL,
    comments character varying(1000),
    response_id character varying(128),
    success integer,
    request_json text,
    response_json text,
    created_at timestamp without time zone,
    invoice_no character varying(500),
    driver_username character varying(64),
    route_stop_id integer
);


ALTER TABLE public.receipt_log OWNER TO neondb_owner;

--
-- Name: receipt_log_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.receipt_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.receipt_log_id_seq OWNER TO neondb_owner;

--
-- Name: receipt_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.receipt_log_id_seq OWNED BY public.receipt_log.id;


--
-- Name: receipt_sequence; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.receipt_sequence (
    id integer NOT NULL,
    last_number integer NOT NULL,
    updated_at timestamp without time zone
);


ALTER TABLE public.receipt_sequence OWNER TO neondb_owner;

--
-- Name: receipt_sequence_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.receipt_sequence_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.receipt_sequence_id_seq OWNER TO neondb_owner;

--
-- Name: receipt_sequence_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.receipt_sequence_id_seq OWNED BY public.receipt_sequence.id;


--
-- Name: receiving_lines; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.receiving_lines (
    id integer NOT NULL,
    session_id integer NOT NULL,
    po_line_id integer NOT NULL,
    barcode_scanned character varying(200),
    item_code_365 character varying(100) NOT NULL,
    qty_received numeric(12,4) NOT NULL,
    expiry_date date,
    lot_note text,
    received_at timestamp without time zone NOT NULL
);


ALTER TABLE public.receiving_lines OWNER TO neondb_owner;

--
-- Name: receiving_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.receiving_lines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.receiving_lines_id_seq OWNER TO neondb_owner;

--
-- Name: receiving_lines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.receiving_lines_id_seq OWNED BY public.receiving_lines.id;


--
-- Name: receiving_sessions; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.receiving_sessions (
    id integer NOT NULL,
    purchase_order_id integer NOT NULL,
    receipt_code character varying(50) NOT NULL,
    operator character varying(64),
    started_at timestamp without time zone NOT NULL,
    finished_at timestamp without time zone,
    comments text
);


ALTER TABLE public.receiving_sessions OWNER TO neondb_owner;

--
-- Name: receiving_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.receiving_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.receiving_sessions_id_seq OWNER TO neondb_owner;

--
-- Name: receiving_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.receiving_sessions_id_seq OWNED BY public.receiving_sessions.id;


--
-- Name: reroute_requests; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.reroute_requests (
    id bigint NOT NULL,
    invoice_no character varying(50) NOT NULL,
    requested_by character varying(100),
    status character varying(50) DEFAULT 'OPEN'::character varying NOT NULL,
    notes text,
    assigned_route_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


ALTER TABLE public.reroute_requests OWNER TO neondb_owner;

--
-- Name: reroute_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.reroute_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reroute_requests_id_seq OWNER TO neondb_owner;

--
-- Name: reroute_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.reroute_requests_id_seq OWNED BY public.reroute_requests.id;


--
-- Name: route_delivery_events; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.route_delivery_events (
    id integer NOT NULL,
    route_id integer NOT NULL,
    route_stop_id integer,
    event_type character varying(50) NOT NULL,
    payload json,
    gps_lat numeric(10,8),
    gps_lng numeric(11,8),
    created_at timestamp without time zone NOT NULL,
    actor_username character varying(64) NOT NULL
);


ALTER TABLE public.route_delivery_events OWNER TO neondb_owner;

--
-- Name: route_delivery_events_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.route_delivery_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.route_delivery_events_id_seq OWNER TO neondb_owner;

--
-- Name: route_delivery_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.route_delivery_events_id_seq OWNED BY public.route_delivery_events.id;


--
-- Name: route_stop; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.route_stop (
    route_stop_id integer NOT NULL,
    shipment_id integer NOT NULL,
    seq_no numeric(10,2) NOT NULL,
    stop_name text,
    stop_addr text,
    stop_city text,
    stop_postcode text,
    notes text,
    window_start timestamp without time zone,
    window_end timestamp without time zone,
    customer_code character varying(50),
    website character varying(500),
    phone character varying(50),
    delivered_at timestamp without time zone,
    failed_at timestamp without time zone,
    failure_reason character varying(100),
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255)
);


ALTER TABLE public.route_stop OWNER TO neondb_owner;

--
-- Name: route_stop_invoice; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.route_stop_invoice (
    route_stop_invoice_id integer NOT NULL,
    route_stop_id integer NOT NULL,
    invoice_no character varying NOT NULL,
    status character varying,
    weight_kg double precision,
    notes text
);


ALTER TABLE public.route_stop_invoice OWNER TO neondb_owner;

--
-- Name: route_stop_invoice_route_stop_invoice_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.route_stop_invoice_route_stop_invoice_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.route_stop_invoice_route_stop_invoice_id_seq OWNER TO neondb_owner;

--
-- Name: route_stop_invoice_route_stop_invoice_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.route_stop_invoice_route_stop_invoice_id_seq OWNED BY public.route_stop_invoice.route_stop_invoice_id;


--
-- Name: route_stop_route_stop_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.route_stop_route_stop_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.route_stop_route_stop_id_seq OWNER TO neondb_owner;

--
-- Name: route_stop_route_stop_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.route_stop_route_stop_id_seq OWNED BY public.route_stop.route_stop_id;


--
-- Name: season_supplier_settings; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.season_supplier_settings (
    season_code_365 character varying(50) NOT NULL,
    supplier_code character varying(50),
    email_to character varying(255),
    email_cc character varying(500),
    email_comment text,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.season_supplier_settings OWNER TO neondb_owner;

--
-- Name: settings; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.settings (
    key character varying(100) NOT NULL,
    value text NOT NULL
);


ALTER TABLE public.settings OWNER TO neondb_owner;

--
-- Name: shifts; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.shifts (
    id integer NOT NULL,
    picker_username character varying(64) NOT NULL,
    check_in_time timestamp without time zone NOT NULL,
    check_out_time timestamp without time zone,
    check_in_coordinates character varying(100),
    check_out_coordinates character varying(100),
    total_duration_minutes integer,
    status character varying(20),
    admin_adjusted boolean,
    adjustment_note text,
    adjustment_by character varying(64),
    adjustment_time timestamp without time zone
);


ALTER TABLE public.shifts OWNER TO neondb_owner;

--
-- Name: shifts_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.shifts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.shifts_id_seq OWNER TO neondb_owner;

--
-- Name: shifts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.shifts_id_seq OWNED BY public.shifts.id;


--
-- Name: shipment_orders; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.shipment_orders (
    id integer NOT NULL,
    shipment_id integer NOT NULL,
    invoice_no character varying(20) NOT NULL
);


ALTER TABLE public.shipment_orders OWNER TO neondb_owner;

--
-- Name: shipment_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.shipment_orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.shipment_orders_id_seq OWNER TO neondb_owner;

--
-- Name: shipment_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.shipment_orders_id_seq OWNED BY public.shipment_orders.id;


--
-- Name: shipments; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.shipments (
    id integer NOT NULL,
    driver_name character varying(100) NOT NULL,
    route_name character varying(100),
    status character varying(20) NOT NULL,
    delivery_date date NOT NULL,
    created_at timestamp without time zone,
    updated_at timestamp without time zone DEFAULT now(),
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    settlement_status character varying(20) DEFAULT 'PENDING'::character varying,
    driver_submitted_at timestamp without time zone,
    cash_expected numeric(12,2),
    cash_handed_in numeric(12,2),
    cash_variance numeric(12,2),
    cash_variance_note text,
    returns_count integer DEFAULT 0,
    returns_weight double precision,
    settlement_notes text,
    completion_reason character varying(50),
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255),
    reconciliation_status character varying(20) DEFAULT 'NOT_READY'::character varying,
    reconciled_at timestamp without time zone,
    reconciled_by character varying(64),
    is_archived boolean DEFAULT false NOT NULL,
    archived_at timestamp without time zone,
    archived_by character varying(64),
    cash_collected numeric(12,2),
    settlement_cleared_at timestamp without time zone,
    settlement_cleared_by character varying(64)
);


ALTER TABLE public.shipments OWNER TO neondb_owner;

--
-- Name: shipments_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.shipments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.shipments_id_seq OWNER TO neondb_owner;

--
-- Name: shipments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.shipments_id_seq OWNED BY public.shipments.id;


--
-- Name: shipping_events; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.shipping_events (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    action character varying(20) NOT NULL,
    actor character varying(64) NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    note text
);


ALTER TABLE public.shipping_events OWNER TO neondb_owner;

--
-- Name: shipping_events_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.shipping_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.shipping_events_id_seq OWNER TO neondb_owner;

--
-- Name: shipping_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.shipping_events_id_seq OWNED BY public.shipping_events.id;


--
-- Name: stock_positions; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.stock_positions (
    id integer NOT NULL,
    item_code character varying(100) NOT NULL,
    item_description character varying(500),
    store_code character varying(50) NOT NULL,
    store_name character varying(200) NOT NULL,
    expiry_date character varying(20),
    stock_quantity numeric(12,4) NOT NULL,
    imported_at timestamp without time zone NOT NULL
);


ALTER TABLE public.stock_positions OWNER TO neondb_owner;

--
-- Name: stock_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.stock_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.stock_positions_id_seq OWNER TO neondb_owner;

--
-- Name: stock_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.stock_positions_id_seq OWNED BY public.stock_positions.id;


--
-- Name: stock_resolutions; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.stock_resolutions (
    id integer NOT NULL,
    discrepancy_type character varying(50) NOT NULL,
    resolution_name character varying(100) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL
);


ALTER TABLE public.stock_resolutions OWNER TO neondb_owner;

--
-- Name: stock_resolutions_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.stock_resolutions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.stock_resolutions_id_seq OWNER TO neondb_owner;

--
-- Name: stock_resolutions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.stock_resolutions_id_seq OWNED BY public.stock_resolutions.id;


--
-- Name: sync_jobs; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.sync_jobs (
    id character varying(50) NOT NULL,
    job_type character varying(50) NOT NULL,
    params text,
    status character varying(20),
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    created_by character varying(64),
    success boolean,
    invoices_created integer,
    invoices_updated integer,
    items_created integer,
    items_updated integer,
    error_count integer,
    error_message text,
    progress_current integer,
    progress_total integer,
    progress_message character varying(255)
);


ALTER TABLE public.sync_jobs OWNER TO neondb_owner;

--
-- Name: sync_state; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.sync_state (
    key character varying(64) NOT NULL,
    value text NOT NULL
);


ALTER TABLE public.sync_state OWNER TO neondb_owner;

--
-- Name: time_tracking_alerts; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.time_tracking_alerts (
    id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    picker_username character varying(64) NOT NULL,
    alert_type character varying(50) NOT NULL,
    expected_duration double precision NOT NULL,
    actual_duration double precision NOT NULL,
    threshold_percentage double precision NOT NULL,
    created_at timestamp without time zone,
    is_resolved boolean,
    resolved_at timestamp without time zone,
    resolved_by character varying(64),
    notes text
);


ALTER TABLE public.time_tracking_alerts OWNER TO neondb_owner;

--
-- Name: time_tracking_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.time_tracking_alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.time_tracking_alerts_id_seq OWNER TO neondb_owner;

--
-- Name: time_tracking_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.time_tracking_alerts_id_seq OWNED BY public.time_tracking_alerts.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.users (
    username character varying(64) NOT NULL,
    password character varying(256) NOT NULL,
    role character varying(20) NOT NULL,
    payment_type_code_365 character varying(50),
    require_gps_check boolean DEFAULT true,
    disabled_at timestamp without time zone,
    disabled_reason character varying(255),
    is_active boolean DEFAULT true NOT NULL
);


ALTER TABLE public.users OWNER TO neondb_owner;

--
-- Name: wms_category_defaults; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_category_defaults (
    category_code_365 character varying(64) NOT NULL,
    default_zone character varying(50),
    default_fragility character varying(20),
    default_stackability character varying(20),
    default_temperature_sensitivity character varying(30),
    default_pressure_sensitivity character varying(20),
    default_shape_type character varying(30),
    default_spill_risk boolean,
    default_pick_difficulty integer,
    default_shelf_height character varying(20),
    default_box_fit_rule character varying(30),
    is_active boolean NOT NULL,
    notes text,
    updated_by character varying(100),
    updated_at timestamp without time zone,
    default_pack_mode character varying(30)
);


ALTER TABLE public.wms_category_defaults OWNER TO neondb_owner;

--
-- Name: wms_classification_runs; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_classification_runs (
    id integer NOT NULL,
    started_at timestamp without time zone NOT NULL,
    finished_at timestamp without time zone,
    run_by character varying(100),
    mode character varying(30),
    active_items_scanned integer,
    items_updated integer,
    items_needing_review integer,
    notes text
);


ALTER TABLE public.wms_classification_runs OWNER TO neondb_owner;

--
-- Name: wms_classification_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.wms_classification_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wms_classification_runs_id_seq OWNER TO neondb_owner;

--
-- Name: wms_classification_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.wms_classification_runs_id_seq OWNED BY public.wms_classification_runs.id;


--
-- Name: wms_dynamic_rules; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_dynamic_rules (
    id integer NOT NULL,
    name character varying(120) NOT NULL,
    target_attr character varying(64) NOT NULL,
    action_value character varying(100) NOT NULL,
    confidence integer NOT NULL,
    priority integer NOT NULL,
    stop_processing boolean NOT NULL,
    is_active boolean NOT NULL,
    condition_json text NOT NULL,
    notes text,
    updated_by character varying(100),
    updated_at timestamp without time zone,
    actions_json text
);


ALTER TABLE public.wms_dynamic_rules OWNER TO neondb_owner;

--
-- Name: wms_dynamic_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.wms_dynamic_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wms_dynamic_rules_id_seq OWNER TO neondb_owner;

--
-- Name: wms_dynamic_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.wms_dynamic_rules_id_seq OWNED BY public.wms_dynamic_rules.id;


--
-- Name: wms_item_overrides; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_item_overrides (
    item_code_365 character varying(64) NOT NULL,
    zone_override character varying(50),
    unit_type_override character varying(50),
    fragility_override character varying(20),
    stackability_override character varying(20),
    temperature_sensitivity_override character varying(30),
    pressure_sensitivity_override character varying(20),
    shape_type_override character varying(30),
    spill_risk_override boolean,
    pick_difficulty_override integer,
    shelf_height_override character varying(20),
    box_fit_rule_override character varying(30),
    override_reason text,
    is_active boolean NOT NULL,
    updated_by character varying(100),
    updated_at timestamp without time zone,
    pack_mode_override character varying(30)
);


ALTER TABLE public.wms_item_overrides OWNER TO neondb_owner;

--
-- Name: wms_packing_profile; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_packing_profile (
    item_code_365 character varying(50) NOT NULL,
    pallet_role character varying(20) NOT NULL,
    flags_json text,
    unit_type character varying(20),
    fragility character varying(10),
    pressure_sensitivity character varying(10),
    stackability character varying(10),
    temperature_sensitivity character varying(20),
    spill_risk boolean,
    box_fit_rule character varying(20),
    updated_at timestamp without time zone NOT NULL,
    pack_mode character varying(20),
    loss_risk boolean,
    carton_type_hint character varying(10),
    max_carton_weight_kg numeric(10,2)
);


ALTER TABLE public.wms_packing_profile OWNER TO neondb_owner;

--
-- Name: wms_pallet; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_pallet (
    pallet_id integer NOT NULL,
    shipment_id integer NOT NULL,
    label character varying(50) NOT NULL,
    lane_code character varying(10),
    lane_slot integer,
    status character varying(20) NOT NULL,
    max_weight_kg numeric(10,2) NOT NULL,
    max_height_m numeric(10,2) NOT NULL,
    used_mask integer NOT NULL,
    used_weight_kg numeric(10,2) NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    deleted_at timestamp without time zone,
    deleted_by character varying(64),
    delete_reason character varying(255)
);


ALTER TABLE public.wms_pallet OWNER TO neondb_owner;

--
-- Name: wms_pallet_order; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.wms_pallet_order (
    id integer NOT NULL,
    pallet_id integer NOT NULL,
    invoice_no character varying(50) NOT NULL,
    blocks_requested integer NOT NULL,
    blocks_mask integer NOT NULL,
    est_weight_kg numeric(10,2),
    stop_seq_no numeric(10,2),
    created_at timestamp without time zone NOT NULL
);


ALTER TABLE public.wms_pallet_order OWNER TO neondb_owner;

--
-- Name: wms_pallet_order_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.wms_pallet_order_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wms_pallet_order_id_seq OWNER TO neondb_owner;

--
-- Name: wms_pallet_order_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.wms_pallet_order_id_seq OWNED BY public.wms_pallet_order.id;


--
-- Name: wms_pallet_pallet_id_seq; Type: SEQUENCE; Schema: public; Owner: neondb_owner
--

CREATE SEQUENCE public.wms_pallet_pallet_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wms_pallet_pallet_id_seq OWNER TO neondb_owner;

--
-- Name: wms_pallet_pallet_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: neondb_owner
--

ALTER SEQUENCE public.wms_pallet_pallet_id_seq OWNED BY public.wms_pallet.pallet_id;


--
-- Name: replit_database_migrations_v1 id; Type: DEFAULT; Schema: _system; Owner: neondb_owner
--

ALTER TABLE ONLY _system.replit_database_migrations_v1 ALTER COLUMN id SET DEFAULT nextval('_system.replit_database_migrations_v1_id_seq'::regclass);


--
-- Name: activity_logs id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.activity_logs ALTER COLUMN id SET DEFAULT nextval('public.activity_logs_id_seq'::regclass);


--
-- Name: batch_picked_items id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picked_items ALTER COLUMN id SET DEFAULT nextval('public.batch_picked_items_id_seq'::regclass);


--
-- Name: batch_picking_sessions id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picking_sessions ALTER COLUMN id SET DEFAULT nextval('public.batch_picking_sessions_id_seq'::regclass);


--
-- Name: cod_receipts id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.cod_receipts ALTER COLUMN id SET DEFAULT nextval('public.cod_receipts_id_seq'::regclass);


--
-- Name: credit_terms id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.credit_terms ALTER COLUMN id SET DEFAULT nextval('public.credit_terms_id_seq'::regclass);


--
-- Name: delivery_discrepancies id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancies ALTER COLUMN id SET DEFAULT nextval('public.delivery_discrepancies_id_seq'::regclass);


--
-- Name: delivery_discrepancy_events id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancy_events ALTER COLUMN id SET DEFAULT nextval('public.delivery_discrepancy_events_id_seq'::regclass);


--
-- Name: delivery_events id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_events ALTER COLUMN id SET DEFAULT nextval('public.delivery_events_id_seq'::regclass);


--
-- Name: delivery_lines id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_lines ALTER COLUMN id SET DEFAULT nextval('public.delivery_lines_id_seq'::regclass);


--
-- Name: discrepancy_types id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.discrepancy_types ALTER COLUMN id SET DEFAULT nextval('public.discrepancy_types_id_seq'::regclass);


--
-- Name: dw_category_penetration id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_category_penetration ALTER COLUMN id SET DEFAULT nextval('public.dw_category_penetration_id_seq'::regclass);


--
-- Name: dw_churn_risk id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_churn_risk ALTER COLUMN id SET DEFAULT nextval('public.dw_churn_risk_id_seq'::regclass);


--
-- Name: dw_invoice_line id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_invoice_line ALTER COLUMN id SET DEFAULT nextval('public.dw_invoice_line_id_seq'::regclass);


--
-- Name: dw_reco_basket id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_reco_basket ALTER COLUMN id SET DEFAULT nextval('public.dw_reco_basket_id_seq'::regclass);


--
-- Name: dw_share_of_wallet id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_share_of_wallet ALTER COLUMN id SET DEFAULT nextval('public.dw_share_of_wallet_id_seq'::regclass);


--
-- Name: idle_periods id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.idle_periods ALTER COLUMN id SET DEFAULT nextval('public.idle_periods_id_seq'::regclass);


--
-- Name: invoice_delivery_events id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_delivery_events ALTER COLUMN id SET DEFAULT nextval('public.invoice_delivery_events_id_seq'::regclass);


--
-- Name: invoice_post_delivery_cases id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_post_delivery_cases ALTER COLUMN id SET DEFAULT nextval('public.invoice_post_delivery_cases_id_seq'::regclass);


--
-- Name: invoice_route_history id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_route_history ALTER COLUMN id SET DEFAULT nextval('public.invoice_route_history_id_seq'::regclass);


--
-- Name: item_time_tracking id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.item_time_tracking ALTER COLUMN id SET DEFAULT nextval('public.item_time_tracking_id_seq'::regclass);


--
-- Name: oi_estimate_lines id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.oi_estimate_lines ALTER COLUMN id SET DEFAULT nextval('public.oi_estimate_lines_id_seq'::regclass);


--
-- Name: oi_estimate_runs id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.oi_estimate_runs ALTER COLUMN id SET DEFAULT nextval('public.oi_estimate_runs_id_seq'::regclass);


--
-- Name: order_time_breakdown id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.order_time_breakdown ALTER COLUMN id SET DEFAULT nextval('public.order_time_breakdown_id_seq'::regclass);


--
-- Name: payment_customers id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.payment_customers ALTER COLUMN id SET DEFAULT nextval('public.payment_customers_id_seq'::regclass);


--
-- Name: picking_exceptions id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.picking_exceptions ALTER COLUMN id SET DEFAULT nextval('public.picking_exceptions_id_seq'::regclass);


--
-- Name: pod_records id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.pod_records ALTER COLUMN id SET DEFAULT nextval('public.pod_records_id_seq'::regclass);


--
-- Name: purchase_order_lines id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_order_lines ALTER COLUMN id SET DEFAULT nextval('public.purchase_order_lines_id_seq'::regclass);


--
-- Name: purchase_orders id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_orders ALTER COLUMN id SET DEFAULT nextval('public.purchase_orders_id_seq'::regclass);


--
-- Name: receipt_log id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_log ALTER COLUMN id SET DEFAULT nextval('public.receipt_log_id_seq'::regclass);


--
-- Name: receipt_sequence id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_sequence ALTER COLUMN id SET DEFAULT nextval('public.receipt_sequence_id_seq'::regclass);


--
-- Name: receiving_lines id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_lines ALTER COLUMN id SET DEFAULT nextval('public.receiving_lines_id_seq'::regclass);


--
-- Name: receiving_sessions id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_sessions ALTER COLUMN id SET DEFAULT nextval('public.receiving_sessions_id_seq'::regclass);


--
-- Name: reroute_requests id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.reroute_requests ALTER COLUMN id SET DEFAULT nextval('public.reroute_requests_id_seq'::regclass);


--
-- Name: route_delivery_events id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_delivery_events ALTER COLUMN id SET DEFAULT nextval('public.route_delivery_events_id_seq'::regclass);


--
-- Name: route_stop route_stop_id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop ALTER COLUMN route_stop_id SET DEFAULT nextval('public.route_stop_route_stop_id_seq'::regclass);


--
-- Name: route_stop_invoice route_stop_invoice_id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop_invoice ALTER COLUMN route_stop_invoice_id SET DEFAULT nextval('public.route_stop_invoice_route_stop_invoice_id_seq'::regclass);


--
-- Name: shifts id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shifts ALTER COLUMN id SET DEFAULT nextval('public.shifts_id_seq'::regclass);


--
-- Name: shipment_orders id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipment_orders ALTER COLUMN id SET DEFAULT nextval('public.shipment_orders_id_seq'::regclass);


--
-- Name: shipments id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipments ALTER COLUMN id SET DEFAULT nextval('public.shipments_id_seq'::regclass);


--
-- Name: shipping_events id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipping_events ALTER COLUMN id SET DEFAULT nextval('public.shipping_events_id_seq'::regclass);


--
-- Name: stock_positions id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.stock_positions ALTER COLUMN id SET DEFAULT nextval('public.stock_positions_id_seq'::regclass);


--
-- Name: stock_resolutions id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.stock_resolutions ALTER COLUMN id SET DEFAULT nextval('public.stock_resolutions_id_seq'::regclass);


--
-- Name: time_tracking_alerts id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.time_tracking_alerts ALTER COLUMN id SET DEFAULT nextval('public.time_tracking_alerts_id_seq'::regclass);


--
-- Name: wms_classification_runs id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_classification_runs ALTER COLUMN id SET DEFAULT nextval('public.wms_classification_runs_id_seq'::regclass);


--
-- Name: wms_dynamic_rules id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_dynamic_rules ALTER COLUMN id SET DEFAULT nextval('public.wms_dynamic_rules_id_seq'::regclass);


--
-- Name: wms_pallet pallet_id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet ALTER COLUMN pallet_id SET DEFAULT nextval('public.wms_pallet_pallet_id_seq'::regclass);


--
-- Name: wms_pallet_order id; Type: DEFAULT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet_order ALTER COLUMN id SET DEFAULT nextval('public.wms_pallet_order_id_seq'::regclass);


--
-- Name: replit_database_migrations_v1 replit_database_migrations_v1_pkey; Type: CONSTRAINT; Schema: _system; Owner: neondb_owner
--

ALTER TABLE ONLY _system.replit_database_migrations_v1
    ADD CONSTRAINT replit_database_migrations_v1_pkey PRIMARY KEY (id);


--
-- Name: activity_logs activity_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT activity_logs_pkey PRIMARY KEY (id);


--
-- Name: batch_picked_items batch_picked_items_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picked_items
    ADD CONSTRAINT batch_picked_items_pkey PRIMARY KEY (id);


--
-- Name: batch_picking_sessions batch_picking_sessions_batch_number_key; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picking_sessions
    ADD CONSTRAINT batch_picking_sessions_batch_number_key UNIQUE (batch_number);


--
-- Name: batch_picking_sessions batch_picking_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picking_sessions
    ADD CONSTRAINT batch_picking_sessions_pkey PRIMARY KEY (id);


--
-- Name: batch_session_invoices batch_session_invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_session_invoices
    ADD CONSTRAINT batch_session_invoices_pkey PRIMARY KEY (batch_session_id, invoice_no);


--
-- Name: cod_receipts cod_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.cod_receipts
    ADD CONSTRAINT cod_receipts_pkey PRIMARY KEY (id);


--
-- Name: credit_terms credit_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.credit_terms
    ADD CONSTRAINT credit_terms_pkey PRIMARY KEY (id);


--
-- Name: delivery_discrepancies delivery_discrepancies_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancies
    ADD CONSTRAINT delivery_discrepancies_pkey PRIMARY KEY (id);


--
-- Name: delivery_discrepancy_events delivery_discrepancy_events_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancy_events
    ADD CONSTRAINT delivery_discrepancy_events_pkey PRIMARY KEY (id);


--
-- Name: delivery_events delivery_events_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_events
    ADD CONSTRAINT delivery_events_pkey PRIMARY KEY (id);


--
-- Name: delivery_lines delivery_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_lines
    ADD CONSTRAINT delivery_lines_pkey PRIMARY KEY (id);


--
-- Name: discrepancy_types discrepancy_types_name_key; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.discrepancy_types
    ADD CONSTRAINT discrepancy_types_name_key UNIQUE (name);


--
-- Name: discrepancy_types discrepancy_types_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.discrepancy_types
    ADD CONSTRAINT discrepancy_types_pkey PRIMARY KEY (id);


--
-- Name: dw_attribute1 dw_attribute1_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_attribute1
    ADD CONSTRAINT dw_attribute1_pkey PRIMARY KEY (attribute_1_code_365);


--
-- Name: dw_attribute2 dw_attribute2_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_attribute2
    ADD CONSTRAINT dw_attribute2_pkey PRIMARY KEY (attribute_2_code_365);


--
-- Name: dw_attribute3 dw_attribute3_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_attribute3
    ADD CONSTRAINT dw_attribute3_pkey PRIMARY KEY (attribute_3_code_365);


--
-- Name: dw_attribute4 dw_attribute4_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_attribute4
    ADD CONSTRAINT dw_attribute4_pkey PRIMARY KEY (attribute_4_code_365);


--
-- Name: dw_attribute5 dw_attribute5_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_attribute5
    ADD CONSTRAINT dw_attribute5_pkey PRIMARY KEY (attribute_5_code_365);


--
-- Name: dw_attribute6 dw_attribute6_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_attribute6
    ADD CONSTRAINT dw_attribute6_pkey PRIMARY KEY (attribute_6_code_365);


--
-- Name: dw_brands dw_brands_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_brands
    ADD CONSTRAINT dw_brands_pkey PRIMARY KEY (brand_code_365);


--
-- Name: dw_cashier dw_cashier_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_cashier
    ADD CONSTRAINT dw_cashier_pkey PRIMARY KEY (user_code_365);


--
-- Name: dw_category_penetration dw_category_penetration_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_category_penetration
    ADD CONSTRAINT dw_category_penetration_pkey PRIMARY KEY (id);


--
-- Name: dw_churn_risk dw_churn_risk_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_churn_risk
    ADD CONSTRAINT dw_churn_risk_pkey PRIMARY KEY (id);


--
-- Name: dw_invoice_header dw_invoice_header_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_invoice_header
    ADD CONSTRAINT dw_invoice_header_pkey PRIMARY KEY (invoice_no_365);


--
-- Name: dw_invoice_line dw_invoice_line_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_invoice_line
    ADD CONSTRAINT dw_invoice_line_pkey PRIMARY KEY (id);


--
-- Name: dw_item_categories dw_item_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_item_categories
    ADD CONSTRAINT dw_item_categories_pkey PRIMARY KEY (category_code_365);


--
-- Name: dw_reco_basket dw_reco_basket_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_reco_basket
    ADD CONSTRAINT dw_reco_basket_pkey PRIMARY KEY (id);


--
-- Name: dw_seasons dw_seasons_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_seasons
    ADD CONSTRAINT dw_seasons_pkey PRIMARY KEY (season_code_365);


--
-- Name: dw_share_of_wallet dw_share_of_wallet_customer_code_365_key; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_share_of_wallet
    ADD CONSTRAINT dw_share_of_wallet_customer_code_365_key UNIQUE (customer_code_365);


--
-- Name: dw_share_of_wallet dw_share_of_wallet_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_share_of_wallet
    ADD CONSTRAINT dw_share_of_wallet_pkey PRIMARY KEY (id);


--
-- Name: dw_store dw_store_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_store
    ADD CONSTRAINT dw_store_pkey PRIMARY KEY (store_code_365);


--
-- Name: idle_periods idle_periods_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.idle_periods
    ADD CONSTRAINT idle_periods_pkey PRIMARY KEY (id);


--
-- Name: invoice_delivery_events invoice_delivery_events_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_delivery_events
    ADD CONSTRAINT invoice_delivery_events_pkey PRIMARY KEY (id);


--
-- Name: invoice_items invoice_items_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_pkey PRIMARY KEY (invoice_no, item_code);


--
-- Name: invoice_post_delivery_cases invoice_post_delivery_cases_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_post_delivery_cases
    ADD CONSTRAINT invoice_post_delivery_cases_pkey PRIMARY KEY (id);


--
-- Name: invoice_route_history invoice_route_history_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_route_history
    ADD CONSTRAINT invoice_route_history_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (invoice_no);


--
-- Name: item_time_tracking item_time_tracking_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.item_time_tracking
    ADD CONSTRAINT item_time_tracking_pkey PRIMARY KEY (id);


--
-- Name: oi_estimate_lines oi_estimate_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.oi_estimate_lines
    ADD CONSTRAINT oi_estimate_lines_pkey PRIMARY KEY (id);


--
-- Name: oi_estimate_runs oi_estimate_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.oi_estimate_runs
    ADD CONSTRAINT oi_estimate_runs_pkey PRIMARY KEY (id);


--
-- Name: order_time_breakdown order_time_breakdown_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.order_time_breakdown
    ADD CONSTRAINT order_time_breakdown_pkey PRIMARY KEY (id);


--
-- Name: payment_customers payment_customers_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.payment_customers
    ADD CONSTRAINT payment_customers_pkey PRIMARY KEY (id);


--
-- Name: picking_exceptions picking_exceptions_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.picking_exceptions
    ADD CONSTRAINT picking_exceptions_pkey PRIMARY KEY (id);


--
-- Name: pod_records pod_records_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.pod_records
    ADD CONSTRAINT pod_records_pkey PRIMARY KEY (id);


--
-- Name: ps365_reserved_stock_777 ps365_reserved_stock_777_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.ps365_reserved_stock_777
    ADD CONSTRAINT ps365_reserved_stock_777_pkey PRIMARY KEY (item_code_365);


--
-- Name: ps_customers ps_customers_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.ps_customers
    ADD CONSTRAINT ps_customers_pkey PRIMARY KEY (customer_code_365);


--
-- Name: ps_items_dw ps_items_dw_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.ps_items_dw
    ADD CONSTRAINT ps_items_dw_pkey PRIMARY KEY (item_code_365);


--
-- Name: purchase_order_lines purchase_order_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT purchase_order_lines_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: receipt_log receipt_log_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_log
    ADD CONSTRAINT receipt_log_pkey PRIMARY KEY (id);


--
-- Name: receipt_log receipt_log_reference_number_key; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_log
    ADD CONSTRAINT receipt_log_reference_number_key UNIQUE (reference_number);


--
-- Name: receipt_sequence receipt_sequence_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_sequence
    ADD CONSTRAINT receipt_sequence_pkey PRIMARY KEY (id);


--
-- Name: receiving_lines receiving_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_lines
    ADD CONSTRAINT receiving_lines_pkey PRIMARY KEY (id);


--
-- Name: receiving_sessions receiving_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_sessions
    ADD CONSTRAINT receiving_sessions_pkey PRIMARY KEY (id);


--
-- Name: reroute_requests reroute_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.reroute_requests
    ADD CONSTRAINT reroute_requests_pkey PRIMARY KEY (id);


--
-- Name: route_delivery_events route_delivery_events_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_delivery_events
    ADD CONSTRAINT route_delivery_events_pkey PRIMARY KEY (id);


--
-- Name: route_stop_invoice route_stop_invoice_invoice_no_unique; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop_invoice
    ADD CONSTRAINT route_stop_invoice_invoice_no_unique UNIQUE (invoice_no);


--
-- Name: route_stop_invoice route_stop_invoice_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop_invoice
    ADD CONSTRAINT route_stop_invoice_pkey PRIMARY KEY (route_stop_invoice_id);


--
-- Name: route_stop_invoice route_stop_invoice_route_stop_id_invoice_no_key; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop_invoice
    ADD CONSTRAINT route_stop_invoice_route_stop_id_invoice_no_key UNIQUE (route_stop_id, invoice_no);


--
-- Name: route_stop route_stop_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop
    ADD CONSTRAINT route_stop_pkey PRIMARY KEY (route_stop_id);


--
-- Name: route_stop route_stop_shipment_id_seq_no_key; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop
    ADD CONSTRAINT route_stop_shipment_id_seq_no_key UNIQUE (shipment_id, seq_no);


--
-- Name: season_supplier_settings season_supplier_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.season_supplier_settings
    ADD CONSTRAINT season_supplier_settings_pkey PRIMARY KEY (season_code_365);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: shifts shifts_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_pkey PRIMARY KEY (id);


--
-- Name: shipment_orders shipment_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipment_orders
    ADD CONSTRAINT shipment_orders_pkey PRIMARY KEY (id);


--
-- Name: shipments shipments_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_pkey PRIMARY KEY (id);


--
-- Name: shipping_events shipping_events_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipping_events
    ADD CONSTRAINT shipping_events_pkey PRIMARY KEY (id);


--
-- Name: stock_positions stock_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.stock_positions
    ADD CONSTRAINT stock_positions_pkey PRIMARY KEY (id);


--
-- Name: stock_resolutions stock_resolutions_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.stock_resolutions
    ADD CONSTRAINT stock_resolutions_pkey PRIMARY KEY (id);


--
-- Name: sync_jobs sync_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.sync_jobs
    ADD CONSTRAINT sync_jobs_pkey PRIMARY KEY (id);


--
-- Name: sync_state sync_state_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.sync_state
    ADD CONSTRAINT sync_state_pkey PRIMARY KEY (key);


--
-- Name: time_tracking_alerts time_tracking_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.time_tracking_alerts
    ADD CONSTRAINT time_tracking_alerts_pkey PRIMARY KEY (id);


--
-- Name: credit_terms uniq_terms_version; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.credit_terms
    ADD CONSTRAINT uniq_terms_version UNIQUE (customer_code, valid_from);


--
-- Name: dw_invoice_line unique_invoice_line; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_invoice_line
    ADD CONSTRAINT unique_invoice_line UNIQUE (invoice_no_365, line_number);


--
-- Name: batch_picked_items uq_batch_picked_items_unique; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picked_items
    ADD CONSTRAINT uq_batch_picked_items_unique UNIQUE (batch_session_id, invoice_no, item_code);


--
-- Name: wms_pallet_order uq_pallet_order_invoice_no; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet_order
    ADD CONSTRAINT uq_pallet_order_invoice_no UNIQUE (invoice_no);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (username);


--
-- Name: wms_category_defaults wms_category_defaults_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_category_defaults
    ADD CONSTRAINT wms_category_defaults_pkey PRIMARY KEY (category_code_365);


--
-- Name: wms_classification_runs wms_classification_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_classification_runs
    ADD CONSTRAINT wms_classification_runs_pkey PRIMARY KEY (id);


--
-- Name: wms_dynamic_rules wms_dynamic_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_dynamic_rules
    ADD CONSTRAINT wms_dynamic_rules_pkey PRIMARY KEY (id);


--
-- Name: wms_item_overrides wms_item_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_item_overrides
    ADD CONSTRAINT wms_item_overrides_pkey PRIMARY KEY (item_code_365);


--
-- Name: wms_packing_profile wms_packing_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_packing_profile
    ADD CONSTRAINT wms_packing_profile_pkey PRIMARY KEY (item_code_365);


--
-- Name: wms_pallet_order wms_pallet_order_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet_order
    ADD CONSTRAINT wms_pallet_order_pkey PRIMARY KEY (id);


--
-- Name: wms_pallet wms_pallet_pkey; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet
    ADD CONSTRAINT wms_pallet_pkey PRIMARY KEY (pallet_id);


--
-- Name: idx_replit_database_migrations_v1_build_id; Type: INDEX; Schema: _system; Owner: neondb_owner
--

CREATE UNIQUE INDEX idx_replit_database_migrations_v1_build_id ON _system.replit_database_migrations_v1 USING btree (build_id);


--
-- Name: idx_batch_picked_items_session; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_picked_items_session ON public.batch_picked_items USING btree (batch_session_id);


--
-- Name: idx_batch_sessions_active; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_sessions_active ON public.batch_picking_sessions USING btree (status, assigned_to) WHERE ((status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Paused'::character varying)::text, ('Created'::character varying)::text]));


--
-- Name: idx_batch_sessions_assigned; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_sessions_assigned ON public.batch_picking_sessions USING btree (assigned_to, status);


--
-- Name: idx_batch_sessions_assigned_to; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_sessions_assigned_to ON public.batch_picking_sessions USING btree (assigned_to);


--
-- Name: idx_batch_sessions_deleted_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_sessions_deleted_at ON public.batch_picking_sessions USING btree (deleted_at);


--
-- Name: idx_batch_sessions_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_sessions_status ON public.batch_picking_sessions USING btree (status);


--
-- Name: idx_batch_sessions_status_created; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_batch_sessions_status_created ON public.batch_picking_sessions USING btree (status, created_at);


--
-- Name: idx_invoice_items_batch_lock; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_batch_lock ON public.invoice_items USING btree (locked_by_batch_id);


--
-- Name: idx_invoice_items_batch_zone; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_batch_zone ON public.invoice_items USING btree (zone, corridor, locked_by_batch_id);


--
-- Name: idx_invoice_items_corridor; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_corridor ON public.invoice_items USING btree (corridor);


--
-- Name: idx_invoice_items_invoice_no; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_invoice_no ON public.invoice_items USING btree (invoice_no);


--
-- Name: idx_invoice_items_invoice_picked; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_invoice_picked ON public.invoice_items USING btree (invoice_no, is_picked);


--
-- Name: idx_invoice_items_invoice_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_invoice_status ON public.invoice_items USING btree (invoice_no, pick_status);


--
-- Name: idx_invoice_items_is_picked; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_is_picked ON public.invoice_items USING btree (is_picked);


--
-- Name: idx_invoice_items_location; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_location ON public.invoice_items USING btree (zone, corridor, location);


--
-- Name: idx_invoice_items_location_sort; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_location_sort ON public.invoice_items USING btree (invoice_no, zone, corridor, location);


--
-- Name: idx_invoice_items_pick_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_pick_status ON public.invoice_items USING btree (pick_status);


--
-- Name: idx_invoice_items_picked; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_picked ON public.invoice_items USING btree (is_picked, picked_qty);


--
-- Name: idx_invoice_items_picking_performance; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_picking_performance ON public.invoice_items USING btree (invoice_no, is_picked, pick_status, locked_by_batch_id);


--
-- Name: idx_invoice_items_zone; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_zone ON public.invoice_items USING btree (zone);


--
-- Name: idx_invoice_items_zone_corridor; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoice_items_zone_corridor ON public.invoice_items USING btree (zone, corridor);


--
-- Name: idx_invoices_assigned_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_assigned_status ON public.invoices USING btree (assigned_to, status);


--
-- Name: idx_invoices_assigned_to; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_assigned_to ON public.invoices USING btree (assigned_to);


--
-- Name: idx_invoices_customer_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_customer_code_365 ON public.invoices USING btree (customer_code_365);


--
-- Name: idx_invoices_deleted_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_deleted_at ON public.invoices USING btree (deleted_at);


--
-- Name: idx_invoices_route_lookup; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_route_lookup ON public.invoices USING btree (route_id, stop_id, status) WHERE (route_id IS NOT NULL);


--
-- Name: idx_invoices_route_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_route_status ON public.invoices USING btree (route_id, status, status_updated_at DESC);


--
-- Name: idx_invoices_routing; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_routing ON public.invoices USING btree (routing);


--
-- Name: idx_invoices_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_status ON public.invoices USING btree (status);


--
-- Name: idx_invoices_status_assigned; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_status_assigned ON public.invoices USING btree (status, assigned_to);


--
-- Name: idx_invoices_status_dates; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_status_dates ON public.invoices USING btree (status, delivered_at, shipped_at) WHERE ((status)::text = ANY (ARRAY[('shipped'::character varying)::text, ('delivered'::character varying)::text, ('delivery_failed'::character varying)::text, ('returned_to_warehouse'::character varying)::text, ('cancelled'::character varying)::text]));


--
-- Name: idx_invoices_status_routing; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_status_routing ON public.invoices USING btree (status, routing);


--
-- Name: idx_invoices_status_updated; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_invoices_status_updated ON public.invoices USING btree (status, status_updated_at);


--
-- Name: idx_ipdc_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_ipdc_status ON public.invoice_post_delivery_cases USING btree (status);


--
-- Name: idx_irh_invoice; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_irh_invoice ON public.invoice_route_history USING btree (invoice_no, created_at DESC);


--
-- Name: idx_item_time_tracking_completed; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_item_time_tracking_completed ON public.item_time_tracking USING btree (item_completed) WHERE (item_completed IS NOT NULL);


--
-- Name: idx_item_time_tracking_invoice_started; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_item_time_tracking_invoice_started ON public.item_time_tracking USING btree (invoice_no, item_started);


--
-- Name: idx_items_batch_eligible; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_items_batch_eligible ON public.invoice_items USING btree (zone, corridor, is_picked, pick_status) WHERE ((is_picked = false) AND ((pick_status)::text = ANY (ARRAY[('not_picked'::character varying)::text, ('reset'::character varying)::text, ('skipped_pending'::character varying)::text])));


--
-- Name: idx_items_batch_locking; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_items_batch_locking ON public.invoice_items USING btree (zone, corridor, is_picked, pick_status, locked_by_batch_id, unit_type) WHERE ((is_picked = false) AND ((pick_status)::text = ANY (ARRAY[('not_picked'::character varying)::text, ('reset'::character varying)::text, ('skipped_pending'::character varying)::text])));


--
-- Name: idx_items_corridor_zone; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_items_corridor_zone ON public.invoice_items USING btree (corridor, zone);


--
-- Name: idx_items_invoice_picked_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_items_invoice_picked_status ON public.invoice_items USING btree (invoice_no, is_picked, pick_status);


--
-- Name: idx_items_zone_status_picked; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_items_zone_status_picked ON public.invoice_items USING btree (zone, pick_status, is_picked);


--
-- Name: idx_picking_exceptions_invoice; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_picking_exceptions_invoice ON public.picking_exceptions USING btree (invoice_no);


--
-- Name: idx_picking_exceptions_invoice_no; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_picking_exceptions_invoice_no ON public.picking_exceptions USING btree (invoice_no);


--
-- Name: idx_ps_customers_deleted_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_ps_customers_deleted_at ON public.ps_customers USING btree (deleted_at);


--
-- Name: idx_ps_customers_is_active; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_ps_customers_is_active ON public.ps_customers USING btree (is_active);


--
-- Name: idx_purchase_order_lines_line_id_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_purchase_order_lines_line_id_365 ON public.purchase_order_lines USING btree (line_id_365);


--
-- Name: idx_purchase_orders_deleted_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_purchase_orders_deleted_at ON public.purchase_orders USING btree (deleted_at);


--
-- Name: idx_purchase_orders_is_archived; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_purchase_orders_is_archived ON public.purchase_orders USING btree (is_archived);


--
-- Name: idx_route_stop_deleted_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_route_stop_deleted_at ON public.route_stop USING btree (deleted_at);


--
-- Name: idx_route_stop_shipment; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_route_stop_shipment ON public.route_stop USING btree (shipment_id);


--
-- Name: idx_route_stop_shipment_seq; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_route_stop_shipment_seq ON public.route_stop USING btree (shipment_id, seq_no);


--
-- Name: idx_rr_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_rr_status ON public.reroute_requests USING btree (status);


--
-- Name: idx_rsi_invoice; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_rsi_invoice ON public.route_stop_invoice USING btree (invoice_no);


--
-- Name: idx_rsi_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_rsi_status ON public.route_stop_invoice USING btree (status);


--
-- Name: idx_rsi_stop; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_rsi_stop ON public.route_stop_invoice USING btree (route_stop_id);


--
-- Name: idx_shipments_deleted_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_shipments_deleted_at ON public.shipments USING btree (deleted_at);


--
-- Name: idx_shipments_driver_status; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_shipments_driver_status ON public.shipments USING btree (driver_name, status, updated_at DESC);


--
-- Name: idx_time_tracking_reporting; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_time_tracking_reporting ON public.item_time_tracking USING btree (invoice_no, item_started, picker_username) WHERE (item_completed IS NOT NULL);


--
-- Name: idx_users_is_active; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_users_is_active ON public.users USING btree (is_active);


--
-- Name: idx_wms_dynamic_rules_active_target; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX idx_wms_dynamic_rules_active_target ON public.wms_dynamic_rules USING btree (is_active, target_attr, priority);


--
-- Name: ix_credit_terms_customer_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_credit_terms_customer_code ON public.credit_terms USING btree (customer_code);


--
-- Name: ix_dw_category_penetration_category_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_category_penetration_category_code ON public.dw_category_penetration USING btree (category_code);


--
-- Name: ix_dw_category_penetration_customer_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_category_penetration_customer_code_365 ON public.dw_category_penetration USING btree (customer_code_365);


--
-- Name: ix_dw_churn_risk_category_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_churn_risk_category_code ON public.dw_churn_risk USING btree (category_code);


--
-- Name: ix_dw_churn_risk_customer_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_churn_risk_customer_code_365 ON public.dw_churn_risk USING btree (customer_code_365);


--
-- Name: ix_dw_invoice_header_customer_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_invoice_header_customer_code_365 ON public.dw_invoice_header USING btree (customer_code_365);


--
-- Name: ix_dw_invoice_header_store_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_invoice_header_store_code_365 ON public.dw_invoice_header USING btree (store_code_365);


--
-- Name: ix_dw_invoice_line_invoice_no_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_invoice_line_invoice_no_365 ON public.dw_invoice_line USING btree (invoice_no_365);


--
-- Name: ix_dw_invoice_line_item_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_invoice_line_item_code_365 ON public.dw_invoice_line USING btree (item_code_365);


--
-- Name: ix_dw_invoice_line_line_number; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_invoice_line_line_number ON public.dw_invoice_line USING btree (line_number);


--
-- Name: ix_dw_reco_basket_from_item_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_reco_basket_from_item_code ON public.dw_reco_basket USING btree (from_item_code);


--
-- Name: ix_dw_reco_basket_to_item_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_dw_reco_basket_to_item_code ON public.dw_reco_basket USING btree (to_item_code);


--
-- Name: ix_oi_estimate_lines_invoice_no; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_oi_estimate_lines_invoice_no ON public.oi_estimate_lines USING btree (invoice_no);


--
-- Name: ix_oi_estimate_lines_item_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_oi_estimate_lines_item_code ON public.oi_estimate_lines USING btree (item_code);


--
-- Name: ix_oi_estimate_lines_run_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_oi_estimate_lines_run_id ON public.oi_estimate_lines USING btree (run_id);


--
-- Name: ix_oi_estimate_runs_invoice_no; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_oi_estimate_runs_invoice_no ON public.oi_estimate_runs USING btree (invoice_no);


--
-- Name: ix_pallet_order_pallet_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_pallet_order_pallet_id ON public.wms_pallet_order USING btree (pallet_id);


--
-- Name: ix_payment_customers_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_payment_customers_code ON public.payment_customers USING btree (code);


--
-- Name: ix_payment_customers_group; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_payment_customers_group ON public.payment_customers USING btree ("group");


--
-- Name: ix_ps365_reserved_stock_777_synced_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_ps365_reserved_stock_777_synced_at ON public.ps365_reserved_stock_777 USING btree (synced_at);


--
-- Name: ix_purchase_order_lines_item_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_purchase_order_lines_item_code_365 ON public.purchase_order_lines USING btree (item_code_365);


--
-- Name: ix_purchase_orders_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_purchase_orders_code_365 ON public.purchase_orders USING btree (code_365);


--
-- Name: ix_purchase_orders_shopping_cart_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_purchase_orders_shopping_cart_code ON public.purchase_orders USING btree (shopping_cart_code);


--
-- Name: ix_receipt_log_customer_code_365; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_receipt_log_customer_code_365 ON public.receipt_log USING btree (customer_code_365);


--
-- Name: ix_receipt_log_reference_number; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_receipt_log_reference_number ON public.receipt_log USING btree (reference_number);


--
-- Name: ix_receiving_sessions_receipt_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_receiving_sessions_receipt_code ON public.receiving_sessions USING btree (receipt_code);


--
-- Name: ix_stock_positions_imported_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_stock_positions_imported_at ON public.stock_positions USING btree (imported_at);


--
-- Name: ix_stock_positions_item_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_stock_positions_item_code ON public.stock_positions USING btree (item_code);


--
-- Name: ix_stock_positions_store_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_stock_positions_store_code ON public.stock_positions USING btree (store_code);


--
-- Name: ix_stock_positions_store_name; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_stock_positions_store_name ON public.stock_positions USING btree (store_name);


--
-- Name: uq_invoice_items_invoice_no_item_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX uq_invoice_items_invoice_no_item_code ON public.invoice_items USING btree (invoice_no, item_code);


--
-- Name: uq_ipdc_invoice_open; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX uq_ipdc_invoice_open ON public.invoice_post_delivery_cases USING btree (invoice_no) WHERE ((status)::text = ANY (ARRAY[('OPEN'::character varying)::text, ('INTAKE_RECEIVED'::character varying)::text, ('REROUTE_QUEUED'::character varying)::text]));


--
-- Name: activity_logs activity_logs_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT activity_logs_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: activity_logs activity_logs_picker_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.activity_logs
    ADD CONSTRAINT activity_logs_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES public.users(username);


--
-- Name: batch_picked_items batch_picked_items_batch_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picked_items
    ADD CONSTRAINT batch_picked_items_batch_session_id_fkey FOREIGN KEY (batch_session_id) REFERENCES public.batch_picking_sessions(id);


--
-- Name: batch_picked_items batch_picked_items_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picked_items
    ADD CONSTRAINT batch_picked_items_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: batch_picking_sessions batch_picking_sessions_assigned_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picking_sessions
    ADD CONSTRAINT batch_picking_sessions_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.users(username);


--
-- Name: batch_picking_sessions batch_picking_sessions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_picking_sessions
    ADD CONSTRAINT batch_picking_sessions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(username);


--
-- Name: batch_session_invoices batch_session_invoices_batch_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_session_invoices
    ADD CONSTRAINT batch_session_invoices_batch_session_id_fkey FOREIGN KEY (batch_session_id) REFERENCES public.batch_picking_sessions(id);


--
-- Name: batch_session_invoices batch_session_invoices_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.batch_session_invoices
    ADD CONSTRAINT batch_session_invoices_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: cod_receipts cod_receipts_driver_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.cod_receipts
    ADD CONSTRAINT cod_receipts_driver_username_fkey FOREIGN KEY (driver_username) REFERENCES public.users(username);


--
-- Name: cod_receipts cod_receipts_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.cod_receipts
    ADD CONSTRAINT cod_receipts_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id);


--
-- Name: cod_receipts cod_receipts_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.cod_receipts
    ADD CONSTRAINT cod_receipts_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id);


--
-- Name: credit_terms credit_terms_customer_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.credit_terms
    ADD CONSTRAINT credit_terms_customer_code_fkey FOREIGN KEY (customer_code) REFERENCES public.payment_customers(code);


--
-- Name: delivery_discrepancies delivery_discrepancies_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancies
    ADD CONSTRAINT delivery_discrepancies_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: delivery_discrepancies delivery_discrepancies_reported_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancies
    ADD CONSTRAINT delivery_discrepancies_reported_by_fkey FOREIGN KEY (reported_by) REFERENCES public.users(username);


--
-- Name: delivery_discrepancies delivery_discrepancies_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancies
    ADD CONSTRAINT delivery_discrepancies_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.users(username);


--
-- Name: delivery_discrepancies delivery_discrepancies_validated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancies
    ADD CONSTRAINT delivery_discrepancies_validated_by_fkey FOREIGN KEY (validated_by) REFERENCES public.users(username);


--
-- Name: delivery_discrepancy_events delivery_discrepancy_events_actor_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancy_events
    ADD CONSTRAINT delivery_discrepancy_events_actor_fkey FOREIGN KEY (actor) REFERENCES public.users(username);


--
-- Name: delivery_discrepancy_events delivery_discrepancy_events_discrepancy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_discrepancy_events
    ADD CONSTRAINT delivery_discrepancy_events_discrepancy_id_fkey FOREIGN KEY (discrepancy_id) REFERENCES public.delivery_discrepancies(id);


--
-- Name: delivery_events delivery_events_actor_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_events
    ADD CONSTRAINT delivery_events_actor_fkey FOREIGN KEY (actor) REFERENCES public.users(username);


--
-- Name: delivery_events delivery_events_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_events
    ADD CONSTRAINT delivery_events_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: delivery_lines delivery_lines_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_lines
    ADD CONSTRAINT delivery_lines_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: delivery_lines delivery_lines_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_lines
    ADD CONSTRAINT delivery_lines_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id);


--
-- Name: delivery_lines delivery_lines_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.delivery_lines
    ADD CONSTRAINT delivery_lines_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id);


--
-- Name: dw_invoice_line dw_invoice_line_invoice_no_365_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.dw_invoice_line
    ADD CONSTRAINT dw_invoice_line_invoice_no_365_fkey FOREIGN KEY (invoice_no_365) REFERENCES public.dw_invoice_header(invoice_no_365);


--
-- Name: invoices fk_invoices_shipped_by; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT fk_invoices_shipped_by FOREIGN KEY (shipped_by) REFERENCES public.users(username);


--
-- Name: invoice_items fk_locked_by_batch_id; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT fk_locked_by_batch_id FOREIGN KEY (locked_by_batch_id) REFERENCES public.batch_picking_sessions(id) ON DELETE SET NULL;


--
-- Name: idle_periods idle_periods_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.idle_periods
    ADD CONSTRAINT idle_periods_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.shifts(id);


--
-- Name: invoice_delivery_events invoice_delivery_events_actor_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_delivery_events
    ADD CONSTRAINT invoice_delivery_events_actor_fkey FOREIGN KEY (actor) REFERENCES public.users(username);


--
-- Name: invoice_delivery_events invoice_delivery_events_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_delivery_events
    ADD CONSTRAINT invoice_delivery_events_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: invoice_items invoice_items_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: invoice_post_delivery_cases invoice_post_delivery_cases_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_post_delivery_cases
    ADD CONSTRAINT invoice_post_delivery_cases_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no) ON DELETE CASCADE;


--
-- Name: invoice_post_delivery_cases invoice_post_delivery_cases_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_post_delivery_cases
    ADD CONSTRAINT invoice_post_delivery_cases_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id) ON DELETE SET NULL;


--
-- Name: invoice_post_delivery_cases invoice_post_delivery_cases_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_post_delivery_cases
    ADD CONSTRAINT invoice_post_delivery_cases_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id) ON DELETE SET NULL;


--
-- Name: invoice_route_history invoice_route_history_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_route_history
    ADD CONSTRAINT invoice_route_history_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no) ON DELETE CASCADE;


--
-- Name: invoice_route_history invoice_route_history_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_route_history
    ADD CONSTRAINT invoice_route_history_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id) ON DELETE SET NULL;


--
-- Name: invoice_route_history invoice_route_history_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoice_route_history
    ADD CONSTRAINT invoice_route_history_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id) ON DELETE SET NULL;


--
-- Name: invoices invoices_assigned_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.users(username);


--
-- Name: invoices invoices_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id);


--
-- Name: invoices invoices_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_stop_id_fkey FOREIGN KEY (stop_id) REFERENCES public.route_stop(route_stop_id);


--
-- Name: item_time_tracking item_time_tracking_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.item_time_tracking
    ADD CONSTRAINT item_time_tracking_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: item_time_tracking item_time_tracking_picker_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.item_time_tracking
    ADD CONSTRAINT item_time_tracking_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES public.users(username);


--
-- Name: oi_estimate_lines oi_estimate_lines_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.oi_estimate_lines
    ADD CONSTRAINT oi_estimate_lines_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.oi_estimate_runs(id) ON DELETE CASCADE;


--
-- Name: oi_estimate_runs oi_estimate_runs_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.oi_estimate_runs
    ADD CONSTRAINT oi_estimate_runs_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: order_time_breakdown order_time_breakdown_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.order_time_breakdown
    ADD CONSTRAINT order_time_breakdown_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: order_time_breakdown order_time_breakdown_picker_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.order_time_breakdown
    ADD CONSTRAINT order_time_breakdown_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES public.users(username);


--
-- Name: picking_exceptions picking_exceptions_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.picking_exceptions
    ADD CONSTRAINT picking_exceptions_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: picking_exceptions picking_exceptions_picker_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.picking_exceptions
    ADD CONSTRAINT picking_exceptions_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES public.users(username);


--
-- Name: pod_records pod_records_collected_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.pod_records
    ADD CONSTRAINT pod_records_collected_by_fkey FOREIGN KEY (collected_by) REFERENCES public.users(username);


--
-- Name: pod_records pod_records_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.pod_records
    ADD CONSTRAINT pod_records_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id);


--
-- Name: pod_records pod_records_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.pod_records
    ADD CONSTRAINT pod_records_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id);


--
-- Name: purchase_order_lines purchase_order_lines_purchase_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT purchase_order_lines_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_archived_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_archived_by_fkey FOREIGN KEY (archived_by) REFERENCES public.users(username);


--
-- Name: purchase_orders purchase_orders_downloaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_downloaded_by_fkey FOREIGN KEY (downloaded_by) REFERENCES public.users(username);


--
-- Name: receipt_log receipt_log_driver_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_log
    ADD CONSTRAINT receipt_log_driver_username_fkey FOREIGN KEY (driver_username) REFERENCES public.users(username);


--
-- Name: receipt_log receipt_log_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receipt_log
    ADD CONSTRAINT receipt_log_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id);


--
-- Name: receiving_lines receiving_lines_po_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_lines
    ADD CONSTRAINT receiving_lines_po_line_id_fkey FOREIGN KEY (po_line_id) REFERENCES public.purchase_order_lines(id) ON DELETE CASCADE;


--
-- Name: receiving_lines receiving_lines_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_lines
    ADD CONSTRAINT receiving_lines_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.receiving_sessions(id) ON DELETE CASCADE;


--
-- Name: receiving_sessions receiving_sessions_operator_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_sessions
    ADD CONSTRAINT receiving_sessions_operator_fkey FOREIGN KEY (operator) REFERENCES public.users(username);


--
-- Name: receiving_sessions receiving_sessions_purchase_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.receiving_sessions
    ADD CONSTRAINT receiving_sessions_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: reroute_requests reroute_requests_assigned_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.reroute_requests
    ADD CONSTRAINT reroute_requests_assigned_route_id_fkey FOREIGN KEY (assigned_route_id) REFERENCES public.shipments(id) ON DELETE SET NULL;


--
-- Name: reroute_requests reroute_requests_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.reroute_requests
    ADD CONSTRAINT reroute_requests_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no) ON DELETE CASCADE;


--
-- Name: route_delivery_events route_delivery_events_actor_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_delivery_events
    ADD CONSTRAINT route_delivery_events_actor_username_fkey FOREIGN KEY (actor_username) REFERENCES public.users(username);


--
-- Name: route_delivery_events route_delivery_events_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_delivery_events
    ADD CONSTRAINT route_delivery_events_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.shipments(id);


--
-- Name: route_delivery_events route_delivery_events_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_delivery_events
    ADD CONSTRAINT route_delivery_events_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id);


--
-- Name: route_stop_invoice route_stop_invoice_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop_invoice
    ADD CONSTRAINT route_stop_invoice_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no) ON DELETE RESTRICT;


--
-- Name: route_stop_invoice route_stop_invoice_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop_invoice
    ADD CONSTRAINT route_stop_invoice_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stop(route_stop_id) ON DELETE CASCADE;


--
-- Name: route_stop route_stop_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.route_stop
    ADD CONSTRAINT route_stop_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: shifts shifts_adjustment_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_adjustment_by_fkey FOREIGN KEY (adjustment_by) REFERENCES public.users(username);


--
-- Name: shifts shifts_picker_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES public.users(username);


--
-- Name: shipment_orders shipment_orders_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipment_orders
    ADD CONSTRAINT shipment_orders_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: shipment_orders shipment_orders_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipment_orders
    ADD CONSTRAINT shipment_orders_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id);


--
-- Name: shipping_events shipping_events_actor_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipping_events
    ADD CONSTRAINT shipping_events_actor_fkey FOREIGN KEY (actor) REFERENCES public.users(username);


--
-- Name: shipping_events shipping_events_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.shipping_events
    ADD CONSTRAINT shipping_events_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: time_tracking_alerts time_tracking_alerts_invoice_no_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.time_tracking_alerts
    ADD CONSTRAINT time_tracking_alerts_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES public.invoices(invoice_no);


--
-- Name: time_tracking_alerts time_tracking_alerts_picker_username_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.time_tracking_alerts
    ADD CONSTRAINT time_tracking_alerts_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES public.users(username);


--
-- Name: time_tracking_alerts time_tracking_alerts_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.time_tracking_alerts
    ADD CONSTRAINT time_tracking_alerts_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.users(username);


--
-- Name: wms_pallet_order wms_pallet_order_pallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet_order
    ADD CONSTRAINT wms_pallet_order_pallet_id_fkey FOREIGN KEY (pallet_id) REFERENCES public.wms_pallet(pallet_id) ON DELETE CASCADE;


--
-- Name: wms_pallet wms_pallet_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.wms_pallet
    ADD CONSTRAINT wms_pallet_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: cloud_admin
--

ALTER DEFAULT PRIVILEGES FOR ROLE cloud_admin IN SCHEMA public GRANT ALL ON SEQUENCES TO neon_superuser WITH GRANT OPTION;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: cloud_admin
--

ALTER DEFAULT PRIVILEGES FOR ROLE cloud_admin IN SCHEMA public GRANT ALL ON TABLES TO neon_superuser WITH GRANT OPTION;


--
-- PostgreSQL database dump complete
--

