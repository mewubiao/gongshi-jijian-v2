from django.core.exceptions import ValidationError

from reporting.models import PieceReport, WorkSession, shift_for
from reporting.ownership import ownership_date
from timekeeping.models import AuxiliaryWorkLog


def assert_ready_to_lock(year, month):
    assert_no_running_work(year, month)
    period = {"ownership_date__year": year, "ownership_date__month": month}
    if PieceReport.objects.filter(status=PieceReport.Status.PENDING, **period).exists():
        raise ValidationError("该月还有未审核报工，不能锁账")
    if AuxiliaryWorkLog.objects.filter(
        status=AuxiliaryWorkLog.Status.PENDING_REVIEW, **period
    ).exists():
        raise ValidationError("该月还有未审核辅助工时，不能锁账")


def assert_no_running_work(year, month):
    """在持有月结期间行锁时检查；只读取归属日，不补写历史快照。"""
    unfinished = (
        WorkSession.objects.filter(ended_at__isnull=True)
        .select_related(
            "dispatch__work_order__work_center", "started_by__team__work_center"
        )
        .order_by("started_at", "pk")
    )
    sessions = list(
        unfinished.filter(
            ownership_date_snapshot__year=year, ownership_date_snapshot__month=month
        )[:3]
    )
    if len(sessions) < 3:
        # ponytail: 仅扫描无快照的历史未完工记录；量大时按开工日期预筛。
        for session in unfinished.filter(
            ownership_date_snapshot__isnull=True
        ).iterator():
            owned = ownership_date(
                session.started_at,
                *shift_for(session.started_by, session.dispatch.work_order),
            )
            if (owned.year, owned.month) == (year, month):
                sessions.append(session)
                if len(sessions) == 3:
                    break
    examples = [
        f"生产任务 #{session.dispatch_id}"
        f"（工单 {session.dispatch.work_order.order_no}，"
        f"开工人 {session.started_by.name}）"
        for session in sessions
    ]
    auxiliary = (
        AuxiliaryWorkLog.objects.filter(
            status=AuxiliaryWorkLog.Status.RUNNING,
            ownership_date__year=year,
            ownership_date__month=month,
        )
        .select_related("employee")
        .order_by("started_at", "pk")[:3]
    )
    examples.extend(
        f"辅助作业 #{log.pk}（{log.employee.name}，{log.category_name_snapshot}）"
        for log in auxiliary
    )
    if examples:
        raise ValidationError(
            [
                f"{year}-{month:02d} 仍有进行中的生产任务或辅助作业，"
                "请先完工并完成审核后再锁账。以下列出部分待处理记录：",
                *examples,
            ]
        )
