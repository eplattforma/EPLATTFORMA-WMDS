"""
Help and Documentation Routes
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from models import Setting
import json

help_bp = Blueprint('help', __name__)

@help_bp.route('/help')
@login_required
def help_dashboard():
    """Main help dashboard"""
    return render_template('help_dashboard.html')

@help_bp.route('/admin/help/manage')
@login_required
def admin_help_manage():
    """Admin interface to manage help content"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('help.help_dashboard'))
    
    # Get all help content from settings
    help_sections = {}
    
    # Get existing help content
    sections = [
        'system_overview',
        'picking_process',
        'batch_picking',
        'shipment_management',
        'user_management',
        'troubleshooting',
        'status_definitions',
        'time_tracking'
    ]
    
    for section in sections:
        setting = Setting.query.filter_by(key=f'help_{section}').first()
        if setting:
            try:
                help_sections[section] = json.loads(setting.value)
            except:
                help_sections[section] = {'title': section.replace('_', ' ').title(), 'content': setting.value}
        else:
            help_sections[section] = {'title': section.replace('_', ' ').title(), 'content': ''}
    
    return render_template('admin_help_manage.html', help_sections=help_sections)

@help_bp.route('/admin/help/save', methods=['POST'])
@login_required
def admin_help_save():
    """Save help content"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('help.help_dashboard'))
    
    try:
        section = request.form.get('section')
        title = request.form.get('title', '')
        content = request.form.get('content', '')
        
        if not section:
            flash('Section is required.', 'error')
            return redirect(url_for('help.admin_help_manage'))
        
        # Save help content
        help_data = {
            'title': title,
            'content': content,
            'last_updated': 'Updated by ' + current_user.username
        }
        
        setting = Setting.query.filter_by(key=f'help_{section}').first()
        if setting:
            setting.value = json.dumps(help_data)
        else:
            setting = Setting(key=f'help_{section}', value=json.dumps(help_data))
            db.session.add(setting)
        
        db.session.commit()
        flash(f'Help section "{title}" saved successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving help content: {str(e)}', 'danger')
        
    return redirect(url_for('help.admin_help_manage'))

@help_bp.route('/help/<section>')
@login_required
def help_section(section):
    """Display specific help section"""
    setting = Setting.query.filter_by(key=f'help_{section}').first()
    if setting:
        try:
            help_data = json.loads(setting.value)
        except:
            help_data = {'title': section.replace('_', ' ').title(), 'content': setting.value}
    else:
        help_data = {'title': section.replace('_', ' ').title(), 'content': 'No content available for this section.'}
    
    return render_template('help_section.html', section=section, help_data=help_data)