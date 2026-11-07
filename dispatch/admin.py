from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from accounts.admin import ScopedAdminMixin
from dispatch.models import Dispatch, DispatchMember, LeaderTransfer


class DispatchMemberInline(TabularInline):
    model = DispatchMember
    extra = 0
    can_delete = False
    readonly_fields = ("employee", "sequence")

    def has_add_permission(self, request, obj=None):
        return False


class LeaderTransferInline(TabularInline):
    model = LeaderTransfer
    extra = 0
    can_delete = False
    readonly_fields = ("from_employee", "to_employee", "transferred_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Dispatch)
class DispatchAdmin(ScopedAdminMixin, ModelAdmin):
    list_display = (
        "work_order",
        "process",
        "kind",
        "quantity",
        "assigned_by",
        "current_leader",
        "assigned_at",
    )
    list_filter = ("kind",)
    search_fields = ("work_order__order_no", "process__name")
    readonly_fields = (
        "work_order",
        "process",
        "kind",
        "quantity",
        "assigned_by",
        "assigned_at",
        "current_leader",
    )
    inlines = [DispatchMemberInline, LeaderTransferInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
