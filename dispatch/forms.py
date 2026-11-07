from decimal import Decimal
from uuid import uuid4

from django import forms

from dispatch.models import Dispatch
from masterdata.models import Employee, Process


class EmployeeChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} · {obj.name}（{obj.team.name}）"


class MemberChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} · {obj.name}（{obj.team.name}）"


class DispatchForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid4)
    kind = forms.ChoiceField(label="派工方式", choices=Dispatch.Kind.choices)
    process = forms.ModelChoiceField(label="工序", queryset=Process.objects.none())
    quantity = forms.DecimalField(
        label="派工数量", max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    assignee = EmployeeChoice(
        label="执行人（整组时为小组长）",
        queryset=Employee.objects.none(),
        help_text="整组时该员工列在第一位，作为初始小组长及尾差接收人。",
    )
    additional_members = MemberChoices(
        label="其他组员",
        queryset=Employee.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="仅整组派工填写，不必再次选择小组长。",
    )
    planned_date = forms.DateField(
        label="计划日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    dispatch_note = forms.CharField(
        label="派工备注",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, actor, work_order, **kwargs):
        super().__init__(*args, **kwargs)
        self.work_order = work_order
        self.fields["process"].queryset = Process.objects.filter(
            product=work_order.product, active=True
        )
        employees = (
            Employee.objects.filter(active=True, team__active=True, team__leader=actor)
            .select_related("team")
            .order_by("team__code", "code")
        )
        self.fields["assignee"].queryset = employees
        self.fields["additional_members"].queryset = employees

    def clean(self):
        data = super().clean()
        members = data.get("additional_members")
        if members is None:
            return data
        if data.get("kind") == Dispatch.Kind.SINGLE and members:
            self.add_error("additional_members", "单人派工不能选择其他组员")
        if data.get("kind") == Dispatch.Kind.GROUP and not members:
            self.add_error("additional_members", "整组至少需要小组长和一名其他组员")
        if data.get("assignee") in members:
            self.add_error("additional_members", "小组长已经列在第一位，请勿重复选择")
        if data.get("quantity") and data["quantity"] > self.work_order.remaining_qty:
            self.add_error("quantity", "派工数量不能超过当前工单剩余量")
        return data
