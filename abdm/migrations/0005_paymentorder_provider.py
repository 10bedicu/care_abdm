from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("abdm", "0004_alter_paymentorder_payment_link_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentorder",
            name="provider",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
    ]
