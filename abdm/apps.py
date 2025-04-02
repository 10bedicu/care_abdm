from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "abdm"


class AbdmConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("ABDM Integration")

    def ready(self):
        import abdm.signals
        
        # Check if HPR should be enabled on plugin enable
        from django.conf import settings
        from abdm.settings import plugin_settings as abdm_settings
        
        if getattr(settings, 'ENABLE_ABDM', False) and getattr(abdm_settings, 'ENABLE_HPR_ON_PLUGIN_ENABLE', False):
            # Import and run in a thread to avoid blocking app startup
            import threading
            
            def run_command():
                from django.core.management import call_command
                try:
                    call_command('create_hpr_for_existing_users')
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.exception(f"Failed to create HPR for existing users: {str(e)}")
                    
            threading.Thread(target=run_command).start()