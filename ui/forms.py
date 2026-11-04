from django import forms


def daisy_widget_class(widget, *, invalid):
    """返回 daisyUI 的 class。类名写全，方便 Tailwind 扫描。"""
    if isinstance(widget, forms.HiddenInput):
        return ""
    if isinstance(widget, forms.CheckboxInput):
        if invalid:
            return "checkbox checkbox-error"
        return "checkbox"
    if isinstance(widget, forms.RadioSelect):
        return ""
    if isinstance(widget, forms.FileInput):
        if invalid:
            return "file-input file-input-error w-full"
        return "file-input w-full"
    if isinstance(widget, forms.Select):
        if invalid:
            return "select select-error w-full"
        return "select w-full"
    if isinstance(widget, forms.Textarea):
        if invalid:
            return "textarea textarea-error w-full"
        return "textarea w-full"
    if isinstance(widget, forms.MultiWidget):
        return ""
    if invalid:
        return "input input-error w-full"
    return "input w-full"


class DaisyFormMixin:
    """给控件加上 daisyUI class。业务表单继承后，用 components/form.html 渲染。"""

    label_suffix = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_daisy_classes()

    def apply_daisy_classes(self):
        invalid = set(self.errors) if self.is_bound else set()
        for name, field in self.fields.items():
            css = daisy_widget_class(field.widget, invalid=name in invalid)
            if not css:
                continue
            current = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{current} {css}".strip()


class DemoReportForm(DaisyFormMixin, forms.Form):
    """组件示例用的表单，不写入业务数据。"""

    worker = forms.CharField(label="工人", max_length=32)
    quantity = forms.IntegerField(label="完工数量", min_value=1)
    shift = forms.ChoiceField(
        label="班次",
        choices=(("day", "白班"), ("night", "夜班")),
    )
    note = forms.CharField(
        label="备注",
        required=False,
        help_text="可以不填。",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirmed = forms.BooleanField(label="确认数量", required=False)
