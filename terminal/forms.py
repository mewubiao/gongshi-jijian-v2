from django import forms

from terminal.models import TerminalDevice


class DeviceForm(forms.ModelForm):
    class Meta:
        model = TerminalDevice
        fields = ["code", "name"]


class ActivationForm(forms.Form):
    code = forms.CharField(
        label="一次性激活码",
        max_length=100,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )


class IdentifyForm(forms.Form):
    method = forms.ChoiceField(
        label="识别方式", choices=[("qr", "员工二维码"), ("nfc", "NFC 工牌")]
    )
    credential = forms.CharField(
        label="工牌号／二维码内容",
        max_length=128,
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "autofocus": True}),
    )

    def clean(self):
        data = super().clean()
        if data.get("method") == "nfc" and len(data.get("credential", "")) > 64:
            self.add_error("credential", "工牌号长度不正确")
        return data


class TaskForm(forms.Form):
    ticket = forms.CharField(widget=forms.HiddenInput)
    quantity = forms.DecimalField(
        label="完工数量", max_digits=12, decimal_places=2, min_value=0, required=False
    )
    reason = forms.CharField(label="原因", max_length=2000, required=False, strip=True)
    member = forms.IntegerField(
        label="组员", required=False, min_value=1, max_value=9223372036854775807
    )
