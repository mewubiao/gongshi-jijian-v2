from django.contrib import admin

from erp.models import (
    ErpSourceMapping,
    ProductionReceipt,
    SyncLog,
    SyncState,
    WorkOrder,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkOrder)
class WorkOrderAdmin(ReadOnlyAdmin):
    """镜像只供查看。写入由同步任务完成。"""

    list_display = (
        "order_no",
        "erp_order_type",
        "product",
        "work_center",
        "status",
        "erp_status",
        "planned_qty",
        "remaining_qty",
        "synced_at",
    )
    list_filter = ("status", "work_center")
    search_fields = ("order_no", "erp_key", "product__code", "product__name")
    readonly_fields = (
        "order_no",
        "erp_key",
        "erp_order_type",
        "product",
        "work_center",
        "status",
        "erp_status",
        "planned_qty",
        "unit",
        "remaining_qty",
        "erp_start_date",
        "erp_due_date",
        "source_modified_at",
        "synced_at",
    )


@admin.register(ProductionReceipt)
class ProductionReceiptAdmin(ReadOnlyAdmin):
    list_display = (
        "erp_receipt_type",
        "erp_receipt_no",
        "erp_line_no",
        "work_order",
        "receipt_qty",
        "receipt_date",
        "active_source_record",
        "last_synced_at",
    )
    list_filter = ("active_source_record",)
    search_fields = (
        "erp_key",
        "erp_receipt_no",
        "erp_work_order_key",
        "work_order__order_no",
    )
    readonly_fields = (
        "erp_key",
        "erp_receipt_type",
        "erp_receipt_no",
        "erp_line_no",
        "receipt_date",
        "product",
        "work_order",
        "erp_work_order_key",
        "receipt_qty",
        "source_qty_11",
        "scrap_qty_12",
        "accepted_qty_13",
        "source_modified_at",
        "last_synced_at",
        "active_source_record",
    )


@admin.register(SyncLog)
class SyncLogAdmin(ReadOnlyAdmin):
    list_display = (
        "started_at",
        "finished_at",
        "sync_type",
        "trigger",
        "status",
        "inserted",
        "updated",
        "skipped",
        "invalidated",
    )
    list_filter = ("sync_type", "status", "trigger")
    readonly_fields = (
        "sync_type",
        "trigger",
        "status",
        "started_at",
        "finished_at",
        "inserted",
        "updated",
        "skipped",
        "invalidated",
        "error_summary",
        "triggered_by",
    )


@admin.register(SyncState)
class SyncStateAdmin(ReadOnlyAdmin):
    list_display = (
        "sync_type",
        "last_started_at",
        "last_succeeded_at",
        "cursor_at",
    )
    readonly_fields = (
        "sync_type",
        "cursor_at",
        "last_started_at",
        "last_succeeded_at",
    )


@admin.register(ErpSourceMapping)
class ErpSourceMappingAdmin(admin.ModelAdmin):
    """映射可以改。默认都还没核对。"""

    list_display = (
        "code",
        "erp_table",
        "erp_column",
        "local_model",
        "local_field",
        "verified",
    )
    list_filter = ("verified", "local_model")
    search_fields = ("code", "erp_table", "erp_column", "note")
