import random

from celery import current_app
from celery.schedules import crontab

from abdm.settings import plugin_settings as settings
from abdm.tasks.link_care_context import link_care_context
from abdm.tasks.patient_share import patient_share_on_share
from abdm.tasks.process_inbound_callback import (
    LOW_PRIORITY,
    process_inbound_callback,
)
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
        interval = settings.ABDM_SCAN_AND_PAY_POLLING_INTERVAL
        # low priority keeps it behind interactive callbacks on a busy worker, and
        # expiring stale runs stops a backlog of polls executing back-to-back
        sender.add_periodic_task(
            interval,
            reconcile_pending_payment_orders.s().set(
                priority=LOW_PRIORITY, expires=interval
            ),
            name="reconcile_pending_payment_orders",
        )
