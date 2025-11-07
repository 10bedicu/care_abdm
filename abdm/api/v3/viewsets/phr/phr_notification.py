import datetime

from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from pydantic import UUID4
from rest_framework.decorators import action
from rest_framework.response import Response

from abdm.authentication import IsPhrAuthenticated, PhrCustomAuthentication
from abdm.models.phr_notification import PhrNotification
from care.emr.api.viewsets.base import EMRBaseViewSet, EMRListMixin, EMRRetrieveMixin
from care.emr.resources.base import EMRResource


class PhrNotificationFilter(filters.FilterSet):
    is_read = filters.BooleanFilter(field_name="is_read")
    category = filters.CharFilter(field_name="category")

    class Meta:
        model = PhrNotification
        fields = ["is_read", "category"]


class PhrNotificationListSpec(EMRResource):
    __model__ = PhrNotification
    id: UUID4 | None = None
    event_id: str | None = None
    category: str | None = None
    subscription_id: str | None = None
    published_at: datetime.datetime | None = None
    abha_address: str | None = None
    hip_id: str | None = None
    contexts: list = []
    title: str | None = None
    description: str | None = None
    is_read: bool = False
    created_date: datetime.datetime | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        mapping["id"] = obj.external_id


class PhrNotificationRetrieveSpec(PhrNotificationListSpec):
    raw_event_data: dict = {}
    updated_date: str | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj):
        super().perform_extra_serialization(mapping, obj)
        mapping["updated"] = (
            obj.modified_date.isoformat() if obj.modified_date else None
        )


@extend_schema(tags=["PHR Notifications"])
class PhrNotificationViewSet(EMRBaseViewSet, EMRListMixin, EMRRetrieveMixin):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]
    database_model = PhrNotification
    pydantic_retrieve_model = PhrNotificationRetrieveSpec
    pydantic_read_model = PhrNotificationListSpec
    filterset_class = PhrNotificationFilter
    filter_backends = [filters.DjangoFilterBackend]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(abha_address=self.request.user.abha_address)
            .order_by("-published_at", "-created_date")
        )

    @action(detail=True, methods=["post"], url_path="mark_read")
    def mark_as_read(self, request, external_id):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])

        return Response(PhrNotificationRetrieveSpec.serialize(notification).to_json())

    @action(detail=False, methods=["post"], url_path="mark_all_read")
    def mark_all_as_read(self, request):
        updated_count = PhrNotification.objects.filter(
            abha_address=request.user.abha_address, is_read=False
        ).update(is_read=True)

        return Response({"detail": f"Marked {updated_count} notifications as read"})

    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        count = PhrNotification.objects.filter(
            abha_address=request.user.abha_address, is_read=False
        ).count()

        return Response({"unread_count": count})
