from django.contrib import admin

from timekeeping.models import (
    AuxiliaryWorkCategory,
    AuxiliaryWorkCorrection,
    AuxiliaryWorkLog,
    AuxiliaryWorkRejection,
)


class AuxiliaryCorrectionInline(admin.TabularInline):
    model = AuxiliaryWorkCorrection
    extra = 0
    can_delete = False
    readonly_fields = (
        "corrected_effective_minutes",
        "reason",
        "corrected_by",
        "corrected_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


class AuxiliaryRejectionInline(admin.TabularInline):
    model = AuxiliaryWorkRejection
    extra = 0
    can_delete = False
    readonly_fields = ("reason", "rejected_by", "rejected_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AuxiliaryWorkCategory)
class AuxiliaryWorkCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active", "note_required")
    search_fields = ("code", "name")


@admin.register(AuxiliaryWorkLog)
class AuxiliaryWorkLogAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "category",
        "ownership_date",
        "shift_name_snapshot",
        "status",
        "effective_minutes",
    )
    list_filter = ("status",)
    inlines = [AuxiliaryCorrectionInline, AuxiliaryRejectionInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
