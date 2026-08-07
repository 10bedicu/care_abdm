import logging

import requests
from celery import shared_task
from django.contrib.auth import get_user_model
from django.db.models import Q

from abdm.service.helper import uuid
from abdm.service.v3.gateway import GatewayService
from abdm.tasks.process_inbound_callback import LOW_PRIORITY
from care.emr.models.observation import Observation
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import QuestionnaireResponse

logger = logging.getLogger(__name__)

User = get_user_model()

# Only network-level failures are worth retrying here. An ABDM-side rejection is
# deterministic -- retrying it four times just multiplies load on a gateway that is
# already unhealthy. Those leave an INITIATED Transaction for the nightly
# retry_failed_care_contexts sweep to pick up, which is fine: linking is not
# time sensitive.
RETRY_FOR = (requests.Timeout, requests.ConnectionError)


def enqueue_link_care_context(
    patient_external_id: str,
    care_context: dict,
    hf_id: str,
    user_id: int | None = None,
    questionnaire_response_external_id: str | None = None,
):
    link_care_context.apply_async(
        kwargs={
            # generated here, not per attempt, so the update_or_create in
            # link__carecontext updates one Transaction instead of inserting a new
            # row -- and a duplicate link request -- on every retry
            "reference_id": uuid(),
            "patient_external_id": patient_external_id,
            "care_context": care_context,
            "hf_id": hf_id,
            "user_id": user_id,
            "questionnaire_response_external_id": questionnaire_response_external_id,
        },
        priority=LOW_PRIORITY,
    )


@shared_task(
    name="abdm.link_care_context",
    bind=True,
    autoretry_for=RETRY_FOR,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def link_care_context(
    self,
    reference_id: str,
    patient_external_id: str,
    care_context: dict,
    hf_id: str,
    user_id: int | None = None,
    questionnaire_response_external_id: str | None = None,
):
    if questionnaire_response_external_id:
        questionnaire_response = QuestionnaireResponse.objects.filter(
            external_id=questionnaire_response_external_id
        ).first()

        if not questionnaire_response:
            logger.warning(
                "LINK_CARE_CONTEXT :: Questionnaire response %s not found, skipping",
                questionnaire_response_external_id,
            )
            return

        has_coded_observation = (
            Observation.objects.filter(questionnaire_response=questionnaire_response)
            .filter(
                Q(main_code__isnull=False) & ~Q(main_code={})
                | Q(alternate_coding__isnull=False) & ~Q(alternate_coding=[])
            )
            .exists()
        )

        if not has_coded_observation:
            return

    patient = Patient.objects.filter(external_id=patient_external_id).first()

    if not patient:
        logger.warning(
            "LINK_CARE_CONTEXT :: Patient %s not found, skipping care context %s",
            patient_external_id,
            care_context.get("reference"),
        )
        return

    user = User.objects.filter(id=user_id).first() if user_id else None

    GatewayService.link__carecontext(
        {
            "reference_id": reference_id,
            "patient": patient,
            "care_contexts": [care_context],
            "user": user,
            "hf_id": hf_id,
        }
    )
