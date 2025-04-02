import logging
from django.db import transaction
from django.conf import settings

from abdm.models import HealthcareProviderRegistry
from abdm.service.helper import ABDMAPIException
from abdm.api.viewsets.healthcare_provider import register_healthcare_provider
from abdm.settings import plugin_settings as abdm_settings

logger = logging.getLogger(__name__)

# Signal handler removed as we're taking a manual approach for HPR ID creation and verification 