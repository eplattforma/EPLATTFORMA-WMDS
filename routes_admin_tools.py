"""
Admin Tools routes for database operations and maintenance
"""
import subprocess
import logging
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from flask import current_app as app
from app import db
import os

bp = Blueprint('admin_tools_custom', __name__, url_prefix='/admin/tools')
logger = logging.getLogger(__name__)

# Helper to check if user is admin
def is_admin():
    return current_user.is_authenticated and getattr(current_user, 'role', None) == 'admin'

@bp.route('/database-clone')
@login_required
def database_clone_page():
    """Show database clone tool page"""
    if not is_admin():
        return "Access denied", 403
    
    return render_template('admin_tools/database_clone.html')


@bp.route('/database-clone/execute', methods=['POST'])
@login_required
def execute_database_clone():
    """Execute database clone from production to development"""
    if not is_admin():
        return jsonify({"error": "Access denied"}), 403
    
    try:
        # Check environment variables
        database_url_prod = os.getenv('DATABASE_URL_PROD')
        database_url_dev = os.getenv('DATABASE_URL_DEV') or os.getenv('DATABASE_URL')
        
        if not database_url_prod:
            return jsonify({"error": "DATABASE_URL_PROD environment variable not set"}), 400
        
        if not database_url_dev:
            return jsonify({"error": "DATABASE_URL or DATABASE_URL_DEV not set"}), 400
        
        # Get confirmation
        if not request.get_json().get('confirmed'):
            return jsonify({"error": "Clone not confirmed"}), 400
        
        logger.info(f"Starting database clone by {current_user.username}")
        
        # Build the clone command - use nix-shell with PostgreSQL 16
        clone_script = f"""
set -e
export PATH="/nix/store/$(ls /nix/store | grep postgresql-16 | head -1)/bin:$PATH"

echo "Checking PostgreSQL version..."
pg_dump --version

echo "Dumping production database..."
pg_dump "{database_url_prod}" \\
  --format=custom \\
  --no-owner \\
  --no-acl \\
  -f /tmp/prod_db.dump

echo "Restoring to development database..."
pg_restore \\
  --clean \\
  --if-exists \\
  --no-owner \\
  -d "{database_url_dev}" \\
  /tmp/prod_db.dump

echo "Verifying clone..."
psql "{database_url_dev}" -c "SELECT COUNT(*) as ps_items_count FROM ps_items_dw;"
"""
        
        # Execute via nix-shell with PostgreSQL 16
        result = subprocess.run(
            ['nix-shell', '-p', 'postgresql_16', '--run', clone_script],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            logger.error(f"Clone failed: {result.stderr}")
            return jsonify({
                "success": False,
                "error": f"Clone failed: {result.stderr}"
            }), 500
        
        logger.info(f"Database clone completed by {current_user.username}")
        
        return jsonify({
            "success": True,
            "output": result.stdout,
            "message": "Database clone completed successfully!"
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({
            "error": "Clone operation timed out (exceeded 5 minutes)"
        }), 500
    except Exception as e:
        logger.error(f"Error in database clone: {str(e)}", exc_info=True)
        return jsonify({
            "error": f"Error: {str(e)}"
        }), 500


# =============================================================================
# ROUTE MAPPING DRIFT DETECTION
# =============================================================================

@bp.route('/route-mapping-drift')
@login_required
def route_mapping_drift_page():
    """Show route mapping drift detection page"""
    if not is_admin():
        return "Access denied", 403
    
    from services_route_lifecycle import check_route_mapping_drift
    
    drift_list = check_route_mapping_drift()
    
    return render_template('admin_tools/route_mapping_drift.html', drift_list=drift_list)


@bp.route('/route-mapping-drift/check', methods=['GET'])
@login_required
def check_drift():
    """API endpoint to check route mapping drift"""
    if not is_admin():
        return jsonify({"error": "Access denied"}), 403
    
    from services_route_lifecycle import check_route_mapping_drift
    
    route_id = request.args.get('route_id', type=int)
    drift_list = check_route_mapping_drift(route_id)
    
    return jsonify({
        "drift_count": len(drift_list),
        "drift_list": drift_list
    })


@bp.route('/route-mapping-drift/fix', methods=['POST'])
@login_required
def fix_drift():
    """Fix route mapping drift by syncing invoice cache columns"""
    if not is_admin():
        return jsonify({"error": "Access denied"}), 403
    
    from services_route_lifecycle import fix_route_mapping_drift
    
    data = request.get_json() or {}
    route_id = data.get('route_id')
    
    try:
        fixed_count = fix_route_mapping_drift(route_id)
        logger.info(f"Fixed {fixed_count} route mapping drifts by {current_user.username}")
        
        return jsonify({
            "success": True,
            "fixed_count": fixed_count,
            "message": f"Fixed {fixed_count} invoice(s) with route mapping drift"
        })
    except Exception as e:
        logger.error(f"Error fixing route mapping drift: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/run-reconciliation-migration', methods=['POST'])
@login_required
def run_reconciliation_migration():
    """Run the route reconciliation database migration"""
    if not is_admin():
        return jsonify({"error": "Access denied"}), 403
    
    try:
        from migrations.route_reconciliation_migration import run_migration, check_migration_status
        
        if check_migration_status():
            return jsonify({
                "success": True,
                "message": "Migration already applied"
            })
        
        run_migration()
        logger.info(f"Route reconciliation migration run by {current_user.username}")
        
        return jsonify({
            "success": True,
            "message": "Route reconciliation migration completed successfully"
        })
    except Exception as e:
        logger.error(f"Error running migration: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/magento-login-import')
@login_required
def magento_login_import_page():
    if not is_admin():
        return "Forbidden", 403
    return render_template('admin_tools/magento_login_import.html')


@bp.route('/preview-magento-login-csv', methods=['POST'])
@login_required
def preview_magento_login_csv():
    if not is_admin():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    import tempfile
    tmp_path = None
    try:
        suffix = os.path.splitext(uploaded.filename)[1] or '.csv'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            uploaded.save(tmp.name)
            tmp_path = tmp.name
        from services.import_magento_login_log import preview_csv
        result = preview_csv(tmp_path)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@bp.route('/import-magento-login-log-upload', methods=['POST'])
@login_required
def import_magento_login_log_upload():
    if not is_admin():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    import tempfile
    suffix = os.path.splitext(uploaded.filename)[1] or '.csv'
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            uploaded.save(tmp.name)
            tmp_path = tmp.name
        from services.import_magento_login_log import import_magento_login_log_csv
        res = import_magento_login_log_csv(tmp_path)
        res['file'] = uploaded.filename
        return jsonify({"ok": True, "result": res})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        logger.error("Magento login log upload error: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "Import failed"}), 500
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@bp.route('/import-magento-login-log', methods=['POST'])
@login_required
def import_magento_login_log_endpoint():
    if not is_admin():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    filepath = ""
    if request.is_json:
        filepath = (request.json or {}).get("filepath", "")
    else:
        filepath = request.form.get("filepath", "")
    if not filepath:
        return jsonify({"ok": False, "error": "filepath is required"}), 400

    try:
        from services.import_magento_login_log import import_magento_login_log_csv
        res = import_magento_login_log_csv(filepath)
        return jsonify({"ok": True, "result": res})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": f"File not found: {filepath}"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        logger.error("Magento login log import error: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "Import failed"}), 500


@bp.route('/magento-last-login-sample')
@login_required
def magento_last_login_sample():
    if not is_admin():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    from models import MagentoCustomerLastLoginCurrent
    rows = (MagentoCustomerLastLoginCurrent.query
            .order_by(MagentoCustomerLastLoginCurrent.last_login_at.desc().nullslast())
            .limit(10).all())

    return jsonify([{
        "customer_code_365": r.customer_code_365,
        "magento_customer_id": r.magento_customer_id,
        "last_login_at_utc": r.last_login_at.isoformat() if r.last_login_at else None,
        "email": r.email,
        "source": r.source_filename
    } for r in rows])


@bp.route('/crm-classifications', methods=['GET', 'POST'])
@login_required
def crm_classifications_settings():
    if not is_admin():
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    from models import Setting
    import json

    if request.method == 'POST':
        items = request.form.get('items', '').strip().split('\n')
        items = [i.strip() for i in items if i.strip()]
        try:
            Setting.set(db.session, "crm_customer_classifications", json.dumps(items))
            db.session.commit()
            return jsonify({"ok": True, "items": items, "msg": f"Updated {len(items)} classifications"})
        except Exception as e:
            logger.error("Error saving classifications: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    current = Setting.query.filter_by(key="crm_customer_classifications").first()
    items = []
    if current:
        try:
            items = json.loads(current.value)
        except Exception:
            items = []

    if request.is_json:
        return jsonify({"ok": True, "items": items})

    return render_template('admin_tools/crm_classifications.html', items=items)
