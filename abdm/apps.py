from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "abdm"


class AbdmConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("ABDM Integration")

    def ready(self):
        import abdm.signals  # noqa F401
