from django import forms

from reporting.review_common import RevisionForm


class AuxiliaryApprovalForm(RevisionForm):
    minutes = forms.DecimalField(
        label="修正有效分钟",
        required=False,
        max_digits=22,
        decimal_places=4,
        min_value=0,
        help_text="留空沿用当前有效分钟，填写 0 表示修正为零。",
    )
    reason = forms.CharField(
        label="修正原因",
        required=False,
        strip=True,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self):
        data = super().clean()
        if data.get("minutes") is not None and not data.get("reason"):
            self.add_error("reason", "修正工时必须填写原因")
        return data
