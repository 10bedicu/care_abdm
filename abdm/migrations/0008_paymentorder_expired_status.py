from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('abdm', '0007_paymentorder_abha_address'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentorder',
            name='status',
            field=models.CharField(
                choices=[
                    ('OPEN_ORDER_SHARED', 'Open Order Shared'),
                    ('PAYMENT_INITIATED', 'Payment Initiated'),
                    ('SUCCESS', 'Success'),
                    ('CANCELED', 'Canceled'),
                    ('PENDING', 'Pending'),
                    ('FAIL', 'Fail'),
                    ('EXPIRED', 'Expired'),
                    ('REFUND_INITIATED', 'Refund Initiated'),
                    ('REFUND_SUCCESS', 'Refund Success'),
                ],
                default='OPEN_ORDER_SHARED',
                max_length=50,
            ),
        ),
    ]
