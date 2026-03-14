"""
Admin Tools routes for database operations and maintenance
"""
import subprocess
import logging
from datetime import datetime, timezone
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from flask import current_app as app
from app import db
import os
import openpyxl
from werkzeug.utils import secure_filename
from models import PostalCodeLookup

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
        
        # Extract connection strings
        def parse_db_url(url):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return {
                'user': parsed.username,
                'password': parsed.password,
                'host': parsed.hostname,
                'port': parsed.port or 5432,
                'database': parsed.path.lstrip('/')
            }
        
        prod_config = parse_db_url(database_url_prod)
        dev_config = parse_db_url(database_url_dev)
        
        # Run pg_dump and psql
        dump_cmd = [
            'pg_dump',
            '-h', prod_config['host'],
            '-p', str(prod_config['port']),
            '-U', prod_config['user'],
            '-F', 'custom',
            prod_config['database']
        ]
        
        restore_cmd = [
            'pg_restore',
            '-h', dev_config['host'],
            '-p', str(dev_config['port']),
            '-U', dev_config['user'],
            '-d', dev_config['database'],
            '--no-owner',
            '--no-acl',
            '-j', '4'
        ]
        
        # Set password env vars
        env_vars = os.environ.copy()
        env_vars['PGPASSWORD'] = prod_config['password']
        
        logger.info("Starting database clone...")
        
        # Dump from prod
        dump_process = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env_vars)
        dump_output, dump_error = dump_process.communicate()
        
        if dump_process.returncode != 0:
            return jsonify({"error": f"Dump failed: {dump_error.decode()}"}), 500
        
        # Restore to dev
        env_vars['PGPASSWORD'] = dev_config['password']
        restore_process = subprocess.Popen(restore_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env_vars)
        restore_output, restore_error = restore_process.communicate(input=dump_output)
        
        if restore_process.returncode != 0:
            return jsonify({"error": f"Restore failed: {restore_error.decode()}"}), 500
        
        logger.info("Database clone completed successfully")
        return jsonify({"ok": True, "message": "Database cloned successfully"})
        
    except Exception as e:
        logger.error(f"Error cloning database: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/route-mapping-drift')
@login_required
def route_mapping_drift():
    """Show route mapping drift report"""
    if not is_admin():
        return "Access denied", 403
    
    try:
        # Use Flask's internal URL map to get all defined routes
        url_map = app.url_map
        routes = {}
        
        for rule in url_map.iter_rules():
            if rule.endpoint == 'static':
                continue
            endpoint = rule.endpoint
            methods = ','.join(rule.methods - {'HEAD', 'OPTIONS'})
            route_str = str(rule)
            
            if endpoint not in routes:
                routes[endpoint] = {'paths': set(), 'methods': set()}
            routes[endpoint]['paths'].add(route_str)
            routes[endpoint]['methods'].add(methods)
        
        drift_list = []
        for endpoint, data in sorted(routes.items()):
            if len(data['paths']) > 1:
                drift_list.append({
                    'endpoint': endpoint,
                    'paths': sorted(data['paths']),
                    'methods': sorted(data['methods'])
                })
        
        return render_template('admin_tools/route_mapping_drift.html', drift_list=drift_list)
        
    except Exception as e:
        logger.error(f"Error analyzing route drift: {e}")
        return render_template('admin_tools/route_mapping_drift.html', drift_list=[], error=str(e))


@bp.route('/postal-codes')
@login_required
def postal_codes_page():
    """Show postal code import page"""
    if not is_admin():
        return "Access denied", 403
    
    count = PostalCodeLookup.query.count()
    return render_template('admin_tools/postal_codes.html', current_count=count)


@bp.route('/postal-codes/import', methods=['POST'])
@login_required
def import_postal_codes():
    """Import postal codes from Excel file"""
    if not is_admin():
        return jsonify({"error": "Access denied"}), 403
    
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({"error": "Only Excel files (.xlsx, .xls) are supported"}), 400
        
        # Load workbook
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        
        imported = 0
        duplicates = 0
        errors = []
        
        # Skip header row
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:  # Skip empty rows
                    continue
                
                postcode = str(row[0]).strip()
                municipality = str(row[1]).strip() if row[1] else ""
                district = str(row[2]).strip() if row[2] else ""
                urban_rural = str(row[3]).strip() if row[3] else None
                
                # Check if exists
                existing = PostalCodeLookup.query.filter_by(postcode=postcode).first()
                if existing:
                    duplicates += 1
                    continue
                
                # Create new record
                lookup = PostalCodeLookup(
                    postcode=postcode,
                    municipality=municipality,
                    district=district,
                    urban_rural=urban_rural
                )
                db.session.add(lookup)
                imported += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        db.session.commit()
        
        msg = f"Imported {imported} postal codes"
        if duplicates:
            msg += f", {duplicates} duplicates skipped"
        
        return jsonify({
            "ok": True,
            "imported": imported,
            "duplicates": duplicates,
            "errors": errors,
            "message": msg
        })
        
    except Exception as e:
        logger.error(f"Error importing postal codes: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/postal-codes/clear', methods=['POST'])
@login_required
def clear_postal_codes():
    """Clear all postal code data"""
    if not is_admin():
        return jsonify({"error": "Access denied"}), 403
    
    try:
        count = PostalCodeLookup.query.count()
        PostalCodeLookup.query.delete()
        db.session.commit()
        return jsonify({"ok": True, "cleared": count})
    except Exception as e:
        logger.error(f"Error clearing postal codes: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/crm-classifications')
@login_required
def crm_classifications_settings():
    if not is_admin():
        return "Access denied", 403
    
    from models import Setting
    items = []
    allowed = Setting.get(db.session, "crm_classifications", {})
    for name, filename in allowed.items():
        items.append({"name": name, "icon": filename})
    
    return render_template('admin_tools/crm_classifications.html', items=items)


@bp.post('/crm-classifications/save')
@login_required
def save_crm_classifications():
    if not is_admin():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    
    try:
        from models import Setting
        import json
        
        items = request.json.get('items', [])
        data_dict = {}
        
        for item in items:
            name = (item.get('name') or '').strip()
            icon = (item.get('icon') or '').strip()
            if name:
                data_dict[name] = icon if icon else None
        
        if not data_dict:
            return jsonify({"ok": False, "error": "At least one classification is required"}), 400
        
        Setting.set(db.session, "crm_classifications", data_dict)
        db.session.commit()
        
        return jsonify({"ok": True, "count": len(data_dict)})
        
    except Exception as e:
        logger.error("Error saving classifications: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route('/crm-classifications/upload-icon', methods=['POST'])
@login_required
def upload_classification_icon():
    if not is_admin():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    
    try:
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "No file"}), 400
        
        file = request.files['file']
        name = request.form.get('name', '').strip()
        
        if not file or not name:
            return jsonify({"ok": False, "error": "Missing file or name"}), 400
        
        if not file.filename.lower().endswith(('.jpg', '.jpeg')):
            return jsonify({"ok": False, "error": "Only JPG files allowed"}), 400
        
        # Save file
        os.makedirs('static/crm-classification-images', exist_ok=True)
        filename = f"{name}_{datetime.now(timezone.utc).timestamp()}.jpg"
        filepath = os.path.join('static/crm-classification-images', filename)
        file.save(filepath)
        
        return jsonify({"ok": True, "filename": filename})
        
    except Exception as e:
        logger.error("Error uploading icon: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route('/crm-bulk-classify', methods=['GET', 'POST'])
@login_required
def crm_bulk_classify():
    """Bulk classify customers via CSV upload"""
    if not is_admin():
        return "Access denied", 403
    
    if request.method == 'POST':
        try:
            from models import CrmCustomerProfile
            import io
            import csv
            
            if 'file' not in request.files:
                return jsonify({"ok": False, "error": "No file uploaded"}), 400
            
            file = request.files['file']
            if not file or not file.filename.endswith('.csv'):
                return jsonify({"ok": False, "error": "CSV file required"}), 400
            
            stream = io.TextIOWrapper(file.stream, encoding='utf-8')
            reader = csv.DictReader(stream)
            
            updated = 0
            errors = []
            
            for row_idx, row in enumerate(reader, start=2):
                try:
                    code = (row.get('customer_code_365') or row.get('code') or '').strip()
                    classif = (row.get('classification') or '').strip()
                    
                    if not code:
                        continue
                    
                    prof = CrmCustomerProfile.query.get(code)
                    if not prof:
                        prof = CrmCustomerProfile(customer_code_365=code)
                        db.session.add(prof)
                    
                    prof.classification = classif or None
                    prof.updated_at = datetime.now(timezone.utc)
                    prof.updated_by = getattr(current_user, "username", None)
                    updated += 1
                    
                except Exception as e:
                    errors.append(f"Row {row_idx}: {str(e)}")
            
            db.session.commit()
            msg = f"Classified {updated} customers"
            if errors:
                msg += f" ({len(errors)} errors)"
            return jsonify({"ok": True, "updated": updated, "errors": errors, "msg": msg})
        except Exception as e:
            logger.error("Error bulk classifying: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    return render_template('admin_tools/crm_bulk_classify.html')
