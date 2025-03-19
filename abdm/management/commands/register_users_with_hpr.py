import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings

from care.users.models import User
from abdm.models.user_profile import UserProfile
from abdm.signals.hpr_registration import register_user_with_hpr

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Register existing users with HPR (Health Professional Registry)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of users to process in each batch',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without making actual API calls or DB changes',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Process only a specific user ID',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force registration even if user already has an HPR ID',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        user_id = options.get('user_id')
        force = options.get('force', False)
        
        # Verify that ABDM is enabled
        is_abdm_enabled = getattr(settings, 'ABDM_ENABLED', False)
        if not is_abdm_enabled and not force:
            self.stdout.write(
                self.style.WARNING(
                    'ABDM is not enabled in settings. Use --force to proceed anyway.'
                )
            )
            return
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    'Running in dry-run mode. No actual changes will be made.'
                )
            )
        
        # Process a specific user if user_id is provided
        if user_id:
            try:
                user = User.objects.get(id=user_id)
                self._process_user(user, dry_run, force)
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'User with ID {user_id} does not exist.')
                )
            return
        
        # Get all users without an ABDM profile or with an empty hiu_id
        if not force:
            users_without_hpr = self._get_users_without_hpr()
            total = users_without_hpr.count()
        else:
            # If force is enabled, process all users
            users_without_hpr = User.objects.all()
            total = users_without_hpr.count()
        
        self.stdout.write(
            self.style.SUCCESS(f'Found {total} users to register with HPR')
        )
        
        # Process users in batches
        processed = 0
        
        for i in range(0, total, batch_size):
            batch = users_without_hpr[i:i + batch_size]
            for user in batch:
                self._process_user(user, dry_run, force)
                processed += 1
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Processed {processed}/{total} users'
                )
            )
    
    def _get_users_without_hpr(self):
        """
        Returns QuerySet of users without an HPR ID
        """
        # Strategy 1: Users without an ABDM profile at all
        users_without_profile = User.objects.filter(abdm_profile__isnull=True)
        
        # Strategy 2: Users with an ABDM profile but no HIU ID
        users_with_empty_hiu = User.objects.filter(
            abdm_profile__isnull=False, 
            abdm_profile__hiu_id__isnull=True
        )
        users_with_blank_hiu = User.objects.filter(
            abdm_profile__isnull=False, 
            abdm_profile__hiu_id=''
        )
        
        # Combine the strategies
        return users_without_profile.union(users_with_empty_hiu, users_with_blank_hiu)
    
    def _process_user(self, user, dry_run, force):
        """
        Process a single user for HPR registration
        """
        try:
            # Check if user already has HPR ID
            has_hpr_id = (
                hasattr(user, 'abdm_profile') and 
                user.abdm_profile.hiu_id is not None and 
                user.abdm_profile.hiu_id != ''
            )
            
            if has_hpr_id and not force:
                self.stdout.write(
                    f'Skipping user {user.username} (ID: {user.id}) - '
                    f'already has HPR ID: {user.abdm_profile.hiu_id}'
                )
                return
            
            if dry_run:
                self.stdout.write(
                    f'Would register user {user.username} (ID: {user.id}) with HPR'
                )
            else:
                # The actual registration is handled by the task
                register_user_with_hpr.delay(user.id)
                self.stdout.write(
                    f'Queued user {user.username} (ID: {user.id}) for HPR registration'
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f'Error processing user {user.username} (ID: {user.id}): {str(e)}'
                )
            ) 