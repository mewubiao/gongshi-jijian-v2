from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from dispatch.models import Dispatch
from dispatch.selectors import available_orders
from masterdata.access import require_role, require_team_leader
from masterdata.models import Employee, Process


def create_dispatch(*, actor, work_order_id, data):
    require_role(actor, Employee.Role.TEAM_LEADER, "派工由启用的班组长执行")
    try:
        with transaction.atomic():
            order = available_orders(actor).filter(pk=work_order_id).first()
            if order is None:
                raise ValidationError("工单不在可派工范围内")
            # 先锁工单，确保易飞同步关闭工单或调整剩余量时不会使用旧值。
            order = order.__class__.objects.select_for_update().get(pk=order.pk)
            if not available_orders(actor).filter(pk=order.pk).exists():
                raise ValidationError("工单的产品或工作中心已变更，请重新选择")
            existing = Dispatch.objects.filter(
                submission_key=data["submission_key"]
            ).first()
            if existing is not None:
                if existing.assigned_by_id != actor.pk:
                    raise ValidationError("提交标识无效，请重新打开派工页面")
                require_team_leader(actor, [existing.team_id])
                return existing, False
            process = Process.objects.filter(
                pk=data["process"].pk, product=order.product, active=True
            ).first()
            if process is None:
                raise ValidationError("工序已停用或不属于该产品，请刷新页面")
            payload = {
                "work_order": order,
                "process": process,
                "quantity": data["quantity"],
                "assigned_by": actor,
            }
            if data["kind"] == Dispatch.Kind.SINGLE:
                dispatch = Dispatch.create_single(assignee=data["assignee"], **payload)
            else:
                dispatch = Dispatch.create_group(
                    members=[data["assignee"], *data["additional_members"]], **payload
                )
            dispatch.submission_key = data["submission_key"]
            dispatch.planned_date = data["planned_date"]
            dispatch.dispatch_note = data["dispatch_note"]
            dispatch.save()
            return dispatch, True
    except IntegrityError:
        existing = Dispatch.objects.filter(
            submission_key=data["submission_key"], assigned_by=actor
        ).first()
        if existing is None:
            raise
        require_team_leader(actor, [existing.team_id])
        return existing, False
