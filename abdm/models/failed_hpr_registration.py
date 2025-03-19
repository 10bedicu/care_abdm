from django.db import models
from django.utils.translation import gettext_lazy as _

from care.users.models import User
from care.utils.models.base import BaseModel


class FailedHPRRegistration(BaseModel):
    """
    Model to track failed HPR registrations.
    This model records users whose HPR registration failed and needs to be retried.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='failed_hpr_registrations',
        verbose_name=_("User")
    )
    
    error_message = models.TextField(
        null=True,
        blank=True,
        verbose_name=_("Error Message"),
        help_text=_("The error message that was returned during registration")
    )
    
    retry_count = models.IntegerField(
        default=0,
        verbose_name=_("Retry Count"),
        help_text=_("Number of times registration has been retried")
    )
    
    last_retry = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Retry"),
        help_text=_("When the registration was last retried")
    )
    
    def __str__(self):
        return f"Failed HPR Registration for {self.user.username}"
    
    class Meta:
        verbose_name = _("Failed HPR Registration")
        verbose_name_plural = _("Failed HPR Registrations")
        unique_together = ['user', 'deleted'] 