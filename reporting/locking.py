"""月结锁账后，该归属月的报工不能再改。"""

from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import transaction


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


@contextmanager
def open_month_transaction(owned):
    """与月结锁账使用同一期间行，统一先锁月份、后锁业务记录。"""
    from settlement.models import SettlementPeriod

    with transaction.atomic():
        period, _ = SettlementPeriod.objects.get_or_create(
            year=owned.year, month=owned.month
        )
        period = SettlementPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status == SettlementPeriod.Status.LOCKED:
            raise ValidationError("该归属月份已锁账，请刷新页面")
        yield
