"""Moves ABHA addresses off ``AbhaNumber.health_id`` before the column is retired
as ``abha_address_legacy``.

Every step walks its table in primary-key batches. Historical models have no
soft-delete manager, so ``deleted`` is filtered explicitly where it matters.
"""

from uuid import UUID

from django.db import migrations

BATCH_SIZE = 1000

LEGACY = "legacy"

SCAN_AND_SHARE = 3
LINK_CARE_CONTEXT = 4
TRANSACTION_INITIATED = 1
TRANSACTION_COMPLETED = 2
TRANSACTION_FAILED = 3

ADMISSION_CLASSES = ("imp", "emer", "obsenc")
HI_TYPE_BY_MODEL = {
    "prescription": "Prescription",
    "file_upload": "HealthDocumentRecord",
    "questionnaire_response": "WellnessRecord",
    "daily_round": "WellnessRecord",
    "diagnostic_report": "DiagnosticReport",
    "investigation_session": "DiagnosticReport",
    "invoice": "Invoice",
    "immunization": "ImmunizationRecord",
}


def normalize(abha_address):
    return (abha_address or "").strip().lower()


def batched(queryset, *fields):
    last_pk = 0
    while True:
        batch = list(
            queryset.filter(pk__gt=last_pk)
            .order_by("pk")
            .values("pk", *fields)[:BATCH_SIZE]
        )
        if not batch:
            return
        yield batch
        last_pk = batch[-1]["pk"]


def as_uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def meta_data_of(transaction):
    meta_data = transaction["meta_data"]
    return meta_data if isinstance(meta_data, dict) else {}


def health_facility_lookups(apps):
    HealthFacility = apps.get_model("abdm", "HealthFacility")

    by_hf_id, by_facility = {}, {}
    for pk, hf_id, facility_external_id in HealthFacility.objects.filter(
        deleted=False
    ).values_list("pk", "hf_id", "facility_id"):
        by_hf_id[hf_id] = pk
        by_facility[facility_external_id] = pk
    return by_hf_id, by_facility


def ensure_legacy_auths(AbhaAuth, addresses):
    """``addresses`` maps (abha number pk, health facility pk) to an ABHA address.

    Returns the legacy auth pk for each pair, creating the ones that are missing.
    """
    if not addresses:
        return {}

    auths = {}
    existing = (
        AbhaAuth.objects.filter(
            method=LEGACY,
            deleted=False,
            abha_number_id__in={pair[0] for pair in addresses},
            health_facility_id__in={pair[1] for pair in addresses},
        )
        .order_by("pk")
        .values_list("abha_number_id", "health_facility_id", "pk")
    )
    for abha_number_id, health_facility_id, pk in existing:
        auths.setdefault((abha_number_id, health_facility_id), pk)

    missing = [
        AbhaAuth(
            abha_number_id=abha_number_id,
            health_facility_id=health_facility_id,
            abha_address=address,
            method=LEGACY,
        )
        for (abha_number_id, health_facility_id), address in addresses.items()
        if address and (abha_number_id, health_facility_id) not in auths
    ]
    for auth in AbhaAuth.objects.bulk_create(missing):
        auths[(auth.abha_number_id, auth.health_facility_id)] = auth.pk

    return auths


def fill_payment_order_addresses(apps):
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    PaymentOrder = apps.get_model("abdm", "PaymentOrder")
    max_length = PaymentOrder._meta.get_field("abha_address").max_length

    for batch in batched(PaymentOrder.objects.filter(abha_address=""), "abha_number_id"):
        addresses = dict(
            AbhaNumber.objects.filter(pk__in={row["abha_number_id"] for row in batch})
            .exclude(health_id__isnull=True)
            .values_list("pk", "health_id")
        )
        PaymentOrder.objects.bulk_update(
            [
                PaymentOrder(
                    pk=row["pk"],
                    abha_address=addresses[row["abha_number_id"]][:max_length],
                )
                for row in batch
                if addresses.get(row["abha_number_id"])
            ],
            ["abha_address"],
        )


def create_legacy_auths(apps, by_facility):
    AbhaAuth = apps.get_model("abdm", "AbhaAuth")
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    Encounter = apps.get_model("emr", "Encounter")
    Facility = apps.get_model("facility", "Facility")

    health_facilities = {
        facility_pk: by_facility[facility_external_id]
        for facility_external_id, facility_pk in Facility.objects.filter(
            external_id__in=list(by_facility)
        ).values_list("external_id", "pk")
    }
    if not health_facilities:
        return

    abha_numbers = AbhaNumber.objects.filter(
        deleted=False, patient_id__isnull=False, health_id__isnull=False
    )
    for batch in batched(abha_numbers, "patient_id", "health_id"):
        by_patient = {row["patient_id"]: row for row in batch}

        # where linking happens today: facilities the patient has an encounter at
        visits = (
            Encounter.objects.filter(
                patient_id__in=list(by_patient),
                facility_id__in=list(health_facilities),
                deleted=False,
            )
            .values_list("patient_id", "facility_id")
            .distinct()
        )
        ensure_legacy_auths(
            AbhaAuth,
            {
                (
                    by_patient[patient_id]["pk"],
                    health_facilities[facility_id],
                ): normalize(by_patient[patient_id]["health_id"])
                for patient_id, facility_id in visits
            },
        )


def backfill_scan_and_share_auths(apps, by_facility):
    AbhaAuth = apps.get_model("abdm", "AbhaAuth")
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    Facility = apps.get_model("facility", "Facility")
    Token = apps.get_model("emr", "Token")
    Transaction = apps.get_model("abdm", "Transaction")

    health_facilities = {
        facility_pk: by_facility[facility_external_id]
        for facility_external_id, facility_pk in Facility.objects.filter(
            external_id__in=list(by_facility)
        ).values_list("external_id", "pk")
    }
    if not health_facilities:
        return

    transactions = Transaction.objects.filter(type=SCAN_AND_SHARE, deleted=False)
    for batch in batched(transactions, "meta_data"):
        meta_datas = [meta_data_of(row) for row in batch]

        abha_numbers = {
            external_id: (pk, health_id)
            for external_id, pk, health_id in AbhaNumber.objects.filter(
                external_id__in={
                    as_uuid(meta_data.get("abha_number")) for meta_data in meta_datas
                }
                - {None},
                deleted=False,
                health_id__isnull=False,
            ).values_list("external_id", "pk", "health_id")
        }
        # the share does not record the facility, the token issued for it does;
        # soft-deleted tokens are kept since they still name it
        token_facilities = dict(
            Token.objects.filter(
                external_id__in={
                    as_uuid(meta_data.get("token")) for meta_data in meta_datas
                }
                - {None}
            ).values_list("external_id", "facility_id")
        )

        addresses = {}
        for meta_data in meta_datas:
            abha_number = abha_numbers.get(as_uuid(meta_data.get("abha_number")))
            health_facility_id = health_facilities.get(
                token_facilities.get(as_uuid(meta_data.get("token")))
            )
            if abha_number and health_facility_id:
                abha_number_id, health_id = abha_number
                addresses[(abha_number_id, health_facility_id)] = normalize(health_id)

        ensure_legacy_auths(AbhaAuth, addresses)


def migrate_consent_requests(apps, by_hf_id, by_facility):
    AbhaAuth = apps.get_model("abdm", "AbhaAuth")
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    ConsentRequest = apps.get_model("abdm", "ConsentRequest")
    Encounter = apps.get_model("emr", "Encounter")

    for batch in batched(
        ConsentRequest.objects.all(), "patient_abha_id", "encounter_id", "hiu"
    ):
        abha_numbers = dict(
            AbhaNumber.objects.filter(
                health_id__in={row["patient_abha_id"] for row in batch}
            ).values_list("health_id", "pk")
        )
        encounter_facilities = dict(
            Encounter.objects.filter(
                external_id__in={
                    row["encounter_id"] for row in batch if row["encounter_id"]
                }
            ).values_list("external_id", "facility__external_id")
        )

        targets = {}
        for row in batch:
            abha_number_id = abha_numbers.get(row["patient_abha_id"])
            health_facility_id = by_facility.get(
                encounter_facilities.get(row["encounter_id"])
            ) or by_hf_id.get(row["hiu"])
            if abha_number_id and health_facility_id:
                targets[row["pk"]] = (abha_number_id, health_facility_id)

        auths = ensure_legacy_auths(
            AbhaAuth,
            {
                targets[row["pk"]]: normalize(row["patient_abha_id"])
                for row in batch
                if row["pk"] in targets
            },
        )
        ConsentRequest.objects.bulk_update(
            [
                ConsentRequest(pk=pk, abha_auth_id=auths[pair])
                for pk, pair in targets.items()
                if pair in auths
            ],
            ["abha_auth"],
        )


def migrate_consent_artefacts(apps, by_hf_id):
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    ConsentArtefact = apps.get_model("abdm", "ConsentArtefact")
    ConsentRequest = apps.get_model("abdm", "ConsentRequest")

    for batch in batched(
        ConsentArtefact.objects.all(), "patient_abha_id", "consent_request_id", "hip"
    ):
        patients = dict(
            AbhaNumber.objects.filter(
                health_id__in={row["patient_abha_id"] for row in batch}
            ).values_list("health_id", "patient_id")
        )
        request_facilities = dict(
            ConsentRequest.objects.filter(
                consent_id__in={
                    row["consent_request_id"]
                    for row in batch
                    if row["consent_request_id"]
                }
            ).values_list("consent_id", "abha_auth__health_facility_id")
        )

        ConsentArtefact.objects.bulk_update(
            [
                ConsentArtefact(
                    pk=row["pk"],
                    abha_address=normalize(row["patient_abha_id"]),
                    patient_id=patients.get(row["patient_abha_id"]),
                    # an artefact with a consent request is ours as the HIU, and
                    # ``hip`` then names the facility that holds the records
                    health_facility_id=request_facilities.get(
                        row["consent_request_id"]
                    )
                    if row["consent_request_id"]
                    else by_hf_id.get(row["hip"]),
                )
                for row in batch
            ],
            ["abha_address", "patient", "health_facility"],
        )


def care_context_hi_types(Encounter, references):
    encounter_ids = {
        as_uuid(reference.rsplit("::", 1)[-1])
        for reference in references
        if reference.startswith("v2::encounter::")
    }
    admissions = set(
        Encounter.objects.filter(
            external_id__in=encounter_ids - {None},
            encounter_class__in=ADMISSION_CLASSES,
        ).values_list("external_id", flat=True)
    )

    hi_types = {}
    for reference in references:
        parts = reference.split("::")
        # references older than v1 are bare consultation ids
        model = parts[1] if len(parts) == 3 else "consultation"  # noqa: PLR2004
        if parts[0] == "v2" and model == "encounter":
            is_admission = as_uuid(parts[2]) in admissions
            hi_types[reference] = (
                "DischargeSummary" if is_admission else "OPConsultation"
            )
        else:
            hi_types[reference] = HI_TYPE_BY_MODEL.get(model, "OPConsultation")
    return hi_types


def backfill_care_contexts(apps, by_hf_id):
    AbhaAuth = apps.get_model("abdm", "AbhaAuth")
    AbhaNumber = apps.get_model("abdm", "AbhaNumber")
    CareContext = apps.get_model("abdm", "CareContext")
    Encounter = apps.get_model("emr", "Encounter")
    Transaction = apps.get_model("abdm", "Transaction")

    max_length = CareContext._meta.get_field("reference").max_length

    # linked rows go in first so a later retry of the same reference cannot
    # turn a link that completed back into a pending one
    for statuses, care_context_status in (
        ([TRANSACTION_COMPLETED], "linked"),
        ([TRANSACTION_INITIATED, TRANSACTION_FAILED], "pending"),
    ):
        transactions = Transaction.objects.filter(
            type=LINK_CARE_CONTEXT, status__in=statuses, deleted=False
        )
        for batch in batched(transactions, "reference_id", "meta_data"):
            abha_number_ids = {
                as_uuid(meta_data_of(row).get("abha_number")) for row in batch
            }
            abha_numbers = {
                external_id: (pk, patient_id, normalize(health_id))
                for external_id, pk, patient_id, health_id in AbhaNumber.objects.filter(
                    external_id__in=abha_number_ids - {None}, deleted=False
                ).values_list("external_id", "pk", "patient_id", "health_id")
            }

            rows = {}
            for row in batch:
                meta_data = meta_data_of(row)
                abha_number_id, patient_id, abha_address = abha_numbers.get(
                    as_uuid(meta_data.get("abha_number")), (None, None, "")
                )
                health_facility_id = by_hf_id.get(meta_data.get("hf_id"))
                if not (patient_id and health_facility_id and abha_address):
                    continue

                user_initiated = meta_data.get("type") == "patient_initiated_linking"
                for reference in meta_data.get("care_contexts") or []:
                    if (
                        not isinstance(reference, str)
                        or not reference
                        or len(reference) > max_length
                        # only current references can still be sent
                        or (
                            care_context_status == "pending"
                            and not reference.startswith("v2::")
                        )
                    ):
                        continue
                    rows.setdefault(
                        (health_facility_id, abha_address, reference),
                        {
                            "abha_number_id": abha_number_id,
                            "patient_id": patient_id,
                            "request_id": row["reference_id"],
                            "user_initiated": user_initiated,
                        },
                    )

            auths = ensure_legacy_auths(
                AbhaAuth,
                {
                    (row["abha_number_id"], health_facility_id): abha_address
                    for (health_facility_id, abha_address, _), row in rows.items()
                    if not row["user_initiated"]
                },
            )
            hi_types = care_context_hi_types(Encounter, {key[2] for key in rows})

            CareContext.objects.bulk_create(
                [
                    CareContext(
                        health_facility_id=health_facility_id,
                        patient_id=row["patient_id"],
                        abha_address=abha_address,
                        abha_auth_id=None
                        if row["user_initiated"]
                        else auths.get((row["abha_number_id"], health_facility_id)),
                        reference=reference,
                        hi_type=hi_types[reference],
                        status=care_context_status,
                        via="user_initiated"
                        if row["user_initiated"]
                        else "hip_initiated",
                        request_id=row["request_id"],
                        legacy=True,
                    )
                    for (health_facility_id, abha_address, reference), row in rows.items()
                ],
                ignore_conflicts=True,
            )


def forwards(apps, schema_editor):
    by_hf_id, by_facility = health_facility_lookups(apps)

    fill_payment_order_addresses(apps)
    create_legacy_auths(apps, by_facility)
    backfill_scan_and_share_auths(apps, by_facility)
    migrate_consent_requests(apps, by_hf_id, by_facility)
    migrate_consent_artefacts(apps, by_hf_id)
    backfill_care_contexts(apps, by_hf_id)


class Migration(migrations.Migration):

    dependencies = [
        ("abdm", "0011_abhaauth_carecontext_consent_scope"),
        ("emr", "0040_alter_observation_interpretation"),
        ("facility", "0432_alter_fileupload_file_type"),
    ]

    operations = [
        migrations.RunPython(forwards, reverse_code=migrations.RunPython.noop),
    ]
