# Generated manually

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("abdm", "0010_healthfacility_registered"),
    ]

    operations = [
        migrations.CreateModel(
            name="HealthcareProviderRegistry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(db_index=True, default=uuid.uuid4, unique=True),
                ),
                (
                    "created_date",
                    models.DateTimeField(auto_now_add=True, db_index=True, null=True),
                ),
                (
                    "modified_date",
                    models.DateTimeField(auto_now=True, db_index=True, null=True),
                ),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("hpr_id", models.CharField(blank=True, max_length=50, null=True, unique=True)),
                ("registered", models.BooleanField(default=False)),
                ("registration_error", models.TextField(blank=True, null=True)),
                ("name", models.CharField(blank=True, max_length=100, null=True)),
                ("mobile", models.CharField(blank=True, max_length=20, null=True)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("is_verified", models.BooleanField(default=False)),
                ("last_updated", models.DateTimeField(auto_now=True)),
                ("verification_time", models.DateTimeField(blank=True, null=True)),
                (
                    "verification_method",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("API", "API Verification"),
                            ("MANUAL", "Manual Verification"),
                            ("AUTO", "Automatic Registration"),
                        ],
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="healthcare_provider",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Healthcare Provider Registry",
                "verbose_name_plural": "Healthcare Provider Registries",
            },
        ),
    ] 