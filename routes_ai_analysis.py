"""
Routes for AI Analysis and Insights Dashboard
"""
from flask import render_template, request, jsonify, flash, redirect, url_for
from datetime import datetime, timedelta
from app import app, db
from models import User
from flask_login import login_required, current_user
from run_analysis import run_comprehensive_analysis, generate_insights_report, quick_analysis, picker_comparison_analysis, zone_deep_dive
import json
import numpy as np
import pandas as pd

def sanitize_for_json(obj):
    """Convert numpy/pandas types and NaN values to JSON-serializable types"""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, pd.DataFrame):
        return obj.replace([np.inf, -np.inf], None).fillna(None).to_dict('records')
    elif isinstance(obj, pd.Series):
        return obj.replace([np.inf, -np.inf], None).fillna(None).to_dict()
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    elif pd.isna(obj) or (isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj))):
        return None
    else:
        return obj

@app.route('/ai_analysis_dashboard')
@login_required
def ai_analysis_dashboard():
    """Main AI insights dashboard"""
    # Get available pickers and zones for filters
    from models import ItemTimeTracking
    
    available_pickers = db.session.query(ItemTimeTracking.picker_username).distinct().filter(
        ItemTimeTracking.picker_username.isnot(None)
    ).all()
    available_pickers = [p[0] for p in available_pickers if p[0]]
    
    available_zones = db.session.query(ItemTimeTracking.zone).distinct().filter(
        ItemTimeTracking.zone.isnot(None)
    ).all()
    available_zones = [z[0] for z in available_zones if z[0]]
    
    return render_template('ai_insights_dashboard.html',
                         pickers=available_pickers,
                         zones=available_zones)

@app.route('/ai_insights/run_analysis', methods=['POST'])
@login_required
def run_ai_analysis():
    """Run AI analysis with specified parameters"""
    try:
        data = request.get_json()
        
        # Parse date inputs
        date_from = None
        date_to = None
        
        if data.get('date_from'):
            date_from = datetime.strptime(data['date_from'], '%Y-%m-%d')
        
        if data.get('date_to'):
            date_to = datetime.strptime(data['date_to'], '%Y-%m-%d')
        
        picker_username = data.get('picker_username') if data.get('picker_username') else None
        zone = data.get('zone') if data.get('zone') else None
        
        # Run comprehensive analysis
        results = run_comprehensive_analysis(
            date_from=date_from,
            date_to=date_to,
            picker_username=picker_username,
            zone=zone
        )
        
        # Sanitize results for JSON serialization
        clean_results = sanitize_for_json(results)
        
        return jsonify(clean_results)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/ai_insights/quick_analysis/<int:days>')
@login_required
def quick_ai_analysis(days):
    """Quick analysis for last N days"""
    try:
        results = quick_analysis(days_back=days)
        clean_results = sanitize_for_json(results)
        return jsonify(clean_results)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/ai_insights/picker_comparison', methods=['POST'])
@login_required
def ai_picker_comparison():
    """Compare performance between two pickers"""
    try:
        data = request.get_json()
        picker1 = data.get('picker1')
        picker2 = data.get('picker2')
        days_back = data.get('days_back', 30)
        
        if not picker1 or not picker2:
            return jsonify({
                'success': False,
                'error': 'Both pickers must be specified'
            }), 400
        
        results = picker_comparison_analysis(picker1, picker2, days_back)
        clean_results = sanitize_for_json(results)
        return jsonify(clean_results)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/ai_insights/zone_analysis/<zone_name>')
@login_required
def ai_zone_analysis(zone_name):
    """Deep dive analysis for a specific zone"""
    try:
        days_back = request.args.get('days', 30, type=int)
        results = zone_deep_dive(zone_name, days_back=days_back)
        return jsonify(results)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/ai_insights/generate_report', methods=['POST'])
@login_required
def generate_ai_report():
    """Generate a text report from analysis results"""
    try:
        data = request.get_json()
        analysis_results = data.get('analysis_results')
        
        if not analysis_results:
            return jsonify({
                'success': False,
                'error': 'Analysis results required'
            }), 400
        
        report = generate_insights_report(analysis_results)
        
        return jsonify({
            'success': True,
            'report': report
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/ai_insights/recommendations')
@login_required
def ai_recommendations():
    """Get AI recommendations for the last 7 days"""
    try:
        results = quick_analysis(days_back=7)
        
        if results.get('success'):
            recommendations = results.get('recommendations', [])
            return render_template('ai_recommendations.html', 
                                 recommendations=recommendations,
                                 analysis_data=results)
        else:
            flash('Unable to generate recommendations: ' + results.get('error', 'Unknown error'), 'error')
            return redirect(url_for('ai_insights_dashboard'))
            
    except Exception as e:
        flash(f'Error generating recommendations: {str(e)}', 'error')
        return redirect(url_for('ai_insights_dashboard'))

@app.route('/ai_insights/performance_trends')
@login_required
def ai_performance_trends():
    """Show performance trends over time"""
    try:
        days_back = request.args.get('days', 30, type=int)
        results = quick_analysis(days_back=days_back)
        
        if results.get('success'):
            return render_template('ai_performance_trends.html',
                                 trends_data=results.get('trends', {}),
                                 analysis_data=results)
        else:
            flash('Unable to load performance trends: ' + results.get('error', 'Unknown error'), 'error')
            return redirect(url_for('ai_insights_dashboard'))
            
    except Exception as e:
        flash(f'Error loading performance trends: {str(e)}', 'error')
        return redirect(url_for('ai_insights_dashboard'))

@app.route('/ai_insights/export_data', methods=['POST'])
@login_required
def export_ai_data():
    """Export analysis data in various formats"""
    try:
        data = request.get_json()
        analysis_results = data.get('analysis_results')
        export_format = data.get('format', 'json')
        
        if not analysis_results:
            return jsonify({
                'success': False,
                'error': 'Analysis results required'
            }), 400
        
        if export_format == 'json':
            return jsonify({
                'success': True,
                'data': analysis_results,
                'filename': f'ai_analysis_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            })
        elif export_format == 'text':
            report = generate_insights_report(analysis_results)
            return jsonify({
                'success': True,
                'data': report,
                'filename': f'ai_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Unsupported export format'
            }), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500