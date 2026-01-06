"""
Location validation utilities for shift check-in/check-out
"""
from math import radians, cos, sin, asin, sqrt

# Reference location (warehouse/facility)
REFERENCE_LATITUDE = 35.0470
REFERENCE_LONGITUDE = 33.3926
ALLOWED_RADIUS_METERS = 200

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    
    Returns distance in meters
    """
    try:
        # Convert decimal degrees to radians
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        
        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        r = 6371000  # Radius of earth in meters
        
        distance = c * r
        return distance
    except Exception as e:
        raise ValueError(f"Invalid coordinates: {str(e)}")

def validate_location(coordinates_str):
    """
    Validate if the given coordinates are within allowed radius
    
    Args:
        coordinates_str: String in format "latitude,longitude"
        
    Returns:
        dict with 'valid': bool, 'distance': float (meters), 'message': str
    """
    try:
        if not coordinates_str:
            return {
                'valid': False,
                'distance': None,
                'message': 'Location data not captured. Please enable location services and try again.'
            }
        
        parts = coordinates_str.split(',')
        if len(parts) != 2:
            return {
                'valid': False,
                'distance': None,
                'message': 'Invalid location format.'
            }
        
        picker_lat = float(parts[0])
        picker_lon = float(parts[1])
        
        distance = calculate_distance(REFERENCE_LATITUDE, REFERENCE_LONGITUDE, picker_lat, picker_lon)
        
        if distance <= ALLOWED_RADIUS_METERS:
            return {
                'valid': True,
                'distance': distance,
                'message': f'Location verified ({distance:.1f}m from facility)'
            }
        else:
            return {
                'valid': False,
                'distance': distance,
                'message': f'You are {distance:.1f}m from the facility. You must be within {ALLOWED_RADIUS_METERS}m to check in/out.'
            }
    
    except ValueError as e:
        return {
            'valid': False,
            'distance': None,
            'message': f'Error validating location: {str(e)}'
        }
