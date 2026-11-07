from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from masterdata.access import employee_for_user
from masterdata.forms import ProcessRateForm
from masterdata.models import (
    Employee,
    Process,
    ProcessRateHistory,
    Product,
    Team,
    WorkCenter,
)


class ProcessRateHistoryInline(TabularInline):
    model = ProcessRateHistory
    extra = 0
    can_delete = False
    readonly_fields = (
        "changed_by_name",
        "changed_at",
        "old_unit_price",
        "new_unit_price",
        "old_standard_minutes",
        "new_standard_minutes",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="修改人")
    def changed_by_name(self, obj):
        return obj.changed_by.name

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("changed_by", "process__product")
        )


class ProcessInline(TabularInline):
    model = Process
    extra = 0
    can_delete = False
    show_change_link = True
    readonly_fields = ("sequence", "name", "unit_price", "standard_minutes")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Employee)
class EmployeeAdmin(ModelAdmin):
    list_display = ("code", "name", "role", "badge_nfc", "qr_code", "team", "active")
    list_filter = ("role", "active", "team")
    search_fields = ("code", "name", "badge_nfc", "qr_code")
    autocomplete_fields = ("user", "team")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return (
            queryset
            if request.user.is_superuser
            else queryset.exclude(user__is_superuser=True)
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user" and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                is_superuser=False
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(WorkCenter)
class WorkCenterAdmin(ModelAdmin):
    list_display = (
        "code",
        "name",
        "day_shift_start",
        "day_shift_end",
        "night_shift_start",
        "night_shift_end",
    )
    search_fields = ("code", "name")


@admin.register(Team)
class TeamAdmin(ModelAdmin):
    list_display = ("name", "work_center", "leader", "has_shift_override")
    list_filter = ("work_center",)
    search_fields = ("code", "name")

    @admin.display(boolean=True, description="已覆盖班次")
    def has_shift_override(self, obj):
        return obj.has_shift_override


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")
    inlines = [ProcessInline]


@admin.register(Process)
class ProcessAdmin(ModelAdmin):
    form = ProcessRateForm
    list_display = (
        "product",
        "sequence",
        "name",
        "unit_price",
        "standard_minutes",
    )
    list_filter = ("product",)
    search_fields = ("operation_code", "name", "product__code", "product__name")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)
    inlines = [ProcessRateHistoryInline]

    def get_readonly_fields(self, request, obj=None):
        employee = employee_for_user(request.user)
        if obj and (
            employee is None or employee.role != Employee.Role.PROCESS_ENGINEER
        ):
            return ("unit_price", "standard_minutes")
        return ()

    def save_model(self, request, obj, form, change):
        obj.save(changed_by=employee_for_user(request.user))
