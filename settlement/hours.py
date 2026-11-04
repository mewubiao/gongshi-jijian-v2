"""已审核辅助工时按员工、归属月汇总。不读单价，不算金额。"""

from decimal import Decimal

from timekeeping.models import AuxiliaryWorkLog

ZERO = Decimal("0")


def approved_auxiliary_minutes(year, month):
    """返回 {employee_id: 分钟}。有审核修正时用修正后的分钟。"""
    totals = {}
    logs = AuxiliaryWorkLog.objects.filter(
        status=AuxiliaryWorkLog.Status.APPROVED,
        ownership_date__year=year,
        ownership_date__month=month,
    )
    for log in logs:
        minutes = log.effective_minutes_for_review() or ZERO
        totals[log.employee_id] = totals.get(log.employee_id, ZERO) + minutes
    return totals
