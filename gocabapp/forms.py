from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .models import Driver, Rider
from django.contrib.auth.password_validation import validate_password
from django.core.validators import RegexValidator
from django import forms

class DriverRegistrationForm(forms.Form):
    
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)
    
    
    full_name = forms.CharField(max_length=255)
    phone_number = forms.RegexField(
    regex=r'^\d{11}$',
    max_length=11,
    error_messages={
        'invalid': "Enter a valid 11-digit phone number."
    },
    widget=forms.TextInput(attrs={'maxlength': '11'})
    )

    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    national_identification_number = forms.CharField(max_length=100)
    
    # Vehicle info
    vehicle_type = forms.ChoiceField(choices=[('Car', 'Car'), ('Bike', 'Bike')])
    vehicle_model = forms.CharField(max_length=100)
    
    
    # Documents
    drivers_license = forms.FileField()
    vehicle_insurance = forms.FileField()
    vehicle_registration = forms.FileField()
    roadworthiness_certificate = forms.FileField()
    proof_of_residency = forms.FileField()
    passport_photo = forms.FileField()
    
    # Banking info
    bank_name = forms.CharField(max_length=100)
    account_number = forms.CharField(
    max_length=10,
    validators=[RegexValidator(r'^\d{10}$', 'Account number must be 10 digits long.')]
)
    account_holder_name = forms.CharField(max_length=255)
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        
        try:
            validate_password(password)
        except ValidationError as e:
            for error in e:
                self.add_error('password', error)

        
        if password != confirm_password:
            self.add_error('confirm_password', "Passwords don't match")

        return cleaned_data
    
    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise ValidationError("This username is already taken")
        return username
    
    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email=email).exists():
            if Driver.objects.filter(user__email=email).exists():
                raise ValidationError("You're already registered as a Driver")
        return email
    
    def clean_phone_number(self):
        phone_number = self.cleaned_data['phone_number']
        if Driver.objects.filter(phone_number=phone_number).exists():
            raise ValidationError("This phone number is already registered")
        return phone_number





class UpgradeToDriverForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'readonly': 'readonly'})
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'readonly': 'readonly'})
    )
    phone_number = forms.RegexField(
        regex=r'^\d{11}$',
        max_length=11,
        error_messages={'invalid': "Enter a valid 11-digit phone number."},
        widget=forms.TextInput(attrs={'readonly': 'readonly'})
    )

    full_name = forms.CharField(max_length=255)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    national_identification_number = forms.CharField(max_length=100)

    vehicle_type = forms.ChoiceField(choices=[('Car', 'Car'), ('Bike', 'Bike')])
    vehicle_model = forms.CharField(max_length=100)

    drivers_license = forms.FileField()
    vehicle_insurance = forms.FileField()
    vehicle_registration = forms.FileField()
    roadworthiness_certificate = forms.FileField()
    proof_of_residency = forms.FileField()
    passport_photo = forms.FileField()

    bank_name = forms.CharField(max_length=100)
    account_number = forms.RegexField(
        regex=r'^\d{10}$',
        error_messages={'invalid': "Account number must be 10 digits long."}
    )
    account_holder_name = forms.CharField(max_length=255)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if user:
            self.fields['username'].initial = user.username
            self.fields['email'].initial = user.email

            try:
                rider = Rider.objects.get(user=user)
                self.fields['phone_number'].initial = rider.phone_number
            except Rider.DoesNotExist:
                pass
