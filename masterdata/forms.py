from django import forms
from django.core import signing
from django.core.exceptions import ValidationError

from masterdata.models import Process


def rate_state(process):
    return [process.pk, str(process.unit_price), str(process.standard_minutes)]


class ProcessRateForm(forms.ModelForm):
    rate_revision = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Process
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["rate_revision"].required = True
            self.initial["rate_revision"] = signing.dumps(
                rate_state(self.instance), salt="process-rate"
            )

    def clean(self):
        data = super().clean()
        if self.instance.pk:
            # Django 后台 POST 的外层事务会将此行锁保持到保存及历史记录完成。
            current = Process.objects.select_for_update().get(pk=self.instance.pk)
            try:
                previous = signing.loads(data.get("rate_revision"), salt="process-rate")
            except (signing.BadSignature, TypeError, ValueError):
                previous = None
            if previous != rate_state(current):
                raise ValidationError("工价已被更新或页面已失效，请刷新核对后重新保存")
        return data
