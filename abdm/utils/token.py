from datetime import UTC, datetime

from care.emr.models.healthcare_service import HealthcareService
from care.emr.models.patient import Patient
from care.emr.models.scheduling.schedule import SchedulableResource
from care.emr.models.scheduling.token import Token, TokenCategory, TokenQueue
from care.emr.resources.scheduling.schedule.spec import SchedulableResourceTypeOptions
from care.emr.resources.scheduling.token.spec import TokenStatusOptions
from care.facility.models.facility import Facility
from abdm.utils.user import get_or_create_abdm_user


def get_or_create_token_queue(facility: Facility):
    abdm_user = get_or_create_abdm_user()

    healthcare_service, _ = HealthcareService.objects.get_or_create(
        facility=facility,
        created_by=abdm_user,
        defaults={
            "facility": facility,
            "created_by": abdm_user,
            "name": "ABDM Healthcare Service",
            "extra_details": "This healthcare service is auto created for ABDM related uses like scheduling patients via scan and share.",
        },
    )

    schedulable_resource, _ = SchedulableResource.objects.get_or_create(
        facility=facility,
        healthcare_service=healthcare_service,
        resource_type=SchedulableResourceTypeOptions.healthcare_service.value,
        created_by=abdm_user,
        defaults={
            "facility": facility,
            "healthcare_service": healthcare_service,
            "resource_type": SchedulableResourceTypeOptions.healthcare_service.value,
            "created_by": abdm_user,
        },
    )

    today = datetime.now(UTC).date()
    token_queue, _ = TokenQueue.objects.get_or_create(
        facility=facility,
        resource=schedulable_resource,
        date=today,
        system_generated=True,
        created_by=abdm_user,
        defaults={
            "facility": facility,
            "resource": schedulable_resource,
            "date": today,
            "system_generated": True,
            "created_by": abdm_user,
            "name": "ABDM Scan and Share Queue",
        },
    )

    return token_queue


def get_or_create_scan_and_share_token_category(facility: Facility):
    abdm_user = get_or_create_abdm_user()

    token_category, _ = TokenCategory.objects.get_or_create(
        facility=facility,
        resource_type=SchedulableResourceTypeOptions.healthcare_service.value,
        shorthand="ABDM",
        created_by=abdm_user,
        defaults={
            "facility": facility,
            "resource_type": SchedulableResourceTypeOptions.healthcare_service.value,
            "shorthand": "ABDM",
            "created_by": abdm_user,
            "name": "Scan and Share",
        },
    )

    return token_category


def get_or_create_scan_and_share_token(patient: Patient, facility: Facility):
    abdm_user = get_or_create_abdm_user()
    token_queue = get_or_create_token_queue(facility)
    token_category = get_or_create_scan_and_share_token_category(facility)

    token = Token.objects.filter(
        facility=facility,
        patient=patient,
        queue=token_queue,
        category=token_category,
        status__in=[
            TokenStatusOptions.UNFULFILLED.value,
            TokenStatusOptions.CREATED.value,
            TokenStatusOptions.IN_PROGRESS.value,
        ],
        created_by=abdm_user,
    ).first()

    if not token:
        number = (
            Token.objects.filter(queue=token_queue, category=token_category).count() + 1
        )
        token = Token.objects.create(
            facility=facility,
            patient=patient,
            queue=token_queue,
            number=number,
            status=TokenStatusOptions.CREATED.value,
            category=token_category,
            created_by=abdm_user,
        )

    return token


def get_scan_and_share_token_by_token_number(token_number: int, facility: Facility):
    abdm_user = get_or_create_abdm_user()
    token_queue = get_or_create_token_queue(facility)
    token_category = get_or_create_scan_and_share_token_category(facility)

    return Token.objects.filter(
        facility=facility,
        queue=token_queue,
        category=token_category,
        number=token_number,
        created_by=abdm_user,
        status__in=[
            TokenStatusOptions.UNFULFILLED.value,
            TokenStatusOptions.CREATED.value,
            TokenStatusOptions.IN_PROGRESS.value,
        ],
    ).first()
