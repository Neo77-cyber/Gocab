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


logger = logging.getLogger(__name__)




#RIDER

@login_required(login_url='home')
def rider_dashboard(request):
    return render (request, 'rider-dashboard.html')

@login_required
def request_ride(request):
    if request.method == 'POST':
        try:
            current_location = request.POST.get('current_location')
            destination = request.POST.get('destination')

            if not current_location or not destination:
                return JsonResponse(
                    {'error': 'Both locations are required'}, 
                    status=400
                )    
            distance_km, duration_min = get_google_distance(current_location, destination)
            
            if not distance_km:
                return JsonResponse(
                    {'error': 'Could not calculate route. Please check locations.'},
                    status=400
                )       
            fare = calculate_ride_fare(distance_km, duration_min)
                  
            ride = RideRequest.objects.create(
                passenger=request.user,
                current_location=current_location,
                destination=destination,
                distance_km=distance_km,
                duration_min=duration_min,
                base_fare=fare['base_fare'],
                distance_fare=fare['distance_fare'],
                time_fare=fare['time_fare'],
                total_fare=fare['total_fare'],
                surge_multiplier=fare['surge_multiplier'],
                status='pending'
            )

            return JsonResponse({
                'status': 'success',
                'ride_id': ride.id,
                'message': 'Ride booked successfully',
                'fare': {
                    'total': fare['total_fare'],
                    'base': fare['base_fare'],
                    'distance': fare['distance_fare'],
                    'time': fare['time_fare'],
                    'surge': fare['surge_multiplier']
                },
                'distance': distance_km,
                'duration': duration_min
            })

        except Exception as e:
            logger.error(f"Error in request_ride: {str(e)}", exc_info=True)
            return JsonResponse(
                {'error': 'Internal server error: ' + str(e)},
                status=500
            )

    return JsonResponse(
        {'error': 'Invalid request method'},
        status=405
    )
                

@login_required
def ride_status(request, ride_id):
    ride = get_object_or_404(RideRequest, id=ride_id, passenger=request.user)
    
    response_data = {
        'status': ride.status,
        'driver': None,
        'eta': None,
        'distance': None
    }
    print(response_data)
    
    if ride.driver:
        
        if ride.status == 'accepted':

            eta_minutes = max(5, int(ride.distance_km * 2))  
            response_data.update({
                'driver': {
                    'name': ride.driver.get_full_name() or ride.driver.username,
                    'rating': ride.driver.driver.rating if hasattr(ride.driver, 'driver') else 4.5,
                    'car_model': ride.driver.driver.vehicle_model if hasattr(ride.driver, 'driver') else 'Unknown',
                    'license_plate': ride.driver.driver.license_plate if hasattr(ride.driver, 'driver') else 'N/A',
                },
                'eta': eta_minutes,
                'distance': f"{ride.distance_km:.1f} km" if ride.distance_km else None
            })
    
    return JsonResponse(response_data)

@login_required
def cancel_ride(request, ride_id):
    ride = get_object_or_404(RideRequest, id=ride_id, passenger=request.user)
    
    if ride.status not in ['completed', 'cancelled']:
        ride.status = 'cancelled'
        ride.cancelled_at = timezone.now()
        ride.save()
        
        return JsonResponse({'status': 'success'})
    
    return JsonResponse({'status': 'already_ended'}, status=400)

#DRIVER

@login_required(login_url='home')
def driver_dashboard(request):
    if not hasattr(request.user, 'driver'):
        messages.error(request, "You must be a registered driver to view this page.")
        return redirect('home')

    context = {
        'new_requests': RideRequest.objects.filter(status='pending', driver__isnull=True).count(),
        'active_trips': RideRequest.objects.filter(driver=request.user, status='started').count(),
        'completed_trips': RideRequest.objects.filter(driver=request.user, status='completed').count(),
        'pending_rides': RideRequest.objects.filter(status='pending', driver__isnull=True).order_by('-requested_at'),
        'accepted_rides': RideRequest.objects.filter(driver=request.user, status='accepted').order_by('-accepted_at'),
        'active_rides': RideRequest.objects.filter(driver=request.user, status='started').order_by('-started_at'),
        'completed_rides': RideRequest.objects.filter(driver=request.user, status='completed').order_by('-completed_at')
    }
    return render(request, 'driver-dashboard.html', context)

@login_required
def accept_ride(request, ride_id):
    ride = get_object_or_404(RideRequest, id=ride_id, status='pending', driver__isnull=True)
    ride.driver = request.user
    ride.status = 'accepted'
    ride.accepted_at = timezone.now()
    ride.save()
    messages.success(request, f"Ride accepted! Please proceed to pickup at {ride.current_location}")
    return redirect('driver_dashboard')

@login_required
def start_trip(request, ride_id):
    ride = get_object_or_404(RideRequest, id=ride_id, driver=request.user, status='accepted')
    ride.status = 'started'
    ride.started_at = timezone.now()
    ride.save()
    messages.success(request, "Trip started! Taking passenger to destination.")
    return redirect('driver_dashboard')

@login_required
def complete_trip(request, ride_id):
    ride = get_object_or_404(RideRequest, id=ride_id, driver=request.user, status='started')
    ride.status = 'completed'
    ride.completed_at = timezone.now()
    ride.save()
    messages.success(request, "Trip completed successfully! Payment processed.")
    return redirect('driver_dashboard')

@login_required(login_url='home')
def completed_trips(request):
    trips = Trip.objects.filter(driver=request.user, status='completed').order_by('-ended_at')
    return render(request, 'completed-trips.html', {'trips': trips})

@login_required(login_url='home')
def available_rides(request):
    if not hasattr(request.user, 'driver'):
        messages.error(request, "You must be a registered driver to view this page.")
        return redirect('driver_dashboard')
    rides = RideRequest.objects.filter(status='pending', driver__isnull=True).order_by('-requested_at')
    print(rides)
    return render(request, 'available_rides.html', {'rides': rides})

@login_required(login_url='home')
def driver_profile(request):
    return render(request, 'driver-profile.html')

@login_required(login_url='home')
def driver_earnings(request):
    return render(request, 'driver-earnings.html')


@csrf_exempt  
def estimate_fare(request):
    if request.method == 'POST':
        try:
            try:
                data = json.loads(request.body.decode('utf-8'))
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON'}, status=400) 
            pickup = data.get('pickup')
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
            password=make_password(password)
        )     
        Rider.objects.create(
            user=user,
            full_name=full_name,
            phone_number=phone_number
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