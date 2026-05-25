from django.conf import settings
from django.db import migrations

ABHA_NUMBER_SYSTEM = "https://care.ohc.network/abha_number"
ABHA_NUMBER_DISPLAY = "ABHA Number"
ABHA_ADDRESS_SYSTEM = "https://care.ohc.network/abha_address"
ABHA_ADDRESS_DISPLAY = "ABHA Address"
ABDM_USERNAME = "abdm_user_internal"


def _get_plugin_setting(key, default):
    plugin_configs = getattr(settings, "PLUGIN_CONFIGS", {}) or {}
    abdm_configs = plugin_configs.get("abdm", {}) or {}
    return abdm_configs.get(key, default)


def _get_or_create_abdm_user(apps):
    User = apps.get_model("users", "User")
    username = _get_plugin_setting("ABDM_USERNAME", ABDM_USERNAME)
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "email": "abdm@ohc.network",
            "phone_number": "917777777777",
            "verified": True,
        },
    )
    return user


def _get_or_create_identifier_config(apps, *, system, display, created_by):
    PatientIdentifierConfig = apps.get_model("emr", "PatientIdentifierConfig")
    patient_identifier_config = PatientIdentifierConfig.objects.filter(
        config__system=system,
    ).first()
    if patient_identifier_config:
        return patient_identifier_config

    return PatientIdentifierConfig.objects.create(
        status="active",
        facility=None,
        created_by=created_by,
        config={
            "use": "official",
            "description": display,
            "required": False,
            "unique": True,
            "regex": "",
            "system": system,
            "display": display,
            "retrieve_config": {
                "retrieve_with_dob": False,
                "retrieve_with_year_of_birth": False,
                "retrieve_with_otp": False,
            },
        },
    )


def _ensure_patient_identifier(apps, *, patient, config, value, created_by):
    PatientIdentifier = apps.get_model("emr", "PatientIdentifier")
    patient_identifier, _ = PatientIdentifier.objects.get_or_create(
        patient=patient,
        config=config,
        defaults={
            "value": value,
            "created_by": created_by,
        },
    )
    if patient_identifier.value != value:
        patient_identifier.value = value
        patient_identifier.save(update_fields=["value"])


def _rebuild_instance_identifiers(apps, patient):
    PatientIdentifier = apps.get_model("emr", "PatientIdentifier")
    PatientIdentifierConfig = apps.get_model("emr", "PatientIdentifierConfig")

    config_external_ids = {
        config.id: str(config.external_id)
        for config in PatientIdentifierConfig.objects.filter(
            id__in=PatientIdentifier.objects.filter(patient=patient).values(
                "config_id"
            )
        )
    }
    patient.instance_identifiers = [
        {
            "config": config_external_ids[identifier.config_id],
            "value": identifier.value,
        }
        for identifier in PatientIdentifier.objects.filter(patient=patient)
        if identifier.config_id in config_external_ids
    ]
    patient.save(update_fields=["instance_identifiers"])


def backfill_abha_patient_identifiers(apps, schema_editor):
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    Patient = apps.get_model("emr", "Patient")

    abdm_user = _get_or_create_abdm_user(apps)

    abha_number_system = _get_plugin_setting(
        "ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_SYSTEM", ABHA_NUMBER_SYSTEM
    )
    abha_number_display = _get_plugin_setting(
        "ABDM_ABHA_NUMBER_IDENTIFIER_SYSTEM_DISPLAY", ABHA_NUMBER_DISPLAY
    )
    abha_address_system = _get_plugin_setting(
        "ABDM_ABHA_ADDRESS_IDENTIFIER_SYSTEM_SYSTEM", ABHA_ADDRESS_SYSTEM
    )
    abha_address_display = _get_plugin_setting(
        "ABDM_ABHA_ADDRESS_IDENTIFIER_SYSTEM_DISPLAY", ABHA_ADDRESS_DISPLAY
    )

    abha_number_config = _get_or_create_identifier_config(
        apps,
        system=abha_number_system,
        display=abha_number_display,
        created_by=abdm_user,
    )
    abha_address_config = _get_or_create_identifier_config(
        apps,
        system=abha_address_system,
        display=abha_address_display,
        created_by=abdm_user,
    )

    patients_to_rebuild = set()
    queryset = AbhaNumber.objects.filter(patient__isnull=False).select_related(
        "patient"
    )
    for abha_number in queryset.iterator(chunk_size=500):
        patient = abha_number.patient
        if abha_number.abha_number:
            _ensure_patient_identifier(
                apps,
                patient=patient,
                config=abha_number_config,
                value=abha_number.abha_number,
                created_by=abdm_user,
            )
        if abha_number.health_id:
            _ensure_patient_identifier(
                apps,
                patient=patient,
                config=abha_address_config,
                value=abha_number.health_id,
                created_by=abdm_user,
            )
        patients_to_rebuild.add(patient.id)

    for patient in Patient.objects.filter(id__in=patients_to_rebuild).iterator(
        chunk_size=500
    ):
        _rebuild_instance_identifiers(apps, patient)


class Migration(migrations.Migration):
    dependencies = [
        ("abdm", "0001_initial_squashed"),
        ("emr", "0078_merge_20260409_1618"),
    ]

    operations = [
        migrations.RunPython(
            backfill_abha_patient_identifiers,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
