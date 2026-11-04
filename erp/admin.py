from django.contrib import admin

from erp.models import SyncRun, WorkOrder


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    """镜像只供查看。写入由同步任务完成。"""

    list_display = (
        "order_no",
        "product",
        "work_center",
        "status",
        "remaining_qty",
        "synced_at",
        "source_updated_at",
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
        "source_updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    """同步记录只供查看。"""

    list_display = (
        "started_at",
        "finished_at",
        "status",
        "fetched_count",
        "created_count",
        "updated_count",
        "closed_count",
        "skipped_count",
        "receipt_fetched_count",
        "receipt_skipped_count",
    )
    list_filter = ("status",)
    readonly_fields = (
        "started_at",
        "finished_at",
        "status",
        "fetched_count",
        "created_count",
        "updated_count",
        "unchanged_count",
        "closed_count",
        "skipped_count",
        "receipt_fetched_count",
        "receipt_created_count",
        "receipt_updated_count",
        "receipt_unchanged_count",
        "receipt_removed_count",
        "receipt_skipped_count",
        "warnings",
        "error",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
