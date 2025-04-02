from django.conf import settings
from django.contrib import admin
from django.utils import timezone

from .models import (
    AbhaNumber,
    ConsentArtefact,
    ConsentRequest,
    Transaction,
    HealthFacility,
    HealthcareProviderRegistry,
)


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


@admin.register(HealthcareProviderRegistry)
class HealthcareProviderRegistryAdmin(admin.ModelAdmin):
    list_display = ('user', 'hpr_id', 'registered', 'is_verified', 'status_display', 'verification_method')
    list_filter = ('registered', 'is_verified', 'verification_method')
    search_fields = ('user__username', 'user__email', 'hpr_id', 'name', 'mobile', 'email')
    readonly_fields = ('last_updated', 'verification_time', 'status_display')
    fieldsets = (
        (None, {
            'fields': ('user', 'hpr_id', 'registered', 'is_verified', 'status_display')
        }),
        ('Personal Information', {
            'fields': ('name', 'email', 'mobile')
        }),
        ('Verification Details', {
            'fields': ('verification_method', 'verification_time')
        }),
        ('Error Details', {
            'fields': ('registration_error',),
            'classes': ('collapse',),
        }),
    )
    
    actions = ['manually_verify_hpr']
    
    @admin.action(description="Manually verify selected HPR IDs")
    def manually_verify_hpr(self, request, queryset):
        for entry in queryset:
            if entry.hpr_id:
                entry.is_verified = True
                entry.registered = True
                entry.verification_method = 'MANUAL'
                entry.verification_time = timezone.now()
                entry.registration_error = None
                entry.save()
        
        count = queryset.count()
        self.message_user(
            request, f"Successfully verified {count} HPR {'entry' if count == 1 else 'entries'}"
        )


admin.site.register(ConsentArtefact)
admin.site.register(ConsentRequest)
admin.site.register(Transaction)
admin.site.register(HealthFacility)
