from django.db import models
from jsonschema import validate

from abdm.models.json_schema.transaction import (
    CREATE_ABHA_ADDRESS,
    CREATE_OR_LINK_ABHA_NUMBER,
    EXCHANGE_DATA,
    LINK_CARE_CONTEXT,
    SCAN_AND_SHARE,
)
from care.users.models import User
from care.utils.models.base import BaseModel


class TransactionType(models.IntegerChoices):
    CREATE_OR_LINK_ABHA_NUMBER = 1
    CREATE_ABHA_ADDRESS = 2
    SCAN_AND_SHARE = 3
    LINK_CARE_CONTEXT = 4
    EXCHANGE_DATA = 5
    ACCESS_DATA = 6  # tracks internal data access within care


class Transaction(BaseModel):
    reference_id = models.CharField(
        max_length=100, null=False, blank=False
    )  # unique id to reference the transaction (request_id / transaction_id from ABDM)

    type = models.SmallIntegerField(
        choices=TransactionType.choices, null=False, blank=False
    )
    meta_data = models.JSONField(null=True, blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def _validate_meta_data(self):
        if self.type == TransactionType.CREATE_OR_LINK_ABHA_NUMBER:
            validate(instance=self.meta_data, schema=CREATE_OR_LINK_ABHA_NUMBER)
        elif self.type == TransactionType.CREATE_ABHA_ADDRESS:
            validate(instance=self.meta_data, schema=CREATE_ABHA_ADDRESS)
        elif self.type == TransactionType.SCAN_AND_SHARE:
            validate(instance=self.meta_data, schema=SCAN_AND_SHARE)
        elif self.type == TransactionType.LINK_CARE_CONTEXT:
            validate(instance=self.meta_data, schema=LINK_CARE_CONTEXT)
        elif self.type == TransactionType.EXCHANGE_DATA:
            validate(instance=self.meta_data, schema=EXCHANGE_DATA)

    def save(self, *args, **kwargs):
        self._validate_meta_data()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Transaction: {TransactionType(self.type).label} - {self.reference_id}"
