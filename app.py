import os
import logging
import atexit
import threading
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
import pytz
from datetime import datetime

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
logging.getLogger('urllib3.util.retry').setLevel(logging.ERROR)

class Base(DeclarativeBase):
    pass

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.secret_key = os.environ.get("SESSION_SECRET")
if not app.secret_key:
    raise RuntimeError("SESSION_SECRET environment variable is required and cannot be empty")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

database_url = os.environ.get("DATABASE_URL")
if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://')
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///picking.db"
    logging.warning("DATABASE_URL not found, using SQLite database")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Cockpit Ticket 3 — Claude advice service. Read at boot from Replit Secrets;
# the actual SDK client is created lazily inside services.claude_advice_service
# so the app boots cleanly even when the key is unset (endpoint then returns
# 503 per cockpit-brief §12.6).
app.config["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "")
app.config["CLAUDE_MODEL"] = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")

is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"

# SQLite (used by the test suite) doesn't accept Postgres-specific pool/connect
# options, so we only apply them when running against a real Postgres URL.
_is_sqlite = app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")

if _is_sqlite:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "echo": False,
    }
elif is_production:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        'pool_pre_ping': True,
        "pool_recycle": 300,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "connect_args": {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "options": "-c statement_timeout=120000 -c lock_timeout=30000"
        },
        "echo": False,
    }
else:
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        'pool_pre_ping': True,
        "pool_recycle": 300,
        "pool_size": 20,
        "max_overflow": 10,
        "pool_timeout": 30,
        "connect_args": {
            "connect_timeout": 10,
        },
        "echo": False,
    }

db = SQLAlchemy(model_class=Base)
db.init_app(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def _sync_classification_images_to_db():
    import json, base64, glob as globmod
    from models import Setting
    img_dir = 'static/crm-classification-images'
    if not os.path.isdir(img_dir):
        return
    local_files = globmod.glob(os.path.join(img_dir, '*'))
    if not local_files:
        return
    raw_b64 = Setting.get(db.session, 'crm_classification_images_b64', '{}')
    try:
        images_dict = json.loads(raw_b64)
    except Exception:
        images_dict = {}
    changed = False
    for fpath in local_files:
        fname = os.path.basename(fpath)
        if fname not in images_dict:
            try:
                with open(fpath, 'rb') as f:
                    images_dict[fname] = base64.b64encode(f.read()).decode('ascii')
                changed = True
                logging.info("Synced classification image to DB: %s", fname)
            except Exception as e:
                logging.warning("Failed to sync image %s: %s", fname, e)
    if changed:
        Setting.set(db.session, 'crm_classification_images_b64', json.dumps(images_dict))
        db.session.commit()
        logging.info("Classification images synced to DB (%d total)", len(images_dict))

    cls_raw = Setting.get(db.session, 'crm_customer_classifications', None)
    if cls_raw:
        try:
            cls_data = json.loads(cls_raw) if isinstance(cls_raw, str) else cls_raw
        except Exception:
            cls_data = None
        if isinstance(cls_data, dict):
            cls_changed = False
            for cls_name, icon_ref in cls_data.items():
                if isinstance(icon_ref, dict):
                    icon_ref = icon_ref.get('icon')
                if icon_ref and icon_ref not in images_dict:
                    prefix = cls_name.replace(' ', '_').upper() + '_'
                    for key in images_dict:
                        if key.startswith(prefix):
                            cls_data[cls_name] = key
                            cls_changed = True
                            logging.info("Remapped classification %s: %s -> %s", cls_name, icon_ref, key)
                            break
            if cls_changed:
                Setting.set(db.session, 'crm_customer_classifications', json.dumps(cls_data))
                db.session.commit()
                logging.info("Updated classification image references")


def run_deferred_db_init():
    """Called from main.py AFTER schema updaters have run."""
    try:
        _sync_classification_images_to_db()
    except Exception as e:
        logging.warning(f"Could not sync classification images: {str(e)}")

    if not is_production:
        try:
            from models import User, Setting
            from utils import create_user

            admin = User.query.filter_by(username='administrator').first()
            if not admin:
                create_user(db.session, 'administrator', 'admin123', 'admin')
                logging.info("Default admin user created")

            picker = User.query.filter_by(username='picker1').first()
            if not picker:
                create_user(db.session, 'picker1', 'picker123', 'picker')
                logging.info("Default picker user created")

            confirm_setting = Setting.query.filter_by(key='confirm_picking_step').first()
            if not confirm_setting:
                confirm_setting = Setting()
                confirm_setting.key = 'confirm_picking_step'
                confirm_setting.value = 'true'
                db.session.add(confirm_setting)

            image_setting = Setting.query.filter_by(key='show_image_on_picking_screen').first()
            if not image_setting:
                image_setting = Setting()
                image_setting.key = 'show_image_on_picking_screen'
                image_setting.value = 'true'
                db.session.add(image_setting)

            timezone_setting = Setting.query.filter_by(key='system_timezone').first()
            if not timezone_setting:
                timezone_setting = Setting()
                timezone_setting.key = 'system_timezone'
                timezone_setting.value = 'Europe/Athens'
                db.session.add(timezone_setting)

            forecast_defaults = {
                'forecast_default_cover_days': '7',
                'forecast_review_cycle_days': '1',
                'forecast_buffer_stock_days': '1',
                'forecast_trend_uplift_trigger': '1.15',
                'forecast_trend_down_trigger': '0.90',
                'forecast_trend_uplift_cap': '1.25',
                'forecast_trend_down_floor': '0.75',
                'forecast_seasonal_cap_min': '0.85',
                'forecast_seasonal_cap_max': '1.15',
                'forecast_min_sample_brand': '6',
                'forecast_min_sample_prefix': '4',
            }
            for fk, fv in forecast_defaults.items():
                if not Setting.query.filter_by(key=fk).first():
                    s = Setting()
                    s.key = fk
                    s.value = fv
                    db.session.add(s)

            db.session.commit()
            logging.info("Settings initialized")
        except Exception as e:
            logging.warning(f"Could not initialize dev settings: {str(e)}")

    from models import Setting
    import json as _json
    try:
        if not Setting.query.filter_by(key="crm_customer_classifications").first():
            Setting.set(db.session, "crm_customer_classifications",
                        _json.dumps(["Customer", "EKO", "Petrolina", "SHELL", "Monitor", "At Risk", "Frozen"]))
            db.session.commit()
            logging.info("Seeded crm_customer_classifications")

        if not Setting.query.filter_by(key="crm_order_window_hours").first():
            Setting.set(db.session, "crm_order_window_hours", "48")
            db.session.commit()
        if not Setting.query.filter_by(key="crm_delivery_anchor_time").first():
            Setting.set(db.session, "crm_delivery_anchor_time", "00:01")
            db.session.commit()
        if not Setting.query.filter_by(key="crm_order_window_close_hours").first():
            Setting.set(db.session, "crm_order_window_close_hours", "48")
            db.session.commit()
        if not Setting.query.filter_by(key="crm_delivery_close_anchor_time").first():
            Setting.set(db.session, "crm_delivery_close_anchor_time", "12:00")
            db.session.commit()

        from services_oi_time_estimator import DEFAULT_PARAMS

        if not Setting.query.filter_by(key="oi_time_params_v1").first():
            Setting.set_json(db.session, "oi_time_params_v1", DEFAULT_PARAMS)
            logging.info("Seeded oi_time_params_v1 with defaults")

        if not Setting.query.filter_by(key="oi_time_params_v1_revision").first():
            Setting.set(db.session, "oi_time_params_v1_revision", "1")
            logging.info("Seeded oi_time_params_v1_revision = 1")

        db.session.commit()
    except Exception as e:
        logging.warning(f"Could not seed settings: {str(e)}")


with app.app_context():
    try:
        import models  # noqa: F401
        import dw_analytics_models  # noqa: F401

        from delete_guards import register_all_guards
        register_all_guards()

        from blueprints.cypost_api import cypost_bp
        app.register_blueprint(cypost_bp)

        logging.info("app.py init complete (no DB queries)")

    except Exception as e:
        logging.error(f"Error during app initialization: {str(e)}")
        logging.exception("App initialization error details:")

import json

@app.template_filter('from_json')
def from_json_filter(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return []

@app.template_filter('format_seq')
def format_seq_filter(value):
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

try:
    from scheduler import setup_scheduler, stop_scheduler
    setup_scheduler(app)
    atexit.register(stop_scheduler)
    logging.info("Background scheduler initialized")
except Exception as e:
    logging.warning(f"Could not initialize background scheduler: {str(e)}")
    logging.info("The app will work normally, but scheduled tasks won't run automatically")


@app.cli.command()
def maintain_db():
    """Run database maintenance - optimize tables, clean logs, rebuild indexes"""
    from database_settings import DatabaseMaintenance
    maintenance = DatabaseMaintenance()
    success = maintenance.run_full_maintenance()
    exit(0 if success else 1)
