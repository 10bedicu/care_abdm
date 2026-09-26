import random

from celery import current_app
from celery.schedules import crontab

from abdm.settings import plugin_settings as settings
from abdm.tasks.link_care_context import link_care_context
from abdm.tasks.patient_share import patient_share_on_share
from abdm.tasks.process_inbound_callback import process_inbound_callback
from abdm.tasks.retry_failed_care_contexts import retry_failed_care_contexts
from abdm.tasks.scan_pay import reconcile_pending_payment_orders


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        crontab(hour="2", minute="22"),
        retry_failed_care_contexts.s(),
        name="retry_failed_care_contexts",
    )

    if settings.ABDM_SCAN_AND_PAY_POLLING_ENABLED:
        sender.add_periodic_task(
            settings.ABDM_SCAN_AND_PAY_POLLING_INTERVAL,
            reconcile_pending_payment_orders.s(),
            name="reconcile_pending_payment_orders",
        )
