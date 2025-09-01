from logging import getLogger

from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from abdm.api.v3.serializers.phr.phr_subscription import (
    PhrSubscriptionApproveSerializer,
    PhrSubscriptionEditSerializer,
    PhrSubscriptionRequestDenySerializer,
    PhrSubscriptionStatusUpdateSerializer,
)
from abdm.authentication import (
    ABDMAuthentication,
    IsPhrAuthenticated,
    PhrCustomAuthentication,
)
from abdm.models.phr_notification import PhrNotification
from abdm.service.phr_helper import (
    get_phr_access_token,
    transform_phr_links_data,
    update_phr_metadata,
)
from abdm.service.v3.phr.phr_consent import PhrConsentService
from abdm.service.v3.phr.phr_gateway import PhrGatewayService
from abdm.service.v3.phr.phr_subscription import PhrSubscriptionService

logger = getLogger(__name__)


@extend_schema(tags=["PHR Subscription"])
class PhrSubscriptionViewSet(GenericViewSet):
    permission_classes = [IsPhrAuthenticated]
    authentication_classes = [PhrCustomAuthentication]

    REQUIRED_REQUEST_FIELDS = ["purpose", "period", "categories", "hiu"]
    VALID_STATUSES = ["ALL", "REQUESTED", "EXPIRED", "REVOKED", "GRANTED", "DENIED"]

    serializer_action_classes = {
        "phr_subscription__request__approve": PhrSubscriptionApproveSerializer,
        "phr_subscription__request__deny": PhrSubscriptionRequestDenySerializer,
        "phr_subscription__status__update": PhrSubscriptionStatusUpdateSerializer,
        "phr_subscription__edit": PhrSubscriptionEditSerializer,
    }

    @property
    def x_token(self):
        return get_phr_access_token(self.request.user.abha_address)

    def get_serializer_class(self):
        if self.action in self.serializer_action_classes:
            return self.serializer_action_classes[self.action]
        return super().get_serializer_class()

    def validate_request(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _get_query_params(self, request):
        status_param = request.query_params.get("status", "ALL")

        if status_param not in self.VALID_STATUSES:
            return None, None, None

        try:
            limit = max(int(request.query_params.get("limit", -1)), -1)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (ValueError, TypeError):
            return None, None, None

        return status_param, limit, offset

    def _is_valid_item(self, item):
        return all(item.get(field) for field in self.REQUIRED_REQUEST_FIELDS)

    def _build_links_response(self, status_value, hips_data=None):
        response = {
            "links": [],
            "availableLinks": [],
        }

        if status_value in ["GRANTED", "REQUESTED"]:
            links = PhrGatewayService.phr__gateway__patient__links(
                {"x_token": self.x_token}
            )
            response["availableLinks"] = transform_phr_links_data(
                links, include_care_contexts=False
            )

        if hips_data:
            response["links"] = [
                {"hip": hip} for hip in hips_data if hip and hip.get("id")
            ]
        else:
            response["links"] = response["availableLinks"]

        return response

    def _transform_subscription_request(self, subscription_request_data):
        details = subscription_request_data.get("details", {})

        return {
            "subscriptionId": subscription_request_data.get("subscriptionId"),
            "requestId": subscription_request_data.get("requestId"),
            "createdAt": subscription_request_data.get("dateCreated"),
            "lastUpdated": subscription_request_data.get("dateModified"),
            "purpose": details.get("purpose"),
            "patient": details.get("patient"),
            "hiu": details.get("hiu"),
            "hips": details.get("hips", []),
            "categories": details.get("categories", []),
            "period": details.get("period"),
            "status": subscription_request_data.get("status"),
            "requesterType": subscription_request_data.get("requesterType"),
        }

    @action(detail=False, methods=["get"], url_path="requests")
    def phr_subscription__requests(self, request):
        status_param, limit, offset = self._get_query_params(request)

        if not status_param:
            return Response(
                {
                    "results": [],
                    "hasMore": False,
                },
                status=status.HTTP_200_OK,
            )

        subscription_requests = PhrSubscriptionService.phr__subscription__requests(
            {
                "x_token": self.x_token,
                "status": status_param,
                "limit": limit,
                "offset": offset,
            }
        )

        filtered_requests = [
            req
            for req in subscription_requests.get("requests", [])
            if self._is_valid_item(req)
        ]

        return Response(
            {
                "results": filtered_requests,
                "hasMore": len(filtered_requests) == limit,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="request/(?P<request_id>[^/.]+)")
    def phr_subscription__request(self, request, request_id):
        subscription_request = PhrSubscriptionService.phr__subscription__request(
            {"x_token": self.x_token, "request_id": request_id}
        )

        transformed_request = self._transform_subscription_request(subscription_request)

        hips_data = transformed_request.get("hips") or None
        links = self._build_links_response(
            subscription_request.get("status"), hips_data
        )

        return Response(
            {
                "request": transformed_request,
                **links,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False, methods=["get"], url_path="artefact/(?P<subscription_id>[^/.]+)"
    )
    def phr_subscription__artefact(self, request, subscription_id):
        subscription_artefact = PhrSubscriptionService.phr__subscription__artefact(
            {"x_token": self.x_token, "subscription_id": subscription_id}
        )

        hips_data = [
            hip
            for source in subscription_artefact.get("includedSources", [])
            if (hip := source.get("hip")) and hip.get("id")
        ]

        links = self._build_links_response(
            subscription_artefact.get("status"), hips_data or None
        )

        return Response(
            {
                "artefact": subscription_artefact,
                **links,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="request/(?P<request_id>[^/.]+)/approve",
    )
    def phr_subscription__request__approve(self, request, request_id):
        validated_data = self.validate_request(request)

        result = PhrSubscriptionService.phr__subscription__request__approve(
            {
                "x_token": self.x_token,
                "request_id": request_id,
                "subscription": validated_data,
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)

    @action(
        detail=False, methods=["post"], url_path="request/(?P<request_id>[^/.]+)/deny"
    )
    def phr_subscription__request__deny(self, request, request_id):
        validated_data = self.validate_request(request)

        result = PhrSubscriptionService.phr__subscription__request__deny(
            {
                "x_token": self.x_token,
                "request_id": request_id,
                "reason": validated_data.get("reason"),
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="(?P<subscription_id>[^/.]+)/update_status",
    )
    def phr_subscription__status__update(self, request, subscription_id):
        validated_data = self.validate_request(request)

        result = PhrSubscriptionService.phr__subscription__status__update(
            {
                "x_token": self.x_token,
                "subscription_id": subscription_id,
                "enable": validated_data.get("enable"),
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["put"], url_path="(?P<subscription_id>[^/.]+)/edit")
    def phr_subscription__edit(self, request, subscription_id):
        validated_data = self.validate_request(request)

        result = PhrSubscriptionService.phr__subscription__edit(
            {
                "x_token": self.x_token,
                "subscription_id": subscription_id,
                "hiu_id": validated_data.get("hiuId"),
                "subscription": validated_data.get("subscription"),
            }
        )

        return Response({"detail": result.get("message")}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="patient_lockers")
    def phr_subscription__lockers(self, request):
        result = PhrSubscriptionService.phr__subscription__lockers(
            {"x_token": self.x_token}
        )

        return Response(result, status=status.HTTP_200_OK)

    @action(
        detail=False, methods=["get"], url_path="patient_locker/(?P<locker_id>[^/.]+)"
    )
    def phr_subscription__locker(self, request, locker_id):
        result = PhrSubscriptionService.phr__subscription__locker(
            {"x_token": self.x_token, "locker_id": locker_id}
        )

        return Response(result, status=status.HTTP_200_OK)


@extend_schema(tags=["PHR Subscription Callbacks"])
class PhrSubscriptionCallbackViewSet(GenericViewSet):
    permission_classes = (IsAuthenticated,)
    authentication_classes = [ABDMAuthentication]

    @action(
        detail=False,
        methods=["post"],
        url_path="hiu/hiecm/subscription-requests/on-init",
    )
    def phr_subscription__request__on__init(self, request):
        data = request.data

        logger.info(f"SUBSCRIPTION REQUEST ON INIT: {data}")

        if data.get("error"):
            return Response(data.get("error"), status=status.HTTP_400_BAD_REQUEST)

        if not data.get("subscriptionRequest"):
            return Response(
                "Missing subscriptionRequest", status=status.HTTP_400_BAD_REQUEST
            )

        return Response(status=status.HTTP_200_OK)

    @action(
        detail=False, methods=["post"], url_path="hiu/subscription-requests/hiu/notify"
    )
    def phr_subscription__request__on__notify(self, request):
        logger.info(f"SUBSCRIPTION  NOTIFY: {request.data}")
        notification_data = request.data.get("notification")

        if not notification_data:
            logger.warning(f"Missing notification data: {request.data}")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        subscription_status = notification_data.get("status")

        if subscription_status == "GRANTED":
            subscription_data = notification_data.get("subscription")
            if not subscription_data:
                logger.warning(
                    f"Missing subscription data in notification: {notification_data}"
                )
                return Response(status=status.HTTP_400_BAD_REQUEST)

            patient_id = subscription_data.get("patient", {}).get("id")
            update_phr_metadata(
                patient_id,
                {
                    "subscription_id": subscription_data.get("id"),
                },
            )

        PhrSubscriptionService.phr__subscription__request__on__notify(
            {
                "subscription_request_id": notification_data.get(
                    "subscriptionRequestId"
                ),
                "request_id": request.headers.get("REQUEST-ID"),
            }
        )

        return Response(status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="hiu/subscription/notify")
    def phr_subscription__notify(self, request):
        logger.info(f"SUBSCRIPTION NOTIFICATION NOTIFY: {request.data}")
        event = request.data.get("event")

        if not event:
            logger.warning(f"Missing event data: {request.data}")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event_id = event.get("id")
        category = event.get("category")

        if category == "LINK":
            content = event.get("content", {})
            hip = PhrGatewayService.phr__gateway__provider(
                {"id": content.get("hip", {}).get("id")}
            ).get("identifier", {})
            PhrNotification.objects.create(
                event_id=event_id,
                category=category,
                subscription_id=event.get("subscriptionId"),
                published_at=parse_datetime(event.get("published")),
                abha_address=content.get("patient", {}).get("id"),
                hip_id=hip.get("id"),
                contexts=content.get("contexts", []),
                title="New Record Linked",
                description=f"A new record is linked to your abha address from {hip.get('name')}",
                raw_event_data=event,
            )

            PhrConsentService.phr_consent_request_init(
                {
                    "patient_id": content.get("patient", {}).get("id"),
                }
            )

        elif category == "DATA":
            logger.info(f"DATA category event received: {event_id}")
            # TODO: Find out the payload structure and store in DB

        PhrSubscriptionService.phr__subscription__on__notify(
            {
                "event_id": event_id,
                "request_id": request.headers.get("REQUEST-ID"),
            }
        )

        return Response(status=status.HTTP_200_OK)
