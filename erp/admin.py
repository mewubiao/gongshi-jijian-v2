from django.contrib import admin

from erp.models import WorkOrder


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    """镜像只供查看。写入留给后续同步任务。"""

    list_display = (
        "order_no",
        "product",
        "work_center",
        "status",
        "remaining_qty",
        "synced_at",
    )
    list_filter = ("status", "work_center")
    search_fields = ("order_no", "product__code", "product__name")
    readonly_fields = (
        "order_no",
        "product",
        "work_center",
        "status",
        "remaining_qty",
        "synced_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
