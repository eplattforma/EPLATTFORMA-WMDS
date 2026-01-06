"""
Analysis module for warehouse picking performance insights
Computes averages, trends, and performance metrics
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def average_times_by_unit_type(df):
    """
    Calculate average picking times by unit type
    """
    if df.empty:
        return {}
    
    return df.groupby("unit_type")["total_time_seconds"].mean().to_dict()

def average_times_by_zone(df):
    """
    Calculate average picking times by zone
    """
    if df.empty:
        return {}
    
    return df.groupby("zone")["total_time_seconds"].mean().to_dict()

def picker_performance(df):
    """
    Calculate detailed picker performance metrics
    """
    if df.empty:
        return pd.DataFrame()
    
    performance = df.groupby("picker_username").agg({
        "total_time_seconds": ["mean", "std", "count"],
        "picking_efficiency": "mean",
        "picked_correctly": ["mean", "sum"],
        "was_skipped": ["mean", "sum"],
        "items_per_second": "mean",
        "location_complexity": "mean",
        "total_weight": "sum"
    }).round(2)
    
    # Flatten column names
    performance.columns = ['_'.join(col).strip() for col in performance.columns]
    performance = performance.reset_index()
    
    # Add performance ranking
    performance['efficiency_rank'] = performance['picking_efficiency_mean'].rank(ascending=False)
    performance['speed_rank'] = performance['items_per_second_mean'].rank(ascending=False)
    
    return performance

def zone_analysis(df):
    """
    Analyze zone performance and identify bottlenecks
    """
    if df.empty:
        return pd.DataFrame()
    
    zone_metrics = df.groupby("zone").agg({
        "total_time_seconds": ["mean", "std", "count"],
        "walking_time_seconds": "mean",
        "picking_time_seconds": "mean",
        "picking_efficiency": "mean",
        "location_complexity": "mean",
        "was_skipped": ["mean", "sum"],
        "picked_correctly": "mean"
    }).round(2)
    
    # Flatten column names
    zone_metrics.columns = ['_'.join(col).strip() for col in zone_metrics.columns]
    zone_metrics = zone_metrics.reset_index()
    
    # Identify problem zones
    overall_avg_time = df["total_time_seconds"].mean()
    zone_metrics['performance_vs_average'] = zone_metrics['total_time_seconds_mean'] / overall_avg_time
    zone_metrics['needs_attention'] = zone_metrics['performance_vs_average'] > 1.2
    
    return zone_metrics

def time_based_analysis(df):
    """
    Analyze performance by time of day and day of week
    """
    if df.empty:
        return {}, {}
    
    # Hour of day analysis
    hourly_performance = df.groupby("hour_of_day").agg({
        "total_time_seconds": "mean",
        "picking_efficiency": "mean",
        "picked_correctly": "mean"
    }).round(2).to_dict()
    
    # Day of week analysis
    daily_performance = df.groupby("day_of_week").agg({
        "total_time_seconds": "mean",
        "picking_efficiency": "mean",
        "picked_correctly": "mean"
    }).round(2).to_dict()
    
    return hourly_performance, daily_performance

def item_complexity_analysis(df):
    """
    Analyze items by complexity and performance
    """
    if df.empty:
        return pd.DataFrame()
    
    item_analysis = df.groupby(["item_code", "item_name"]).agg({
        "total_time_seconds": ["mean", "std", "count"],
        "picking_efficiency": "mean",
        "location_complexity": "mean",
        "unit_type": "first",
        "weight_per_unit": "first",
        "was_skipped": ["mean", "sum"],
        "picked_correctly": "mean"
    }).round(2)
    
    # Flatten column names
    item_analysis.columns = ['_'.join(col).strip() for col in item_analysis.columns]
    item_analysis = item_analysis.reset_index()
    
    # Identify problematic items
    overall_avg_time = df["total_time_seconds"].mean()
    item_analysis['performance_vs_average'] = item_analysis['total_time_seconds_mean'] / overall_avg_time
    item_analysis['frequently_skipped'] = item_analysis['was_skipped_mean'] > 0.1
    item_analysis['low_accuracy'] = item_analysis['picked_correctly_mean'] < 0.9
    
    return item_analysis

def efficiency_trends(df, days_back=30):
    """
    Calculate efficiency trends over time
    """
    if df.empty:
        return pd.DataFrame()
    
    # Convert start_time to date
    df['pick_date'] = pd.to_datetime(df['start_time']).dt.date
    
    # Calculate daily efficiency
    daily_efficiency = df.groupby('pick_date').agg({
        'picking_efficiency': 'mean',
        'total_time_seconds': 'mean',
        'picked_correctly': 'mean',
        'tracking_id': 'count'
    }).round(2)
    
    daily_efficiency.columns = ['avg_efficiency', 'avg_time', 'accuracy', 'pick_count']
    daily_efficiency = daily_efficiency.reset_index()
    
    # Calculate trend (simple linear regression slope)
    if len(daily_efficiency) > 1:
        x = np.arange(len(daily_efficiency))
        efficiency_trend = np.polyfit(x, daily_efficiency['avg_efficiency'], 1)[0]
        time_trend = np.polyfit(x, daily_efficiency['avg_time'], 1)[0]
    else:
        efficiency_trend = 0
        time_trend = 0
    
    return daily_efficiency, efficiency_trend, time_trend

def location_hotspots(df):
    """
    Identify location hotspots and problem areas
    """
    if df.empty:
        return pd.DataFrame()
    
    location_analysis = df.groupby(["zone", "corridor", "level"]).agg({
        "total_time_seconds": ["mean", "count"],
        "walking_time_seconds": "mean",
        "picking_time_seconds": "mean",
        "was_skipped": "mean",
        "picked_correctly": "mean"
    }).round(2)
    
    # Flatten column names
    location_analysis.columns = ['_'.join(col).strip() for col in location_analysis.columns]
    location_analysis = location_analysis.reset_index()
    
    # Filter locations with enough data
    location_analysis = location_analysis[location_analysis['total_time_seconds_count'] >= 5]
    
    # Identify problem locations
    if not location_analysis.empty:
        overall_avg = df["total_time_seconds"].mean()
        location_analysis['performance_score'] = location_analysis['total_time_seconds_mean'] / overall_avg
        location_analysis['problem_location'] = (
            (location_analysis['performance_score'] > 1.3) | 
            (location_analysis['was_skipped_mean'] > 0.15) |
            (location_analysis['picked_correctly_mean'] < 0.85)
        )
    
    return location_analysis

def generate_performance_summary(df):
    """
    Generate overall performance summary
    """
    if df.empty:
        return {}
    
    summary = {
        'total_picks': len(df),
        'unique_pickers': df['picker_username'].nunique(),
        'unique_items': df['item_code'].nunique(),
        'avg_time_per_pick': round(df['total_time_seconds'].mean(), 2),
        'avg_efficiency': round(df['picking_efficiency'].mean(), 2),
        'overall_accuracy': round(df['picked_correctly'].mean() * 100, 1),
        'skip_rate': round(df['was_skipped'].mean() * 100, 1),
        'total_items_picked': int(df['picked_qty'].sum()),
        'total_weight_picked': round(df['total_weight'].sum(), 2),
        'most_active_zone': df['zone'].mode().iloc[0] if not df['zone'].mode().empty else 'N/A',
        'most_efficient_picker': df.loc[df['picking_efficiency'].idxmax(), 'picker_username'] if not df.empty else 'N/A',
        'slowest_zone': df.groupby('zone')['total_time_seconds'].mean().idxmax() if not df.empty else 'N/A'
    }
    
    return summary