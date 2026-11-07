"""按当前员工状态和班组负责人校验权限，不信任调用方缓存的角色。"""

from django.core.exceptions import ValidationError
from django.db.models import Q


def employee_for_user(user):
    from masterdata.models import Employee

    if not user.is_authenticated or not user.is_active:
        return None
    return Employee.objects.filter(
        user_id=user.pk, active=True, user__is_active=True
    ).first()


def require_role(actor, role, message):
    from masterdata.models import Employee

    employee = (
        Employee.objects.filter(pk=getattr(actor, "pk", None), active=True, role=role)
        .filter(Q(user__isnull=True) | Q(user__is_active=True))
        .first()
    )
    if employee is None:
        raise ValidationError(message)
    return employee


def require_team_leader(actor, team_ids):
    employee = require_role(actor, "team_leader", "操作须由启用的班组长执行")
    team_ids = set(team_ids)
    if None in team_ids or not team_ids:
        raise ValidationError("人员或任务必须归属班组")
    if employee.led_teams.filter(pk__in=team_ids, active=True).count() != len(team_ids):
        raise ValidationError("只能管理本人负责的班组")
