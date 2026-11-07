from contextlib import contextmanager

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from dispatch.models import Dispatch
from masterdata.models import Employee
from reporting.locking import open_month_transaction
from reporting.models import PieceReport, WorkSession, shift_for
from reporting.ownership import ownership_date
from reporting.review_common import revision_payload
from terminal.auth import active_employees
from terminal.models import TerminalDevice
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


def task_state(dispatch, employee):
    session = dispatch.sessions.order_by("-started_at", "-pk").first()
    report = dispatch.reports.order_by("-submitted_at", "-pk").first()
    return [
        employee.pk,
        dispatch.pk,
        dispatch.current_leader_id,
        [
            session.pk,
            str(session.ended_at),
            [
                [key, str(value)]
                for key, value in session.pauses.values_list("pk", "resumed_at")
            ],
            [
                [key, str(value)]
                for key, value in session.member_spans.values_list("pk", "left_at")
            ],
        ]
        if session
        else None,
        [report.pk, report.status] if report else None,
    ]


def task_ticket(dispatch, employee):
    return signing.dumps(
        task_state(dispatch, employee),
        salt="terminal-task",
        serializer=signing.JSONSerializer,
    )


def report_ticket(report, employee):
    return signing.dumps(
        [employee.pk, revision_payload(report)], salt="terminal-report"
    )


def check_ticket(ticket, payload, salt):
    try:
        actual = signing.loads(ticket, salt=salt)
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError("页面已失效，请返回任务列表刷新") from None
    # 日期统一为字符串，签名数据只用于版本核对，不接受客户端业务时间。
    if actual != payload:
        raise ValidationError("任务状态或当前身份已经变化，请刷新后操作")


@contextmanager
def operation_transaction(owned, device, employee, employee_ids=()):
    with open_month_transaction(owned):
        current = TerminalDevice.objects.select_for_update().get(pk=device.pk)
        if not current.active or (current.generation, current.login_sequence) != (
            device.generation,
            device.login_sequence,
        ):
            raise ValidationError("终端或员工身份已失效，请重新识别")
        list(
            Employee.objects.select_for_update()
            .filter(pk__in=set(employee_ids) | {employee.pk})
            .order_by("pk")
        )
        actor = active_employees().filter(pk=employee.pk).first()
        if actor is None:
            raise ValidationError("员工已停用或权限发生变化")
        yield actor


def task_action(*, device, employee, dispatch_id, action, data, present_ids=None):
    dispatch = Dispatch.objects.get(pk=dispatch_id)
    session = dispatch.sessions.filter(ended_at__isnull=True).first()
    owner = session.started_by if session else employee
    when = session.started_at if session else timezone.now()
    owned = (
        session.ownership_date_snapshot
        if session and session.ownership_date_snapshot
        else ownership_date(when, *shift_for(owner, dispatch.work_order))
    )
    crew_ids = list(dispatch.members.values_list("employee_id", flat=True))
    with operation_transaction(owned, device, employee, crew_ids) as actor:
        dispatch = Dispatch.objects.select_for_update().get(pk=dispatch_id)
        if not dispatch.members.filter(employee=actor).exists():
            raise ValidationError("只能操作本人派工任务")
        dispatch.assert_terminal_operator(actor)
        check_ticket(data["ticket"], task_state(dispatch, actor), "terminal-task")
        now = timezone.now()
        session = (
            dispatch.sessions.select_for_update().filter(ended_at__isnull=True).first()
        )
        if action == "open":
            if session:
                raise ValidationError("任务已经开工")
            present = None
            if dispatch.kind == Dispatch.Kind.GROUP:
                try:
                    chosen_ids = [int(value) for value in (present_ids or [])]
                except (ValueError, TypeError):
                    raise ValidationError("组员选择无效，请重新选择") from None
                if any(not 0 < value <= 9223372036854775807 for value in chosen_ids):
                    raise ValidationError("组员选择无效")
                present = list(
                    active_employees()
                    .filter(
                        pk__in=chosen_ids,
                        dispatch_memberships__dispatch=dispatch,
                    )
                    .distinct()
                )
                if len(present) != len(chosen_ids):
                    raise ValidationError("只能选择本任务中启用的组员，且不能重复")
            session = WorkSession.open(
                dispatch=dispatch,
                started_by=actor,
                started_at=now,
                present_employees=present,
            )
            if session.ownership_date_snapshot != owned:
                raise ValidationError("班次配置发生变化，请刷新后开工")
        elif session is None:
            raise ValidationError("任务未开工或已经报工")
        elif action == "pause":
            session.pause(reason=data.get("reason", ""), paused_at=now)
        elif action == "resume":
            session.resume(resumed_at=now)
        elif action == "finish":
            if data.get("quantity") is None:
                raise ValidationError("请填写完工数量")
            return session.finish_and_report(
                completed_qty=data["quantity"], reported_by=actor, submitted_at=now
            )
        elif action in ("join", "leave", "transfer"):
            member = (
                dispatch.members.select_related("employee")
                .filter(employee_id=data.get("member"))
                .first()
            )
            if (
                member is None
                or not active_employees().filter(pk=member.employee_id).exists()
            ):
                raise ValidationError("只能选择启用的派工组员")
            if action == "join":
                session.join_member(
                    employee=member.employee, joined_at=now, recorded_by=actor
                )
            elif action == "leave":
                span = session.member_spans.filter(
                    employee=member.employee, left_at__isnull=True
                ).first()
                if span is None:
                    raise ValidationError("该组员当前不在组")
                span.leave(at=now, recorded_by=actor)
            else:
                dispatch.transfer_leader(
                    to_employee=member.employee, transferred_at=now
                )
        else:
            raise ValidationError("操作无效")
        return None


def personal_report_action(*, device, employee, report_id, action, data):
    report = PieceReport.objects.get(pk=report_id)
    with operation_transaction(report.ownership_date, device, employee) as actor:
        report = PieceReport.objects.select_for_update().get(pk=report_id)
        if report.reported_by_id != actor.pk:
            raise ValidationError("只能撤回或重报本人提交的记录")
        check_ticket(
            data["ticket"], [actor.pk, revision_payload(report)], "terminal-report"
        )
        if action == "withdraw":
            report.withdraw(data.get("reason", ""), at=timezone.now())
        elif action == "resubmit":
            if data.get("quantity") is None:
                raise ValidationError("请填写重报数量")
            return report.resubmit(completed_qty=data["quantity"], reported_by=actor)
        else:
            raise ValidationError("操作无效")
        return report


def auxiliary_action(
    *, device, employee, action, category_id=None, log_id=None, note="", reason=""
):
    log = AuxiliaryWorkLog.objects.get(pk=log_id) if log_id else None
    when = timezone.now()
    owned = (
        log.ownership_date
        if log
        else ownership_date(when, *shift_for(employee, None))
        if employee.team_id
        else when.date()
    )
    with operation_transaction(owned, device, employee) as actor:
        if action == "start":
            if actor.team_id is None:
                raise ValidationError("员工尚未配置班组")
            if AuxiliaryWorkLog.objects.filter(
                employee=actor, status=AuxiliaryWorkLog.Status.RUNNING
            ).exists():
                raise ValidationError("已有进行中的辅助工时，请先提交")
            category = AuxiliaryWorkCategory.objects.filter(
                pk=category_id, active=True
            ).first()
            if category is None:
                raise ValidationError("请选择启用的辅助作业类别")
            log = AuxiliaryWorkLog.start(
                employee=actor,
                team=actor.team,
                category=category,
                started_at=timezone.now(),
                work_note=note,
            )
            if log.ownership_date != owned:
                raise ValidationError("班次配置发生变化，请刷新后开始")
            return log
        log = AuxiliaryWorkLog.objects.select_for_update().get(pk=log_id)
        if log.employee_id != actor.pk:
            raise ValidationError("只能操作本人辅助工时")
        if action == "submit":
            log.submit(ended_at=timezone.now(), work_note=note)
        elif action == "withdraw":
            log.withdraw(reason, at=timezone.now())
        else:
            raise ValidationError("操作无效")
        return log
