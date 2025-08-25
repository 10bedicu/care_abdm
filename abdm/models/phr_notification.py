from django.db import models

from care.utils.models.base import BaseModel


class PhrNotification(BaseModel):
    event_id = models.TextField(null=True, blank=True, unique=True)
    category = models.TextField(null=True, blank=True)
    subscription_id = models.TextField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    abha_address = models.TextField(null=True, blank=True)
    hip_id = models.TextField(null=True, blank=True)
    contexts = models.JSONField(default=list, blank=True)

    title = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)

    is_read = models.BooleanField(default=False)
    raw_event_data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.pk} {self.event_id} - {self.category}"
