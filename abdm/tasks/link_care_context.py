import logging

import requests
from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q

from abdm.models import HealthFacility
from abdm.service.helper import uuid
from abdm.service.linking import (
    link_care_contexts,
    notify_care_context_updated,
    sync_linked_record_type,
)
from abdm.tasks.process_inbound_callback import LOW_PRIORITY
from care.emr.models.encounter import Encounter
from care.emr.models.observation import Observation
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import QuestionnaireResponse
from care.emr.resources.questionnaire_response.spec import (
    QuestionnaireResponseStatusChoices,
)

logger = logging.getLogger(__name__)

User = get_user_model()

# Only network-level failures are worth retrying here. An ABDM-side rejection is
# deterministic -- retrying it four times just multiplies load on a gateway that is
# already unhealthy. Those leave the care context pending or failed for the nightly
# retry_failed_care_contexts sweep to pick up, which is fine: linking is not
# time sensitive.
RETRY_FOR = (requests.Timeout, requests.ConnectionError)

# a prescription written in one go adds several medication requests: ABDM is
# told once, after the last of them
CARE_CONTEXT_UPDATE_DELAY = 60
CARE_CONTEXT_UPDATE_KEY = "abdm_care_context_update__{hf_id}__{patient}__{reference}"


def enqueue_link_care_context(
    patient_external_id: str,
    care_context: dict,
    hf_id: str,
    user_id: int | None = None,
    questionnaire_response_external_id: str | None = None,
):
    link_care_context.apply_async(
        kwargs={
            # no longer read: the care context ledger makes a retry send the same
            # link request again. Still sent, for workers running the release
            # before it during a deploy
            "reference_id": uuid(),
            "patient_external_id": patient_external_id,
            "care_context": care_context,
            "hf_id": hf_id,
            "user_id": user_id,
            "questionnaire_response_external_id": questionnaire_response_external_id,
        },
        priority=LOW_PRIORITY,
    )


def enqueue_link_patient_care_contexts(patient, health_facility, user=None):
    link_patient_care_contexts.apply_async(
        kwargs={
            "patient_external_id": str(patient.external_id),
            "health_facility_external_id": str(health_facility.external_id),
            "user_id": user.id if user else None,
        },
        priority=LOW_PRIORITY,
    )


def enqueue_notify_care_context_update(
    patient_external_id: str, reference: str, hf_id: str
):
    # one notification, sent when the delay is over, covers every change made
    # until then
    if not cache.add(
        CARE_CONTEXT_UPDATE_KEY.format(
            hf_id=hf_id, patient=patient_external_id, reference=reference
        ),
        1,
        timeout=CARE_CONTEXT_UPDATE_DELAY,
    ):
        return

    notify_care_context_update.apply_async(
        kwargs={
            "patient_external_id": patient_external_id,
            "reference": reference,
            "hf_id": hf_id,
        },
        countdown=CARE_CONTEXT_UPDATE_DELAY,
        priority=LOW_PRIORITY,
    )


def enqueue_sync_encounter_record_type(encounter_external_id: str):
    sync_encounter_record_type.apply_async(
        kwargs={"encounter_external_id": encounter_external_id},
        priority=LOW_PRIORITY,
    )


# the name and parameters are kept so that tasks queued before an upgrade still run
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
        questionnaire_response = (
            QuestionnaireResponse.objects.filter(
                external_id=questionnaire_response_external_id
            )
            .exclude(status=QuestionnaireResponseStatusChoices.entered_in_error.value)
            .first()
        )

        if not questionnaire_response:
            logger.warning(
                "LINK_CARE_CONTEXT :: Questionnaire response %s not found or entered in error, skipping",
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

    health_facility = HealthFacility.objects.filter(hf_id=hf_id).first()

    if not health_facility:
        logger.warning(
            "LINK_CARE_CONTEXT :: Health facility %s not found, skipping care context %s",
            hf_id,
            care_context.get("reference"),
        )
        return

    user = User.objects.filter(id=user_id).first() if user_id else None

    link_care_contexts(patient, health_facility, care_contexts=[care_context], user=user)


@shared_task(
    name="abdm.link_patient_care_contexts",
    bind=True,
    autoretry_for=RETRY_FOR,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def link_patient_care_contexts(
    self,
    patient_external_id: str,
    health_facility_external_id: str,
    user_id: int | None = None,
):
    """Link every care context of a patient that is ready at a health facility."""
    patient = Patient.objects.filter(external_id=patient_external_id).first()
    health_facility = HealthFacility.objects.filter(
        external_id=health_facility_external_id
    ).first()

    if not patient or not health_facility:
        logger.warning(
            "LINK_CARE_CONTEXT :: Patient %s or health facility %s not found, skipping",
            patient_external_id,
            health_facility_external_id,
        )
        return

    user = User.objects.filter(id=user_id).first() if user_id else None

    link_care_contexts(patient, health_facility, user=user)


@shared_task(
    name="abdm.notify_care_context_update",
    bind=True,
    autoretry_for=RETRY_FOR,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def notify_care_context_update(
    self, patient_external_id: str, reference: str, hf_id: str
):
    """Tell ABDM that a linked care context of a patient has gained records."""
    patient = Patient.objects.filter(external_id=patient_external_id).first()
    health_facility = HealthFacility.objects.filter(hf_id=hf_id).first()

    if not patient or not health_facility:
        logger.warning(
            "LINK_CONTEXT_NOTIFY :: Patient %s or health facility %s not found, "
            "skipping care context %s",
            patient_external_id,
            hf_id,
            reference,
        )
        return

    notify_care_context_updated(patient, health_facility, reference)


@shared_task(
    name="abdm.sync_encounter_record_type",
    bind=True,
    autoretry_for=RETRY_FOR,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def sync_encounter_record_type(self, encounter_external_id: str):
    """Tell ABDM the record type of a linked encounter whose class changed."""
    encounter = (
        Encounter.objects.filter(external_id=encounter_external_id)
        .select_related("patient", "facility")
        .first()
    )

    if encounter:
        sync_linked_record_type(encounter)
