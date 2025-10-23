from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib import messages
from django.urls import reverse
from .models import *
from django.contrib.auth.hashers import make_password
import logging
from .forms import *
from django.http import HttpResponse
from django.contrib.auth import authenticate, login
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import  auth
from django.utils import timezone
from .services import *
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string
from .encoders import DjangoSafeJSONEncoder
from django.db.models import Sum  
import requests
from django.conf import settings
import decimal
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

logger = logging.getLogger(__name__)




@receiver(post_save, sender=RideRequest)
def ride_request_update(sender, instance, created, **kwargs):
    if not created:
        channel_layer = get_channel_layer()
        
        
        if instance.status == 'accepted':

            Notification.objects.filter(user=instance.passenger, is_active=True).delete()
            Notification.objects.create(
                user=instance.passenger,
                message=f"Driver {instance.driver.get_full_name()} accepted your ride",
                is_active=True
            )
            async_to_sync(channel_layer.group_send)(
                f"ride_updates_{instance.passenger.id}",
                {
                    'type': 'ride_update',
                    'event': 'accepted',
                    'ride_id': instance.id,
                    'driver': {
                        'name': instance.driver.get_full_name() or instance.driver.username,
                        'rating': instance.driver.driver.rating if hasattr(instance.driver, 'driver') else 4.5,
                        'car_model': instance.driver.driver.vehicle_model if hasattr(instance.driver, 'driver') else 'Unknown',
                        'license_plate': instance.driver.driver.license_plate if hasattr(instance.driver, 'driver') else 'N/A',
                    },
                    'eta': max(5, int(instance.distance_km * 2)) if instance.distance_km else 10,
                    'distance': f"{instance.distance_km:.1f} km" if instance.distance_km else None,
                    'fare': instance.total_fare
                }
            )
            async_to_sync(channel_layer.group_send)(
                f"notifications_{instance.passenger.id}",
                {
                    'type': 'notification.update',
                    'count': 1
                }
            )
        elif instance.status == 'completed':
            
            Notification.objects.filter(
                user=instance.passenger, 
                is_active=True
            ).update(is_active=False)

            async_to_sync(channel_layer.group_send)(
                f"ride_updates_{instance.passenger.id}",
                {
                    'type': 'ride_update',
                    'event': 'completed',
                    'ride_id': instance.id,
                    'message': 'Ride completed'
                }
            )

            async_to_sync(channel_layer.group_send)(
                f"notifications_{instance.passenger.id}",
                {
                    'type': 'notification.update',
                    'count': 0
                }
            )

        elif instance.status in ['started',  'cancelled']:
            async_to_sync(channel_layer.group_send)(
                f"ride_updates_{instance.passenger.id}",
                {
                    'type': 'ride_update',
                    'event': instance.status,
                    'ride_id': instance.id,
                    'message': f'Ride {instance.status}'
                }
            )

        
        if instance.driver and instance.status in ['cancelled']:
            async_to_sync(channel_layer.group_send)(
                f"driver_updates_{instance.driver.id}",
                {
                    'type': 'driver_update',
                    'event': instance.status,
                    'ride_id': instance.id,
                    'message': f'Ride {instance.status} by passenger'
                }
            )

@login_required
def ride_status(request, ride_id):
    try:
        ride = get_object_or_404(RideRequest, id=ride_id, passenger=request.user)

        if ride.status in ['completed', 'cancelled']:
            if 'current_ride_id' in request.session:
                del request.session['current_ride_id']
        
        
        last_updated = ride.requested_at.isoformat()
        
        driver_info = None
        if ride.driver and hasattr(ride.driver, 'driver'):
            driver_info = {
                'name': ride.driver.get_full_name() or ride.driver.username,
                'car_model': ride.driver.driver.vehicle_model or 'Unknown',
                'license_plate': ride.driver.driver.license_plate or 'N/A'
            }
        
        
        response_data = {
            'status': ride.status,
            'fare': float(ride.total_fare) if ride.total_fare else 0,
            'ride_id': ride.id,
            'driver': driver_info,
            'current_location': ride.current_location,
            'destination': ride.destination,
            'last_updated': last_updated,
            'payment_status': ride.payment_status,

        }
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"Error in ride_status: {str(e)}")
        return JsonResponse({
            'error': 'Could not fetch ride status',
            'details': str(e)
        }, status=500)

@login_required(login_url='home')
def rider_dashboard(request):
    debug_session(request, "Entering rider_dashboard")

    active_ride = RideRequest.objects.filter(
        passenger=request.user,
        status__in=['pending', 'accepted', 'started']
    ).order_by('-requested_at').first()

   
    if active_ride:
        request.session['current_ride_id'] = str(active_ride.id)
        request.session['current_ride_status'] = active_ride.status
        
        if active_ride.driver and hasattr(active_ride.driver, 'driver'):
            vehicle_model = getattr(active_ride.driver.driver, 'vehicle_model', None) or 'Unknown'
            license_plate = getattr(active_ride.driver.driver, 'license_plate', None) or 'N/A'
            rating = getattr(active_ride.driver.driver, 'rating', None)
            rating_value = float(rating) if rating is not None else 4.5
            
            request.session['current_driver'] = {
                'name': active_ride.driver.get_full_name() or active_ride.driver.username,
                'car_model': vehicle_model,
                'license_plate': license_plate,
                'rating': rating_value,
            }
        
        request.session.modified = True
    else:
        
        completed_ride = RideRequest.objects.filter(
            passenger=request.user,
            status='completed',
            payment_status__in=['pending', 'processing']
        ).order_by('-completed_at').first()
        
        if completed_ride and completed_ride.completed_at:
            time_since_completion = timezone.now() - completed_ride.completed_at
            if time_since_completion.total_seconds() < 600: 
                request.session['current_ride_id'] = str(completed_ride.id)
                request.session['current_ride_status'] = 'completed'
                request.session['completed_ride_fare'] = float(completed_ride.total_fare) if completed_ride.total_fare is not None else 0.0
                
                if completed_ride.driver and hasattr(completed_ride.driver, 'driver'):
                    vehicle_model = getattr(completed_ride.driver.driver, 'vehicle_model', None) or 'Unknown'
                    license_plate = getattr(completed_ride.driver.driver, 'license_plate', None) or 'N/A'
                    rating = getattr(completed_ride.driver.driver, 'rating', None)
                    rating_value = float(rating) if rating is not None else 4.5
                    
                    request.session['current_driver'] = {
                        'name': completed_ride.driver.get_full_name() or completed_ride.driver.username,
                        'car_model': vehicle_model,
                        'license_plate': license_plate,
                        'rating': rating_value,
                    }
                
                request.session.modified = True

    try:
        rider_profile = Rider.objects.get(user=request.user)
        address = rider_profile.address if hasattr(rider_profile, 'address') else "Address not set"
    except Rider.DoesNotExist:
        rider_profile = None
        address = "Address not set"
    
    total_rides = RideRequest.objects.filter(passenger=request.user).count()
    completed_rides = RideRequest.objects.filter(
        passenger=request.user,
        status='completed'
    )
    
    # Safely calculate total miles
    total_miles = 0.0
    for ride in completed_rides:
        if ride.distance_km is not None:
            total_miles += float(ride.distance_km)

    recent_rides_list = RideRequest.objects.filter(
        passenger=request.user,
        status='completed'
    ).order_by('-completed_at').select_related('driver')
    
    paginator = Paginator(recent_rides_list, 5)
    page = request.GET.get('page')
    
    try:
        recent_rides = paginator.page(page)
    except PageNotAnInteger:
        recent_rides = paginator.page(1)
    except EmptyPage:
        recent_rides = paginator.page(paginator.num_pages)

    notification_count = Notification.objects.filter(
        user=request.user,
        is_active=True
    ).count()

    context = {
        'active_ride': active_ride,
        'is_rider': True,
        'rider_profile': rider_profile,
        'address': address,
        'total_rides': total_rides,
        'completed_rides': completed_rides.count(),
        'total_miles': round(total_miles, 1) if total_miles else 0,
        'member_since': request.user.date_joined.strftime("%B %Y") if request.user.date_joined else None,
        'recent_rides': recent_rides,
        'notification_count': notification_count,
    }
    
    debug_session(request, "Exiting rider_dashboard")
    return render(request, 'rider-dashboard.html', context)

from django.db.models import Q, ExpressionWrapper, FloatField
from django.db.models.functions import ACos, Cos, Radians, Sin
import math

# Add these imports at the top of views.py
from django.db.models import F, ExpressionWrapper, FloatField
from django.db.models.functions import ACos, Cos, Radians, Sin


def estimate_pickup_time(distance_km, city):
    """
    Estimate pickup time based on distance and city traffic patterns
    """
    # Average speeds by city (km/h) - considering traffic
    city_speeds = {
        'lagos': 20,      # Heavy traffic
        'benin': 30,      # Moderate traffic
        'ibadan': 25,     # Moderate-heavy traffic
        'abuja': 35,      # Better roads
        'port harcourt': 25,
        'default': 25
    }
    
    avg_speed = city_speeds.get(city, 25)
    
    # Time = Distance / Speed (convert to minutes)
    base_time = (distance_km / avg_speed) * 60
    
    # Add buffer for traffic lights, etc.
    pickup_time = base_time + (distance_km * 1.5)  # 1.5 minutes per km buffer
    
    return round(pickup_time)

def get_nearby_rides(driver_lat, driver_lng):
    """
    Get rides within appropriate distance AND reasonable pickup time
    """
    if not driver_lat or not driver_lng:
        return RideRequest.objects.none()
    
    try:
        # Get distance limits based on driver's city
        distance_limits = get_city_distance_limits(driver_lat, driver_lng)
        max_distance = distance_limits['max_pickup_distance']
        max_pickup_time = distance_limits['max_pickup_time']
        
        driver_city = detect_city_from_coordinates(driver_lat, driver_lng)
        print(f"📍 Driver in {driver_city} - max {max_distance}km pickup, max {max_pickup_time}min pickup time")
        
        # Get ALL pending rides first (we'll filter in Python)
        all_pending_rides = RideRequest.objects.filter(
            status='pending',
            driver__isnull=True,
            pickup_latitude__isnull=False,
            pickup_longitude__isnull=False,
            destination_latitude__isnull=False,
            destination_longitude__isnull=False
        ).select_related('passenger')
        
        print(f"📊 Found {all_pending_rides.count()} total pending rides")
        
        # Filter by distance, city, and time in Python
        valid_rides = []
        for ride in all_pending_rides:
            # Calculate distance
            distance = calculate_distance_haversine(
                driver_lat, driver_lng,
                ride.pickup_latitude, ride.pickup_longitude
            )
            
            if not distance or distance > max_distance:
                continue
            
            # City validation
            if not should_show_ride_to_driver(
                driver_lat, driver_lng,
                ride.pickup_latitude, ride.pickup_longitude,
                ride.destination_latitude, ride.destination_longitude
            ):
                continue
            
            # Estimate pickup time
            estimated_pickup_time = estimate_pickup_time(distance, driver_city)
            
            if estimated_pickup_time <= max_pickup_time:
                # Add the calculated fields to the ride object
                ride.distance_from_driver = distance
                ride.estimated_pickup_time = estimated_pickup_time
                valid_rides.append(ride)
                print(f"✅ Ride {ride.id}: {distance:.1f}km away, ~{estimated_pickup_time}min pickup")
            else:
                print(f"🚫 Ride {ride.id}: Too far - {distance:.1f}km, ~{estimated_pickup_time}min pickup")
        
        # Sort by distance (closest first)
        valid_rides.sort(key=lambda x: x.distance_from_driver)
        
        print(f"📊 Found {len(valid_rides)} valid rides after filtering")
        
        # Return the list (we'll handle pagination in the view)
        return valid_rides
    
    except Exception as e:
        logger.error(f"Error in get_nearby_rides: {str(e)}")
        # Fallback to basic query
        return list(RideRequest.objects.filter(
            status='pending',
            driver__isnull=True
        ).select_related('passenger').order_by('-requested_at'))

@csrf_exempt
@login_required
def request_ride(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)
    
    try:
        if request.content_type == 'application/json':
                data = json.loads(request.body)
        else:
                data = request.POST
        current_location = data.get('current_location')
        destination = data.get('destination')
        
        if not current_location or not destination:
            return JsonResponse({'error': 'Both locations required'}, status=400)
        
        # Calculate distance, duration AND coordinates
        distance_km, duration_min, pickup_coords, dest_coords = get_google_distance_with_coords(current_location, destination)
        if not distance_km:
            return JsonResponse({'error': 'Could not calculate route'}, status=400)
            
        fare = calculate_ride_fare(distance_km, duration_min)
        
        # Create ride with coordinates
        ride = RideRequest.objects.create(
            passenger=request.user,
            current_location=current_location,
            destination=destination,
            distance_km=distance_km,
            duration_min=duration_min,
            total_fare=fare['total_fare'],
            status='pending',
            pickup_latitude=pickup_coords['lat'] if pickup_coords else None,
            pickup_longitude=pickup_coords['lng'] if pickup_coords else None,
            destination_latitude=dest_coords['lat'] if dest_coords else None,
            destination_longitude=dest_coords['lng'] if dest_coords else None,
        )
        
        request.session['current_ride_id'] = ride.id
        request.session.modified = True  
        
        # Notify drivers with location data
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "available_drivers",
            {
                'type': 'driver_update',
                'event': 'new_ride',
                'ride_id': ride.id,
                'pickup': ride.current_location,
                'destination': ride.destination,
                'fare': ride.total_fare,
                'pickup_lat': ride.pickup_latitude,
                'pickup_lng': ride.pickup_longitude,
            }
        )
        
        
        return JsonResponse({
            'status': 'success',
            'ride_id': ride.id,
            'fare': ride.total_fare,
            'distance': distance_km,
            'duration': duration_min
        })
        
    except Exception as e:
        logger.error(f"Ride request error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
@login_required
def cancel_ride(request, ride_id):
    try:
        ride = RideRequest.objects.get(id=ride_id, passenger=request.user)
        
        if ride.status in ['completed', 'cancelled']:
            return JsonResponse({
                'status': 'error',
                'message': 'Ride has already ended'
            }, status=400)
            
        ride.status = 'cancelled'
        ride.cancelled_at = timezone.now()
        ride.save()

        clear_ride_session(request)

        if 'current_ride_id' in request.session:
            del request.session['current_ride_id']
        
        # Notify both rider and driver via WebSocket
        channel_layer = get_channel_layer()
        
        # Notify rider
        async_to_sync(channel_layer.group_send)(
            f"ride_{ride.id}",
            {
                'type': 'ride_update',
                'event': 'cancelled',
                'ride_id': ride.id,
                'message': 'Ride cancelled by passenger'
            }
        )
        
        # Notify driver if assigned
        if ride.driver:
            async_to_sync(channel_layer.group_send)(
                f"driver_{ride.driver.id}",
                {
                    'type': 'ride_update',
                    'event': 'cancelled',
                    'ride_id': ride.id,
                    'message': 'Passenger cancelled the ride'
                }
            )
        
        return JsonResponse({
            'status': 'success',
            'message': 'Ride cancelled successfully'
        })
        
    except RideRequest.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Ride not found or you are not the passenger'
        }, status=404)
    except Exception as e:
        logger.error(f"Error cancelling ride: {str(e)}")
        return JsonResponse({
            'status': 'error',
            'message': 'Failed to cancel ride'
        }, status=500)


# Driver Views
@login_required(login_url='home')
def driver_dashboard(request):
    if not hasattr(request.user, 'driver'):
        return redirect('home')

    
    active_ride = RideRequest.objects.filter(
        driver=request.user,
        status__in=['accepted', 'started']
    ).order_by('-accepted_at').first()
    
    
    available_rides = None
    if not active_ride:
        available_rides = RideRequest.objects.filter(
            status='pending',
            driver__isnull=True
        ).order_by('-requested_at')
    
    
    completed_rides = RideRequest.objects.filter(
        driver=request.user,
        status='completed'
    ).order_by('-completed_at')[:10]  
    
    
    
        
    
    context = {
        'active_ride': active_ride,
        'available_rides': available_rides,
        'completed_rides': completed_rides,
        'is_driver': True
    }
    return render(request, 'driver-dashboard.html', context)

@login_required
def pending_rides(request):
    page = request.GET.get('page', 1)
    
    # Get driver's current location
    try:
        driver = request.user.driver
        driver_lat = driver.latitude
        driver_lng = driver.longitude
        has_location = bool(driver_lat and driver_lng)
        
        if has_location:
            # Detect city and get limits
            city = detect_city_from_coordinates(driver_lat, driver_lng)
            distance_limits = get_city_distance_limits(driver_lat, driver_lng)
            max_distance = distance_limits['max_pickup_distance']
            
            print(f"🚗 Driver in {city}, showing rides within {max_distance}km")
        else:
            city = "unknown"
            max_distance = 6
            
    except Exception as e:
        logger.error(f"Driver location error: {str(e)}")
        driver_lat = None
        driver_lng = None
        has_location = False
        city = "unknown"
        max_distance = 6
    
    # Get nearby rides
    if has_location:
        rides_list = get_nearby_rides(driver_lat, driver_lng)  # This returns a list now
    else:
        rides_list = list(RideRequest.objects.filter(
            status='pending',
            driver__isnull=True
        ).select_related('passenger').order_by('-requested_at'))
    
    # Handle pagination for lists
    paginator = Paginator(rides_list, 4)
    
    try:
        rides = paginator.page(page)
    except PageNotAnInteger:
        rides = paginator.page(1)
    except EmptyPage:
        rides = paginator.page(paginator.num_pages)
    
    # Add passenger phone numbers for template (already handled in get_nearby_rides)
    for ride in rides:
        if not hasattr(ride, 'passenger_phone'):
            ride.passenger_phone = ride.passenger.rider.phone_number if hasattr(ride.passenger, 'rider') else "No phone number"
    
    html = render_to_string('partials/_pending_rides.html', {
        'pending_rides': rides,
        'request': request,
        'has_location': has_location,
        'max_distance': max_distance,
        'city': city
    })
    return JsonResponse({'html': html})

@csrf_exempt
@login_required
def update_driver_location(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)
    
    try:
        driver = request.user.driver
        data = json.loads(request.body)
        
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        address = data.get('address')
        
        if not latitude or not longitude:
            return JsonResponse({'error': 'Latitude and longitude required'}, status=400)
        
        driver.update_location(latitude, longitude, address)
        
        return JsonResponse({
            'status': 'success',
            'message': 'Location updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Location update error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def accepted_rides(request):
    page = request.GET.get('page', 1)
    rides_list = RideRequest.objects.filter(
        driver=request.user,
        status='accepted'
    ).select_related('passenger__rider').order_by('-accepted_at')
    
    paginator = Paginator(rides_list, 1)
    
    try:
        rides = paginator.page(page)
    except PageNotAnInteger:
        rides = paginator.page(1)
    except EmptyPage:
        rides = paginator.page(paginator.num_pages)
    
    html = render_to_string('partials/_accepted_rides.html', {
        'accepted_rides': rides,
        'request': request
    })
    return JsonResponse({'html': html})

@login_required
def active_rides(request):
    page = request.GET.get('page', 1)
    rides_list = RideRequest.objects.filter(
        driver=request.user,
        status='started'
    ).select_related('passenger__rider').order_by('-started_at')
    
    paginator = Paginator(rides_list, 10)
    
    try:
        rides = paginator.page(page)
    except PageNotAnInteger:
        rides = paginator.page(1)
    except EmptyPage:
        rides = paginator.page(paginator.num_pages)
    
    html = render_to_string('partials/_active_rides.html', {
        'active_rides': rides,
        'request': request
    })
    return JsonResponse({'html': html})

@login_required(login_url='home')
def completed_trips(request):
    try:
        page = request.GET.get('page', 1)
        rides_list = RideRequest.objects.filter(
            driver=request.user,
            status='completed'
        ).order_by('-completed_at')
        
        paginator = Paginator(rides_list, 10)
        
        try:
            rides = paginator.page(page)
        except PageNotAnInteger:
            rides = paginator.page(1)
        except EmptyPage:
            rides = paginator.page(paginator.num_pages)
        
        html = render_to_string('partials/_completed_rides.html', {
            'completed_rides': rides,
            'request': request
        })
        
        return JsonResponse({'html': html})
        
    except Exception as e:
        logger.error(f"Error fetching completed trips: {str(e)}")
        return JsonResponse({
            'error': 'Could not load completed trips',
            'details': str(e)
        }, status=500)


def update_ride_session(request, ride_id, status):
    """Helper function to update ride session consistently"""
    try:
        ride = RideRequest.objects.get(id=ride_id)
        
        # Always update these session variables
        request.session['current_ride_id'] = str(ride_id)
        request.session['current_ride_status'] = str(status)
        request.session['last_updated'] = timezone.now().isoformat()
        
        # Store fare for completed rides - handle None values
        if status == 'completed':
            request.session['completed_ride_fare'] = float(ride.total_fare) if ride.total_fare is not None else 0.0
        
        # Store driver info if available
        if hasattr(ride, 'driver') and ride.driver and hasattr(ride.driver, 'driver'):
            # Safely get driver attributes with defaults
            vehicle_model = getattr(ride.driver.driver, 'vehicle_model', None) or 'Unknown'
            license_plate = getattr(ride.driver.driver, 'license_plate', None) or 'N/A'
            rating = getattr(ride.driver.driver, 'rating', None)
            rating_value = float(rating) if rating is not None else 4.5
            
            request.session['current_driver'] = {
                'name': ride.driver.get_full_name() or ride.driver.username,
                'car_model': vehicle_model,
                'license_plate': license_plate,
                'rating': rating_value,
            }
        
        request.session.modified = True
        logger.info(f"[SESSION] Updated - Ride: {ride_id}, Status: {status}, User: {request.user.username}")
        
        # Debug output
        debug_session(request, f"After update_ride_session for ride {ride_id}")
        return True
        
    except RideRequest.DoesNotExist:
        logger.error(f"[SESSION] Ride {ride_id} not found")
        return False
    except Exception as e:
        logger.error(f"[SESSION] Error updating session: {e}")
        import traceback
        traceback.print_exc()
        return False

def clear_ride_session(request):
    """Clear ride session data"""
    keys_to_remove = [
        'current_ride_id', 
        'current_ride_status', 
        'completed_ride_fare', 
        'current_driver',
        'current_passenger',
        'last_updated'
    ]
    
    for key in keys_to_remove:
        if key in request.session:
            del request.session[key]
    
    request.session.modified = True
    logger.info(f"[SESSION] Cleared ride session for user: {request.user.username}")
    debug_session(request, "After clear_ride_session")

@login_required
def debug_session_view(request):
    """Debug endpoint to check session state"""
    session_data = {
        'current_ride_id': request.session.get('current_ride_id'),
        'current_ride_status': request.session.get('current_ride_status'),
        'completed_ride_fare': request.session.get('completed_ride_fare'),
        'current_driver': request.session.get('current_driver'),
        'session_key': request.session.session_key,
    }
    return JsonResponse(session_data)

@csrf_exempt
def accept_ride(request, ride_id):
    if request.method == 'POST':
        try:
            ride = RideRequest.objects.get(id=ride_id, status='pending')
            ride.driver = request.user
            ride.status = 'accepted'
            ride.accepted_at = timezone.now()
            ride.save()

            # Safely build ride data with None checks
            ride_data = {
                'id': ride.id,
                'fare': float(ride.total_fare) if ride.total_fare is not None else 0.0,
                'status': ride.status,
                'pickup': ride.current_location or '',
                'dropoff': ride.destination or '',
                'distance_km': float(ride.distance_km) if ride.distance_km is not None else 0.0,
                'duration_min': float(ride.duration_min) if ride.duration_min is not None else 0.0,
                'passenger': ride.passenger.username,
                'requested_at': ride.requested_at.isoformat() if ride.requested_at else '',
            }

            if not update_ride_session(request, ride_id, 'accepted'):
                return JsonResponse({'status': 'error', 'message': 'Failed to update session'}, status=500)

            # Serialize safely
            ride_payload = json.loads(json.dumps(ride_data, cls=DjangoSafeJSONEncoder))

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"driver_{request.user.id}",
                {
                    "type": "ride.accepted",
                    "ride": ride_payload
                }
            )

            # Safely get driver attributes
            vehicle_model = getattr(request.user.driver, 'vehicle_model', None) or 'Unknown'
            license_plate = getattr(request.user.driver, 'license_plate', None) or 'N/A'
            rating = getattr(request.user.driver, 'rating', None)
            rating_value = float(rating) if rating is not None else 4.5

            async_to_sync(channel_layer.group_send)(
                f"ride_{ride.id}",
                {
                    "type": "ride_update",
                    "event": "accepted",
                    "ride_id": ride.id,
                    "driver": {
                        "name": request.user.get_full_name() or request.user.username,
                        "car_model": vehicle_model,
                        "license_plate": license_plate,
                        "rating": rating_value,
                    },
                    "eta": 7,
                    "distance": float(ride.distance_km) if ride.distance_km is not None else 0.0,
                    "fare": float(ride.total_fare) if ride.total_fare is not None else 0.0,
                }
            )

            return JsonResponse({'status': 'success'})

        except RideRequest.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Ride not found'}, status=404)
        except Exception as e:
            logger.error(f"Accept ride error: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def start_trip(request, ride_id):
    if not hasattr(request.user, 'driver'):
        return JsonResponse({'error': 'Driver profile not found'}, status=403)
    
    try:
        ride = RideRequest.objects.get(
            id=ride_id,
            driver=request.user,
            status='accepted'
        )
        
        ride.status = 'started'
        ride.started_at = timezone.now()
        ride.save()
        
        # Safely build ride data with None checks
        vehicle_model = getattr(request.user.driver, 'vehicle_model', None) or 'Unknown'
        license_plate = getattr(request.user.driver, 'license_plate', None) or 'N/A'
        rating = getattr(request.user.driver, 'rating', None)
        rating_value = float(rating) if rating is not None else 4.5
        phone_number = getattr(request.user.driver, 'phone_number', None) or ''
        
        ride_data = {
            'id': ride.id,
            'status': ride.status,
            'started_at': ride.started_at.isoformat() if ride.started_at else '',
            'pickup': ride.current_location or '',
            'dropoff': ride.destination or '',
            'distance_km': float(ride.distance_km) if ride.distance_km is not None else 0.0,
            'duration_min': float(ride.duration_min) if ride.duration_min is not None else 0.0,
            'fare': float(ride.total_fare) if ride.total_fare is not None else 0.0,
            'driver': {
                'id': request.user.id,
                'name': request.user.get_full_name() or request.user.username,
                'car_model': vehicle_model,
                'license_plate': license_plate,
                'rating': rating_value,
                'phone': str(phone_number),
            },
            'passenger': {
                'id': ride.passenger.id,
                'name': ride.passenger.get_full_name() or ride.passenger.username,
                'rating': 0,
            }
        }

        if not update_ride_session(request, ride_id, 'started'):
            return JsonResponse({'status': 'error', 'message': 'Failed to update session'}, status=500)

        ride_payload = json.loads(json.dumps(ride_data, cls=DjangoSafeJSONEncoder))
        
        channel_layer = get_channel_layer()
        
        async_to_sync(channel_layer.group_send)(
            f"ride_{ride.id}",
            {
                "type": "ride_update",
                "event": "started",
                "ride_id": ride.id,
                "message": "Your ride has started",
                "data": ride_payload
            }
        )
        
        async_to_sync(channel_layer.group_send)(
            f"driver_{request.user.id}",
            {
                "type": "ride_update",
                "event": "started",
                "ride_id": ride.id,
                "message": "Trip started successfully",
                "data": ride_payload
            }
        )

        return JsonResponse({
            'status': 'success',
            'ride_id': ride.id,
            'message': 'Trip started successfully',
            'data': ride_data
        })

    except RideRequest.DoesNotExist:
        return JsonResponse({
            'error': 'Ride not found, already started, or not assigned to you'
        }, status=404)
    except Exception as e:
        logger.error(f"Start trip error: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def complete_trip(request, ride_id):
    if not hasattr(request.user, 'driver'):
        return JsonResponse({
            'status': 'error',
            'error': 'Driver profile not found'
        }, status=403)

    try:
        ride = RideRequest.objects.get(id=ride_id, driver=request.user)
        
        if ride.status != 'started':
            return JsonResponse({
                'status': 'error', 
                'error': f'Ride must be started first. Current status: {ride.status}'
            }, status=400)
        
        ride.status = 'completed'
        ride.completed_at = timezone.now()
        ride.payment_status = 'pending'
        ride.save()

        if not update_ride_session(request, ride_id, 'completed'):
            return JsonResponse({'status': 'error', 'message': 'Failed to update session'}, status=500)
        
        ride.refresh_from_db()

        # Safely build ride data with None checks
        vehicle_model = getattr(request.user.driver, 'vehicle_model', None) or 'Unknown'
        license_plate = getattr(request.user.driver, 'license_plate', None) or 'N/A'
        rating = getattr(request.user.driver, 'rating', None)
        rating_value = float(rating) if rating is not None else 4.5
        phone_number = getattr(request.user.driver, 'phone_number', None) or ''

        ride_data = {
            'id': ride.id,
            'status': ride.status,
            'fare': float(ride.total_fare) if ride.total_fare is not None else 0.0,
            'started_at': ride.started_at.isoformat() if ride.started_at else None,
            'pickup': ride.current_location or '',
            'dropoff': ride.destination or '',
            'distance_km': float(ride.distance_km) if ride.distance_km is not None else 0.0,
            'duration_min': float(ride.duration_min) if ride.duration_min is not None else 0.0,
            'driver': {
                'id': request.user.id,
                'name': request.user.get_full_name() or request.user.username,
                'car_model': vehicle_model,
                'license_plate': license_plate,
                'rating': rating_value,
                'phone': str(phone_number),
            },
            'passenger': {
                'id': ride.passenger.id,
                'name': ride.passenger.get_full_name() or ride.passenger.username,
                'rating': 0,
            }
        }

        ride_payload = json.loads(json.dumps(ride_data, cls=DjangoSafeJSONEncoder))
        payment_url = create_paystack_payment_link(ride)

        channel_layer = get_channel_layer()
        
        try:
            async_to_sync(channel_layer.group_send)(
                f"ride_{ride.id}",
                {
                    "type": "ride_update",
                    "event": "completed",
                    "ride_id": ride.id,
                    "status": "completed",
                    "message": "Your ride is complete",
                    "data": ride_payload
                }
            )
        except Exception as e:
            print(f"Error sending passenger notification: {e}")
        
        try:
            async_to_sync(channel_layer.group_send)(
                f"driver_{request.user.id}",
                {
                    "type": "ride_update",
                    "event": "completed",
                    "ride_id": ride.id,
                    "status": "completed",
                    "message": "Trip completed successfully",
                    "data": ride_payload,
                    "driver": {
                        "id": request.user.id
                    }
                }
            )
        except Exception as e:
            print(f"Error sending driver notification: {e}")
    
        response_data = {
            'status': 'success',
            'ride_id': ride.id,
            'message': 'Trip completed successfully',
            'data': ride_data
        }
        
        return JsonResponse(response_data)

    except RideRequest.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'error': 'Trip not found or already completed'
        }, status=404)
    except Exception as e:
        logger.error(f"Complete trip error: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'status': 'error',
            'error': str(e)
        }, status=500)


@csrf_exempt
def initiate_payment(request, ride_id):
    print(f"=== INITIATE PAYMENT CALLED ===")
    print(f"Ride ID: {ride_id}, User: {request.user.username}")
    
    if request.method != 'POST':
        print("❌ ERROR: Method not allowed - expected POST")
        return JsonResponse({'status': 'error', 'error': 'Method not allowed'}, status=405)
    
    try:
        ride = RideRequest.objects.get(id=ride_id, passenger=request.user)
        print(f"✅ Ride found: ID={ride.id}, Status={ride.status}, Payment Status={ride.payment_status}")
        print(f"💰 Fare: {ride.total_fare}, Passenger: {ride.passenger.username}")
        
        # Get passenger email
        try:
            rider_profile = Rider.objects.get(user=ride.passenger)
            passenger_email = rider_profile.email
            print(f"📧 Email from Rider model: {passenger_email}")
        except Rider.DoesNotExist:
            passenger_email = ride.passenger.email
            print(f"📧 Email from User model: {passenger_email}")
        
        # Validate ride status
        if ride.status != 'completed':
            print(f"❌ Ride not completed. Current status: {ride.status}")
            return JsonResponse({
                'status': 'error', 
                'error': f'Ride must be completed before payment. Current status: {ride.status}'
            }, status=400)
        
        # Check if already paid
        if ride.payment_status == 'paid':
            print("❌ Payment already completed")
            return JsonResponse({
                'status': 'error', 
                'error': 'Payment already completed'
            }, status=400)
        
        # Validate fare amount
        if not ride.total_fare or ride.total_fare <= 0:
            print(f"❌ Invalid fare amount: {ride.total_fare}")
            return JsonResponse({
                'status': 'error',
                'error': f'Invalid fare amount: {ride.total_fare}'
            }, status=400)
        
        # Validate email
        if not passenger_email or '@' not in passenger_email:
            print(f"❌ Invalid email: {passenger_email}")
            return JsonResponse({
                'status': 'error',
                'error': 'Passenger email is required for payment. Please update your profile with a valid email address.'
            }, status=400)
        
        print("🔄 Creating Paystack payment link...")
        payment_url = create_paystack_payment_link(ride)
        
        if payment_url:
            print(f"✅ Payment link created successfully: {payment_url[:50]}...")
            return JsonResponse({
                'status': 'success',
                'payment_url': payment_url,
                'ride_id': ride.id
            })
        else:
            print("❌ Failed to create payment link")
            return JsonResponse({
                'status': 'error',
                'error': 'Failed to create payment link. Please try again.'
            }, status=500)
            
    except RideRequest.DoesNotExist:
        print(f"❌ Ride not found: ID={ride_id}, User={request.user.username}")
        return JsonResponse({
            'status': 'error',
            'error': 'Ride not found or you are not authorized to pay for this ride'
        }, status=404)
    except Exception as e:
        print(f"❌ Unexpected error in initiate_payment: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'status': 'error',
            'error': 'Internal server error'
        }, status=500)

import time
import random

def create_paystack_payment_link(ride):
    print(f"=== CREATE PAYSTACK PAYMENT LINK ===")
    try:
        # Get passenger email
        try:
            rider_profile = Rider.objects.get(user=ride.passenger)
            passenger_email = rider_profile.email
            print(f"📧 Using Rider model email: {passenger_email}")
        except Rider.DoesNotExist:
            passenger_email = ride.passenger.email
            print(f"📧 Using User model email: {passenger_email}")
        
        # Validate email
        if not passenger_email:
            print("❌ ERROR: No email found for passenger")
            return None
        
        # Prepare payment data with UNIQUE reference
        amount_in_kobo = int(ride.total_fare * 100)
        print(f"💰 Amount: {ride.total_fare} Naira -> {amount_in_kobo} kobo")
        
        # Generate unique reference (multiple options)
        timestamp = int(time.time())
        random_suffix = random.randint(1000, 9999)
        unique_reference = f"RIDE_{ride.id}_{timestamp}_{random_suffix}"
        
        # Alternative simpler unique reference:
        # unique_reference = f"RIDE_{ride.id}_{timestamp}"
        
        payment_data = {
            'email': passenger_email,
            'amount': amount_in_kobo,
            'reference': unique_reference,  # Use unique reference
            'callback_url': f"{settings.BASE_URL}/payment/success/{ride.id}/",
            'metadata': {
                'ride_id': ride.id,
                'passenger_id': ride.passenger.id,
                'driver_id': ride.driver.id if ride.driver else None
            }
        }
        
        print(f"📦 Payment data: {payment_data}")
        print(f"🔑 Unique Reference: {unique_reference}")
        
        # Make Paystack API call
        print("🔄 Calling Paystack API...")
        response = requests.post(
            'https://api.paystack.co/transaction/initialize',
            headers={'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'},
            json=payment_data,
            timeout=30
        )
        
        print(f"📡 Paystack Response Status: {response.status_code}")
        print(f"📄 Paystack Response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Paystack API success - Status: {data.get('status')}")
            
            if data.get('status') and data['data'].get('authorization_url'):
                ride.payment_reference = unique_reference  # Save the unique reference
                ride.save()
                print(f"✅ Payment reference saved: {unique_reference}")
                return data['data']['authorization_url']
            else:
                print(f"❌ Paystack API returned false status: {data}")
                return None
        else:
            print(f"❌ Paystack HTTP error: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"❌ Exception in create_paystack_payment_link: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

@csrf_exempt
def payment_success(request, ride_id):
    print(f"=== PAYMENT SUCCESS CALLED ===")
    print(f"Ride ID: {ride_id}")
    print(f"GET Parameters: {dict(request.GET)}")
    
    try:
        ride = RideRequest.objects.get(id=ride_id)
        print(f"✅ Ride found: {ride.id}, Status: {ride.status}, Payment Status: {ride.payment_status}")
        print(f"💰 Total Fare: {ride.total_fare}, Passenger: {ride.passenger.username}")
        
        # Get reference from URL parameters (Paystack will send this back)
        reference = request.GET.get('reference')
        if not reference:
            # Fallback to ride's payment reference
            reference = ride.payment_reference
            print(f"🔍 Using stored payment reference: {reference}")
        else:
            print(f"🔍 Using URL reference parameter: {reference}")
        
        if not reference:
            print("❌ ERROR: No payment reference found")
            return redirect('/rider-dashboard/?payment=failed&error=no_reference')
        
        print(f"🔄 Verifying payment with Paystack...")
        response = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'}
        )
        
        print(f"📡 Paystack Verification Response Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"📊 Paystack Verification Data: {data}")
            
            if data.get('status') and data['data'].get('status') == 'success':
                print("✅ Paystack verification SUCCESS")
                
                # Update the ride's payment reference to match what was actually used
                if reference != ride.payment_reference:
                    print(f"🔄 Updating ride payment reference from {ride.payment_reference} to {reference}")
                    ride.payment_reference = reference
                
                # Calculate driver earnings
                driver_earnings = float(ride.total_fare) * 0.8 
                print(f"💸 Driver earnings: {driver_earnings}")
                
                # Update ride payment status
                ride.payment_status = 'paid'
                ride.paid_at = timezone.now()
                ride.save()
                print("✅ Ride payment status updated to PAID")
                
                # Send WebSocket notifications
                channel_layer = get_channel_layer()
                
                # Notify rider
                async_to_sync(channel_layer.group_send)(
                    f"ride_{ride.id}",
                    {
                        "type": "ride_update",
                        "event": "payment_completed",
                        "ride_id": ride.id,
                        "message": "Payment completed! Thank you for your ride."
                    }
                )
                print("✅ Rider WebSocket notification sent")
                
                # Notify driver
                async_to_sync(channel_layer.group_send)(
                    f"driver_{ride.driver.id}",
                    {
                        "type": "ride_update",
                        "event": "payment_received", 
                        "ride_id": ride.id,
                        "message": "Rider payment completed!",
                        "earnings": driver_earnings
                    }
                )
                print("✅ Driver WebSocket notification sent")
                
                # Redirect with success
                print("🔄 Redirecting to dashboard with success...")
                return redirect(f'/rider-dashboard/?payment=success&amount={ride.total_fare}&ride_id={ride.id}')
            else:
                paystack_status = data.get('data', {}).get('status', 'unknown')
                print(f"❌ Paystack verification FAILED - Status: {paystack_status}")
                return redirect(f'/rider-dashboard/?payment=failed&error=paystack_failed&status={paystack_status}')
        else:
            print(f"❌ Paystack HTTP ERROR - Status: {response.status_code}")
            return redirect(f'/rider-dashboard/?payment=failed&error=http_error&status={response.status_code}')
    
    except RideRequest.DoesNotExist:
        print(f"❌ ERROR: Ride with ID {ride_id} does not exist")
        return redirect('/rider-dashboard/?payment=failed&error=ride_not_found')
    
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR in payment_success: {str(e)}")
        import traceback
        traceback.print_exc()
        return redirect(f'/rider-dashboard/?payment=failed&error=unexpected&message={str(e)}')

def debug_session(request, message):
    """Helper to debug session state"""
    session_data = {
        'current_ride_id': request.session.get('current_ride_id'),
        'current_ride_status': request.session.get('current_ride_status'),
        'completed_ride_fare': request.session.get('completed_ride_fare'),
        'current_driver': request.session.get('current_driver'),
    }
    print(f"[SESSION DEBUG] {message}: {session_data}")




@login_required(login_url='home')
def driver_profile(request):
    if not hasattr(request.user, 'driver'):
        return redirect('home')
    
    driver = request.user.driver
    
    
    
    completed_rides = RideRequest.objects.filter(
        driver=request.user,
        status='completed'
    )
    
    
    total_earnings = sum(ride.driver_earnings for ride in completed_rides)
    
    
    completed_trips = completed_rides.count()
    
    
    
    
    context = {
        'driver': {
            'full_name': driver.full_name,
            'initials': ''.join([name[0] for name in driver.full_name.split()[:2]]).upper(),
            'rating': float(driver.rating) if driver.rating else 4.5,
            'trip_count': completed_trips,
            'status': 'Online' if driver.is_approved else 'Offline',
            'vehicle': {
                'model': driver.vehicle_model,
                'type': driver.vehicle_type,
                'license_plate': driver.license_plate or "Not Set",
                
            },
            'stats': {
                'total_earnings': total_earnings,
                'completed_trips': completed_trips,
                'rating': float(driver.rating) if driver.rating else 4.5,
                'online_hours': "5h 42m"  
            }
        }
    }
    return render(request, 'driver-profile.html', context)

@login_required(login_url='home')
def driver_earnings(request):
    return render(request, 'driver-earnings.html')
    


@csrf_exempt  
def estimate_fare(request):
    if request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST
            
            pickup = data.get('pickup') or data.get('current_location')
            destination = data.get('destination')
            if not pickup or not destination:
                return JsonResponse(
                    {'error': 'Both pickup and destination are required'}, 
                    status=400
                )
            distance_km, duration_min = get_google_distance(pickup, destination)
            if distance_km is None:
                return JsonResponse(
                    {'error': 'Could not calculate route - check addresses'},
                    status=400
                )
            fare = calculate_ride_fare(distance_km, duration_min)
            return JsonResponse(fare)      
        except Exception as e:
            logger.error(f"Error in estimate_fare: {str(e)}", exc_info=True)
            return JsonResponse(
                {'error': 'Internal server error'},
                status=500
            )   
    return JsonResponse(
        {'error': 'Only POST requests are supported'},
        status=405
    )


#AUTHENTICATION

def home(request):
    return render(request, 'home.html')


def get_a_ride(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        full_name = request.POST.get('full_name')
        email = request.POST.get('email')
        phone_number = request.POST.get('phone_number')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        address = request.POST.get('address')

        if not username or not full_name or not email or not phone_number or not password or not confirm_password:
            messages.error(request, 'All fields are required.')
            return redirect('getaride')   
        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return redirect('getaride') 
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username is already taken.')
            return redirect('getaride')
        if User.objects.filter(email=email).exists():
            messages.error(request, 'Email is already registered.')
            return redirect('getaride')
        if Rider.objects.filter(phone_number=phone_number).exists():
            messages.error(request, 'Phone number is already registered.')
            return redirect('getaride')   
        user = User.objects.create(
            username=username,
            email=email,
            password=make_password(password),
            address = address
        )     
        Rider.objects.create(
            user=user,
            full_name=full_name,
            phone_number=phone_number,
            address = address
        )
        messages.success(request, 'Registration successful! You can now log in.')
        return redirect('signin')
    return render(request, 'get-a-ride.html')


def become_a_driver(request):
    if request.method == 'POST':     
        form = DriverRegistrationForm(request.POST, request.FILES)
        if form.is_valid():         
            email = form.cleaned_data['email']    
            if Rider.objects.filter(user__email=email).exists():
                messages.info(request, "You're already registered as a Rider. Contact support to upgrade to Driver.")
                return redirect('upgrade_to_driver')
            try:
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data['password'],          
                )         
                driver = Driver.objects.create(
                    user=user,
                    full_name=form.cleaned_data['full_name'],
                    phone_number=form.cleaned_data['phone_number'],
                    date_of_birth=form.cleaned_data['date_of_birth'],
                    vehicle_type=form.cleaned_data['vehicle_type'],
                    vehicle_model=form.cleaned_data['vehicle_model'],
                    drivers_license=form.cleaned_data['drivers_license'],
                    vehicle_insurance=form.cleaned_data['vehicle_insurance'],
                    vehicle_registration=form.cleaned_data['vehicle_registration'],
                    roadworthiness_certificate=form.cleaned_data['roadworthiness_certificate'],
                    national_identification_number=form.cleaned_data['national_identification_number'],
                    proof_of_residency=form.cleaned_data['proof_of_residency'],
                    passport_photo=form.cleaned_data['passport_photo'],
                    bank_name=form.cleaned_data['bank_name'],
                    account_number=form.cleaned_data['account_number'],
                    account_holder_name=form.cleaned_data['account_holder_name'],
                    is_approved=False,
                    latitude=form.cleaned_data.get('latitude'),
                    longitude=form.cleaned_data.get('longitude'),
                    current_address=form.cleaned_data.get('current_address', '')
                )
                
                messages.success(request, 'Registration successful! Awaiting admin approval.')
                return redirect('signin')
            except Exception as e:
                if 'user' in locals():
                    user.delete()       
                logger.error(f"Driver registration error: {str(e)}")
                print(f"Error: {str(e)}")  
        else:     
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.capitalize()}: {error}")
    else:
        form = DriverRegistrationForm()
    return render(request, 'become-a-driver.html', {'form': form})


def upgrade_to_driver(request):
    user = request.user
    if not user.is_authenticated:
        messages.error(request, "You must be logged in to access this page.")
        return redirect('signin')
    if request.method == 'POST':
        form = UpgradeToDriverForm(request.POST, request.FILES, user=user)
    else:
        form = UpgradeToDriverForm(user=user)
    if form.is_valid():
        try:       
            Driver.objects.create(
                user=user,
                full_name=form.cleaned_data['full_name'],
                phone_number=form.cleaned_data['phone_number'],
                date_of_birth=form.cleaned_data['date_of_birth'],
                vehicle_type=form.cleaned_data['vehicle_type'],
                vehicle_model=form.cleaned_data['vehicle_model'],
                drivers_license=form.cleaned_data['drivers_license'],
                vehicle_insurance=form.cleaned_data['vehicle_insurance'],
                vehicle_registration=form.cleaned_data['vehicle_registration'],
                roadworthiness_certificate=form.cleaned_data['roadworthiness_certificate'],
                national_identification_number=form.cleaned_data['national_identification_number'],
                proof_of_residency=form.cleaned_data['proof_of_residency'],
                passport_photo=form.cleaned_data['passport_photo'],
                bank_name=form.cleaned_data['bank_name'],
                account_number=form.cleaned_data['account_number'],
                account_holder_name=form.cleaned_data['account_holder_name'],
                is_approved=False
            )
            messages.success(request, 'Upgrade successful! Awaiting admin approval.')
            return redirect('driver_dashboard')
        except Rider.DoesNotExist:
            messages.error(request, 'You are not registered as a Rider.')
        except Exception as e:
            messages.error(request, f'An error occurred: {str(e)}')
    return render(request, 'upgrade-to-driver.html', {'form': form})



def signin(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password') 
        user = authenticate(request, username=username, password=password)
        if user:    
            try:
                driver = Driver.objects.get(user=user)
                if not driver.is_approved:
                    messages.error(request, 'Your driver account is pending approval. Please wait for admin verification.')
                    return redirect('signin')
                login(request, user)
                return redirect('driver_dashboard')

            except Driver.DoesNotExist:
                pass       
            try:
                rider = Rider.objects.get(user=user)
                login(request, user)
                return redirect('rider_dashboard')
            except Rider.DoesNotExist:
                pass
            messages.error(request, 'Invalid account type. Please contact support.')
        else:
            messages.error(request, 'Wrong username or password.')
    return render(request, 'signin.html')

def logout(request):
    auth.logout(request)
    return redirect('signin')