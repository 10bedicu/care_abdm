from care.emr.models.patient import PatientIdentifier, PatientIdentifierConfig


def ensure_abdm_patient_identifier(patient, *, system, display, value, created_by):
    patient_identifier_config = PatientIdentifierConfig.objects.filter(
        config__system=system,
    ).first()
    if not patient_identifier_config:
        patient_identifier_config = PatientIdentifierConfig.objects.create(
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
                "auto_maintained": True,
                "retrieve_config": {
                    "retrieve_with_dob": False,
                    "retrieve_with_year_of_birth": False,
                    "retrieve_with_otp": False,
                },
            },
        )

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
