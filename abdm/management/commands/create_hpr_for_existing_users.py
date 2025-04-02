import logging
from django.core.management.base import BaseCommand
from django.apps import apps
from abdm.models import HealthcareProviderRegistry
from abdm.api.viewsets.healthcare_provider import register_healthcare_provider
from abdm.settings import plugin_settings as settings

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Create HPR entries for existing users'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of users to process in each batch',
        )
        
    def handle(self, *args, **options):
        batch_size = options['batch_size']
        
        # Dynamically get the User model
        User = apps.get_model(settings.AUTH_USER_MODEL)
        
        total_users = User.objects.count()
        users_with_hpr = HealthcareProviderRegistry.objects.values_list('user_id', flat=True)
        
        # Get users without HPR entries
        users_without_hpr = User.objects.exclude(id__in=users_with_hpr)
        total_without_hpr = users_without_hpr.count()
        
        self.stdout.write(f"Total users: {total_users}")
        self.stdout.write(f"Users without HPR: {total_without_hpr}")
        
        # Process in batches
        processed = 0
        failed = 0
        
        for i in range(0, total_without_hpr, batch_size):
            batch = users_without_hpr[i:i+batch_size]
            self.stdout.write(f"Processing batch {i//batch_size + 1} ({len(batch)} users)")
            
            for user in batch:
                try:
                    # Skip system users
                    if user.username == settings.ABDM_USERNAME:
                        continue
                        
                    # Create HPR entry
                    hpr_registry = HealthcareProviderRegistry.objects.create(
                        user=user,
                        name=f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip() or user.username,
                        email=getattr(user, 'email', ''),
                        mobile=getattr(user, 'phone_number', ''),
                    )
                    
                    # Queue registration task
                    register_healthcare_provider.delay(hpr_registry.id)
                    processed += 1
                    
                except Exception as e:
                    self.stderr.write(f"Error processing user {user.username}: {str(e)}")
                    logger.exception(f"Error creating HPR for user {user.username}")
                    failed += 1
            
        self.stdout.write(self.style.SUCCESS(f"Successfully processed {processed} users"))
        if failed > 0:
            self.stdout.write(self.style.WARNING(f"Failed to process {failed} users")) 