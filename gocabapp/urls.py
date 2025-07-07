from django.urls import path
from . import views
from .views import estimate_fare

urlpatterns = [
    path('', views.home, name='home'),
    path('signin/', views.signin, name='signin'),
    path('get-a-ride/', views.get_a_ride, name='getaride'),
    path('become-a-driver/', views.become_a_driver, name='becomeadriver'),
    path('upgrade-to-driver/', views.upgrade_to_driver, name='upgrade_to_driver'),
    path('driver-dashboard/', views.driver_dashboard, name='driver_dashboard'),
    path('driver-profile/', views.driver_profile, name='driver_profile'),
    path('driver-earnings/', views.driver_earnings, name='driver_earnings'),
    path('rider-dashboard/', views.rider_dashboard, name='rider_dashboard'),
    path('request_ride/', views.request_ride, name='request_ride'), 
    path('driver/accept-ride/<int:ride_id>/', views.accept_ride, name='accept_ride'),
    path('driver/start-trip/<int:ride_id>/', views.start_trip, name='start_trip'),
    path('complete-trip/<int:ride_id>/', views.complete_trip, name='complete_trip'),
    path('driver/completed-trips/', views.completed_trips, name='completed_trips'),
    path('api/estimate-fare/', estimate_fare, name='estimate_fare'),
    path('ride-status/<int:ride_id>/', views.ride_status, name='ride_status'),
    path('driver/pending-rides/', views.pending_rides, name='pending_rides'),
    path('driver/accepted-rides/', views.accepted_rides, name='accepted_rides'), 
    path('driver/active-rides/', views.active_rides, name='active_rides'),
    path('driver/cancel-ride/<int:ride_id>/', views.cancel_ride, name='cancel_ride'),
    path('logout', views.logout, name = 'logout'),


    ]