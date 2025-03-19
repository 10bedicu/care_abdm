import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from celery import shared_task

from care.users.models import User
from abdm.models.user_profile import UserProfile
from abdm.models.failed_hpr_registration import FailedHPRRegistration
from abdm.service.v3.hpr import HPRService
from abdm.service.helper import ABDMAPIException

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def register_user_with_hpr(self, user_id):
    """
    Celery task to register a user with the HPR.
    This is run asynchronously to avoid blocking the user creation/update process.
    It also retries if there are any errors.
    """
    try:
        user = User.objects.get(id=user_id)
        
        # Check if user already has HPR ID
        if hasattr(user, 'abdm_profile') and user.abdm_profile.hiu_id:
            logger.info(f"User {user.username} already has HPR ID: {user.abdm_profile.hiu_id}")
            
            # Clear any failed registration records
            FailedHPRRegistration.objects.filter(user=user).delete()
            return
        
        # Register with HPR
        response = HPRService.register_health_professional({
            "user": user,
            # You can extend this with more professional details if available
        })
        
        # Get HPR ID from response
        hpr_id = response.get("hprId")
        
        if not hpr_id:
            logger.error(f"No HPR ID returned from API for user {user.username}")
            record_failed_registration(user, "No HPR ID returned from API")
            return
        
        # Update or create ABDM profile
        with transaction.atomic():
            abdm_profile, created = UserProfile.objects.get_or_create(
                user=user,
                defaults={"hiu_id": hpr_id}
            )
            
            if not created:
                abdm_profile.hiu_id = hpr_id
                abdm_profile.save()
            
            # Clear any failed registration records
            FailedHPRRegistration.objects.filter(user=user).delete()
                
        logger.info(f"Successfully registered user {user.username} with HPR ID: {hpr_id}")
        
    except User.DoesNotExist:
        logger.error(f"User with ID {user_id} does not exist")
    except ABDMAPIException as e:
        error_message = str(e)
        logger.error(f"ABDM API error while registering user with HPR: {error_message}")
        record_failed_registration(User.objects.get(id=user_id), error_message)
        # Retry with exponential backoff
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=e, countdown=countdown)
    except Exception as e:
        error_message = str(e)
        logger.exception(f"Error registering user with HPR: {error_message}")
        record_failed_registration(User.objects.get(id=user_id), error_message)
        # Retry with exponential backoff
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=e, countdown=countdown)

@shared_task(bind=True, max_retries=3)
def update_user_hpr_details(self, user_id):
    """
    Celery task to update a user's HPR details.
    This is run asynchronously to avoid blocking the user update process.
    """
    try:
        user = User.objects.get(id=user_id)
        
        # Check if user has HPR ID
        if not hasattr(user, 'abdm_profile') or not user.abdm_profile.hiu_id:
            # User doesn't have an HPR ID yet, register instead of update
            register_user_with_hpr.delay(user_id)
            return
        
        # Update HPR details
        HPRService.update_health_professional({
            "user": user,
            "hpr_id": user.abdm_profile.hiu_id,
            # You can extend this with more professional details if available
        })
        
        # Clear any failed registration records
        FailedHPRRegistration.objects.filter(user=user).delete()
        
        logger.info(f"Successfully updated HPR details for user {user.username}")
        
    except User.DoesNotExist:
        logger.error(f"User with ID {user_id} does not exist")
    except ABDMAPIException as e:
        error_message = str(e)
        logger.error(f"ABDM API error while updating user HPR details: {error_message}")
        record_failed_registration(User.objects.get(id=user_id), error_message)
        # Retry with exponential backoff
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=e, countdown=countdown)
    except Exception as e:
        error_message = str(e)
        logger.exception(f"Error updating user HPR details: {error_message}")
        record_failed_registration(User.objects.get(id=user_id), error_message)
        # Retry with exponential backoff
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=e, countdown=countdown)


def record_failed_registration(user, error_message=None):
    """
    Record a failed HPR registration for later retry
    """
    try:
        failed_registration, created = FailedHPRRegistration.objects.get_or_create(
            user=user,
            defaults={
                "error_message": error_message,
                "retry_count": 0,
                "last_retry": timezone.now()
            }
        )
        
        if not created:
            failed_registration.error_message = error_message
            failed_registration.retry_count += 1
            failed_registration.last_retry = timezone.now()
            failed_registration.save()
            
        logger.info(f"Recorded failed HPR registration for user {user.username}")
    except Exception as e:
        logger.exception(f"Error recording failed HPR registration: {str(e)}")


@receiver(post_save, sender=User)
def user_post_save_handler(sender, instance, created, **kwargs):
    """
    Signal handler for post save of User model
    """
    # Get plugin settings
    is_abdm_enabled = getattr(settings, 'ABDM_ENABLED', False)
    
    # Only proceed if ABDM is enabled
    if not is_abdm_enabled:
        return
    
    if created:
        # New user registration
        register_user_with_hpr.delay(instance.id)
    else:
        # User update
        update_user_hpr_details.delay(instance.id) 