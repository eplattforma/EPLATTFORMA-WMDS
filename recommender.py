"""
Recommendation engine for warehouse picking optimization
Generates actionable insights based on performance data
"""
import pandas as pd
import numpy as np

def generate_recommendations(df):
    """
    Generate AI-powered recommendations based on picking data analysis
    
    Args:
        df: DataFrame with picking performance data
        
    Returns:
        List of recommendation dictionaries with priority and action items
    """
    recommendations = []
    
    if df.empty:
        return recommendations
    
    # 1. Zone Performance Analysis
    zone_recommendations = analyze_zone_performance(df)
    recommendations.extend(zone_recommendations)
    
    # 2. Picker Performance Analysis
    picker_recommendations = analyze_picker_performance(df)
    recommendations.extend(picker_recommendations)
    
    # 3. Item-Specific Recommendations
    item_recommendations = analyze_item_performance(df)
    recommendations.extend(item_recommendations)
    
    # 4. Location Layout Recommendations
    location_recommendations = analyze_location_efficiency(df)
    recommendations.extend(location_recommendations)
    
    # 5. Time-Based Recommendations
    time_recommendations = analyze_time_patterns(df)
    recommendations.extend(time_recommendations)
    
    # Sort by priority (high=1, medium=2, low=3)
    recommendations.sort(key=lambda x: x['priority'])
    
    return recommendations

def analyze_zone_performance(df):
    """Analyze zone performance and generate zone-specific recommendations"""
    recommendations = []
    
    # Calculate zone statistics
    zone_stats = df.groupby('zone').agg({
        'total_time_seconds': ['mean', 'std', 'count'],
        'walking_time_seconds': 'mean',
        'picking_time_seconds': 'mean',
        'was_skipped': 'mean',
        'picked_correctly': 'mean'
    }).round(2)
    
    zone_stats.columns = ['_'.join(col).strip() for col in zone_stats.columns]
    zone_stats = zone_stats.reset_index()
    
    overall_avg_time = df['total_time_seconds'].mean()
    
    for _, zone in zone_stats.iterrows():
        zone_name = zone['zone']
        avg_time = zone['total_time_seconds_mean']
        skip_rate = zone['was_skipped_mean']
        accuracy = zone['picked_correctly_mean']
        pick_count = zone['total_time_seconds_count']
        
        # Skip zones with insufficient data
        if pick_count < 5:
            continue
            
        # Slow zone detection
        if avg_time > overall_avg_time * 1.3:
            recommendations.append({
                'category': 'Zone Performance',
                'priority': 1,  # High priority
                'title': f'Zone {zone_name} Performance Issue',
                'description': f'Zone {zone_name} is {((avg_time / overall_avg_time - 1) * 100):.1f}% slower than average',
                'action_items': [
                    'Review zone layout and accessibility',
                    'Check if items are properly organized',
                    'Consider relocating high-volume items closer to main aisles',
                    'Evaluate lighting and signage in this zone'
                ],
                'metrics': {
                    'current_avg_time': f'{avg_time:.1f}s',
                    'target_improvement': f'{((avg_time - overall_avg_time) / avg_time * 100):.1f}%'
                }
            })
        
        # High skip rate detection
        if skip_rate > 0.15:  # More than 15% skip rate
            recommendations.append({
                'category': 'Zone Accessibility',
                'priority': 2,  # Medium priority
                'title': f'High Skip Rate in Zone {zone_name}',
                'description': f'Zone {zone_name} has a {(skip_rate * 100):.1f}% skip rate',
                'action_items': [
                    'Investigate common reasons for skipping items in this zone',
                    'Check stock availability and replenishment frequency',
                    'Review item placement and accessibility',
                    'Consider staff training for this zone'
                ],
                'metrics': {
                    'current_skip_rate': f'{(skip_rate * 100):.1f}%',
                    'target_skip_rate': '< 10%'
                }
            })
        
        # Low accuracy detection
        if accuracy < 0.9:  # Less than 90% accuracy
            recommendations.append({
                'category': 'Zone Quality',
                'priority': 1,  # High priority
                'title': f'Accuracy Issues in Zone {zone_name}',
                'description': f'Zone {zone_name} has {(accuracy * 100):.1f}% picking accuracy',
                'action_items': [
                    'Review labeling and item identification in this zone',
                    'Check for similar-looking items that cause confusion',
                    'Improve lighting and visibility',
                    'Provide additional training for complex items'
                ],
                'metrics': {
                    'current_accuracy': f'{(accuracy * 100):.1f}%',
                    'target_accuracy': '> 95%'
                }
            })
    
    return recommendations

def analyze_picker_performance(df):
    """Analyze picker performance and generate training recommendations"""
    recommendations = []
    
    picker_stats = df.groupby('picker_username').agg({
        'total_time_seconds': ['mean', 'std', 'count'],
        'efficiency_ratio': 'mean',
        'picked_correctly': 'mean',
        'was_skipped': 'mean'
    }).round(2)
    
    picker_stats.columns = ['_'.join(col).strip() for col in picker_stats.columns]
    picker_stats = picker_stats.reset_index()
    
    # Only analyze pickers with sufficient data
    picker_stats = picker_stats[picker_stats['total_time_seconds_count'] >= 10]
    
    if picker_stats.empty:
        return recommendations
    
    # Calculate performance quartiles
    efficiency_q75 = picker_stats['efficiency_ratio_mean'].quantile(0.75)
    efficiency_q25 = picker_stats['efficiency_ratio_mean'].quantile(0.25)
    
    # Identify underperforming pickers
    underperformers = picker_stats[picker_stats['efficiency_ratio_mean'] < efficiency_q25]
    
    if not underperformers.empty:
        recommendations.append({
            'category': 'Staff Training',
            'priority': 2,  # Medium priority
            'title': 'Picker Performance Development Opportunity',
            'description': f'{len(underperformers)} picker(s) could benefit from additional training',
            'action_items': [
                'Provide one-on-one coaching sessions',
                'Review picking techniques and best practices',
                'Pair with high-performing pickers for mentoring',
                'Focus on zone-specific training where needed'
            ],
            'metrics': {
                'pickers_needing_support': len(underperformers),
                'potential_improvement': f'{((efficiency_q75 - efficiency_q25) * 100):.1f}%'
            }
        })
    
    # Identify star performers for best practice sharing
    top_performers = picker_stats[picker_stats['efficiency_ratio_mean'] > efficiency_q75]
    
    if not top_performers.empty:
        recommendations.append({
            'category': 'Best Practices',
            'priority': 3,  # Low priority but valuable
            'title': 'Leverage Top Performer Expertise',
            'description': f'{len(top_performers)} picker(s) consistently outperform expectations',
            'action_items': [
                'Document their picking techniques and strategies',
                'Have them mentor other team members',
                'Create training materials based on their methods',
                'Consider them for team lead or training roles'
            ],
            'metrics': {
                'top_performers': len(top_performers),
                'average_efficiency': f'{top_performers["efficiency_ratio_mean"].mean():.2f}'
            }
        })
    
    return recommendations

def analyze_item_performance(df):
    """Analyze item-specific performance issues"""
    recommendations = []
    
    item_stats = df.groupby(['item_code', 'item_name']).agg({
        'total_time_seconds': ['mean', 'count'],
        'was_skipped': ['mean', 'sum'],
        'picked_correctly': 'mean',
        'zone': lambda x: x.mode().iloc[0] if not x.mode().empty else 'Unknown'
    }).round(2)
    
    item_stats.columns = ['_'.join(col).strip() if isinstance(col, tuple) else col for col in item_stats.columns]
    item_stats = item_stats.reset_index()
    
    # Filter items with sufficient data
    item_stats = item_stats[item_stats['total_time_seconds_count'] >= 3]
    
    if item_stats.empty:
        return recommendations
    
    # Identify frequently skipped items
    high_skip_items = item_stats[item_stats['was_skipped_mean'] > 0.2]  # More than 20% skip rate
    
    if not high_skip_items.empty:
        recommendations.append({
            'category': 'Inventory Management',
            'priority': 1,  # High priority
            'title': 'Frequently Skipped Items',
            'description': f'{len(high_skip_items)} items are frequently skipped during picking',
            'action_items': [
                'Review stock levels and replenishment schedules',
                'Check item placement and accessibility',
                'Verify item codes and labeling accuracy',
                'Consider relocating to more accessible locations'
            ],
            'metrics': {
                'affected_items': len(high_skip_items),
                'worst_skip_rate': f'{(high_skip_items["was_skipped_mean"].max() * 100):.1f}%'
            },
            'details': high_skip_items[['item_code', 'item_name', 'was_skipped_mean']].head(5).to_dict('records')
        })
    
    # Identify items with accuracy issues
    low_accuracy_items = item_stats[item_stats['picked_correctly_mean'] < 0.8]  # Less than 80% accuracy
    
    if not low_accuracy_items.empty:
        recommendations.append({
            'category': 'Item Quality Control',
            'priority': 1,  # High priority
            'title': 'Items with Accuracy Issues',
            'description': f'{len(low_accuracy_items)} items have picking accuracy below 80%',
            'action_items': [
                'Review item labeling and identification',
                'Check for similar items causing confusion',
                'Improve product images and descriptions',
                'Provide specific training for these items'
            ],
            'metrics': {
                'affected_items': len(low_accuracy_items),
                'worst_accuracy': f'{(low_accuracy_items["picked_correctly_mean"].min() * 100):.1f}%'
            },
            'details': low_accuracy_items[['item_code', 'item_name', 'picked_correctly_mean']].head(5).to_dict('records')
        })
    
    return recommendations

def analyze_location_efficiency(df):
    """Analyze location-based efficiency patterns"""
    recommendations = []
    
    if 'level' not in df.columns or df['level'].isna().all():
        return recommendations
    
    # Analyze performance by shelf level
    level_stats = df.groupby('level').agg({
        'total_time_seconds': ['mean', 'count'],
        'walking_time_seconds': 'mean',
        'picking_time_seconds': 'mean'
    }).round(2)
    
    level_stats.columns = ['_'.join(col).strip() for col in level_stats.columns]
    level_stats = level_stats.reset_index()
    
    # Filter levels with sufficient data
    level_stats = level_stats[level_stats['total_time_seconds_count'] >= 5]
    
    if not level_stats.empty:
        ground_level_time = level_stats[level_stats['level'] == '1']['total_time_seconds_mean'].iloc[0] if '1' in level_stats['level'].values else None
        
        if ground_level_time:
            high_levels = level_stats[
                (level_stats['level'].astype(str).str.isdigit()) &
                (level_stats['level'].astype(int) > 3) &
                (level_stats['total_time_seconds_mean'] > ground_level_time * 1.4)
            ]
            
            if not high_levels.empty:
                recommendations.append({
                    'category': 'Layout Optimization',
                    'priority': 2,  # Medium priority
                    'title': 'High Shelf Level Inefficiency',
                    'description': 'Items on high shelves take significantly longer to pick',
                    'action_items': [
                        'Move frequently picked items to lower levels',
                        'Use picking equipment for high shelves',
                        'Review safety procedures for high-level picking',
                        'Consider batch picking for high-level items'
                    ],
                    'metrics': {
                        'affected_levels': list(high_levels['level']),
                        'time_increase': f'{((high_levels["total_time_seconds_mean"].mean() / ground_level_time - 1) * 100):.1f}%'
                    }
                })
    
    return recommendations

def analyze_time_patterns(df):
    """Analyze time-based performance patterns"""
    recommendations = []
    
    if 'start_time' not in df.columns or df['start_time'].isna().all():
        return recommendations
    
    # Add hour analysis
    df['hour'] = pd.to_datetime(df['start_time']).dt.hour
    
    hourly_performance = df.groupby('hour').agg({
        'total_time_seconds': 'mean',
        'efficiency_ratio': 'mean',
        'picked_correctly': 'mean'
    }).round(2)
    
    if not hourly_performance.empty:
        # Identify slow hours
        avg_efficiency = hourly_performance['efficiency_ratio'].mean()
        slow_hours = hourly_performance[hourly_performance['efficiency_ratio'] < avg_efficiency * 0.8]
        
        if not slow_hours.empty:
            recommendations.append({
                'category': 'Schedule Optimization',
                'priority': 2,  # Medium priority
                'title': 'Time-Based Performance Variations',
                'description': f'Performance drops during certain hours: {list(slow_hours.index)}',
                'action_items': [
                    'Review staffing levels during slow periods',
                    'Check for distractions or interruptions',
                    'Consider break scheduling optimization',
                    'Analyze workload distribution throughout the day'
                ],
                'metrics': {
                    'affected_hours': list(slow_hours.index),
                    'efficiency_drop': f'{((1 - slow_hours["efficiency_ratio"].mean() / avg_efficiency) * 100):.1f}%'
                }
            })
    
    return recommendations

def prioritize_recommendations(recommendations):
    """
    Prioritize recommendations based on impact and effort
    """
    # Add impact and effort scores
    for rec in recommendations:
        # Calculate impact score (1-10)
        impact_factors = []
        
        if 'metrics' in rec:
            # High skip rates or low accuracy = high impact
            if any('skip_rate' in str(v) for v in rec['metrics'].values()):
                impact_factors.append(8)
            if any('accuracy' in str(v) for v in rec['metrics'].values()):
                impact_factors.append(9)
            if any('improvement' in str(v) for v in rec['metrics'].values()):
                impact_factors.append(7)
        
        rec['impact_score'] = np.mean(impact_factors) if impact_factors else 5
        
        # Estimate effort (1-10, where 1 is easy)
        effort_map = {
            'Zone Performance': 6,
            'Staff Training': 4,
            'Inventory Management': 3,
            'Layout Optimization': 8,
            'Schedule Optimization': 5,
            'Best Practices': 2
        }
        
        rec['effort_score'] = effort_map.get(rec['category'], 5)
        rec['roi_score'] = rec['impact_score'] / rec['effort_score']
    
    return sorted(recommendations, key=lambda x: x['roi_score'], reverse=True)