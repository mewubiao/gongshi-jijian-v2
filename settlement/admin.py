from django.contrib import admin

from settlement.models import SettlementLockEvent, SettlementPeriod


class SettlementLockEventInline(admin.TabularInline):
    model = SettlementLockEvent
    extra = 0
    can_delete = False
    readonly_fields = ("action", "actor", "occurred_at", "reason")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SettlementPeriod)
class SettlementPeriodAdmin(admin.ModelAdmin):
    list_display = ("year", "month", "status", "locked_by", "locked_at")
    list_filter = ("status",)
    inlines = [SettlementLockEventInline]

    def get_readonly_fields(self, request, obj=None):
        locked = ("status", "locked_by", "locked_at")
        if obj:
            return ("year", "month", *locked)
        return locked
