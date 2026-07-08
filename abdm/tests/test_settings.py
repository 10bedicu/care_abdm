from config.settings.test import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "abdm"]  # noqa: F405

PLUGIN_CONFIGS = {  # noqa: F405
    **PLUGIN_CONFIGS,  # noqa: F405
    "abdm": {},
}
