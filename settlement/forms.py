from django import forms


class MonthForm(forms.Form):
    month = forms.DateField(
        label="归属月份",
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )


class LockForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput, max_length=128)


class UnlockForm(LockForm):
    reason = forms.CharField(
        label="解锁原因", max_length=2000, widget=forms.Textarea(attrs={"rows": 3})
    )
