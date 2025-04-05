from celery import current_app
from celery.schedules import crontab

from abdm.tasks.retry_failed_care_contexts import retry_failed_care_contexts


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        crontab(hour="0", minute="0"),
        retry_failed_care_contexts.s(),
        name="retry_failed_care_contexts",
    )
