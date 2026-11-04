"""月结锁账后，该归属月的报工不能再改。"""

from django.core.exceptions import ValidationError


def assert_month_open(ownership_date):
    if ownership_date is None:
        return
    from settlement.models import SettlementPeriod

    locked = SettlementPeriod.objects.filter(
        year=ownership_date.year,
        month=ownership_date.month,
        status=SettlementPeriod.Status.LOCKED,
    ).exists()
    if locked:
        raise ValidationError("该归属月份已锁账，报工不能修改")
