from datetime import date

from django.db.models import Q
from django.utils import timezone

from masterdata.access import employee_for_user
from masterdata.models import Employee

LEADER_TEAM_FIELDS = {
    "dispatch.dispatch": "team",
    "reporting.worksession": "dispatch__team",
    "reporting.piecereport": "dispatch__team",
    "timekeeping.auxiliaryworklog": "team",
}


def scope_queryset(user, queryset):
    """列表与对象详情共用相同数据范围；未知模型默认拒绝。"""
    label = queryset.model._meta.label_lower
    app, model = label.split(".")
    if not user.has_perm(f"{app}.view_{model}"):
        return queryset.none()
    if user.is_superuser:
        return queryset
    employee = employee_for_user(user)
    if employee is None:
        return queryset.none()
    if employee.role in (
        Employee.Role.ADMIN,
        Employee.Role.SETTLEMENT,
        Employee.Role.PROCESS_ENGINEER,
    ):
        return queryset
    if employee.role == Employee.Role.TEAM_LEADER:
        team_field = LEADER_TEAM_FIELDS.get(label)
        if team_field:
            return queryset.filter(
                **{
                    f"{team_field}__leader": employee,
                    f"{team_field}__active": True,
                }
            )
        return queryset.none()
    if label == "dispatch.dispatch":
        return queryset.filter(members__employee=employee).distinct()
    if label == "reporting.worksession":
        return queryset.filter(
            Q(started_by=employee) | Q(member_spans__employee=employee)
        ).distinct()
    if label == "reporting.piecereport":
        today = timezone.localdate()
        previous_month = date(today.year - (today.month == 1), today.month - 1 or 12, 1)
        next_month = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        return queryset.filter(
            Q(reported_by=employee) | Q(work_session__member_spans__employee=employee),
            ownership_date__gte=previous_month,
            ownership_date__lt=next_month,
        ).distinct()
    if label == "timekeeping.auxiliaryworklog":
        return queryset.filter(employee=employee)
    return queryset.none()
