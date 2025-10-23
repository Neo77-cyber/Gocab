import googlemaps
from django.conf import settings
from datetime import datetime
import logging
from django.core.cache import cache
import math

logger = logging.getLogger(__name__)

gmaps = googlemaps.Client(key=settings.GOOGLE_MAPS_API_KEY)


# Add this to your services.py or views.py

CITY_DISTANCE_LIMITS = {
    'lagos': {
        'max_pickup_distance': 8,    # km - Reduced from 15km (Lagos traffic is terrible!)
        'max_ride_distance': 25,     # km - Maximum trip distance within Lagos
        'min_fare_distance': 1,      # km
        'max_pickup_time': 25,       # minutes - Maximum acceptable pickup time
    },
    'benin': {
        'max_pickup_distance': 6,    # km - Smaller city
        'max_ride_distance': 20,     # km
        'min_fare_distance': 1,      # km
        'max_pickup_time': 20,       # minutes
    },
    'ibadan': {
        'max_pickup_distance': 7,    # km
        'max_ride_distance': 22,     # km
        'min_fare_distance': 1,      # km
        'max_pickup_time': 22,       # minutes
    },
    'abuja': {
        'max_pickup_distance': 10,   # km - Abuja has better roads
        'max_ride_distance': 30,     # km
        'min_fare_distance': 1,      # km
        'max_pickup_time': 25,       # minutes
    },
    'port harcourt': {
        'max_pickup_distance': 6,    # km
        'max_ride_distance': 18,     # km
        'min_fare_distance': 1,      # km
        'max_pickup_time': 20,       # minutes
    },
    'default': {
        'max_pickup_distance': 6,    # km
        'max_ride_distance': 20,     # km
        'min_fare_distance': 1,      # km
        'max_pickup_time': 20,       # minutes
    }
}

def detect_city_from_coordinates(lat, lng):
    
    try:
        # Expanded Nigerian city boundaries (approximate)
        city_boundaries = {
            'lagos': {'min_lat': 6.3, 'max_lat': 6.7, 'min_lng': 3.0, 'max_lng': 3.8},
            'benin': {'min_lat': 6.2, 'max_lat': 6.4, 'min_lng': 5.5, 'max_lng': 5.8},
            'ibadan': {'min_lat': 7.3, 'max_lat': 7.5, 'min_lng': 3.8, 'max_lng': 4.0},
            'abuja': {'min_lat': 8.9, 'max_lat': 9.2, 'min_lng': 7.3, 'max_lng': 7.6},
            'port harcourt': {'min_lat': 4.7, 'max_lat': 5.0, 'min_lng': 6.9, 'max_lng': 7.1},
            'kano': {'min_lat': 11.9, 'max_lat': 12.1, 'min_lng': 8.4, 'max_lng': 8.6},
            'ilorin': {'min_lat': 8.4, 'max_lat': 8.6, 'min_lng': 4.5, 'max_lng': 4.7},
            'aba': {'min_lat': 5.1, 'max_lat': 5.2, 'min_lng': 7.3, 'max_lng': 7.4},
            'owerri': {'min_lat': 5.4, 'max_lat': 5.5, 'min_lng': 7.0, 'max_lng': 7.1},
        }
        
        for city, bounds in city_boundaries.items():
            if (bounds['min_lat'] <= lat <= bounds['max_lat'] and 
                bounds['min_lng'] <= lng <= bounds['max_lng']):
                return city
        
        return 'unknown'
    except Exception as e:
        logger.error(f"Error detecting city: {str(e)}")
        return 'unknown'
    
def should_show_ride_to_driver(driver_lat, driver_lng, ride_pickup_lat, ride_pickup_lng, ride_dest_lat, ride_dest_lng):
    """
    Determine if a ride should be shown to driver based on cities
    """
    try:
        # Detect cities
        driver_city = detect_city_from_coordinates(driver_lat, driver_lng)
        pickup_city = detect_city_from_coordinates(ride_pickup_lat, ride_pickup_lng)
        destination_city = detect_city_from_coordinates(ride_dest_lat, ride_dest_lng)
        
        print(f"🏙️ City check - Driver: {driver_city}, Pickup: {pickup_city}, Destination: {destination_city}")
        
        # Block inter-city rides (driver should only see rides within their city)
        if driver_city != pickup_city:
            print(f"🚫 BLOCKED: Driver in {driver_city} but pickup in {pickup_city}")
            return False
            
        # Also block if pickup and destination are in different cities (inter-city trips)
        if pickup_city != destination_city:
            print(f"🚫 BLOCKED: Inter-city trip from {pickup_city} to {destination_city}")
            return False
            
        print(f"✅ APPROVED: All locations in same city ({driver_city})")
        return True
        
    except Exception as e:
        logger.error(f"Error in city validation: {str(e)}")
        return True  # Fallback to allow the ride if there's an error

def get_city_distance_limits(lat, lng):
    """Get appropriate distance limits based on city"""
    city = detect_city_from_coordinates(lat, lng)
    return CITY_DISTANCE_LIMITS.get(city, CITY_DISTANCE_LIMITS['default'])

# Your existing functions remain the same...
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

# ADD THESE NEW FUNCTIONS FOR LOCATION-BASED FILTERING:

def get_google_distance_with_coords(origin, destination):
    """Get distance, duration, and coordinates from Google APIs with better error handling"""
    try:
        print(f"🔍 Calculating distance from: {origin} to {destination}")
        
        # Get coordinates first
        pickup_coords = geocode_address(origin)
        dest_coords = geocode_address(destination)
        
        print(f"📍 Pickup coords: {pickup_coords}")
        print(f"📍 Destination coords: {dest_coords}")
        
        if not pickup_coords or not dest_coords:
            logger.error("Could not geocode one or both addresses")
            return None, None, None, None
        
        # Calculate straight-line distance as fallback
        straight_distance = calculate_distance_haversine(
            pickup_coords['lat'], pickup_coords['lng'],
            dest_coords['lat'], dest_coords['lng']
        )
        print(f"📏 Straight-line distance: {straight_distance} km")
        
        # Get driving distance from Google
        distance_km, duration_min = get_google_distance(origin, destination)
        
        print(f"🚗 Google driving distance: {distance_km} km, Duration: {duration_min} min")
        
        # Validate the distance makes sense
        if distance_km and straight_distance:
            # Driving distance should be similar to or greater than straight-line distance
            if distance_km < straight_distance * 0.5:  # If driving distance is less than half straight-line
                print(f"⚠️ Suspicious distance: driving={distance_km}, straight={straight_distance}")
                # Use straight-line distance with a buffer
                distance_km = straight_distance * 1.2  # Add 20% buffer for routes
                duration_min = (distance_km / 40) * 60  # Estimate 40km/h average speed
        
        return distance_km, duration_min, pickup_coords, dest_coords
        
    except Exception as e:
        logger.error(f"Error getting distance with coords: {str(e)}")
        return None, None, None, None

def geocode_address(address):
    """Get coordinates for an address using Google Geocoding API"""
    try:
        cache_key = f"geocode_{address}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return cached_result
        
        result = gmaps.geocode(address)
        
        if result and len(result) > 0:
            location = result[0]['geometry']['location']
            coords = {
                'lat': location['lat'],
                'lng': location['lng']
            }
            
            # Cache for 1 hour
            cache.set(cache_key, coords, 3600)
            return coords
            
    except Exception as e:
        logger.error(f"Geocoding error for {address}: {str(e)}")
    
    return None

def calculate_distance_haversine(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two points in kilometers using Haversine formula
    """
    try:
        R = 6371  # Earth's radius in kilometers
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(delta_lat / 2) ** 2 + 
             math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        distance_km = R * c
        return round(distance_km, 2)
        
    except Exception as e:
        logger.error(f"Distance calculation error: {str(e)}")
        return None

def calculate_ride_fare(distance_km, duration_min, vehicle_type='standard'):
    """
    Calculate realistic fare breakdown for Nigerian cities - DOUBLED PRICES
    """
    if distance_km is None or duration_min is None:
        return None
        
    if distance_km <= 0 or duration_min <= 0:
        return None
    
    # DOUBLED Nigerian ride rates (in Naira)
    RATES = {
        'standard': {
            'base': 1000.00,      # Doubled from 500
            'per_km': 200.00,     # Doubled from 100
            'per_min': 10.00,     # Doubled from 5
            'min_fare': 800.00,   # Doubled from 400
        },
        'premium': {
            'base': 1600.00,      # Doubled from 800
            'per_km': 300.00,     # Doubled from 150
            'per_min': 16.00,     # Doubled from 8
            'min_fare': 1200.00,  # Doubled from 600
        },
        'xl': {
            'base': 1400.00,      # Doubled from 700
            'per_km': 240.00,     # Doubled from 120
            'per_min': 12.00,     # Doubled from 6
            'min_fare': 1000.00,  # Doubled from 500
        }
    }
    
    # Validate vehicle type
    if vehicle_type not in RATES:
        vehicle_type = 'standard'
    
    rates = RATES[vehicle_type]
    
    # Realistic surge pricing
    def get_surge_multiplier():
        now = datetime.now()
        
        # Peak hours (7-9am, 5-7pm weekdays)
        if now.weekday() < 5:  # Weekday
            if (7 <= now.hour <= 9) or (17 <= now.hour <= 19):
                return 1.3
        
        # Weekend surge
        if now.weekday() >= 5:  # Weekend
            if (12 <= now.hour <= 22):
                return 1.2
                
        return 1.0
    
    try:
        surge_multiplier = get_surge_multiplier()
        
        # Calculations
        distance_fare = distance_km * rates['per_km'] 
        time_fare = duration_min * rates['per_min']
        subtotal = (rates['base'] + distance_fare + time_fare) * surge_multiplier
        
        # Apply minimum fare
        total_fare = max(subtotal, rates['min_fare'])
        
        # Cap very long distances but keep it reasonable
        if distance_km > 50:
            total_fare = min(total_fare, 30000)  # Max ₦30,000 for very long rides
        
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