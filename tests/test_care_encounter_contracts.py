from datetime import datetime

from abdm.extensions import EXTENSION_NAME
from django.urls import reverse

from care.emr.extensions.base import ExtensionOwners
from care.emr.models.encounter import Encounter
from care.emr.registries.extensions.registry import ExtensionRegistry
from care.emr.resources.encounter.constants import (
    ClassChoices,
    EncounterPriorityChoices,
    StatusChoices,
)
from care.utils.tests.base import CareAPITestBase

OPTED_OUT = {"link_records": False}


class CareEncounterExtensionContractTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.organization = self.create_facility_organization(self.facility)
        self.patient = self.create_patient()
        self.client.force_authenticate(user=self.user)

    def create(self, **fields):
        # CARE releases its encounter creation lock on commit
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("encounter-list"),
                {
                    "patient": str(self.patient.external_id),
                    "facility": str(self.facility.external_id),
                    "status": StatusChoices.in_progress.value,
                    "encounter_class": ClassChoices.amb.value,
                    "priority": EncounterPriorityChoices.elective.value,
                    "organizations": [str(self.organization.external_id)],
                    **fields,
                },
                format="json",
            )

    def created(self, **fields):
        response = self.create(**fields)
        self.assertEqual(response.status_code, 200, response.data)
        return Encounter.objects.get(external_id=response.data["id"])

    def update(self, encounter, **fields):
        response = self.client.put(
            reverse("encounter-detail", kwargs={"external_id": encounter.external_id}),
            {
                "status": StatusChoices.in_progress.value,
                "priority": EncounterPriorityChoices.ASAP.value,
                **fields,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        encounter.refresh_from_db()
        return response

    def test_abdm_extension_is_registered_for_encounters(self):
        extension = ExtensionRegistry.get_extension_obj("encounter", EXTENSION_NAME)

        self.assertEqual(extension.extension_owner, ExtensionOwners.plug)
        self.assertEqual(
            extension.get_write_schema()["properties"]["link_records"]["type"],
            "boolean",
        )

    def test_opt_out_is_stamped_as_the_encounter_is_created(self):
        response = self.create(
            extensions={EXTENSION_NAME: {**OPTED_OUT, "recorded_by": "someone else"}}
        )

        self.assertEqual(response.status_code, 200, response.data)
        encounter = Encounter.objects.get(external_id=response.data["id"])
        stored = encounter.extensions[EXTENSION_NAME]
        self.assertEqual(
            stored,
            {
                "link_records": False,
                "recorded_by": str(self.user.external_id),
                "recorded_at": stored["recorded_at"],
            },
        )
        self.assertIsNotNone(datetime.fromisoformat(stored["recorded_at"]).tzinfo)
        self.assertEqual(response.data["extensions"][EXTENSION_NAME], stored)

        # only an opt-out is stamped
        linked = self.created(extensions={EXTENSION_NAME: {"link_records": True}})
        self.assertEqual(linked.extensions[EXTENSION_NAME], {"link_records": True})

    def test_core_update_without_extensions_keeps_the_abdm_key(self):
        encounter = self.created(extensions={EXTENSION_NAME: OPTED_OUT})
        stored = encounter.extensions[EXTENSION_NAME]

        response = self.update(encounter, status=StatusChoices.completed.value)

        self.assertEqual(encounter.status, StatusChoices.completed.value)
        self.assertEqual(encounter.extensions[EXTENSION_NAME], stored)
        self.assertEqual(response.data["extensions"][EXTENSION_NAME], stored)

    def test_opt_out_cannot_be_changed_after_the_encounter_is_created(self):
        opted_out = self.created(extensions={EXTENSION_NAME: OPTED_OUT})
        stored = opted_out.extensions[EXTENSION_NAME]

        for sent in (
            {EXTENSION_NAME: {"link_records": True}},
            # what a client reads, sent back, is not refused
            {EXTENSION_NAME: {**stored, "link_records": True}},
            # CARE replaces the whole of extensions with what was sent
            {"core": {}},
        ):
            with self.subTest(sent=sent):
                self.update(opted_out, extensions=sent)
                self.assertEqual(opted_out.extensions[EXTENSION_NAME], stored)

        linked = self.created()
        self.update(linked, extensions={EXTENSION_NAME: OPTED_OUT})
        self.assertNotIn(EXTENSION_NAME, linked.extensions)

    def test_abdm_extension_of_another_shape_is_refused(self):
        for value in (
            {"link_records": "no"},
            {"link_records": 0},
            {"link_records": False, "reason": "asked"},
            [False],
        ):
            with self.subTest(value=value):
                response = self.create(extensions={EXTENSION_NAME: value})
                self.assertEqual(response.status_code, 400, response.data)
