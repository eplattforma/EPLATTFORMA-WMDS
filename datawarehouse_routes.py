"""
Data Warehouse routes for PS365 sync operations
"""
from flask import Blueprint, render_template_string, request, jsonify, flash, redirect, url_for, render_template
from flask_login import login_required, current_user
from app import app, db
from models import SyncState
from datawarehouse_sync import full_dw_update, incremental_dw_update, sync_invoices_from_date, test_fetch_single_item
from ps365_invoices import fetch_invoice_lines_from_date, fetch_invoice_headers_from_date
import logging
import threading

dw_bp = Blueprint('datawarehouse', __name__, url_prefix='/datawarehouse')

logger = logging.getLogger(__name__)


def _capture_sync_output(sync_func):
    """Run sync and capture output to return to user"""
    import io
    import logging as py_logging
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    import os
    
    try:
        # Create a string buffer to capture log output
        log_capture = io.StringIO()
        handler = py_logging.StreamHandler(log_capture)
        handler.setLevel(py_logging.INFO)
        formatter = py_logging.Formatter('%(message)s')
        handler.setFormatter(formatter)
        
        # Get the sync logger and add our handler
        sync_logger = py_logging.getLogger('datawarehouse_sync')
        sync_logger.addHandler(handler)
        
        # Use app context properly in background thread
        with app.app_context():
            # Create a brand new session with explicit connection for background thread
            # This ensures commits are persisted even in background threads
            session = db.session
            try:
                sync_func(session)
                # Explicitly commit after sync to ensure data persists
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Sync error: {str(e)}", exc_info=True)
                raise
            finally:
                session.close()
        
        # Get captured output
        output = log_capture.getvalue()
        sync_logger.removeHandler(handler)
        
        return {
            'success': True,
            'output': output,
            'message': 'Sync completed successfully!'
        }
    except Exception as e:
        logger.error(f"Error in sync: {str(e)}", exc_info=True)
        return {
            'success': False,
            'output': str(e),
            'message': f'Error during sync: {str(e)}'
        }


def _run_sync_in_background(sync_func, callback=None):
    """Run a sync function in a background thread and call callback with result"""
    def worker():
        try:
            result = _capture_sync_output(sync_func)
            if callback:
                callback(result)
        except Exception as e:
            logger.error(f"Background worker error: {str(e)}", exc_info=True)
    
    # Use non-daemon thread to ensure it completes before shutdown
    thread = threading.Thread(target=worker, daemon=False)
    thread.start()
    return thread


@dw_bp.route('/menu', methods=['GET'])
@login_required
def dw_menu():
    """Display data warehouse menu"""
    # Only allow admins
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Data Warehouse Management</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 600px; margin: 0 auto; }
            .menu-option { 
                padding: 20px; 
                margin: 10px 0; 
                border: 1px solid #ddd; 
                border-radius: 5px;
                cursor: pointer;
                transition: all 0.3s;
            }
            .menu-option:hover {
                background-color: #f5f5f5;
                border-color: #0066cc;
            }
            .menu-option a {
                display: block;
                text-decoration: none;
                color: #333;
            }
            .menu-option h3 { margin-top: 0; color: #0066cc; }
            .menu-option p { margin: 0; color: #666; font-size: 0.9em; }
            .back-link { margin-top: 20px; }
            .back-link a { color: #0066cc; text-decoration: none; }
            .back-link a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Data Warehouse Management</h1>
            <p>Select an operation to manage PS365 data synchronization:</p>
            
            <div class="menu-option">
                <a href="/datawarehouse/full-sync">
                    <h3>Full DW Update</h3>
                    <p>Perform a complete refresh of all items and dimensions (categories, brands, seasons, attributes) from PS365.</p>
                </a>
            </div>
            
            <div class="menu-option">
                <a href="/datawarehouse/incremental-sync">
                    <h3>Incremental Item Update</h3>
                    <p>Sync only changed items from PS365 since the last update. Faster for regular syncs.</p>
                </a>
            </div>
            
            <div class="menu-option">
                <a href="{{ url_for('payment_terms.sync_customers_page') }}">
                    <h3>Synchronise Customers</h3>
                    <p>Sync customer data from PS365 to update payment terms and customer information.</p>
                </a>
            </div>
            
            <div class="menu-option">
                <a href="/datawarehouse/logs">
                    <h3>View Sync Logs</h3>
                    <p>View logs from recent sync operations to monitor progress and troubleshoot issues.</p>
                </a>
            </div>
            
            <div class="menu-option">
                <a href="/datawarehouse/invoice-sync">
                    <h3>Sync Invoices from PS365</h3>
                    <p>Load invoice headers and line items from PS365 for a specific date range into the data warehouse.</p>
                </a>
            </div>
            
            <div class="menu-option">
                <a href="/datawarehouse/database-settings">
                    <h3>Database Settings</h3>
                    <p>Manage scheduled tasks, clone database, and database configuration.</p>
                </a>
            </div>
            
            <div class="back-link">
                <a href="/">← Back to Home</a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)


@dw_bp.route('/test-one-item', methods=['GET', 'POST'])
@login_required
def test_one_item():
    """Test endpoint - fetch one item to debug"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Admin only'}), 403
    
    try:
        with app.app_context():
            session = db.session()
            try:
                test_fetch_single_item(session)
                session.commit()
                return jsonify({'success': True, 'message': 'Test item inserted!'})
            except Exception as e:
                session.rollback()
                logger.error(f"Test error: {str(e)}", exc_info=True)
                return jsonify({'success': False, 'message': str(e)}), 500
            finally:
                session.close()
    except Exception as e:
        logger.error(f"Test endpoint error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@dw_bp.route('/full-sync', methods=['GET', 'POST'])
@login_required
def full_sync():
    """Execute full data warehouse update in background"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        # Start sync in background thread
        def run_sync():
            try:
                with app.app_context():
                    session = db.session
                    try:
                        full_dw_update(session)
                        session.commit()
                        logger.info("✓ Background full sync completed successfully")
                    except Exception as e:
                        session.rollback()
                        logger.error(f"Background sync error: {str(e)}", exc_info=True)
            except Exception as e:
                logger.error(f"Background sync context error: {str(e)}", exc_info=True)
        
        # Start in background thread so app stays responsive
        sync_thread = threading.Thread(target=run_sync, daemon=True)
        sync_thread.start()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Full DW Update Started</title></head>
        <body style="font-family: Arial; margin: 40px;">
            <h1>✓ Sync Started in Background</h1>
            <p>The full data warehouse update has been started. It will continue running in the background.</p>
            <p>Check the admin panel or logs to monitor progress.</p>
            <p><a href="/datawarehouse/menu">← Back to Menu</a></p>
        </body>
        </html>
        """
        return html
    
    # GET request - show confirmation page
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Full DW Update</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 600px; margin: 0 auto; }
            .warning { background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0; }
            button { padding: 10px 20px; background-color: #0066cc; color: white; border: none; border-radius: 5px; cursor: pointer; }
            button:hover { background-color: #0052a3; }
            .back-link { margin-top: 20px; }
            .back-link a { color: #0066cc; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Full Data Warehouse Update</h1>
            <p>This operation will:</p>
            <ul>
                <li>Fetch ALL items from PS365 (active and inactive)</li>
                <li>Update all item categories</li>
                <li>Update all brands</li>
                <li>Update all seasons</li>
                <li>Update all attributes</li>
            </ul>
            
            <div class="warning">
                <strong>⚠️ Warning:</strong> This operation may take several minutes depending on the number of items in PS365.
            </div>
            
            <form method="POST">
                <button type="submit">Start Full Update</button>
            </form>
            
            <div class="back-link">
                <a href="/datawarehouse/menu">← Back to Menu</a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)


@dw_bp.route('/full-sync-status', methods=['GET'])
@login_required
def full_sync_status():
    """Get current status of full sync"""
    if current_user.role != 'admin':
        return jsonify({'success': False}), 403
    
    try:
        from models import SyncState
        
        with app.app_context():
            status_obj = db.session.get(SyncState, "full_sync_status")
            if not status_obj or not status_obj.value:
                return jsonify({
                    'status': 'IDLE',
                    'items_updated': 0,
                    'progress_output': 'No sync running'
                })
            
            parts = status_obj.value.split('|', 1)
            status = parts[0]
            output = parts[1] if len(parts) > 1 else ""
            
            # Also get the output from SyncState if stored separately
            output_obj = db.session.get(SyncState, "full_sync_output")
            if output_obj:
                output = output_obj.value
            
            if status == "COMPLETED":
                count_obj = db.session.get(SyncState, "full_sync_items_updated")
                items_updated = int(count_obj.value) if count_obj else 0
                return jsonify({
                    'status': 'COMPLETED',
                    'items_updated': items_updated,
                    'progress_output': output,
                    'success': True
                })
            elif status == "ERROR":
                return jsonify({
                    'status': 'ERROR',
                    'items_updated': 0,
                    'progress_output': output,
                    'success': False
                })
            else:
                return jsonify({
                    'status': 'RUNNING',
                    'items_updated': 0,
                    'progress_output': output
                })
    except Exception as e:
        logger.error(f"Error getting sync status: {str(e)}")
        return jsonify({
            'status': 'ERROR',
            'items_updated': 0,
            'progress_output': str(e),
            'success': False
        }), 500


@dw_bp.route('/incremental-sync', methods=['GET', 'POST'])
@login_required
def incremental_sync():
    """Execute incremental data warehouse update"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        # Show progress page
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Incremental DW Update - In Progress</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .container { max-width: 600px; margin: 0 auto; }
                .progress { background-color: #f0f0f0; padding: 20px; border-radius: 5px; }
                .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #0066cc; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 20px auto; }
                @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                .status { margin-top: 20px; padding: 15px; background-color: #d1ecf1; border-radius: 5px; }
                .status-msg { font-size: 16px; color: #004085; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Incremental Data Warehouse Update</h1>
                <div class="progress">
                    <div class="spinner"></div>
                    <div class="status">
                        <div class="status-msg">Processing... This may take a few minutes.</div>
                    </div>
                </div>
            </div>
            <script>
                // Submit form and show results
                async function runSync() {
                    try {
                        const response = await fetch('/datawarehouse/incremental-sync-execute', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'}
                        });
                        const result = await response.json();
                        
                        let html = '';
                        if (result.success) {
                            html = '<strong>✓ Sync Completed!</strong><br><br>';
                            html += '<div style="background:#f0f0f0;padding:15px;border-radius:5px;max-height:400px;overflow-y:auto;font-family:monospace;font-size:12px;white-space:pre-wrap;word-break:break-word;">';
                            if (result.output) {
                                html += result.output.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                            } else {
                                html += 'Sync completed successfully!';
                            }
                            html += '</div>';
                        } else {
                            html = '<strong>✗ Error:</strong><br>';
                            html += (result.message || 'Unknown error') + '<br><br>';
                            if (result.output) {
                                html += '<div style="background:#ffe0e0;padding:10px;border-radius:5px;font-family:monospace;font-size:12px;">' + 
                                        result.output.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
                            }
                        }
                        document.querySelector('.status-msg').innerHTML = html;
                        
                        if (result.success) {
                            setTimeout(() => window.location.href = '/datawarehouse/menu?status=success', 5000);
                        }
                    } catch (error) {
                        document.querySelector('.status-msg').innerHTML = 
                            '<strong>✗ Error:</strong><br>' + error.message;
                    }
                }
                runSync();
            </script>
        </body>
        </html>
        """)
    
    # GET request - show confirmation page
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Incremental DW Update</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 600px; margin: 0 auto; }
            .info { background-color: #d1ecf1; padding: 15px; border-radius: 5px; margin: 20px 0; }
            button { padding: 10px 20px; background-color: #0066cc; color: white; border: none; border-radius: 5px; cursor: pointer; }
            button:hover { background-color: #0052a3; }
            .back-link { margin-top: 20px; }
            .back-link a { color: #0066cc; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Incremental Data Warehouse Update</h1>
            <p>This operation will:</p>
            <ul>
                <li>Fetch only CHANGED items since the last sync</li>
                <li>Update item records that have changed</li>
                <li>Maintain sync state for future incremental updates</li>
            </ul>
            
            <div class="info">
                <strong>ℹ️ Info:</strong> This is much faster than a full update and is suitable for regular scheduled syncs. Dimension tables (categories, brands, seasons, attributes) are not updated incrementally.
            </div>
            
            <form method="POST">
                <button type="submit">Start Incremental Update</button>
            </form>
            
            <div class="back-link">
                <a href="/datawarehouse/menu">← Back to Menu</a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)


@dw_bp.route('/incremental-sync-execute', methods=['POST'])
@login_required
def incremental_sync_execute():
    """Execute incremental DW update synchronously and return detailed results"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Access denied. Admin privileges required.'}), 403
    
    try:
        logger.info(f"Starting incremental DW update by {current_user.username}")
        result = _capture_sync_output(incremental_dw_update)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in incremental DW update: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'output': str(e),
            'message': f'Error during sync: {str(e)}'
        }), 500


@dw_bp.route('/database-settings', methods=['GET'])
@login_required
def database_settings():
    """Database management settings page"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    try:
        from scheduler import list_scheduled_jobs
        scheduled_jobs = list_scheduled_jobs()
    except Exception as e:
        logger.warning(f"Could not retrieve scheduled jobs: {str(e)}")
        scheduled_jobs = []
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Database Settings</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f5f5f5; }
            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            .header h1 { margin-bottom: 5px; font-size: 28px; }
            .header p { opacity: 0.9; font-size: 14px; }
            .container { max-width: 1000px; margin: 0 auto; padding: 20px; }
            .section { background: white; border-radius: 8px; padding: 25px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
            .section h2 { color: #333; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #667eea; }
            .settings-grid { display: grid; gap: 15px; }
            .setting-item { padding: 15px; background: #f9f9f9; border-radius: 6px; border-left: 4px solid #667eea; }
            .setting-item label { font-weight: 600; color: #333; display: block; margin-bottom: 5px; }
            .setting-item p { color: #666; font-size: 13px; line-height: 1.5; }
            .button-group { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
            button { padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.3s; }
            .btn-primary { background: #667eea; color: white; }
            .btn-primary:hover { background: #5568d3; transform: translateY(-2px); box-shadow: 0 4px 8px rgba(102,126,234,0.3); }
            .btn-secondary { background: #e0e0e0; color: #333; }
            .btn-secondary:hover { background: #d0d0d0; }
            .btn-danger { background: #ef5350; color: white; }
            .btn-danger:hover { background: #e53935; }
            .jobs-list { background: #f9f9f9; padding: 15px; border-radius: 6px; }
            .job-item { padding: 12px; background: white; border-radius: 4px; margin-bottom: 10px; border-left: 3px solid #4caf50; }
            .job-name { font-weight: 600; color: #333; }
            .job-detail { color: #666; font-size: 12px; margin-top: 3px; font-family: monospace; }
            .job-time { color: #667eea; font-weight: 600; font-size: 12px; margin-top: 5px; }
            .no-jobs { color: #999; font-style: italic; padding: 20px; text-align: center; }
            .warning-box { background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; border-radius: 6px; margin-bottom: 20px; color: #856404; }
            .warning-box strong { display: block; margin-bottom: 5px; }
            .back-link { margin-top: 30px; }
            .back-link a { color: #667eea; text-decoration: none; font-weight: 600; }
            .back-link a:hover { text-decoration: underline; }
            .success-message { background: #d4edda; color: #155724; padding: 12px; border-radius: 5px; margin-bottom: 15px; border-left: 4px solid #28a745; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>⚙️ Database Settings</h1>
            <p>Manage database operations, schedules, and configurations</p>
        </div>
        
        <div class="container">
            <!-- Scheduled Tasks Section -->
            <div class="section">
                <h2>🕐 Scheduled Tasks</h2>
                <p style="margin-bottom: 15px; color: #666;">Automatic data warehouse syncs that run at specified times. The scheduler runs continuously in the background.</p>
                
                {% if scheduled_jobs %}
                <div class="jobs-list">
                    {% for job in scheduled_jobs %}
                    <div class="job-item">
                        <div class="job-name">{{ job.name }}</div>
                        <div class="job-detail">ID: {{ job.id }}</div>
                        <div class="job-detail">Schedule: {{ job.trigger }}</div>
                        {% if job.next_run %}
                        <div class="job-time">Next run: {{ job.next_run }}</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <div class="no-jobs">
                    <p>No scheduled tasks configured. Background scheduling will be enabled when your app is published to production.</p>
                </div>
                {% endif %}
                
                <div class="settings-grid" style="margin-top: 15px;">
                    <div class="setting-item">
                        <label>Current Schedule:</label>
                        <p>• Full DW Sync: Every Sunday at 3:00 AM</p>
                        <p>• Incremental Sync: Daily at 1:00 AM and 1:00 PM</p>
                        <p style="margin-top: 10px; color: #999; font-size: 12px;"><em>To modify schedules, edit scheduler.py</em></p>
                    </div>
                </div>
            </div>
            
            <!-- Database Clone Section -->
            <div class="section">
                <h2>📋 Database Management</h2>
                
                <div class="warning-box">
                    <strong>⚠️ Important:</strong>
                    Make sure that you published all changes before proceeding with database operations.
                </div>
                
                <div class="settings-grid">
                    <div class="setting-item">
                        <label>Clone Production to Development</label>
                        <p>Create a copy of the production database for development and testing purposes. This will overwrite the development database with production data.</p>
                        <div class="button-group">
                            <button class="btn-primary" onclick="cloneDatabase()">Clone Database</button>
                        </div>
                    </div>
                </div>
                
                <div id="clone-status" style="margin-top: 15px; display: none;"></div>
            </div>
            
            <!-- Data Warehouse Operations -->
            <div class="section">
                <h2>🏭 Data Warehouse Operations</h2>
                <p style="margin-bottom: 15px; color: #666;">Manual sync operations for data warehouse management.</p>
                
                <div class="button-group">
                    <a href="/datawarehouse/menu" style="text-decoration: none;">
                        <button class="btn-primary">Data Warehouse Menu</button>
                    </a>
                    <a href="/datawarehouse/logs" style="text-decoration: none;">
                        <button class="btn-secondary">View Sync Logs</button>
                    </a>
                </div>
            </div>
            
            <div class="back-link">
                <a href="/">← Back to Home</a>
            </div>
        </div>
        
        <script>
            async function cloneDatabase() {
                const statusDiv = document.getElementById('clone-status');
                
                if (!confirm('This will overwrite the development database with production data.\\n\\nMake sure you published all your changes first!\\n\\nContinue?')) {
                    return;
                }
                
                const btn = event.target;
                btn.disabled = true;
                btn.textContent = 'Cloning...';
                
                statusDiv.style.display = 'block';
                statusDiv.innerHTML = '<div class="success-message">Starting database clone...</div>';
                
                try {
                    const response = await fetch('/admin/tools/database-clone/execute', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({confirmed: true})
                    });
                    
                    // Check content-type before parsing JSON
                    const contentType = response.headers.get('content-type');
                    if (!contentType || !contentType.includes('application/json')) {
                        const text = await response.text();
                        throw new Error('Server returned non-JSON response: ' + text.substring(0, 200));
                    }
                    
                    const result = await response.json();
                    
                    if (result.success) {
                        statusDiv.innerHTML = '<div class="success-message"><strong>✓ Success!</strong><br>' + result.message + '</div>';
                    } else {
                        const errorMsg = result.error || result.message || 'Unknown error';
                        const stderr = result.stderr ? '<br><pre style="font-size: 12px; overflow-x: auto; max-height: 200px;">' + result.stderr + '</pre>' : '';
                        statusDiv.innerHTML = '<div style="background: #f8d7da; border-left: 4px solid #dc3545; padding: 12px; border-radius: 5px; color: #721c24;"><strong>✗ Error:</strong><br>' + errorMsg + stderr + '</div>';
                    }
                } catch (error) {
                    statusDiv.innerHTML = '<div style="background: #f8d7da; border-left: 4px solid #dc3545; padding: 12px; border-radius: 5px; color: #721c24;"><strong>✗ Error:</strong><br>' + error.message + '</div>';
                } finally {
                    btn.disabled = false;
                    btn.textContent = 'Clone Database';
                }
            }
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html, scheduled_jobs=scheduled_jobs)


@dw_bp.route('/logs', methods=['GET'])
@login_required
def view_logs():
    """View sync logs from the logs directory"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    import os
    from pathlib import Path
    
    logs_dir = Path('logs')
    logs_list = []
    
    if logs_dir.exists():
        for log_file in sorted(logs_dir.glob('sync_*.log'), reverse=True)[:20]:
            logs_list.append({
                'name': log_file.name,
                'size': log_file.stat().st_size,
                'mtime': log_file.stat().st_mtime
            })
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sync Logs</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 900px; margin: 0 auto; }
            table { width: 100%; border-collapse: collapse; margin: 20px 0; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background-color: #0066cc; color: white; }
            tr:hover { background-color: #f5f5f5; }
            a { color: #0066cc; text-decoration: none; cursor: pointer; }
            a:hover { text-decoration: underline; }
            .back-link { margin-top: 20px; }
            .back-link a { color: #0066cc; }
            .log-content { background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0; max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
            .no-logs { padding: 20px; background-color: #f0f0f0; border-radius: 5px; text-align: center; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Sync Logs</h1>
            <p>Recent data warehouse sync logs:</p>
            
            {% if logs_list %}
            <table>
                <tr>
                    <th>Timestamp</th>
                    <th>Size</th>
                    <th>Action</th>
                </tr>
                {% for log in logs_list %}
                <tr>
                    <td>{{ log.name }}</td>
                    <td>{{ log.size }} bytes</td>
                    <td><a onclick="viewLog('{{ log.name }}')">View</a> | <a href="/datawarehouse/log-download/{{ log.name }}" target="_blank">Download</a></td>
                </tr>
                {% endfor %}
            </table>
            
            <div id="log-viewer" style="display:none;">
                <h2>Log Content</h2>
                <button onclick="closeLogViewer()">← Close</button>
                <div class="log-content" id="log-content"></div>
            </div>
            {% else %}
            <div class="no-logs">
                <p>No logs found yet. Run a full or incremental sync to generate logs.</p>
            </div>
            {% endif %}
            
            <div class="back-link">
                <a href="/datawarehouse/menu">← Back to Data Warehouse</a>
            </div>
        </div>
        
        <script>
            async function viewLog(filename) {
                try {
                    const response = await fetch('/datawarehouse/log-content/' + filename);
                    const data = await response.json();
                    document.querySelector('#log-content').textContent = data.content;
                    document.querySelector('#log-viewer').style.display = 'block';
                    window.scrollTo(0, 0);
                } catch (error) {
                    alert('Error loading log: ' + error.message);
                }
            }
            
            function closeLogViewer() {
                document.querySelector('#log-viewer').style.display = 'none';
            }
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html, logs_list=logs_list)


@dw_bp.route('/log-content/<filename>', methods=['GET'])
@login_required
def get_log_content(filename):
    """Get log file content as JSON"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        import os
        from pathlib import Path
        
        # Sanitize filename to prevent path traversal
        if '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        log_file = Path('logs') / filename
        if not log_file.exists():
            return jsonify({'error': 'Log not found'}), 404
        
        with open(log_file, 'r') as f:
            content = f.read()
        
        return jsonify({'content': content})
    except Exception as e:
        logger.error(f"Error reading log: {str(e)}")
        return jsonify({'error': str(e)}), 500


@dw_bp.route('/log-download/<filename>', methods=['GET'])
@login_required
def download_log(filename):
    """Download log file"""
    if current_user.role != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        from flask import send_file
        from pathlib import Path
        
        # Sanitize filename to prevent path traversal
        if '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        log_file = Path('logs') / filename
        if not log_file.exists():
            return jsonify({'error': 'Log not found'}), 404
        
        return send_file(str(log_file), as_attachment=True, download_name=filename)
    except Exception as e:
        logger.error(f"Error downloading log: {str(e)}")
        return jsonify({'error': str(e)}), 500


@dw_bp.route('/invoice-sync-status', methods=['GET'])
def invoice_sync_status():
    """Return the current invoice sync status as JSON (no auth required - status endpoint only)"""
    try:
        status_obj = db.session.get(SyncState, "invoice_sync_status")
        if status_obj and status_obj.value:
            parts = status_obj.value.split('|', 1)
            status = parts[0]
            message = parts[1] if len(parts) > 1 else ''
            return jsonify({'status': status, 'message': message})
        return jsonify({'status': 'IDLE', 'message': 'No sync in progress'})
    except Exception as e:
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500


@dw_bp.route('/invoice-sync', methods=['GET', 'POST'])
@login_required
def invoice_sync():
    """Sync invoices from PS365 for a date range"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        date_from = request.form.get('date_from', '')
        date_to = request.form.get('date_to', '')
        
        if not date_from:
            flash('Please select a start date', 'error')
            return redirect(url_for('datawarehouse.invoice_sync'))
        
        # Start sync in background with app context
        def run_sync():
            with app.app_context():
                try:
                    sync_invoices_from_date(db.session, date_from, date_to if date_to else None)
                    logger.info("Invoice sync completed successfully")
                except Exception as e:
                    logger.error(f"Invoice sync failed: {str(e)}", exc_info=True)
        
        thread = threading.Thread(target=run_sync, daemon=True)
        thread.start()
        
        date_range_msg = f'{date_from} to {date_to}' if date_to else f'{date_from} to today'
        flash(f'Invoice sync started for dates {date_range_msg}. Check logs for progress.', 'success')
        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Invoice Sync - In Progress</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .container { max-width: 600px; margin: 0 auto; }
                .progress { background-color: #f0f0f0; padding: 20px; border-radius: 5px; }
                .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #0066cc; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 20px auto; display: block; }
                .spinner.hidden { display: none; }
                @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                .status { margin-top: 20px; padding: 15px; background-color: #d1ecf1; border-radius: 5px; }
                .status.complete { background-color: #d4edda; }
                .status.error { background-color: #f8d7da; }
                .status-msg { font-size: 16px; color: #004085; }
                .status.complete .status-msg { color: #155724; }
                .status.error .status-msg { color: #721c24; }
                .checkmark { font-size: 40px; text-align: center; display: none; }
                .checkmark.visible { display: block; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Invoice Sync from """ + date_from + (f""" to {date_to}""" if date_to else " to today") + """</h1>
                <div class="progress">
                    <div class="spinner" id="spinner"></div>
                    <div class="checkmark" id="checkmark">✅</div>
                    <div class="status" id="status-box">
                        <div class="status-msg" id="status-msg">Starting sync...</div>
                    </div>
                </div>
                <p style="margin-top: 30px; text-align: center;">
                    <a href="/datawarehouse/logs" style="color: #0066cc; text-decoration: none;">View Sync Logs</a> | 
                    <a href="/datawarehouse/menu" style="color: #0066cc; text-decoration: none;">Back to Menu</a>
                </p>
            </div>
            <script>
                let syncComplete = false;
                
                function checkStatus() {
                    fetch('/datawarehouse/invoice-sync-status')
                        .then(response => response.json())
                        .then(data => {
                            const statusBox = document.getElementById('status-box');
                            const statusMsg = document.getElementById('status-msg');
                            const spinner = document.getElementById('spinner');
                            const checkmark = document.getElementById('checkmark');
                            
                            // Display summary of results
                            let displayMsg = data.message || data.status;
                            if (displayMsg && displayMsg.includes(':')) {
                                displayMsg = '<strong>✅ Sync Completed Successfully!</strong><br><br><strong>Summary:</strong><br>' + displayMsg.replace(/,\s*/g, '<br>');
                            }
                            
                            if (data.status === 'COMPLETE') {
                                syncComplete = true;
                                statusBox.className = 'status complete';
                                spinner.classList.add('hidden');
                                checkmark.classList.add('visible');
                                statusMsg.innerHTML = displayMsg;
                                // Redirect after user sees the completion for 4 seconds
                                setTimeout(() => {
                                    window.location.href = '/datawarehouse/menu?sync_complete=1';
                                }, 4000);
                            } else if (data.status === 'FAILED' || data.status === 'ERROR') {
                                syncComplete = true;
                                statusBox.className = 'status error';
                                spinner.classList.add('hidden');
                                statusMsg.innerHTML = '<strong>❌ Sync Failed</strong><br>' + displayMsg;
                            } else if (data.status === 'RUNNING' || data.status === 'IDLE') {
                                statusMsg.innerHTML = '<strong>Processing...</strong><br>' + displayMsg;
                                if (!syncComplete) {
                                    setTimeout(checkStatus, 2000);
                                }
                            }
                        })
                        .catch(err => {
                            console.error('Status check failed:', err);
                            if (!syncComplete) {
                                setTimeout(checkStatus, 3000);
                            }
                        });
                }
                // Start checking status after 1 second
                setTimeout(checkStatus, 1000);
            </script>
        </body>
        </html>
        """)
    
    # GET request - show form
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sync Invoices from PS365</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 600px; margin: 0 auto; }
            .form-group { margin: 20px 0; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input[type="date"] { padding: 8px; width: 100%; box-sizing: border-box; font-size: 16px; }
            button { background-color: #0066cc; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
            button:hover { background-color: #0052a3; }
            .info { background-color: #e8f4f8; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
            .info p { margin: 5px 0; }
            .back-link { margin-top: 20px; }
            .back-link a { color: #0066cc; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Sync Invoices from PS365</h1>
            <div class="info">
                <p><strong>How it works:</strong></p>
                <p>Select a date range to sync invoice headers and line items from PS365.</p>
                <p>If you only specify a start date, it will sync from that date to today.</p>
                <p>Duplicates are automatically prevented using hash-based comparison.</p>
            </div>
            <form method="POST">
                <div class="form-group">
                    <label for="date_from">From (YYYY-MM-DD):</label>
                    <input type="date" id="date_from" name="date_from" value=\"""" + today + """\" required>
                </div>
                <div class="form-group">
                    <label for="date_to">To (YYYY-MM-DD) - Optional:</label>
                    <input type="date" id="date_to" name="date_to" value=\"""" + today + """\" placeholder="Leave empty for today">
                </div>
                <button type="submit">Start Invoice Sync</button>
            </form>
            <div class="back-link">
                <a href="/datawarehouse/menu">← Back to Data Warehouse Menu</a>
            </div>
        </div>
    </body>
    </html>
    """)


@dw_bp.route('/invoice-lines-preview')
@login_required
def invoice_lines_preview():
    """Temporary in-memory preview of invoice headers and lines from PS365"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    try:
        # default from date 2025-11-21 if not provided (date only, no time)
        date_from = request.args.get("from", "2025-11-21")

        # Fetch both headers and lines
        headers = fetch_invoice_headers_from_date(date_from)
        lines = fetch_invoice_lines_from_date(date_from)

        # Header columns
        header_columns = [
            "invoice_no_365",
            "invoice_type",
            "invoice_date_utc0",
            "customer_code_365",
            "customer_name",
            "store_code_365",
            "user_code_365",
            "total_sub",
            "total_discount",
            "total_vat",
            "total_grand",
        ]

        # Line columns
        line_columns = [
            "invoice_no_365",
            "item_code_365",
            "item_name",
            "qty",
            "price_excl",
            "price_incl",
            "vat_percent",
            "line_total_excl",
            "line_total_incl",
        ]

        return render_template(
            "dw_invoice_lines_preview.html",
            headers=headers,
            header_columns=header_columns,
            lines=lines,
            line_columns=line_columns,
            date_from=date_from,
            header_count=len(headers),
            line_count=len(lines),
        )
    except Exception as e:
        logger.error(f"Error fetching invoice preview: {str(e)}", exc_info=True)
        flash(f'Error fetching invoice preview: {str(e)}', 'error')
        return redirect(url_for('datawarehouse.dw_menu'))


# Blueprint will be registered by routes.py
