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
from .services import get_google_distance, calculate_ride_fare
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


logger = logging.getLogger(__name__)




@receiver(post_save, sender=RideRequest)
def ride_request_update(sender, instance, created, **kwargs):
    if not created:
        channel_layer = get_channel_layer()
        
        # Send to passenger
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

        # Send to driver
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
        
        # Basic timestamp handling
        last_updated = ride.requested_at.isoformat()
        
        driver_info = None
        if ride.driver and hasattr(ride.driver, 'driver'):
            driver_info = {
                'name': ride.driver.get_full_name() or ride.driver.username,
                'car_model': ride.driver.driver.vehicle_model or 'Unknown',
                'license_plate': ride.driver.driver.license_plate or 'N/A'
            }
        
        # Minimal response without any decimal fields
        response_data = {
            'status': ride.status,
            'ride_id': ride.id,
            'driver': driver_info,
            'current_location': ride.current_location,
            'destination': ride.destination,
            'last_updated': last_updated
        }
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"Error in ride_status: {str(e)}")
        return JsonResponse({
            'error': 'Could not fetch ride status',
            'details': str(e)
        }, status=500)

# Rider Views
@login_required(login_url='home')
def rider_dashboard(request):
    active_ride = RideRequest.objects.filter(
        passenger=request.user,
        status__in=['pending', 'accepted', 'started']
    ).order_by('-requested_at').first()

    
    try:
        rider_profile = Rider.objects.get(user=request.user)
    except Rider.DoesNotExist:
        rider_profile = None

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
    total_miles = sum(ride.distance_km for ride in completed_rides if ride.distance_km)  

    recent_rides = RideRequest.objects.filter(
        passenger=request.user,
        status='completed'
    ).order_by('-completed_at')[:5].select_related('driver')

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
    return render(request, 'rider-dashboard.html', context)

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
        
        # Calculate distance and fare
        distance_km, duration_min = get_google_distance(current_location, destination)
        if not distance_km:
            return JsonResponse({'error': 'Could not calculate route'}, status=400)
            
        fare = calculate_ride_fare(distance_km, duration_min)
        
        # Create ride
        ride = RideRequest.objects.create(
            passenger=request.user,
            current_location=current_location,
            destination=destination,
            distance_km=distance_km,
            duration_min=duration_min,
            total_fare=fare['total_fare'],
            status='pending'
        )
        
        
        # Notify drivers
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            "available_drivers",
            {
                'type': 'driver_update',
                'event': 'new_ride',
                'ride_id': ride.id,
                'pickup': ride.current_location,
                'destination': ride.destination,
                'fare': ride.total_fare
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

    # Get active ride if exists
    active_ride = RideRequest.objects.filter(
        driver=request.user,
        status__in=['accepted', 'started']
    ).order_by('-accepted_at').first()
    
    # Get available rides if no active ride
    available_rides = None
    if not active_ride:
        available_rides = RideRequest.objects.filter(
            status='pending',
            driver__isnull=True
        ).order_by('-requested_at')
    
    context = {
        'active_ride': active_ride,
        'available_rides': available_rides,
        'is_driver': True
    }
    return render(request, 'driver-dashboard.html', context)

@login_required
def pending_rides(request):
    rides = RideRequest.objects.filter(
        status='pending',
        driver__isnull=True
    ).select_related('passenger').order_by('-requested_at')[:20]
    for ride in rides:
        
        phone_number = ride.passenger.rider.phone_number if hasattr(ride.passenger, 'rider') else "No phone number"
        
    
    html = render_to_string('partials/_pending_rides.html', {
        'pending_rides': rides,
        'request': request
    })
    return JsonResponse({'html': html})

@login_required
def accepted_rides(request):

    rides = RideRequest.objects.filter(
        driver=request.user,
        status='accepted'
    ).select_related('passenger__rider').order_by('-accepted_at')[:20]

    
    html = render_to_string('partials/_accepted_rides.html', {
        'accepted_rides': rides,
        'request': request
    })
    return JsonResponse({'html': html})

@login_required
def active_rides(request):
    rides = RideRequest.objects.filter(
        driver=request.user,
        status='started'
    ).select_related('passenger__rider').order_by('-started_at')
    
    html = render_to_string('partials/_active_rides.html', {
        'active_rides': rides,
        'request': request
    })
    return JsonResponse({'html': html})

@login_required(login_url='home')
def completed_trips(request):
    try:
        rides = RideRequest.objects.filter(
            driver=request.user,
            status='completed'
        ).order_by('-completed_at')
        
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

@csrf_exempt
def accept_ride(request, ride_id):
    if request.method == 'POST':
        try:
            ride = RideRequest.objects.get(id=ride_id, status='pending')
            ride.driver = request.user
            ride.status = 'accepted'
            ride.accepted_at = timezone.now()
            ride.save()

            # Prepare ride data for broadcast
            ride_data = {
                'id': ride.id,
                'fare': ride.total_fare,
                'status': ride.status,
                'pickup': ride.current_location,
                'dropoff': ride.destination,
                'distance_km': float(ride.distance_km),  
                'duration_min': float(ride.duration_min), 
                'fare': float(ride.total_fare),
                'passenger': ride.passenger.username,
                'requested_at': ride.requested_at,
            }

            # Serialize safely before sending to channel layer
            ride_payload = json.loads(json.dumps(ride_data, cls=DjangoSafeJSONEncoder))

            # WebSocket broadcast
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"driver_{request.user.id}",
                {
                    "type": "ride.accepted",
                    "ride": ride_payload
                    
                }
            )

            async_to_sync(channel_layer.group_send)(
                        f"ride_{ride.id}",
                        {
                            "type": "ride_update",
                            "event": "accepted",
                            "ride_id": ride.id,
                            "driver": {
                                "name": request.user.get_full_name(),
                                "car_model": request.user.driver.vehicle_model,
                                "license_plate": request.user.driver.license_plate,
                                "rating": float(request.user.driver.rating or 0),
                            },
                            "eta": 7,
                            "distance": float(ride.distance_km or 0),
                            "fare": float(ride.total_fare or 0),
                        }
                    )

            return JsonResponse({'status': 'success'})

        except RideRequest.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Ride not found'}, status=404)


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
        
        # Update ride status
        ride.status = 'started'
        ride.started_at = timezone.now()
        ride.save()
        
        ride_data = {
            'id': ride.id,
            'status': ride.status,
            'started_at': ride.started_at.isoformat(),
            'pickup': ride.current_location,
            'dropoff': ride.destination,
            'distance_km': float(ride.distance_km),
            'duration_min': float(ride.duration_min),
            'fare': float(ride.total_fare),
            'driver': {
                'id': request.user.id,
                'name': request.user.get_full_name(),
                'car_model': request.user.driver.vehicle_model,
                'license_plate': request.user.driver.license_plate,
                'rating': float(request.user.driver.rating or 0),
                'phone': str(request.user.driver.phone_number),  # Changed this line
            },
            'passenger': {
                'id': ride.passenger.id,
                'name': ride.passenger.get_full_name(),
                'rating': 0,
            }
        }

        # Serialize safely
        ride_payload = json.loads(json.dumps(ride_data, cls=DjangoSafeJSONEncoder))
        
        channel_layer = get_channel_layer()
        
        # Notify passenger (through ride-specific group)
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
        
        # Notify driver (through driver-specific group)
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
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def complete_trip(request, ride_id):
    
    
    if not hasattr(request.user, 'driver'):
        
        return JsonResponse({
            'status': 'error',
            'error': 'Driver profile not found'
        }, status=403)

    try:
        
        all_rides_with_id = RideRequest.objects.filter(id=ride_id)
        
        
        
        user_rides = RideRequest.objects.filter(driver=request.user)
        
        
        
        ride = RideRequest.objects.get(id=ride_id, driver=request.user)
        
        
        if ride.status != 'started':
            
            return JsonResponse({
                'status': 'error', 
                'error': f'Ride must be started first. Current status: {ride.status}'
            }, status=400)
        else:
            print(f"✅ STATUS OK: Ride is started, proceeding with completion")
            
        
        ride.status = 'completed'
        ride.completed_at = timezone.now()
        ride.save()
        
        
        ride.refresh_from_db()
        

        ride_data = {
            'id': ride.id,
            'status': ride.status,
            'started_at': ride.started_at.isoformat() if ride.started_at else None,
            'pickup': ride.current_location,
            'dropoff': ride.destination,
            'distance_km': float(ride.distance_km) if ride.distance_km else 0,
            'duration_min': float(ride.duration_min) if ride.duration_min else 0,
            'fare': float(ride.total_fare) if ride.total_fare else 0,
            'driver': {
                'id': request.user.id,
                'name': request.user.get_full_name(),
                'car_model': getattr(request.user.driver, 'vehicle_model', ''),
                'license_plate': getattr(request.user.driver, 'license_plate', ''),
                'rating': float(getattr(request.user.driver, 'rating', 0) or 0),
                'phone': str(getattr(request.user.driver, 'phone_number', '')),  
            },
            'passenger': {
                'id': ride.passenger.id,
                'name': ride.passenger.get_full_name(),
                'rating': 0,
            }
        }

        
        ride_payload = json.loads(json.dumps(ride_data, cls=DjangoSafeJSONEncoder))
        

        channel_layer = get_channel_layer()
        
        
        # Notify passenger (through ride-specific group)
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
        
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'status': 'error',
            'error': str(e)
        }, status=500)





@login_required(login_url='home')
def driver_profile(request):
    if not hasattr(request.user, 'driver'):
        return redirect('home')
    
    driver = request.user.driver
    
    
    today = timezone.now().date()
    completed_trips = RideRequest.objects.filter(
        driver=request.user,
        status='completed'
    ).count()
    
    today_earnings = RideRequest.objects.filter(
        driver=request.user,
        status='completed',
        completed_at__date=today
    ).aggregate(total=Sum('total_fare'))['total'] or 0
    
    
    
    
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
                'today_earnings': today_earnings,
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