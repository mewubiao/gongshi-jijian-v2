from django import forms

from reporting.review_common import RevisionForm


class PieceApprovalForm(RevisionForm):
    corrected_qty = forms.DecimalField(
        label="修正完工数量",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        help_text="留空沿用当前有效数量，原始数量保留。",
    )
    corrected_hours = forms.IntegerField(
        label="修正工时：小时", min_value=0, max_value=1000000, required=False
    )
    corrected_minutes = forms.IntegerField(
        label="分钟", min_value=0, max_value=59, required=False
    )
    corrected_seconds = forms.IntegerField(
        label="秒", min_value=0, max_value=59, required=False
    )
    reason = forms.CharField(
        label="修正原因",
        required=False,
        strip=True,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirm_excess = forms.BooleanField(
        label="已核对超出派工数量，确认按有效数量通过", required=False
    )

    def clean(self):
        data = super().clean()
        times = [
            data.get(name)
            for name in ("corrected_hours", "corrected_minutes", "corrected_seconds")
        ]
        data["actual_seconds"] = (
            None
            if all(value is None for value in times)
            else (times[0] or 0) * 3600 + (times[1] or 0) * 60 + (times[2] or 0)
        )
        if (
            data.get("corrected_qty") is not None or data["actual_seconds"] is not None
        ) and not data.get("reason"):
            self.add_error("reason", "修正数量或工时必须填写原因")
        return data


class QueueFilterForm(forms.Form):
    q = forms.CharField(label="工单／员工", required=False, max_length=100)
    status = forms.ChoiceField(label="状态", required=False)
    month = forms.DateField(
        label="归属月份",
        required=False,
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )

    def __init__(self, data=None, *, statuses, default_status="", **kwargs):
        if data is not None:
            data = data.copy()
            data.setdefault("status", default_status)
        super().__init__(data, **kwargs)
        self.fields["status"].choices = [("", "全部状态"), *statuses]
