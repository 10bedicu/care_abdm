from django.db import migrations

ABDM_FIRST_NAME = "ABDM"
ABDM_LAST_NAME = "User"


def set_abdm_user_name(apps, schema_editor):
    from abdm.settings import plugin_settings

    User = apps.get_model("users", "User")
    User.objects.filter(
        username=plugin_settings.ABDM_USERNAME,
        first_name="",
        last_name="",
    ).update(first_name=ABDM_FIRST_NAME, last_name=ABDM_LAST_NAME)


def unset_abdm_user_name(apps, schema_editor):
    from abdm.settings import plugin_settings

    User = apps.get_model("users", "User")
    User.objects.filter(
        username=plugin_settings.ABDM_USERNAME,
        first_name=ABDM_FIRST_NAME,
        last_name=ABDM_LAST_NAME,
    ).update(first_name="", last_name="")


class Migration(migrations.Migration):
    dependencies = [
        ("abdm", "0008_paymentorder_expired_status"),
        ("users", "0027_user_cached_role_orgs"),
    ]

    operations = [
        migrations.RunPython(set_abdm_user_name, reverse_code=unset_abdm_user_name),
    ]
