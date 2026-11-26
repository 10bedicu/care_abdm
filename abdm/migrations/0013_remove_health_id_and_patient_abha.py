from django.db import migrations, models


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
        migrations.AlterField(
            model_name='abhanumber',
            name='health_id',
            field=models.TextField(blank=True, editable=False, null=True),
        ),
        migrations.RenameField(
            model_name='abhanumber',
            old_name='health_id',
            new_name='abha_address_legacy',
        ),
    ]
