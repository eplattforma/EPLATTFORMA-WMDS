"""
Admin Tools routes for database operations and maintenance
"""
import subprocess
import logging
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from flask import current_app as app
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


