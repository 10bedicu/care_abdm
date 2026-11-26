from datetime import timedelta
from unittest.mock import patch

from abdm.models import HealthFacility
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_bakery import baker

from care.utils.tests.base import CareAPITestBase

URL = "/api/abdm/health_facility/"


class HealthFacilityIdChangeTests(CareAPITestBase):
    def setUp(self):
        super().setUp()
        self.user = self.create_super_user()
        self.facility = self.create_facility(self.user)
        self.attach_role_facility_organization_user(
            self.create_facility_organization(self.facility),
            self.user,
            self.create_role_with_permissions([]),
        )
        self.client.force_authenticate(user=self.user)

        # registering with ABDM is a call to its facility registry
        register = patch(
            "abdm.api.viewsets.health_facility.register_health_facility_as_service.delay"
        )
        self.register = register.start()
        self.addCleanup(register.stop)

    def added_hours_ago(self, hours, hf_id="IN0001"):
        health_facility = baker.make(HealthFacility, facility=self.facility, hf_id=hf_id)
        HealthFacility.objects.filter(pk=health_facility.pk).update(
            created_date=timezone.now() - timedelta(hours=hours)
        )
        return health_facility

    def put(self, hf_id):
        return self.client.put(
            f"{URL}{self.facility.external_id}/",
            {"facility": str(self.facility.external_id), "hf_id": hf_id},
            format="json",
        )

    def test_hf_id_can_be_set_and_changed_within_two_days(self):
        created = self.client.post(
            URL,
            {"facility": str(self.facility.external_id), "hf_id": "IN0001"},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        HealthFacility.objects.update(created_date=timezone.now() - timedelta(hours=47))

        response = self.put("IN0002")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(HealthFacility.objects.get().hf_id, "IN0002")
        self.assertEqual(self.register.call_count, 2)

    def test_hf_id_cannot_be_changed_after_two_days(self):
        health_facility = self.added_hours_ago(49)

        response = self.put("IN0002")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("48 hours", response.data["detail"])
        health_facility.refresh_from_db()
        self.assertEqual(health_facility.hf_id, "IN0001")
        self.register.assert_not_called()

        # saving it as it is still works
        self.assertEqual(self.put("IN0001").status_code, 200)

        # whatever saves it, and with a form error in the admin
        health_facility.hf_id = "IN0003"
        with self.assertRaises(ValidationError):
            health_facility.save()
        with self.assertRaises(ValidationError) as refused:
            health_facility.full_clean()
        self.assertIn("hf_id", refused.exception.message_dict)

    def test_registration_does_not_take_another_id_after_two_days(self):
        self.added_hours_ago(49)

        with patch(
            "abdm.api.viewsets.health_facility.FacilityService.add_update_service"
        ) as add_update_service:
            add_update_service.return_value.status_code = 200
            add_update_service.return_value.json.return_value = [
                {"servicesLinked": {"id": "IN0009"}}
            ]
            response = self.client.post(
                f"{URL}{self.facility.external_id}/register_service/"
            )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("48 hours", response.data["detail"])
        health_facility = HealthFacility.objects.get()
        self.assertEqual(health_facility.hf_id, "IN0001")
        self.assertFalse(health_facility.registered)
