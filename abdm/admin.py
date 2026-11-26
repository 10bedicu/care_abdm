from django.conf import settings
from django.contrib import admin
from django.db.models import Q

from .models import (
    AbhaAuth,
    AbhaNumber,
    CareContext,
    ConsentArtefact,
    ConsentRequest,
    Transaction,
    HealthFacility,
)


@admin.register(AbhaNumber)
class AbhaNumberAdmin(admin.ModelAdmin):
    list_display = (
        "abha_number",
        "patient",
        "name",
        "mobile",
    )
    search_fields = ("abha_number", "auths__abha_address", "name", "mobile")

    @admin.action(description="Delete selected ABHA number and consent records")
    def delete_abdm_records(self, request, queryset):
        ConsentArtefact.objects.filter(
            Q(consent_request__abha_auth__abha_number__in=queryset)
            | Q(patient__abha_number__in=queryset)
        ).delete()
        ConsentRequest.objects.filter(abha_auth__abha_number__in=queryset).delete()
        CareContext.objects.filter(abha_auth__abha_number__in=queryset).delete()
        AbhaAuth.objects.filter(abha_number__in=queryset).delete()
        queryset.delete()
        self.message_user(
            request, "Selected ABHA number and consent records have been deleted"
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not settings.IS_PRODUCTION:
            # delete_abdm_records should only be available in non-production environments
            actions["delete_abdm_records"] = self.get_action("delete_abdm_records")
        return actions


@admin.register(AbhaAuth)
class AbhaAuthAdmin(admin.ModelAdmin):
    list_display = (
        "abha_number",
        "health_facility",
        "abha_address",
        "method",
        "created_date",
        "invalidated_at",
    )
    search_fields = ("abha_address", "abha_number__abha_number")
    list_filter = ("method",)

    # an auth records what was presented; it is not edited afterwards
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CareContext)
class CareContextAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "health_facility",
        "abha_address",
        "status",
        "via",
        "legacy",
    )
    search_fields = ("reference", "abha_address", "request_id")
    list_filter = ("status", "via", "legacy")
    raw_id_fields = ("patient", "abha_auth")


admin.site.register(ConsentArtefact)
admin.site.register(ConsentRequest)
admin.site.register(Transaction)
admin.site.register(HealthFacility)
