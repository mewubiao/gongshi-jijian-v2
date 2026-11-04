from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from masterdata.models import (
    Employee,
    Process,
    ProcessRateHistory,
    Product,
    Team,
    WorkCenter,
)


class ProcessAdminForm(forms.ModelForm):
    changed_by = forms.ModelChoiceField(
        label="修改人",
        queryset=Employee.objects.filter(role=Employee.Role.PROCESS_ENGINEER),
        required=False,
        help_text="修改单价或标准工时时必填，须为工艺人员。",
    )

    class Meta:
        model = Process
        fields = (
            "product",
            "sequence",
            "name",
            "unit_price",
            "standard_minutes",
        )

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk:
            return cleaned
        price = cleaned.get("unit_price")
        minutes = cleaned.get("standard_minutes")
        if price is None or minutes is None:
            return cleaned
        changed = (
            price != self.instance.unit_price
            or minutes != self.instance.standard_minutes
        )
        if changed and cleaned.get("changed_by") is None:
            raise ValidationError("修改单价或标准工时必须记录修改人")
        return cleaned


class ProcessRateHistoryInline(admin.TabularInline):
    model = ProcessRateHistory
    extra = 0
    can_delete = False
    readonly_fields = (
        "changed_by",
        "changed_at",
        "old_unit_price",
        "new_unit_price",
        "old_standard_minutes",
        "new_standard_minutes",
    )

    def has_add_permission(self, request, obj=None):
        return False


class ProcessInline(admin.TabularInline):
    model = Process
    extra = 0
    can_delete = False
    show_change_link = True
    readonly_fields = ("sequence", "name", "unit_price", "standard_minutes")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "badge_nfc", "qr_code", "team", "is_active")
    list_filter = ("role", "is_active", "team")
    search_fields = ("name", "badge_nfc", "qr_code")


@admin.register(WorkCenter)
class WorkCenterAdmin(admin.ModelAdmin):
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
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "work_center", "leader", "has_shift_override")
    list_filter = ("work_center",)
    search_fields = ("name",)

    @admin.display(boolean=True, description="已覆盖班次")
    def has_shift_override(self, obj):
        return obj.has_shift_override


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")
    inlines = [ProcessInline]


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    form = ProcessAdminForm
    list_display = (
        "product",
        "sequence",
        "name",
        "unit_price",
        "standard_minutes",
    )
    list_filter = ("product",)
    search_fields = ("name", "product__code")
    inlines = [ProcessRateHistoryInline]

    def save_model(self, request, obj, form, change):
        obj.save(changed_by=form.cleaned_data.get("changed_by"))
