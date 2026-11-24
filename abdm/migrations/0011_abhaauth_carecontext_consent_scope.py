import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('abdm', '0010_paymentorder_status_index'),
        ('emr', '0040_alter_observation_interpretation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AbhaAuth',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_id', models.UUIDField(db_index=True, default=uuid.uuid4, unique=True)),
                ('created_date', models.DateTimeField(auto_now_add=True, db_index=True, null=True)),
                ('modified_date', models.DateTimeField(auto_now=True, db_index=True, null=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('abha_address', models.CharField(db_index=True, max_length=255)),
                ('method', models.CharField(choices=[('scan_and_share', 'Scan And Share'), ('staff_verification', 'Staff Verification'), ('abha_creation', 'Abha Creation'), ('legacy', 'Legacy')], max_length=30)),
                ('link_token', models.TextField(blank=True, null=True)),
                ('link_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('invalidated_at', models.DateTimeField(blank=True, null=True)),
                ('invalidation_reason', models.CharField(blank=True, default='', max_length=50)),
                ('abha_number', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='auths', to='abdm.abhanumber')),
                ('health_facility', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='abha_auths', to='abdm.healthfacility')),
                ('performed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CareContext',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_id', models.UUIDField(db_index=True, default=uuid.uuid4, unique=True)),
                ('created_date', models.DateTimeField(auto_now_add=True, db_index=True, null=True)),
                ('modified_date', models.DateTimeField(auto_now=True, db_index=True, null=True)),
                ('deleted', models.BooleanField(db_index=True, default=False)),
                ('abha_address', models.CharField(max_length=255)),
                ('reference', models.CharField(max_length=255)),
                ('hi_type', models.CharField(choices=[('Prescription', 'Prescription'), ('DiagnosticReport', 'Diagnostic Report'), ('OPConsultation', 'Op Consultation'), ('DischargeSummary', 'Discharge Summary'), ('ImmunizationRecord', 'Immunization Record'), ('HealthDocumentRecord', 'Record Artifact'), ('WellnessRecord', 'Wellness Record'), ('Invoice', 'Invoice')], max_length=50)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('requested', 'Requested'), ('linked', 'Linked'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('via', models.CharField(choices=[('hip_initiated', 'Hip Initiated'), ('user_initiated', 'User Initiated')], default='hip_initiated', max_length=20)),
                ('request_id', models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ('legacy', models.BooleanField(default=False)),
                ('abha_auth', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='care_contexts', to='abdm.abhaauth')),
                ('health_facility', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='abdm.healthfacility')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='emr.patient')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('health_facility', 'abha_address', 'reference'), name='abdm_carecontext_unique_link')],
            },
        ),
        migrations.AddField(
            model_name='consentrequest',
            name='abha_auth',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='consent_requests', to='abdm.abhaauth'),
        ),
        migrations.AddField(
            model_name='consentartefact',
            name='abha_address',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='consentartefact',
            name='health_facility',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='abdm.healthfacility'),
        ),
        migrations.AddField(
            model_name='consentartefact',
            name='patient',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='emr.patient'),
        ),
    ]
