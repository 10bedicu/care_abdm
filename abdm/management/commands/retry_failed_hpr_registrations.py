import logging
import json
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.db import models

from care.users.models import User
from abdm.models.user_profile import UserProfile
from abdm.signals.hpr_registration import register_user_with_hpr

logger = logging.getLogger(__name__)

class FailedHPRRegistration(models.Model):
    """
    Model to track failed HPR registrations
    This is purely a tracking model and not for persistence
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    last_retry = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'abdm'
        db_table = 'abdm_failed_hpr_registration'


class Command(BaseCommand):
    help = 'Retry failed HPR registrations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-retry-age',
            type=int,
            default=7,
            help='Maximum age in days to retry registrations',
        )
        parser.add_argument(
            '--max-retries',
            type=int,
            default=5,
            help='Maximum number of retry attempts',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of registrations to retry in each batch',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without making actual API calls or DB changes',
        )

    def handle(self, *args, **options):
        max_retry_age = options['max_retry_age']
        max_retries = options['max_retries']
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        
        # Verify that ABDM is enabled
        is_abdm_enabled = getattr(settings, 'ABDM_ENABLED', False)
        if not is_abdm_enabled:
            self.stdout.write(
                self.style.WARNING(
                    'ABDM is not enabled in settings. Exiting.'
                )
            )
            return
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    'Running in dry-run mode. No actual changes will be made.'
                )
            )
        
        # Find all failed registrations within the retry period
        cutoff_date = timezone.now() - timedelta(days=max_retry_age)
        
        failed_registrations = FailedHPRRegistration.objects.filter(
            models.Q(retry_count__lt=max_retries) &
            (
                models.Q(last_retry__lt=cutoff_date) |
                models.Q(last_retry__isnull=True)
            )
        ).select_related('user').order_by('retry_count', 'last_retry')[:batch_size]
        
        total = failed_registrations.count()
        
        self.stdout.write(
            self.style.SUCCESS(f'Found {total} failed registrations to retry')
        )
        
        # Process each failed registration
        for registration in failed_registrations:
            self._process_registration(registration, dry_run)
    
    def _process_registration(self, registration, dry_run):
        """
        Process a single failed registration retry
        """
        try:
            user = registration.user
            
            # Check if user already has HPR ID (may have been registered manually)
            has_hpr_id = (
                hasattr(user, 'abdm_profile') and 
                user.abdm_profile.hiu_id is not None and 
                user.abdm_profile.hiu_id != ''
            )
            
            if has_hpr_id:
                self.stdout.write(
                    f'User {user.username} (ID: {user.id}) now has HPR ID: '
                    f'{user.abdm_profile.hiu_id} - Removing from failed registrations'
                )
                if not dry_run:
                    registration.delete()
                return
            
            if dry_run:
                self.stdout.write(
                    f'Would retry HPR registration for user {user.username} '
                    f'(ID: {user.id}) - Retry count: {registration.retry_count}'
                )
            else:
                # Update retry count and timestamp
                registration.retry_count += 1
                registration.last_retry = timezone.now()
                registration.save()
                
                # The actual registration is handled by the task
                register_user_with_hpr.delay(user.id)
                
                self.stdout.write(
                    f'Retrying HPR registration for user {user.username} '
                    f'(ID: {user.id}) - Retry count: {registration.retry_count}'
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f'Error processing registration retry: {str(e)}'
                )
            ) 