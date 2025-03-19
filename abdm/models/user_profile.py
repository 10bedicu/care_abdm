from django.db import models
from django.utils.translation import gettext_lazy as _

from care.users.models import User
from care.utils.models.base import BaseModel


class UserProfile(BaseModel):
    """
    Model to store ABDM specific user profile information.
    This model has a one-to-one relationship with the User model.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='abdm_profile',
        verbose_name=_("User")
    )
    
    # ABDM specific fields
    hip_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("HIP ID"),
        help_text=_("Health Information Provider ID for the user")
    )
    
    hiu_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("HIU ID"),
        help_text=_("Health Information User ID")
    )
    
    is_hip_admin = models.BooleanField(
        default=False,
        verbose_name=_("Is HIP Admin"),
        help_text=_("Whether the user is an administrator for a Health Information Provider")
    )
    
    is_hiu_admin = models.BooleanField(
        default=False,
        verbose_name=_("Is HIU Admin"),
        help_text=_("Whether the user is an administrator for a Health Information User")
    )
    
    metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Metadata"),
        help_text=_("Additional information in JSON format")
    )
    
    def __str__(self):
        return f"{self.user.username}'s ABDM Profile"
    
    class Meta:
        verbose_name = _("ABDM User Profile")
        verbose_name_plural = _("ABDM User Profiles") 