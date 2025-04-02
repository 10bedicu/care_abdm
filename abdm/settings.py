from typing import Any

import environ
from abdm.apps import PLUGIN_NAME
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from rest_framework.settings import perform_import

env = environ.Env()


class PluginSettings:  # pragma: no cover
    """
    A settings object that allows plugin settings to be accessed as
    properties. For example:

        from plugin.settings import plugin_settings
        print(plugin_settings.API_KEY)

    Any setting with string import paths will be automatically resolved
    and return the class, rather than the string literal.

    """

    def __init__(
        self,
        plugin_name: str = None,
        defaults: dict | None = None,
        import_strings: set | None = None,
        required_settings: set | None = None,
    ) -> None:
        if not plugin_name:
            raise ValueError("Plugin name must be provided")
        self.plugin_name = plugin_name
        self.defaults = defaults or {}
        self.import_strings = import_strings or set()
        self.required_settings = required_settings or set()
        self._cached_attrs = set()
        self.validate()

    def __getattr__(self, attr) -> Any:
        if attr not in self.defaults:
            raise AttributeError("Invalid setting: '%s'" % attr)

        # Try to find the setting from user settings, then from environment variables
        val = self.defaults[attr]
        try:
            val = self.user_settings[attr]
        except KeyError:
            try:
                val = env(attr, cast=type(val))
            except environ.ImproperlyConfigured:
                # Fall back to defaults
                pass

        # Coerce import strings into classes
        if attr in self.import_strings:
            val = perform_import(val, attr)

        self._cached_attrs.add(attr)
        setattr(self, attr, val)
        return val

    @property
    def user_settings(self) -> dict:
        if not hasattr(self, "_user_settings"):
            self._user_settings = getattr(settings, "PLUGIN_CONFIGS", {}).get(
                self.plugin_name, {}
            )
        return self._user_settings

    def validate(self) -> None:
        """
        This method handles the validation of the plugin settings.
        It could be overridden to provide custom validation logic.

        the base implementation checks if all the required settings are truthy.
        """
        for setting in self.required_settings:
            if not getattr(self, setting):
                raise ImproperlyConfigured(
                    f'The "{setting}" setting is required. '
                    f'Please set the "{setting}" in the environment or the {PLUGIN_NAME} plugin config.'
                )

    def reload(self) -> None:
        """
        Deletes the cached attributes so they will be recomputed next time they are accessed.
        """
        for attr in self._cached_attrs:
            delattr(self, attr)
        self._cached_attrs.clear()
        if hasattr(self, "_user_settings"):
            delattr(self, "_user_settings")


REQUIRED_SETTINGS = {
    "ABDM_CLIENT_ID",
    "ABDM_CLIENT_SECRET",
    "ABDM_GATEWAY_URL",
    "ABDM_ABHA_URL",
    "ABDM_FACILITY_URL",
    "ABDM_CM_ID",
    "CURRENT_DOMAIN",
    "BACKEND_DOMAIN",
    "ABDM_HPR_URL",  # HPR URL is required
}

DEFAULTS = {
    "ABDM_CLIENT_ID": "SBX_001",
    "ABDM_CLIENT_SECRET": "xxxx",
    "ABDM_AUTH_URL": "",
    "ABDM_GATEWAY_URL": "https://dev.abdm.gov.in/api/hiecm",
    "ABDM_ABHA_URL": "https://abhasbx.abdm.gov.in/abha/api",
    "ABDM_FACILITY_URL": "https://facilitysbx.abdm.gov.in",
    "ABDM_HIP_NAME_PREFIX": "",
    "ABDM_HIP_NAME_SUFFIX": "",
    "ABDM_USERNAME": "abdm_user_internal",
    "ABDM_CM_ID": "sbx",
    "AUTH_USER_MODEL": "users.User",
    "CURRENT_DOMAIN": "https://care.ohc.network",
    "BACKEND_DOMAIN": "https://careapi.ohc.network",
    # HPR settings
    "ABDM_HPR_URL": "https://hprsbx.abdm.gov.in",  # Sandbox URL for HPR
    "ENABLE_HPR_ON_PLUGIN_ENABLE": True,  # Run the management command when plugin is enabled
    "HPR_VERIFY_AADHAAR_ENABLED": True,  # Enable Aadhaar-based HPR ID verification
    "HPR_CREATE_ALLOWED": True,  # Allow creation of new HPR IDs
}

plugin_settings = PluginSettings(
    PLUGIN_NAME, defaults=DEFAULTS, required_settings=REQUIRED_SETTINGS
)


@receiver(setting_changed)
def reload_plugin_settings(*args, **kwargs) -> None:
    setting = kwargs["setting"]
    if setting == "PLUGIN_CONFIGS":
        plugin_settings.reload()
