"""模型层共用的数值与时间校验。"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

TWO_PLACES = Decimal("0.01")


def ensure_two_places(value, field_name, label="数值"):
    """拒绝超过两位小数的值，避免金额和工时被静默四舍五入。"""
    if value is None:
        return value
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    if value != value.quantize(TWO_PLACES):
        raise ValidationError({field_name: f"{label}最多保留两位小数"})
    return value


def ensure_aware(value, field_name):
    """时间必须是带时区的 datetime。项目时区为 Asia/Shanghai。"""
    if value is None:
        return value
    if timezone.is_naive(value):
        raise ValidationError({field_name: "必须是带时区的时间"})
    return value
