from django.conf import settings
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    AbhaNumber,
    ConsentArtefact,
    ConsentRequest,
    Transaction,
    HealthFacility,
)
from abdm.models.user_profile import UserProfile
from abdm.models.failed_hpr_registration import FailedHPRRegistration
from abdm.signals.hpr_registration import register_user_with_hpr


@admin.register(AbhaNumber)
class AbhaNumberAdmin(admin.ModelAdmin):
    list_display = (
        "abha_number",
        "health_id",
        "patient",
        "name",
        "mobile",
    )
    search_fields = ("abha_number", "health_id", "name", "mobile")

    @admin.action(description="Delete selected ABHA number and consent records")
    def delete_abdm_records(self, request, queryset):
        ConsentArtefact.objects.filter(patient_abha__in=queryset).delete()
        ConsentRequest.objects.filter(patient_abha__in=queryset).delete()
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


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'hip_id', 'hiu_id', 'is_hip_admin', 'is_hiu_admin')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'hip_id', 'hiu_id')
    list_filter = ('is_hip_admin', 'is_hiu_admin')
    raw_id_fields = ('user',)
    fieldsets = (
        (None, {
            'fields': ('user',)
        }),
        (_('ABDM IDs'), {
            'fields': ('hip_id', 'hiu_id')
        }),
        (_('Roles'), {
            'fields': ('is_hip_admin', 'is_hiu_admin')
        }),
        (_('Additional Information'), {
            'fields': ('metadata',)
        }),
    )


@admin.register(FailedHPRRegistration)
class FailedHPRRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'retry_count', 'last_retry', 'updated_at', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'error_message')
    list_filter = ('retry_count', 'last_retry', 'created_at')
    readonly_fields = ('retry_count', 'last_retry', 'created_at', 'updated_at')
    raw_id_fields = ('user',)
    actions = ['retry_registration']
    
    fieldsets = (
        (None, {
            'fields': ('user',)
        }),
        (_('Error Details'), {
            'fields': ('error_message',)
        }),
        (_('Retry Information'), {
            'fields': ('retry_count', 'last_retry')
        }),
        (_('Timestamps'), {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def retry_registration(self, request, queryset):
        """
        Admin action to manually retry the failed registrations
        """
        count = 0
        for registration in queryset:
            register_user_with_hpr.delay(registration.user.id)
            count += 1
        
        self.message_user(
            request, 
            _('Initiated retry for %(count)d failed registrations.') % {'count': count}
        )
    
    retry_registration.short_description = _("Retry selected registrations")


admin.site.register(ConsentArtefact)
admin.site.register(ConsentRequest)
admin.site.register(Transaction)
admin.site.register(HealthFacility)
