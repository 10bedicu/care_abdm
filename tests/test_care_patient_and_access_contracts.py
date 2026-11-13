from unittest.mock import patch

from abdm.models import AbhaNumber
from abdm.models.base import HealthInformationType
from abdm.models.consent import ConsentRequest
from abdm.service.helper import uuid
from abdm.service.v3.callback_handlers.hip import handle_patient_share
from abdm.utils import token as token_utils
from abdm.utils import user as abdm_user
from django.core.exceptions import ValidationError

from care.emr.models.patient import Patient, PatientIdentifier, PatientIdentifierConfig
from care.emr.models.scheduling.schedule import SchedulableResource
from care.emr.models.scheduling.token import Token, TokenCategory, TokenQueue
from care.emr.resources.patient_identifier.spec import PatientIdentifierStatus
from care.security.permissions.account import AccountPermissions
from care.utils.lock import ObjectLocked
from tests.test_scan_pay_orders import ORDERS_URL, ScanPayOrderTestBase


class CarePatientAndAccessContractTests(ScanPayOrderTestBase):
    def user_with(self, *permissions):
        user = self.create_user()
        role = self.create_role_with_permissions(list(permissions))
        organization = self.create_facility_organization(self.facility)
        self.attach_role_facility_organization_user(organization, user, role)
        return user

    def setUp(self):
        super().setUp()
        abdm_user.ABDM_USER = None
        token_utils.SCAN_AND_SHARE_TOKEN_CATEGORY.clear()

    def share_payload(self, **patient):
        details = {
            "abhaAddress": "newpatient@sbx",
            "abhaNumber": "91987654321098",
            "name": "New Patient",
            "gender": "M",
            "yearOfBirth": 1990,
            "monthOfBirth": 1,
            "dayOfBirth": 15,
            "phoneNumber": "9999999998",
            "address": {
                "line": "1 Road",
                "district": "Ernakulam",
                "state": "Kerala",
                "pinCode": "682001",
            },
        }
        details.update(patient)
        return {
            "metaData": {"hipId": self.health_facility.hf_id, "context": "counter"},
            "profile": {"patient": details},
        }

    @patch("abdm.service.v3.callback_handlers.hip.GatewayService.patient_share__on_share")
    def test_patient_share_creates_a_patient_and_its_default_identifier(self, _gateway):
        PatientIdentifierConfig.objects.create(
            status=PatientIdentifierStatus.active.value,
            facility=None,
            created_by=self.user,
            config={
                "system": "https://care.example/mrn",
                "display": "MRN",
                "default_value": "f'P{patient_count}'",
            },
        )

        with self.captureOnCommitCallbacks(execute=True):
            handle_patient_share(self.share_payload(), {"REQUEST-ID": uuid()})

        abha = AbhaNumber.objects.get(health_id="newpatient@sbx")
        self.assertIsNotNone(abha.patient_id)
        patient = Patient.objects.get(id=abha.patient_id)
        self.assertEqual(patient.name, "New Patient")
        self.assertTrue(
            PatientIdentifier.objects.filter(patient=patient, value="P0").exists()
        )

    def test_scan_and_share_token_is_created_once_and_can_be_found(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = token_utils.get_or_create_scan_and_share_token(
                self.patient, self.facility
            )
            second = token_utils.get_or_create_scan_and_share_token(
                self.patient, self.facility
            )

        self.assertEqual(first.id, second.id)
        self.assertTrue(
            SchedulableResource.objects.filter(facility=self.facility).exists()
        )
        self.assertTrue(TokenQueue.objects.filter(facility=self.facility).exists())
        self.assertTrue(TokenCategory.objects.filter(facility=self.facility).exists())
        self.assertEqual(Token.objects.filter(patient=self.patient).count(), 1)
        found = token_utils.get_scan_and_share_token_by_token_number(
            first.number, self.facility
        )
        self.assertEqual(found.id, first.id)

    def test_order_list_follows_care_authorization_and_lookup(self):
        order, _invoice, _item = self.make_order()
        allowed = self.user_with(AccountPermissions.can_read_account.name)
        self.client.force_authenticate(user=allowed)

        listed = self.client.get(ORDERS_URL, {"facility": self.facility.external_id})

        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["results"][0]["id"], str(order.external_id))

        self.client.force_authenticate(user=self.create_user())
        denied = self.client.get(ORDERS_URL, {"facility": self.facility.external_id})
        self.assertEqual(denied.status_code, 403)

        missing = self.client.get(ORDERS_URL, {"facility": uuid()})
        self.assertEqual(missing.status_code, 404)

    def test_locked_order_status_check_stays_a_client_error(self):
        order, _invoice, _item = self.make_order()

        def raise_locked(_order):
            raise ObjectLocked()

        self.reconcile = raise_locked
        response = self.post(order, "check-status/")

        self.assertEqual(response.status_code, 423)

    def test_consent_care_contexts_use_the_json_schema_validator(self):
        organization = self.create_facility_organization(self.facility)
        encounter = self.create_encounter(
            self.patient, self.facility, organization
        )
        shared = {
            "patient_abha": self.abha_number,
            "encounter": encounter,
            "hi_types": [HealthInformationType.OP_CONSULTATION.value],
        }
        valid = ConsentRequest(
            care_contexts=[
                {
                    "patientReference": "patient-1",
                    "careContextReference": "visit-1",
                }
            ],
            **shared,
        )
        valid.full_clean()
        valid.save()
        self.assertEqual(
            ConsentRequest.objects.get(pk=valid.pk).care_contexts[0][
                "patientReference"
            ],
            "patient-1",
        )

        invalid = ConsentRequest(
            care_contexts={"patientReference": "patient-1"},
            **shared,
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()
