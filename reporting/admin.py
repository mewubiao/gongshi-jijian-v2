from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from accounts.admin import ScopedAdminMixin
from reporting.models import (
    GroupMemberSpan,
    PauseInterval,
    PieceReport,
    ReportApprovalReversal,
    ReportCorrection,
    ReportRejection,
    WorkSession,
)


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PauseInline(TabularInline):
    model = PauseInterval
    extra = 0
    can_delete = False
    readonly_fields = ("reason", "paused_at", "resumed_at")

    def has_add_permission(self, request, obj=None):
        return False


class GroupMemberSpanInline(TabularInline):
    model = GroupMemberSpan
    extra = 0
    can_delete = False
    readonly_fields = (
        "employee",
        "joined_at",
        "left_at",
        "recorded_by",
        "frozen_actual_seconds",
    )

    def has_add_permission(self, request, obj=None):
        return False


class ReportCorrectionInline(TabularInline):
    model = ReportCorrection
    extra = 0
    can_delete = False
    readonly_fields = (
        "corrected_qty",
        "corrected_actual_seconds",
        "reason",
        "corrected_by",
        "corrected_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


class ReportRejectionInline(TabularInline):
    model = ReportRejection
    extra = 0
    can_delete = False
    readonly_fields = ("reason", "rejected_by", "rejected_at")

    def has_add_permission(self, request, obj=None):
        return False


class ReportApprovalReversalInline(TabularInline):
    model = ReportApprovalReversal
    extra = 0
    can_delete = False
    readonly_fields = ("reversed_by", "reversed_at", "approved_by", "approved_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkSession)
class WorkSessionAdmin(ScopedAdminMixin, ReadOnlyAdmin):
    list_display = ("dispatch", "started_by", "started_at", "ended_at")
    inlines = [PauseInline, GroupMemberSpanInline]


@admin.register(PieceReport)
class PieceReportAdmin(ScopedAdminMixin, ReadOnlyAdmin):
    list_display = (
        "dispatch",
        "ownership_date",
        "completed_qty",
        "frozen_unit_price",
        "status",
        "reported_by",
    )
    list_filter = ("status",)
    inlines = [
        ReportCorrectionInline,
        ReportRejectionInline,
        ReportApprovalReversalInline,
    ]
