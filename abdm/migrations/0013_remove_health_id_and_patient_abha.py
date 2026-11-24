from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('abdm', '0012_migrate_abha_addresses_to_auths'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='consentartefact',
            name='patient_abha',
        ),
        migrations.RemoveField(
            model_name='consentrequest',
            name='patient_abha',
        ),
        migrations.RemoveField(
            model_name='abhanumber',
            name='health_id',
        ),
    ]
