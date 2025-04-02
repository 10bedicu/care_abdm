from django.db import models

from care.utils.models.base import BaseModel
from abdm.settings import plugin_settings as settings


class HealthcareProviderRegistry(BaseModel):
    hpr_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    registered = models.BooleanField(default=False)
    registration_error = models.TextField(null=True, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="healthcare_provider"
    )
    
    # Additional fields that might be required based on HPR API
    name = models.CharField(max_length=100, null=True, blank=True)
    mobile = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    
    # Status fields
    is_verified = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)
    verification_time = models.DateTimeField(null=True, blank=True)
    verification_method = models.CharField(
        max_length=20, 
        choices=[
            ('API', 'API Verification'),
            ('MANUAL', 'Manual Verification'),
            ('AUTO', 'Automatic Registration'),
        ],
        null=True, 
        blank=True
    )
    
    class Meta:
        verbose_name = "Healthcare Provider Registry"
        verbose_name_plural = "Healthcare Provider Registries"
    
    def __str__(self):
        return f"{self.hpr_id or 'Unregistered'} - {self.user.username}" 
        
    @property
    def requires_registration(self):
        """
        Check if the record requires registration with the HPR system
        """
        return not self.registered and not self.hpr_id
        
    @property
    def status_display(self):
        """
        Get a user-friendly status display
        """
        if self.is_verified:
            return "Verified"
        if self.registered:
            return "Registered"
        if self.registration_error:
            return f"Registration Failed: {self.registration_error}"
        return "Pending Registration" 