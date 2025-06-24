from django.db import models
from django.contrib.auth.models import User

# Create your models here.


class Rider(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, blank= True, null = True)
    full_name = models.CharField(max_length=255, blank= True, null = True)
    email = models.EmailField(unique=True, blank= True, null = True)
    phone_number = models.CharField(max_length=15, unique=True, blank= True, null = True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name
    

class Driver(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=11, unique=True, blank=True, null=True)
    date_of_birth = models.DateField()
    vehicle_type = models.CharField(max_length=50)
    vehicle_model = models.CharField(max_length=100)
    rating = models.CharField(max_length=100, blank=True, null=True)
    license_plate = models.CharField(max_length=100, blank=True, null=True)
    drivers_license = models.FileField(upload_to='documents/drivers_license/')
    vehicle_insurance = models.FileField(upload_to='documents/vehicle_insurance/')
    vehicle_registration = models.FileField(upload_to='documents/vehicle_registration/')
    roadworthiness_certificate = models.FileField(upload_to='documents/roadworthiness_certificate/')
    national_identification_number = models.CharField(max_length=100)
    proof_of_residency = models.FileField(upload_to='documents/proof_of_residency/')
    passport_photo = models.FileField(upload_to='documents/passport_photos/')
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=20, unique=True)
    account_holder_name = models.CharField(max_length=255)
    is_approved = models.BooleanField(default=False)  # Approval status
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username
    






class RideRequest(models.Model):
    STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('accepted', 'Accepted'),
    ('started', 'Started'),  
    ('completed', 'Completed'), 
    ('cancelled', 'Cancelled'),
]

    passenger = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_requests')
    driver = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='ride_requests_as_driver')
    current_location = models.CharField(max_length=255)
    pickup_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    destination = models.CharField(max_length=255)
    dropoff_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    dropoff_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=500.00)
    distance_km = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    duration_min = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    time_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    distance_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    surge_multiplier = models.DecimalField(max_digits=3, decimal_places=1, default=1.0)

    def __str__(self):
        return f"Request by {self.passenger.username} from {self.current_location} to {self.destination}"

class Trip(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('started', 'Started'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    passenger = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trips')
    driver = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='trips_as_driver')
    pickup_location = models.CharField(max_length=255)
    pickup_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    dropoff_location = models.CharField(max_length=255)
    dropoff_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    dropoff_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    distance_km = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    estimated_time = models.DurationField(null=True, blank=True)
    is_paid = models.BooleanField(default=False)
    payment_reference = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Trip from {self.pickup_location} to {self.dropoff_location} - {self.status}"

class Fare(models.Model):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='fare')
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=500.00)
    per_km_rate = models.DecimalField(max_digits=10, decimal_places=2, default=50.00)
    per_minute_rate = models.DecimalField(max_digits=10, decimal_places=2, default=10.00)
    total_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    def calculate_fare(self, distance_km, duration_minutes):
        self.total_fare = self.base_fare + (distance_km * self.per_km_rate) + (duration_minutes * self.per_minute_rate)
        self.save()
        return self.total_fare

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    def __str__(self):
        return f"Notification for {self.user.username}"