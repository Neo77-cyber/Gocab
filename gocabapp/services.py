import googlemaps
from django.conf import settings
from datetime import datetime
import logging
from django.core.cache import cache

logger = logging.getLogger(__name__)

gmaps = googlemaps.Client(key=settings.GOOGLE_MAPS_API_KEY)

def get_google_distance(origin, destination):
    """Get distance/duration from Google Matrix API with robust error handling"""
    try:
        # Cache key to avoid duplicate API calls
        cache_key = f"distance_{origin}_{destination}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return cached_result['distance'], cached_result['duration']
        
        result = gmaps.distance_matrix(
            origins=[origin],
            destinations=[destination],
            mode="driving",
            units="metric",
            departure_time=datetime.now(),  # Get traffic-aware estimates
            traffic_model="best_guess"
        )
        
        if result['status'] != 'OK':
            logger.error(f"Google API error: {result.get('error_message', 'Unknown error')}")
            return None, None
            
        element = result['rows'][0]['elements'][0]
        
        if element['status'] != 'OK':
            logger.error(f"Route error: {element.get('status', 'Unknown status')}")
            return None, None
            
        distance = element['distance']['value'] / 1000  # km
        duration = element['duration_in_traffic']['value'] / 60  # mins (with traffic)
        
        # Cache for 15 minutes
        cache.set(cache_key, {'distance': distance, 'duration': duration}, 900)
        
        return distance, duration
        
    except Exception as e:
        logger.error(f"Distance matrix error: {str(e)}")
        return None, None

def calculate_ride_fare(distance_km, duration_min, vehicle_type='standard'):
    """
    Calculate fare breakdown with enhanced features
    
    Args:
        distance_km: Distance in kilometers
        duration_min: Duration in minutes
        vehicle_type: Vehicle type (standard, premium, xl)
    
    Returns:
        Dict with fare details or None if invalid
    """
    if distance_km is None or duration_min is None:
        return None
        
    if distance_km <= 0 or duration_min <= 0:
        return None
    
    # Vehicle type rates (in Naira)
    RATES = {
        'standard': {
            'base': 500.00,
            'per_km': 50.00,
            'per_min': 10.00,
            'min_fare': 800.00
        },
        'premium': {
            'base': 800.00,
            'per_km': 75.00,
            'per_min': 15.00,
            'min_fare': 1200.00
        },
        'xl': {
            'base': 700.00,
            'per_km': 60.00,
            'per_min': 12.00,
            'min_fare': 1000.00
        }
    }
    
    # Validate vehicle type
    if vehicle_type not in RATES:
        vehicle_type = 'standard'
    
    rates = RATES[vehicle_type]
    
    # Dynamic surge pricing based on demand
    def get_surge_multiplier():
        now = datetime.now()
        
        # Peak hours (7-9am, 5-7pm weekdays)
        if now.weekday() < 5:  # Weekday
            if (7 <= now.hour <= 9) or (17 <= now.hour <= 19):
                return 1.5
        
        # Weekend surge
        if now.weekday() >= 5:  # Weekend
            if (12 <= now.hour <= 22):
                return 1.3
                
        # Special events (could be enhanced with external data)
        return 1.0
    
    try:
        surge_multiplier = get_surge_multiplier()
        
        # Calculations
        distance_fare = distance_km * rates['per_km'] 
        time_fare = duration_min * rates['per_min']
        subtotal = (rates['base'] + distance_fare + time_fare) * surge_multiplier
        
        # Apply minimum fare
        total_fare = max(subtotal, rates['min_fare'])
        
        return {
            'base_fare': rates['base'],
            'distance_km': round(distance_km, 2),
            'duration_min': round(duration_min, 2),
            'distance_fare': round(distance_fare, 2),
            'time_fare': round(time_fare, 2),
            'surge_multiplier': surge_multiplier,
            'total_fare': round(total_fare, 2),
            'vehicle_type': vehicle_type,
            'currency': 'NGN'
        }
        
    except Exception as e:
        logger.error(f"Fare calculation error: {str(e)}")
        return None