"""
Main analysis runner for warehouse picking AI insights
Coordinates data loading, analysis, recommendations, and model training
"""
from data_preparation import load_picking_data, get_picker_statistics, get_zone_statistics, get_item_statistics
from analysis import (
    picker_performance, zone_analysis, time_based_analysis, 
    item_complexity_analysis, efficiency_trends, location_hotspots,
    generate_performance_summary
)
from recommender import generate_recommendations, prioritize_recommendations
from model_trainer import train_and_evaluate_model, PickingTimePredictor
from datetime import datetime, timedelta
import pandas as pd

def run_comprehensive_analysis(date_from=None, date_to=None, picker_username=None, zone=None):
    """
    Run comprehensive AI analysis on warehouse picking data
    
    Args:
        date_from: Start date for analysis
        date_to: End date for analysis
        picker_username: Filter by specific picker
        zone: Filter by specific zone
        
    Returns:
        Dictionary with complete analysis results
    """
    try:
        # Load data
        df = load_picking_data(date_from, date_to, picker_username, zone)
        
        if df.empty:
            return {
                'success': False,
                'message': 'No picking data found for the specified criteria',
                'data_points': 0
            }
        
        # Generate performance summary
        summary = generate_performance_summary(df)
        
        # Picker analysis
        picker_stats = picker_performance(df)
        
        # Zone analysis
        zone_stats = zone_analysis(df)
        
        # Time-based analysis
        hourly_perf, daily_perf = time_based_analysis(df)
        
        # Item complexity analysis
        item_stats = item_complexity_analysis(df)
        
        # Efficiency trends
        daily_trends, efficiency_trend, time_trend = efficiency_trends(df)
        
        # Location hotspots
        location_stats = location_hotspots(df)
        
        # Generate recommendations
        recommendations = generate_recommendations(df)
        prioritized_recommendations = prioritize_recommendations(recommendations)
        
        # Train ML model if enough data
        model_results = None
        if len(df) >= 20:  # Minimum data for meaningful model
            model_results = train_and_evaluate_model(df)
        
        return {
            'success': True,
            'data_points': len(df),
            'date_range': {
                'from': df['start_time'].min() if not df.empty else None,
                'to': df['start_time'].max() if not df.empty else None
            },
            'summary': summary,
            'picker_performance': picker_stats.to_dict('records') if not picker_stats.empty else [],
            'zone_performance': zone_stats.to_dict('records') if not zone_stats.empty else [],
            'time_analysis': {
                'hourly': hourly_perf,
                'daily': daily_perf
            },
            'item_analysis': item_stats.to_dict('records') if not item_stats.empty else [],
            'trends': {
                'daily_efficiency': daily_trends.to_dict('records') if not daily_trends.empty else [],
                'efficiency_trend': efficiency_trend,
                'time_trend': time_trend
            },
            'location_hotspots': location_stats.to_dict('records') if not location_stats.empty else [],
            'recommendations': prioritized_recommendations,
            'model_results': model_results,
            'analysis_timestamp': datetime.now().isoformat()
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'data_points': 0
        }

def generate_insights_report(analysis_results):
    """
    Generate a human-readable insights report from analysis results
    """
    if not analysis_results.get('success'):
        return f"Analysis failed: {analysis_results.get('error', 'Unknown error')}"
    
    report = []
    summary = analysis_results.get('summary', {})
    
    # Executive Summary
    report.append("=== WAREHOUSE PICKING PERFORMANCE ANALYSIS ===\n")
    report.append(f"Analysis Period: {analysis_results.get('date_range', {}).get('from')} to {analysis_results.get('date_range', {}).get('to')}")
    report.append(f"Total Data Points: {analysis_results.get('data_points', 0)}")
    report.append(f"Analysis Date: {analysis_results.get('analysis_timestamp', 'N/A')}")
    report.append("")
    
    # Key Metrics
    report.append("--- KEY PERFORMANCE METRICS ---")
    report.append(f"Total Picks: {summary.get('total_picks', 0):,}")
    report.append(f"Unique Pickers: {summary.get('unique_pickers', 0)}")
    report.append(f"Unique Items: {summary.get('unique_items', 0)}")
    report.append(f"Average Time per Pick: {summary.get('avg_time_per_pick', 0):.1f} seconds")
    report.append(f"Overall Efficiency: {summary.get('avg_efficiency', 0):.2f}")
    report.append(f"Overall Accuracy: {summary.get('overall_accuracy', 0):.1f}%")
    report.append(f"Skip Rate: {summary.get('skip_rate', 0):.1f}%")
    report.append(f"Total Items Picked: {summary.get('total_items_picked', 0):,}")
    report.append(f"Total Weight Picked: {summary.get('total_weight_picked', 0):.1f} kg")
    report.append("")
    
    # Top Recommendations
    recommendations = analysis_results.get('recommendations', [])
    if recommendations:
        report.append("--- TOP PRIORITY RECOMMENDATIONS ---")
        for i, rec in enumerate(recommendations[:5], 1):
            report.append(f"{i}. {rec.get('title', 'Unknown')}")
            report.append(f"   Category: {rec.get('category', 'N/A')}")
            report.append(f"   Impact: {rec.get('impact_score', 'N/A')}/10")
            report.append(f"   Description: {rec.get('description', 'N/A')}")
            report.append("")
    
    # Zone Performance
    zone_perf = analysis_results.get('zone_performance', [])
    if zone_perf:
        report.append("--- ZONE PERFORMANCE SUMMARY ---")
        for zone in zone_perf:
            report.append(f"Zone {zone.get('zone', 'Unknown')}: {zone.get('total_time_seconds_mean', 0):.1f}s avg")
        report.append("")
    
    # Model Results
    model_results = analysis_results.get('model_results')
    if model_results and model_results.get('success'):
        metrics = model_results.get('metrics', {})
        report.append("--- PREDICTIVE MODEL PERFORMANCE ---")
        report.append(f"Model Type: {metrics.get('model_type', 'Unknown')}")
        report.append(f"Prediction Accuracy (R²): {metrics.get('r2_score', 0):.3f}")
        report.append(f"Average Prediction Error: {metrics.get('mae', 0):.1f} seconds")
        report.append("")
    
    return "\n".join(report)

def quick_analysis(days_back=7):
    """
    Quick analysis for the last N days
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    
    return run_comprehensive_analysis(
        date_from=start_date,
        date_to=end_date
    )

def picker_comparison_analysis(picker1, picker2, days_back=30):
    """
    Compare performance between two pickers
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    
    # Get data for both pickers
    picker1_analysis = run_comprehensive_analysis(
        date_from=start_date,
        date_to=end_date,
        picker_username=picker1
    )
    
    picker2_analysis = run_comprehensive_analysis(
        date_from=start_date,
        date_to=end_date,
        picker_username=picker2
    )
    
    return {
        'picker1': picker1,
        'picker2': picker2,
        'picker1_data': picker1_analysis,
        'picker2_data': picker2_analysis,
        'comparison_timestamp': datetime.now().isoformat()
    }

def zone_deep_dive(zone_name, days_back=30):
    """
    Deep dive analysis for a specific zone
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    
    return run_comprehensive_analysis(
        date_from=start_date,
        date_to=end_date,
        zone=zone_name
    )