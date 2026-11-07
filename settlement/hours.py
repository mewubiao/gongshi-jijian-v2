"""已审核辅助工时按员工、归属月汇总。不读单价，不算金额。"""

from django.db.models import Sum

from timekeeping.models import AuxiliaryWorkLog
from timekeeping.selectors import with_effective_minutes


def approved_auxiliary_minutes(year, month):
    """返回 {employee_id: 分钟}。有审核修正时用修正后的分钟。"""
    logs = (
        with_effective_minutes(
            AuxiliaryWorkLog.objects.filter(
                status=AuxiliaryWorkLog.Status.APPROVED,
                ownership_date__year=year,
                ownership_date__month=month,
            )
        )
        .values("employee_id")
        .annotate(total=Sum("review_minutes"))
    )
    return {row["employee_id"]: row["total"] for row in logs}
