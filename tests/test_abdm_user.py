import importlib

from abdm.authentication import ABDMAuthentication
from abdm.settings import plugin_settings as settings
from abdm.utils import user as abdm_user
from django.test import TestCase

from care.users.models import User


class _Apps:
    def get_model(self, app_label, model_name):
        return User


class AbdmUserNameTests(TestCase):
    def setUp(self):
        abdm_user.ABDM_USER = None
        User.objects.filter(username=settings.ABDM_USERNAME).delete()

    def tearDown(self):
        abdm_user.ABDM_USER = None

    def test_new_user_is_created_with_a_display_name(self):
        user = abdm_user.get_or_create_abdm_user()

        self.assertEqual(user.first_name, "ABDM")
        self.assertEqual(user.last_name, "User")
        self.assertEqual(user.full_name, "ABDM User")

    def test_existing_name_is_left_alone(self):
        existing = User.objects.create(
            username=settings.ABDM_USERNAME,
            first_name="Custom",
            last_name="Name",
            email="abdm@ohc.network",
            phone_number="917777777777",
        )

        user = abdm_user.get_or_create_abdm_user()

        self.assertEqual(user.id, existing.id)
        self.assertEqual(user.first_name, "Custom")
        self.assertEqual(user.last_name, "Name")

    def test_authentication_uses_the_same_user(self):
        created = abdm_user.get_or_create_abdm_user()
        abdm_user.ABDM_USER = None

        user = ABDMAuthentication().get_user({"sub": "abdm"})

        self.assertEqual(user.id, created.id)
        self.assertEqual(user.full_name, "ABDM User")

    def test_migration_fills_a_blank_name_and_keeps_a_set_one(self):
        blank = User.objects.create(
            username=settings.ABDM_USERNAME,
            first_name="",
            last_name="",
            email="abdm@ohc.network",
            phone_number="917777777777",
        )
        named = User.objects.create(
            username="someone_else",
            first_name="Ada",
            last_name="Lovelace",
            email="ada@ohc.network",
            phone_number="918888888888",
        )

        migration = importlib.import_module("abdm.migrations.0009_set_abdm_user_name")
        migration.set_abdm_user_name(_Apps(), None)
        blank.refresh_from_db()
        named.refresh_from_db()

        self.assertEqual(blank.first_name, "ABDM")
        self.assertEqual(blank.last_name, "User")
        self.assertEqual(named.first_name, "Ada")

        migration.unset_abdm_user_name(_Apps(), None)
        blank.refresh_from_db()

        self.assertEqual(blank.first_name, "")
        self.assertEqual(blank.last_name, "")
