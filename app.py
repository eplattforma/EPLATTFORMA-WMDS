import os
import logging
import atexit
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
import pytz
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

# Initialize Flask app
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.secret_key = os.environ.get("SESSION_SECRET")
if not app.secret_key:
    raise RuntimeError("SESSION_SECRET environment variable is required and cannot be empty")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # needed for url_for to generate with https

# Database configuration
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Ensure the URL is in the correct format for PostgreSQL
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://')
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # Fallback to SQLite for development
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///picking.db"
    logging.warning("DATABASE_URL not found, using SQLite database")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Detect if running in production
is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"

# Optimize pool size for production concurrency
if is_production:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        'pool_pre_ping': True,
        "pool_recycle": 300,
        "pool_size": 10,         # Larger pool for concurrent requests
        "max_overflow": 5,       # Allow burst capacity
        "pool_timeout": 30,      # Fail fast if no connection available
        "echo": False,
        "connect_args": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=120000 -c lock_timeout=30000"  # 120s statement (for large imports), 30s lock timeout
        }
    }
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        'pool_pre_ping': True,
        "pool_recycle": 300,
        "pool_size": 20,
        "max_overflow": 10,
        "pool_timeout": 30,
        "echo": False,
    }

# Initialize the SQLAlchemy extension
db = SQLAlchemy(model_class=Base)
db.init_app(app)

# Create the upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database initialization for both development and production
with app.app_context():
    try:
        # Import models
        import models  # noqa: F401
        import dw_analytics_models  # noqa: F401
        
        # Register delete guards to prevent data inconsistency
        from delete_guards import register_all_guards
        register_all_guards()
        
        # Create all tables that don't yet exist
        db.create_all()
        logging.info("Database tables created if they didn't exist")
        
        # Run schema updates for new columns
        try:
            from update_invoice_items_schema import update_invoice_items_schema
            update_invoice_items_schema(db)
        except Exception as e:
            logging.warning(f"Could not run invoice items schema update: {str(e)}")
        
        # Only initialize default users and settings in development
        if not is_production:
            from models import User, Setting
            from utils import create_user
            
            # Check if admin user exists
            admin = User.query.filter_by(username='administrator').first()
            if not admin:
                create_user(db.session, 'administrator', 'admin123', 'admin')
                logging.info("Default admin user created")
            
            # Check if picker user exists
            picker = User.query.filter_by(username='picker1').first()
            if not picker:
                create_user(db.session, 'picker1', 'picker123', 'picker')
                logging.info("Default picker user created")
            
            # Initialize default settings
            confirm_setting = Setting.query.filter_by(key='confirm_picking_step').first()
            if not confirm_setting:
                confirm_setting = Setting()
                confirm_setting.key = 'confirm_picking_step'
                confirm_setting.value = 'true'
                db.session.add(confirm_setting)
            
            # Add image display setting
            image_setting = Setting.query.filter_by(key='show_image_on_picking_screen').first()
            if not image_setting:
                image_setting = Setting()
                image_setting.key = 'show_image_on_picking_screen'
                image_setting.value = 'true'
                db.session.add(image_setting)
            
            # Set timezone to Europe/Athens
            timezone_setting = Setting.query.filter_by(key='system_timezone').first()
            if not timezone_setting:
                timezone_setting = Setting()
                timezone_setting.key = 'system_timezone'
                timezone_setting.value = 'Europe/Athens'
                db.session.add(timezone_setting)
                
            db.session.commit()
            logging.info("Settings initialized")
        
        # Seed OI time params (runs in both dev and production)
        from models import Setting
        from services_oi_time_estimator import DEFAULT_PARAMS
        
        if not Setting.query.filter_by(key="oi_time_params_v1").first():
            Setting.set_json(db.session, "oi_time_params_v1", DEFAULT_PARAMS)
            logging.info("Seeded oi_time_params_v1 with defaults")
        
        if not Setting.query.filter_by(key="oi_time_params_v1_revision").first():
            Setting.set(db.session, "oi_time_params_v1_revision", "1")
            logging.info("Seeded oi_time_params_v1_revision = 1")
        
        db.session.commit()
        
    except Exception as e:
        logging.error(f"Error during database initialization: {str(e)}")
        logging.exception("Database initialization error details:")

# Add custom Jinja filters
import json

@app.template_filter('from_json')
def from_json_filter(value):
    """Parse JSON string to Python object"""
    if not value:
        return []
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return []

@app.template_filter('format_seq')
def format_seq_filter(value):
    """Format sequence number: integer if whole, one decimal if not.
    Examples: 1.00 -> 1, 1.50 -> 1.5, 2.10 -> 2.1"""
    if value is None:
        return ''
    try:
        from decimal import Decimal
        val = Decimal(str(value))
        if val == val.to_integral_value():
            return str(int(val))
        else:
            return str(val.quantize(Decimal('0.1')))
    except:
        return str(value)

# Initialize background scheduler for scheduled tasks
try:
    from scheduler import setup_scheduler, stop_scheduler
    setup_scheduler(app)
    # Ensure scheduler stops when app shuts down
    atexit.register(stop_scheduler)
    logging.info("Background scheduler initialized")
except Exception as e:
    logging.warning(f"Could not initialize background scheduler: {str(e)}")
    logging.info("The app will work normally, but scheduled tasks won't run automatically")


# CLI command for database maintenance
@app.cli.command()
def maintain_db():
    """Run database maintenance - optimize tables, clean logs, rebuild indexes"""
    from database_settings import DatabaseMaintenance
    maintenance = DatabaseMaintenance()
    success = maintenance.run_full_maintenance()
    exit(0 if success else 1)
