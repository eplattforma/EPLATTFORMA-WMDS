import os
os.environ['TZ'] = 'Europe/Athens'
import logging
logging.basicConfig(level=logging.INFO)
logging.warning("PHASE 1: top of main.py")
print("PHASE 1: top of main.py", flush=True)

try:
    from app import app, db, is_production, run_deferred_db_init
    logging.warning("PHASE 2: imported app, db")
    print("PHASE 2: imported app, db", flush=True)
except Exception:
    logging.exception("FAILED DURING: from app import app, db")
    raise

db_uri = app.config.get('SQLALCHEMY_DATABASE_URI') or ''
if db_uri and '@' in db_uri:
    left, right = db_uri.split('@', 1)
    db_uri = left.split('://', 1)[0] + '://***:***@' + right
logging.warning(f"DATABASE_URL present: {bool(os.getenv('DATABASE_URL'))}")
logging.warning(f"DB URI: {db_uri}")

from sqlalchemy import text as _sa_text

_db_available = False
with app.app_context():
    try:
        result = db.session.execute(_sa_text("SELECT 1")).scalar()
        logging.warning(f"DB smoke test result: {result}")
        print(f"DB smoke test result: {result}", flush=True)
        _db_available = True
    except Exception:
        logging.exception("DB SMOKE TEST FAILED - DB is unreachable, app will start without DB-dependent init")
        try:
            db.session.rollback()
        except Exception:
            pass

if _db_available:
    logging.warning("PHASE 3: DB smoke test passed")
    print("PHASE 3: DB smoke test passed", flush=True)

    try:
        from update_forecast_ordering_schema import update_forecast_ordering_schema
        logging.warning("Running forecast ordering schema updater...")
        update_forecast_ordering_schema()
        logging.warning("Forecast ordering schema updater completed")
        print("Forecast ordering schema updater completed", flush=True)
    except Exception:
        logging.exception("Forecast ordering schema updater failed (non-fatal)")

    try:
        from update_receiving_desktop_schema import update_receiving_desktop_schema
        update_receiving_desktop_schema()
    except Exception:
        logging.exception("Receiving desktop schema updater failed (non-fatal)")

    try:
        from update_forecast_profile_baseline_source_schema import update_forecast_profile_baseline_source_schema
        update_forecast_profile_baseline_source_schema()
        logging.warning("Forecast profile schema updater completed")
        print("Forecast profile schema updater completed", flush=True)
    except Exception:
        logging.exception("Forecast profile baseline_source schema updater FAILED")
        raise

    with app.app_context():
        with db.engine.connect() as conn:
            _vrows = conn.execute(_sa_text("""
                SELECT column_name, character_maximum_length
                FROM information_schema.columns
                WHERE table_name = 'sku_forecast_profile'
                  AND column_name IN (
                    'forecast_method',
                    'seasonality_source',
                    'seed_source',
                    'analogue_level',
                    'baseline_source'
                  )
            """)).fetchall()
            _vlengths = {r[0]: r[1] for r in _vrows}
            for _vcol in ['forecast_method','seasonality_source','seed_source','analogue_level','baseline_source']:
                if (_vlengths.get(_vcol) or 0) < 128:
                    raise RuntimeError(f"Forecast profile column {_vcol} is too small: {_vlengths.get(_vcol)}")
    logging.warning("Forecast profile schema validation passed")
    print("Forecast profile schema validation passed", flush=True)

    try:
        with app.app_context():
            with db.engine.connect() as conn:
                cols = conn.execute(_sa_text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'sku_forecast_profile'
                      AND column_name IN (
                        'target_weeks_of_stock',
                        'target_weeks_updated_at',
                        'target_weeks_updated_by',
                        'seeded_cap_applied'
                      )
                    ORDER BY column_name
                """)).fetchall()
                logging.warning(f"sku_forecast_profile forecast columns found: {[r[0] for r in cols]}")
                tbl = conn.execute(_sa_text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_name = 'sku_ordering_snapshot'
                """)).fetchall()
                logging.warning(f"sku_ordering_snapshot table found: {bool(tbl)}")
    except Exception:
        logging.exception("Schema verification failed (non-fatal)")

    logging.warning("PHASE 4: schema updater and verification done")
    print("PHASE 4: schema updater and verification done", flush=True)
else:
    logging.warning("PHASE 3-4: SKIPPED (DB unavailable)")
    print("PHASE 3-4: SKIPPED (DB unavailable)", flush=True)

DEFAULT_TERMS_CODE = os.getenv("DEFAULT_TERMS_CODE", "POD")
DEFAULT_DUE_DAYS = int(os.getenv("DEFAULT_DUE_DAYS", "0"))
DEFAULT_IS_CREDIT = os.getenv("DEFAULT_IS_CREDIT", "false").lower() in ("1","true","yes","y")

DEFAULT_ALLOW_CASH = os.getenv("DEFAULT_ALLOW_CASH", "true").lower() in ("1","true","yes","y")
DEFAULT_ALLOW_CARD_POS = os.getenv("DEFAULT_ALLOW_CARD_POS", "true").lower() in ("1","true","yes","y")
DEFAULT_ALLOW_BANK_TRANSFER = os.getenv("DEFAULT_ALLOW_BANK_TRANSFER", "true").lower() in ("1","true","yes","y")
DEFAULT_ALLOW_CHEQUE = os.getenv("DEFAULT_ALLOW_CHEQUE", "false").lower() in ("1","true","yes","y")

DEFAULT_CHEQUE_DAYS_ALLOWED = os.getenv("DEFAULT_CHEQUE_DAYS_ALLOWED")
DEFAULT_CREDIT_LIMIT = os.getenv("DEFAULT_CREDIT_LIMIT")
import routes  # noqa: F401
import routes_ai_analysis  # noqa: F401
import routes_operations  # noqa: F401
import routes_daily_reports  # noqa: F401
import routes_oi  # noqa: F401
import routes_time_analysis  # noqa: F401
import pytz
from timezone_utils import get_utc_now
from datetime import datetime, timezone, timedelta
import flask_login

app.config.update({
    'DEBUG': not is_production,
    'SESSION_COOKIE_HTTPONLY': True,
    'PERMANENT_SESSION_LIFETIME': timedelta(hours=1),
    'JSON_SORT_KEYS': False,
    'JSONIFY_PRETTYPRINT_REGULAR': False,
})

@app.template_filter('local_time')
def local_time_filter(dt, format_str='%d/%m/%y %H:%M'):
    if dt is None:
        return 'N/A'
    athens_tz = pytz.timezone('Europe/Athens')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    athens_dt = dt.astimezone(athens_tz)
    return athens_dt.strftime(format_str)

@app.template_filter('current_athens_time')
def current_athens_time_filter(placeholder, format_str='%d/%m/%y %H:%M:%S'):
    athens_tz = pytz.timezone('Europe/Athens')
    utc_now = get_utc_now()
    athens_now = utc_now.astimezone(athens_tz)
    return athens_now.strftime(format_str)

@app.template_filter('status_badge')
def status_badge_filter(status_value):
    from order_status_constants import get_status_info, get_status_badge_class, get_status_icon
    status_info = get_status_info(status_value)
    if not status_info:
        return f'<span class="badge bg-secondary">Unknown Status</span>'
    badge_class = get_status_badge_class(status_value)
    icon = get_status_icon(status_value)
    label = status_info['label']
    return f'<span class="badge {badge_class}"><i class="{icon} me-1"></i>{label}</span>'

from update_schema_skipped_items import update_database_schema
from routes_batch import batch_bp
from routes_help import help_bp
from routes_delivery_issues import delivery_issues_bp
from routes_routes import bp as routes_bp
from routes_invoices import bp as route_invoices_bp
from routes_powersoft import bp_powersoft
from routes_delivery_dashboard import bp as delivery_dashboard_bp
from routes_driver_api import driver_api_bp
from routes_receipts import bp as receipts_bp
from routes_find_invoice import bp as find_invoice_bp

app.register_blueprint(batch_bp, url_prefix='')
app.register_blueprint(delivery_issues_bp, url_prefix='')
app.register_blueprint(routes_bp, url_prefix='/routes')
app.register_blueprint(route_invoices_bp, url_prefix='/route-invoices')
app.register_blueprint(delivery_dashboard_bp, url_prefix='')
app.register_blueprint(bp_powersoft)
app.register_blueprint(help_bp, url_prefix='')
app.register_blueprint(driver_api_bp)
app.register_blueprint(receipts_bp)
app.register_blueprint(find_invoice_bp)

from routes_payment_terms import bp as payment_terms_bp
app.register_blueprint(payment_terms_bp)

from routes_driver import driver_bp
app.register_blueprint(driver_bp)

from routes_payments import payments_bp
app.register_blueprint(payments_bp)

from routes_warehouse_intake import warehouse_bp
app.register_blueprint(warehouse_bp)

from routes_po_receiving import po_receiving_bp
app.register_blueprint(po_receiving_bp)

from routes_item_scanner import item_scanner_bp
app.register_blueprint(item_scanner_bp)

from routes_oi_time_admin import oi_time_admin_bp
app.register_blueprint(oi_time_admin_bp)

from routes_oi_reports import oi_reports_bp
app.register_blueprint(oi_reports_bp)

from routes_pallets import bp as pallets_bp
app.register_blueprint(pallets_bp, url_prefix='/routes')

try:
    from routes_admin_tools import bp as admin_tools_bp
    app.register_blueprint(admin_tools_bp)
except ValueError:
    logging.info("Admin Tools blueprint already registered")

from routes_reconciliation import reconciliation_bp
app.register_blueprint(reconciliation_bp)

from routes_customer_analytics import customer_analytics_bp
app.register_blueprint(customer_analytics_bp)

from routes_pricing_analytics import pricing_bp
app.register_blueprint(pricing_bp)

from blueprints.peer_analytics import peer_bp
app.register_blueprint(peer_bp)

from blueprints.category_manager import catmgr_bp
app.register_blueprint(catmgr_bp)

from routes_customer_reporting_groups import crg_bp
app.register_blueprint(crg_bp)

from routes_customer_benchmark import benchmark_bp
app.register_blueprint(benchmark_bp)

from routes_ai_feedback import ai_feedback_bp
app.register_blueprint(ai_feedback_bp)

from blueprints.sms import sms_bp
app.register_blueprint(sms_bp)

from blueprints.communications import communications_bp
app.register_blueprint(communications_bp)

from blueprints.replenishment_mvp import replenishment_bp
app.register_blueprint(replenishment_bp)

from blueprints.forecast_workbench import forecast_bp
app.register_blueprint(forecast_bp)

from blueprints.abandoned_carts import abandoned_bp
app.register_blueprint(abandoned_bp)

from blueprints.magento_api import magento_api_bp
app.register_blueprint(magento_api_bp)

from routes_crm_dashboard import crm_dashboard_bp
app.register_blueprint(crm_dashboard_bp)

from routes_erp_bot import erp_bot_bp
app.register_blueprint(erp_bot_bp)

logging.warning("PHASE 5: all blueprints registered")
print("PHASE 5: all blueprints registered", flush=True)

import datetime as dt
from decimal import Decimal

def _default_terms_values_for(code: str):
    credit_limit_val = None
    if DEFAULT_CREDIT_LIMIT not in (None, "", "None"):
        try:
            credit_limit_val = Decimal(DEFAULT_CREDIT_LIMIT)
        except:
            credit_limit_val = None

    cheque_days_val = None
    if DEFAULT_CHEQUE_DAYS_ALLOWED not in (None, "", "None"):
        try:
            cheque_days_val = int(DEFAULT_CHEQUE_DAYS_ALLOWED)
        except:
            cheque_days_val = None

    return {
        "customer_code": code,
        "terms_code": DEFAULT_TERMS_CODE,
        "due_days": DEFAULT_DUE_DAYS,
        "is_credit": DEFAULT_IS_CREDIT,
        "credit_limit": credit_limit_val,
        "allow_cash": DEFAULT_ALLOW_CASH,
        "allow_card_pos": DEFAULT_ALLOW_CARD_POS,
        "allow_bank_transfer": DEFAULT_ALLOW_BANK_TRANSFER,
        "allow_cheque": DEFAULT_ALLOW_CHEQUE,
        "cheque_days_allowed": cheque_days_val,
        "valid_from": dt.date.today(),
        "notes_for_driver": None,
    }

from sqlalchemy import event
from sqlalchemy.sql import text
from models import PaymentCustomer, CreditTerms

@event.listens_for(PaymentCustomer, "after_insert")
def _create_default_terms_after_customer_insert(mapper, connection, target: PaymentCustomer):
    import threading
    skip_auto_create = getattr(threading.current_thread(), 'skip_auto_payment_terms', False)

    if skip_auto_create:
        return

    sql = text("""
        INSERT INTO credit_terms (
            customer_code, terms_code, due_days, is_credit,
            credit_limit, allow_cash, allow_card_pos, allow_bank_transfer, allow_cheque,
            cheque_days_allowed, notes_for_driver, valid_from, valid_to
        )
        SELECT
            :customer_code, :terms_code, :due_days, :is_credit,
            :credit_limit, :allow_cash, :allow_card_pos, :allow_bank_transfer, :allow_cheque,
            :cheque_days_allowed, :notes_for_driver, :valid_from, NULL
        WHERE NOT EXISTS (
            SELECT 1 FROM credit_terms
            WHERE customer_code = :customer_code AND valid_to IS NULL
        )
    """)
    params = _default_terms_values_for(target.code)
    params["customer_code"] = target.code
    connection.execute(sql, params)

if _db_available:
  with app.app_context():
    try:
        update_database_schema()
    except Exception as e:
        logging.error(f"Error updating skip schema: {str(e)}")

    try:
        from update_batch_picking_schema import update_database_schema as update_batch_schema
        update_batch_schema()
    except Exception as e:
        logging.error(f"Error updating batch schema: {str(e)}")

    try:
        from update_batch_number_schema import update_database_schema as update_batch_number_schema
        update_batch_number_schema()
    except Exception as e:
        logging.error(f"Error updating batch number schema: {str(e)}")

    try:
        from update_unit_types_schema import update_unit_types_schema
        update_unit_types_schema()
    except Exception as e:
        logging.error(f"Error updating unit types schema: {str(e)}")

    try:
        from update_item_tracking_schema import update_item_tracking_schema
        update_item_tracking_schema()
    except Exception as e:
        logging.error(f"Error updating item tracking schema: {str(e)}")

    try:
        from update_invoice_status_timestamp import add_status_timestamp_column
        add_status_timestamp_column()
    except Exception as e:
        logging.error(f"Error updating invoice status timestamp schema: {str(e)}")

    try:
        from update_route_stop_schema import update_route_stop_schema
        update_route_stop_schema()
    except Exception as e:
        logging.error(f"Error updating RouteStop schema: {str(e)}")

    try:
        from update_shipment_settlement_schema import update_shipment_settlement_schema
        update_shipment_settlement_schema()
    except Exception as e:
        logging.error(f"Error updating Shipment settlement schema: {str(e)}")

    try:
        from update_warehouse_intake_schema import update_warehouse_intake_schema
        update_warehouse_intake_schema()
    except Exception as e:
        logging.error(f"Error updating warehouse intake schema: {str(e)}")

    try:
        from update_oi_schema import update_oi_schema
        update_oi_schema()
    except Exception as e:
        logging.error(f"Error updating OI schema: {str(e)}")

    try:
        from sqlalchemy import text as sa_text
        from app import db as appdb
        for col, ctype in [("supplier_code_365", "VARCHAR(50)"), ("supplier_name", "VARCHAR(255)")]:
            try:
                appdb.session.execute(sa_text(f"ALTER TABLE ps_items_dw ADD COLUMN IF NOT EXISTS {col} {ctype}"))
            except Exception:
                pass
        appdb.session.execute(sa_text("CREATE INDEX IF NOT EXISTS ix_ps_items_dw_supplier_code ON ps_items_dw (supplier_code_365)"))
        appdb.session.commit()
    except Exception as e:
        logging.error(f"Error updating supplier columns: {str(e)}")

    try:
        from update_packing_profile_schema import update_packing_profile_schema
        update_packing_profile_schema()
    except Exception as e:
        logging.error(f"Error updating WmsPackingProfile schema: {str(e)}")

    try:
        from update_route_reconciliation_schema import update_route_reconciliation_schema
        update_route_reconciliation_schema()
    except Exception as e:
        logging.error(f"Error updating route reconciliation schema: {str(e)}")

    try:
        from update_discrepancy_verification_schema import update_discrepancy_verification_schema
        update_discrepancy_verification_schema()
    except Exception as e:
        logging.error(f"Error updating discrepancy verification schema: {str(e)}")

    try:
        from update_cod_receipts_locking_schema import update_cod_receipts_locking_schema
        update_cod_receipts_locking_schema()
    except Exception as e:
        logging.error(f"Error updating COD receipts locking schema: {str(e)}")

    try:
        from update_payment_entries_schema import update_payment_entries_schema
        update_payment_entries_schema()
    except Exception as e:
        logging.error(f"Error updating payment entries schema: {str(e)}")

    try:
        from update_bank_transactions_schema import update_bank_transactions_schema
        update_bank_transactions_schema()
    except Exception as e:
        logging.error(f"Error updating bank transactions schema: {str(e)}")

    try:
        from update_sms_schema import update_sms_schema
        update_sms_schema()
    except Exception as e:
        logging.error(f"Error updating SMS schema: {str(e)}")

    try:
        from app import db as _db
        from sqlalchemy import text as sa_text
        inspector = _db.inspect(_db.engine)
        dw_cols = [col['name'] for col in inspector.get_columns('ps_items_dw')]
        for col_name, col_def in [
            ('vat_code_365', 'VARCHAR(20)'),
            ('vat_percent', 'NUMERIC(6,2)'),
            ('cost_price', 'NUMERIC(12,4)'),
        ]:
            if col_name not in dw_cols:
                _db.session.execute(sa_text(f'ALTER TABLE ps_items_dw ADD COLUMN {col_name} {col_def}'))
                logging.info(f"Added {col_name} column to ps_items_dw")
        _db.session.commit()
    except Exception as e:
        logging.warning(f"DwItem pricing columns migration: {e}")
        try:
            _db.session.rollback()
        except:
            pass

    try:
        from update_replenishment_schema import update_replenishment_schema
        update_replenishment_schema()
    except Exception as e:
        logging.error(f"Error updating replenishment schema: {str(e)}")

    try:
        from update_forecast_runs_schema import update_forecast_runs_schema
        update_forecast_runs_schema()
    except Exception as e:
        logging.error(f"Error updating forecast runs schema: {str(e)}")

    try:
        from update_magento_login_log_schema import update_magento_login_log_schema
        update_magento_login_log_schema()
    except Exception as e:
        logging.error(f"Error updating Magento login log schema: {e}")

    try:
        from update_magento_last_login_current_schema import update_magento_last_login_current_schema
        update_magento_last_login_current_schema()
    except Exception as e:
        logging.error(f"Error updating Magento last login current schema: {e}")

    try:
        from update_crm_offer_schema import ensure_crm_offer_schema
        ensure_crm_offer_schema()
    except Exception as e:
        logging.error(f"Error updating CRM offer schema: {e}")

    db.create_all()

    try:
        from update_order_status_system import update_order_status_system
        update_order_status_system()
    except Exception as e:
        logging.error(f"Error updating order status system: {str(e)}")

    try:
        from models import Setting
        skip_reasons = Setting.query.filter_by(key='skip_reasons').first()
        if not skip_reasons:
            import json
            default_reasons = ["Out of Stock", "Damaged", "Location Empty", "Other"]
            new_setting = Setting()
            new_setting.key = 'skip_reasons'
            new_setting.value = json.dumps(default_reasons)
            db.session.add(new_setting)
            db.session.commit()
    except Exception as e:
        logging.error(f"Error initializing settings: {str(e)}")
        try:
            db.session.rollback()
        except:
            pass

    try:
        run_deferred_db_init()
    except Exception as e:
        logging.error(f"Error in deferred DB init: {str(e)}")

logging.warning("PHASE 6: all schema updates and DB init done")
print("PHASE 6: all schema updates and DB init done", flush=True)

@app.route('/debug/forecast-schema')
def debug_forecast_schema():
    with db.engine.connect() as conn:
        cols = conn.execute(_sa_text("""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_name IN ('sku_forecast_profile', 'sku_ordering_snapshot')
            ORDER BY table_name, column_name
        """)).fetchall()
    from flask import jsonify
    return jsonify([{"table": r[0], "column": r[1]} for r in cols])

from flask import send_file, abort, flash, redirect, url_for, render_template_string, request
import os as os_module

@app.route('/download-project-export')
def download_project_export():
    file_path = '/home/runner/workspace/warehouse-system-export.zip'
    if os_module.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name='warehouse-system-export.zip')
    else:
        return "Export file not found. Please create it first.", 404

@app.route('/admin/maintenance/dedup-cod', methods=['GET', 'POST'])
@flask_login.login_required
def admin_dedup_cod():
    if flask_login.current_user.role != 'admin':
        abort(403)
    from app import db
    from sqlalchemy import text

    find_sql = text("""
        SELECT c1.id, c1.route_id, c1.route_stop_id, c1.invoice_nos::text,
               c1.received_amount, c1.payment_method, c1.created_at,
               c2.id AS kept_id, c2.created_at AS kept_created_at
        FROM cod_receipts c1
        INNER JOIN cod_receipts c2
            ON c1.route_id = c2.route_id
            AND c1.route_stop_id = c2.route_stop_id
            AND c1.invoice_nos::text = c2.invoice_nos::text
            AND c1.received_amount = c2.received_amount
            AND c1.payment_method = c2.payment_method
            AND c1.id > c2.id
            AND ABS(EXTRACT(EPOCH FROM (c1.created_at - c2.created_at))) < 5
        ORDER BY c1.id
    """)

    if request.method == 'POST':
        result = db.session.execute(find_sql)
        dup_ids = [row[0] for row in result]
        if dup_ids:
            for dup_id in dup_ids:
                db.session.execute(text(
                    "DELETE FROM cod_invoice_allocations WHERE cod_receipt_id = :rid"
                ), {'rid': dup_id})
                db.session.execute(text(
                    "DELETE FROM cod_receipts WHERE id = :rid"
                ), {'rid': dup_id})
            db.session.commit()
            flash(f"Removed {len(dup_ids)} duplicate COD receipts: {dup_ids}", "success")
        else:
            flash("No duplicates found", "info")
        return redirect(url_for('admin_dedup_cod'))

    result = db.session.execute(find_sql)
    duplicates = [dict(row._mapping) for row in result]
    return render_template_string("""
    {% extends "base.html" %}
    {% block content %}
    <div class="container mt-4">
        <h3>COD Receipt Deduplication</h3>
        <p>Found <strong>{{ duplicates|length }}</strong> duplicate COD receipt(s) to remove.</p>
        {% if duplicates %}
        <table class="table table-sm table-dark">
            <tr><th>Dup ID</th><th>Route</th><th>Stop</th><th>Invoices</th><th>Amount</th><th>Method</th><th>Created</th><th>Keeping ID</th></tr>
            {% for d in duplicates %}
            <tr>
                <td>{{ d.id }}</td><td>{{ d.route_id }}</td><td>{{ d.route_stop_id }}</td>
                <td>{{ d.invoice_nos }}</td><td>&euro;{{ "%.2f"|format(d.received_amount) }}</td>
                <td>{{ d.payment_method }}</td><td>{{ d.created_at }}</td><td>{{ d.kept_id }}</td>
            </tr>
            {% endfor %}
        </table>
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button class="btn btn-danger" onclick="return confirm('Delete {{ duplicates|length }} duplicate records?')">Remove Duplicates</button>
        </form>
        {% else %}
        <div class="alert alert-success">No duplicates found. All clean!</div>
        {% endif %}
    </div>
    {% endblock %}
    """, duplicates=duplicates)

logging.warning("PHASE 7: main.py fully loaded")
print("PHASE 7: main.py fully loaded", flush=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
