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
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    try:
        # Check environment variables - STRICT: require explicit DATABASE_URL_DEV
        database_url_prod = os.getenv('DATABASE_URL_PROD')
        database_url_dev = os.getenv('DATABASE_URL_DEV')
        
        if not database_url_prod:
            return jsonify({
                "success": False,
                "error": "DATABASE_URL_PROD environment variable not set. Please configure it in Secrets."
            }), 400
        
        if not database_url_dev:
            return jsonify({
                "success": False,
                "error": "DATABASE_URL_DEV environment variable not set. Please configure it in Secrets to avoid accidentally overwriting the wrong database."
            }), 400
        
        # Get confirmation from request body
        request_data = request.get_json(silent=True) or {}
        if not request_data.get('confirmed'):
            return jsonify({
                "success": False,
                "error": "Clone not confirmed. Please confirm the operation."
            }), 400
        
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
  --single-transaction \\
  --exit-on-error \\
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
            # Include both stdout and stderr in response for debugging
            return jsonify({
                "success": False,
                "error": "Clone failed - see details below",
                "stdout": result.stdout[-4000:] if result.stdout else "",
                "stderr": result.stderr[-4000:] if result.stderr else "",
                "returncode": result.returncode
            }), 500
        
        logger.info(f"Database clone completed by {current_user.username}")
        
        return jsonify({
            "success": True,
            "output": result.stdout,
            "message": "Database clone completed successfully!"
        })
        
    except subprocess.TimeoutExpired as te:
        logger.error(f"Clone timed out: {str(te)}")
        return jsonify({
            "success": False,
            "error": "Clone operation timed out (exceeded 5 minutes). The database may be large - consider running during off-peak hours."
        }), 500
    except Exception as e:
        logger.error(f"Error in database clone: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }), 500
