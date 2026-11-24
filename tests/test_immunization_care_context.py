from unittest.mock import patch

from abdm.models import AbhaNumber, HealthFacility, HealthInformationType
from abdm.service.helper import (
    care_context_dict_from_reference_id,
    generate_care_contexts_for_existing_data,
)
from model_bakery import baker

from care.emr.models.immunisation import Immunization
from care.emr.resources.immunisation.immunization import ImmunizationStatus
from care.utils.tests.base import CareAPITestBase

VACCINE = {
    "system": "http://snomed.info/sct",
    "code": "1119305005",
    "display": "COVID-19 antigen vaccine",
}


class ImmunizationCareContextTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        baker.make(HealthFacility, facility=self.facility, hf_id="TEST_HIP")
        self.patient = self.create_patient(name="Vaccine Patient", gender="female")
        baker.make(AbhaNumber, patient=self.patient, health_id="vaccine@sbx")
        self.encounter = self.create_encounter(
            self.patient,
            self.facility,
            self.create_facility_organization(self.facility),
        )
        enqueue = patch("abdm.signals.register_care_contexts.enqueue_link_care_context")
        self.enqueue = enqueue.start()
        self.addCleanup(enqueue.stop)

    def record(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return baker.make(
                Immunization,
                **{
                    "patient": self.patient,
                    "encounter": self.encounter,
                    "facility": self.facility,
                    "created_by": self.user,
                    "status": ImmunizationStatus.completed.value,
                    "code": VACCINE,
                    "primary_source": True,
                    **kwargs,
                },
            )

    def update(self, immunization, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            for field, value in fields.items():
                setattr(immunization, field, value)
            immunization.save()

    def linked_care_contexts(self):
        return [call.kwargs["care_context"] for call in self.enqueue.call_args_list]

    def test_recording_a_dose_links_one_non_clinical_care_context(self):
        immunization = self.record()
        self.update(immunization, note="Mild fever after the dose")

        [care_context] = self.linked_care_contexts()
        self.assertEqual(
            care_context,
            {
                "reference": f"v2::immunization::{immunization.external_id}",
                "display": "Immunization Recorded on "
                f"{immunization.created_date:%Y-%m-%d %H:%M:%S}",
                "hi_type": HealthInformationType.IMMUNIZATION_RECORD,
            },
        )
        self.assertEqual(self.enqueue.call_args.kwargs["hf_id"], "TEST_HIP")

    def test_draft_is_linked_once_it_is_finalised(self):
        immunization = self.record(status=ImmunizationStatus.draft.value)
        self.assertEqual(self.linked_care_contexts(), [])

        self.update(immunization, status=ImmunizationStatus.completed.value)
        self.update(immunization, status=ImmunizationStatus.entered_in_error.value)

        self.assertEqual(len(self.linked_care_contexts()), 1)

    def test_only_shareable_doses_are_offered_for_linking(self):
        kept = self.record(status=ImmunizationStatus.not_done.value)
        retracted = self.record(status=ImmunizationStatus.entered_in_error.value)
        self.record(status=ImmunizationStatus.draft.value)

        discovered = [
            care_context["reference"]
            for care_context in generate_care_contexts_for_existing_data(self.patient)[
                "TEST_HIP"
            ]
            if care_context["hi_type"] == HealthInformationType.IMMUNIZATION_RECORD
        ]

        self.assertEqual(discovered, [f"v2::immunization::{kept.external_id}"])
        self.assertEqual(
            care_context_dict_from_reference_id(discovered[0])["reference"],
            discovered[0],
        )
        self.assertIsNone(
            care_context_dict_from_reference_id(
                f"v2::immunization::{retracted.external_id}"
            )
        )
