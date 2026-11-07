from datetime import date

from django.db.models import Q
from django.utils import timezone

from dispatch.models import Dispatch
from reporting.models import PieceReport


def assigned_tasks(employee):
    return (
        Dispatch.objects.filter(members__employee=employee)
        .select_related("work_order__product", "process", "current_leader", "team")
        .distinct()
    )


def personal_reports(employee):
    today = timezone.localdate()
    start = date(today.year - (today.month == 1), today.month - 1 or 12, 1)
    stop = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return (
        PieceReport.objects.filter(
            Q(reported_by=employee) | Q(work_session__member_spans__employee=employee),
            ownership_date__gte=start,
            ownership_date__lt=stop,
        )
        .select_related("dispatch__work_order", "dispatch__process", "reported_by")
        .distinct()
    )
