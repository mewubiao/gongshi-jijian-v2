from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from accounts.admin import ScopedAdminMixin
from timekeeping.models import (
    AuxiliaryWorkCategory,
    AuxiliaryWorkCorrection,
    AuxiliaryWorkLog,
    AuxiliaryWorkRejection,
)


class AuxiliaryCorrectionInline(TabularInline):
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


class AuxiliaryRejectionInline(TabularInline):
    model = AuxiliaryWorkRejection
    extra = 0
    can_delete = False
    readonly_fields = ("reason", "rejected_by", "rejected_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AuxiliaryWorkCategory)
class AuxiliaryWorkCategoryAdmin(ModelAdmin):
    list_display = ("code", "name", "active", "note_required")
    search_fields = ("code", "name")


@admin.register(AuxiliaryWorkLog)
class AuxiliaryWorkLogAdmin(ScopedAdminMixin, ModelAdmin):
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
