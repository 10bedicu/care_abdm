from abdm.settings import plugin_settings as settings
from abdm.utils.user import get_or_create_abdm_user
from care.emr.models.patient import (
    PatientIdentifier,
    PatientIdentifierConfig,
    PatientIdentifierConfigCache,
)


def ensure_abha_patient_identifiers(patient, abha_number):
    identifiers = []
    if abha_number.abha_number:
        identifiers.append(
            {
                "system": settings.ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_SYSTEM,
                "display": settings.ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_DISPLAY,
                "value": abha_number.abha_number,
                "use": "official",
            }
        )
    if abha_number.health_id:
        identifiers.append(
            {
                "system": settings.ABDM_ABHA_ADDRESS_IDENTIFIER_SYSTEM_SYSTEM,
                "display": settings.ABDM_ABHA_ADDRESS_IDENTIFIER_SYSTEM_DISPLAY,
                "value": abha_number.health_id,
                "use": "usual",
            }
        )
    if not identifiers:
        return

    created_by = get_or_create_abdm_user()
    for identifier in identifiers:
        _ensure_abdm_patient_identifier(patient, created_by=created_by, **identifier)
    patient.build_instance_identifiers()
    patient.save()


def _ensure_abdm_patient_identifier(
    patient, *, system, display, value, created_by, use
):
    patient_identifier_config = PatientIdentifierConfig.objects.filter(
        config__system=system,
    ).first()
    if not patient_identifier_config:
        patient_identifier_config = PatientIdentifierConfig.objects.create(
            status="active",
            facility=None,
            created_by=created_by,
            config={
                "use": use,
                "description": display,
                "required": False,
                "unique": True,
                "regex": "",
                "system": system,
                "display": display,
                "auto_maintained": True,
                "retrieve_config": {
                    "retrieve_with_dob": False,
                    "retrieve_with_year_of_birth": False,
                    "retrieve_with_otp": False,
                },
            },
        )
    elif patient_identifier_config.config.get("use") != use:
        config = dict(patient_identifier_config.config)
        config["use"] = use
        patient_identifier_config.config = config
        patient_identifier_config.save(update_fields=["config"])
        PatientIdentifierConfigCache.clear_cache(patient_identifier_config.external_id)

    patient_identifier, _ = PatientIdentifier.objects.get_or_create(
        patient=patient,
        config=patient_identifier_config,
        defaults={
            "value": value,
            "created_by": created_by,
        },
    )
    if patient_identifier.value != value:
        patient_identifier.value = value
        patient_identifier.save(update_fields=["value"])
