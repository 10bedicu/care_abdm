from django.urls import path
from rest_framework.routers import SimpleRouter

from abdm.api.v3.viewsets.health_id import HealthIdViewSet
from abdm.api.v3.viewsets.hip import HIPCallbackViewSet, HIPViewSet
from abdm.api.v3.viewsets.hiu import HIUCallbackViewSet, HIUViewSet
from abdm.api.viewsets.abha_number import AbhaNumberViewSet
from abdm.api.viewsets.consent import ConsentViewSet
from abdm.api.viewsets.health_facility import HealthFacilityViewSet
from abdm.api.viewsets.health_information import HealthInformationViewSet
from abdm.api.viewsets.healthcare_provider import HealthcareProviderRegistryViewSet


class OptionalSlashRouter(SimpleRouter):
    def __init__(self):
        super().__init__()
        self.trailing_slash = "/?"


router = OptionalSlashRouter()

## Model Routes
router.register("consent", ConsentViewSet, basename="abdm__consent")
router.register(
    "health_information",
    HealthInformationViewSet,
    basename="abdm__health_information",
)
router.register("abha_number", AbhaNumberViewSet, basename="abdm__abha_number")
router.register(
    "health_facility", HealthFacilityViewSet, basename="abdm__health_facility"
)
router.register(
    "healthcare_provider", HealthcareProviderRegistryViewSet, basename="abdm__healthcare_provider"
)

## ABDM Proxy Routes
router.register("v3/health_id", HealthIdViewSet, basename="abdm__v3__health_id")
router.register("v3/hip", HIPViewSet, basename="abdm__v3__hip")
router.register("v3/hiu", HIUViewSet, basename="abdm__v3__hiu")


## Callback Routes
router.register("api/v3", HIPCallbackViewSet, basename="abdm__v3__hip__callback")
router.register("api/v3", HIUCallbackViewSet, basename="abdm__v3__hiu__callback")

urlpatterns = router.urls
