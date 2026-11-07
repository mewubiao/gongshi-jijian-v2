from django import forms
from django.core import signing
from django.core.exceptions import ValidationError


def revision_payload(record):
    payload = [
        record._meta.label_lower,
        record.pk,
        record.status,
        record.approved_at.isoformat() if record.approved_at else None,
    ]
    for relation in ("corrections", "rejections", "approval_reversals"):
        manager = getattr(record, relation, None)
        payload.append(
            manager.order_by("-pk").values_list("pk", flat=True).first()
            if manager is not None
            else None
        )
    return payload


def review_revision(record):
    return signing.dumps(revision_payload(record), salt="work-review")


def check_revision(record, revision):
    try:
        payload = signing.loads(revision, salt="work-review")
    except (signing.BadSignature, TypeError, ValueError):
        raise ValidationError("审核页面已失效，请刷新后重新审核") from None
    if payload != revision_payload(record):
        raise ValidationError("记录已被更新，请刷新后核对最新内容")


class RevisionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)


class RejectForm(RevisionForm):
    reason = forms.CharField(
        label="驳回原因",
        strip=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=2000,
    )


class ReverseForm(RevisionForm):
    confirm = forms.BooleanField(label="确认撤销审核，记录将返回待审核状态")
