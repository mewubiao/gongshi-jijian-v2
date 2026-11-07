"""开工、暂停、报工，以及审核留痕和整组加入 / 离开。

终端入口复用这些领域方法。新单人报工冻结并行分摊工时，原始打卡工时保留；
整组任务不参与并行。实际工时不进入单人计薪公式。
TODO(计件): 计件工资 = 合格件数 × 报工时冻结的单价。完工数量就是合格件数。
"""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from dispatch.models import Dispatch
from masterdata.access import require_role, require_team_leader
from masterdata.models import Employee, Process
from masterdata.shifts import effective_shift
from masterdata.validation import ensure_aware, ensure_two_places
from reporting.audit import ReportApprovalReversal
from reporting.duration import overlap_seconds
from reporting.locking import assert_month_open
from reporting.ownership import ownership_date

_REPORT_IMMUTABLE = (
    "dispatch_id",
    "work_session_id",
    "reported_by_id",
    "previous_id",
    "submitted_at",
    "ownership_date",
    "completed_qty",
    "frozen_unit_price",
    "frozen_standard_minutes",
    "original_actual_seconds",
    "allocated_actual_seconds",
)


def shift_for(employee, work_order):
    """开工员工有班组时用班组有效班次，否则用工单工作中心。"""
    if employee.team_id:
        return effective_shift(team=employee.team)
    return effective_shift(work_center=work_order.work_center)


def employee_in_open_group(employee):
    return GroupMemberSpan.objects.filter(
        employee=employee,
        left_at__isnull=True,
        work_session__ended_at__isnull=True,
        work_session__dispatch__kind=Dispatch.Kind.GROUP,
    ).exists()


def employee_has_open_work(employee, *, excluding_session=None):
    from timekeeping.models import AuxiliaryWorkLog

    if AuxiliaryWorkLog.objects.filter(
        employee=employee, status=AuxiliaryWorkLog.Status.RUNNING
    ).exists():
        return True
    sessions = WorkSession.objects.filter(
        ended_at__isnull=True,
        started_by=employee,
    )
    spans = GroupMemberSpan.objects.filter(
        employee=employee,
        left_at__isnull=True,
        work_session__ended_at__isnull=True,
    )
    if excluding_session is not None:
        sessions = sessions.exclude(pk=excluding_session.pk)
        spans = spans.exclude(work_session=excluding_session)
    return sessions.exists() or spans.exists()


class WorkSession(models.Model):
    """一次开工。完工前不自动停表；实际秒数在报工时冻结。"""

    dispatch = models.ForeignKey(
        Dispatch,
        verbose_name="派工",
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    started_by = models.ForeignKey(
        Employee,
        verbose_name="开工操作人",
        on_delete=models.PROTECT,
        related_name="started_sessions",
        help_text="单人是被派工人，整组是开工当时的小组长。计时以这个开工时刻为准。",
    )
    started_at = models.DateTimeField("开工时间")
    ended_at = models.DateTimeField("完工时间", null=True, blank=True)
    ownership_date_snapshot = models.DateField("开工归属日", null=True, blank=True)

    class Meta:
        verbose_name = "开工"
        verbose_name_plural = "开工"
        constraints = [
            models.UniqueConstraint(
                fields=["dispatch"],
                condition=models.Q(ended_at__isnull=True),
                name="one_open_session_per_dispatch",
            )
        ]

    def __str__(self):
        return f"{self.dispatch} @ {timezone.localtime(self.started_at):%Y-%m-%d %H:%M}"

    def clean(self):
        ensure_aware(self.started_at, "started_at")
        ensure_aware(self.ended_at, "ended_at")
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValidationError("完工不能早于开工")

    def save(self, *args, **kwargs):
        if self.pk:
            old = WorkSession.objects.get(pk=self.pk)
            if old.started_at != self.started_at or old.dispatch_id != self.dispatch_id:
                raise ValidationError("开工时间和派工不能改")
            if old.started_by_id != self.started_by_id:
                raise ValidationError("开工操作人不能改")
            if old.ownership_date_snapshot != self.ownership_date_snapshot:
                raise ValidationError("开工归属日不能改")
            if old.ended_at is not None and old.ended_at != self.ended_at:
                raise ValidationError("完工时间不能改")
            for report in old.reports.all():
                assert_month_open(report.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        for report in self.reports.all():
            assert_month_open(report.ownership_date)
        if self.reports.exists():
            raise ValidationError("已有报工的开工记录不能删除")
        super().delete(*args, **kwargs)

    @classmethod
    def open(
        cls,
        *,
        dispatch,
        started_by,
        started_at=None,
        present_employees=None,
    ):
        """开工。整组默认当时列出的全部组员都在组，也可以只带其中一部分。"""
        started_at = started_at or timezone.now()
        ensure_aware(started_at, "started_at")
        dispatch.validate_roster()
        dispatch.assert_terminal_operator(started_by)
        if dispatch.reports.filter(
            status__in=[PieceReport.Status.PENDING, PieceReport.Status.APPROVED]
        ).exists():
            raise ValidationError("该任务已有待审核或已通过报工，不能重复开工")
        latest = dispatch.reports.order_by("-submitted_at", "-pk").first()
        if latest is not None and latest.status == PieceReport.Status.REJECTED:
            raise ValidationError("驳回后请修改原报工数量重新提交")
        owned = ownership_date(started_at, *shift_for(started_by, dispatch.work_order))
        if dispatch.sessions.filter(ended_at__isnull=True).exists():
            raise ValidationError("该派工已有未完工的开工")
        if dispatch.kind == Dispatch.Kind.GROUP:
            return cls._open_group(
                dispatch=dispatch,
                started_by=started_by,
                started_at=started_at,
                present_employees=present_employees,
                owned=owned,
            )
        if employee_in_open_group(started_by):
            raise ValidationError("整组任务期间不能同时开别的任务")
        return cls.objects.create(
            dispatch=dispatch,
            started_by=started_by,
            started_at=started_at,
            ownership_date_snapshot=owned,
        )

    @classmethod
    def _open_group(cls, *, dispatch, started_by, started_at, present_employees, owned):
        roster = list(dispatch.members.select_related("employee"))
        if present_employees is None:
            present = [member.employee for member in roster]
        else:
            present = list(present_employees)
        roster_ids = {member.employee_id for member in roster}
        present_ids = [employee.pk for employee in present]
        if len(present_ids) != len(set(present_ids)):
            raise ValidationError("组员不能重复")
        if any(employee_id not in roster_ids for employee_id in present_ids):
            raise ValidationError("只能让派工组员开工")
        if started_by.pk not in present_ids:
            raise ValidationError("小组长必须在开工的组员里")
        for employee in present:
            if employee_has_open_work(employee):
                raise ValidationError(
                    f"{employee.name}已有未完工任务，不能开始整组任务"
                )
        with transaction.atomic():
            session = cls.objects.create(
                dispatch=dispatch,
                started_by=started_by,
                started_at=started_at,
                ownership_date_snapshot=owned,
            )
            for employee in present:
                GroupMemberSpan.objects.create(
                    work_session=session,
                    employee=employee,
                    joined_at=started_at,
                    recorded_by=started_by,
                )
            return session

    def pause(self, *, reason, paused_at=None):
        """暂停必须写原因。整组一次暂停，时段内全组都不计工时。"""
        if self.ended_at is not None:
            raise ValidationError("已经完工，不能暂停")
        paused_at = paused_at or timezone.now()
        return PauseInterval.objects.create(
            work_session=self,
            reason=reason,
            paused_at=paused_at,
        )

    def resume(self, *, resumed_at=None):
        pause = self.pauses.filter(resumed_at__isnull=True).first()
        if pause is None:
            raise ValidationError("当前没有暂停")
        resumed_at = resumed_at or timezone.now()
        ensure_aware(resumed_at, "resumed_at")
        if resumed_at < pause.paused_at:
            raise ValidationError("恢复时间不能早于暂停时间")
        pause.resumed_at = resumed_at
        pause.save(update_fields=["resumed_at"])
        return pause

    def actual_seconds(self):
        if self.ended_at is None:
            raise ValidationError("尚未完工，不能计算实际工时")
        total = int((self.ended_at - self.started_at).total_seconds())
        for pause in PauseInterval.objects.filter(work_session=self):
            pause_end = pause.resumed_at or self.ended_at
            total -= overlap_seconds(
                self.started_at,
                self.ended_at,
                pause.paused_at,
                pause_end,
            )
        return max(total, 0)

    def finish_and_report(self, *, completed_qty, reported_by, submitted_at=None):
        """报工。冻结当时的单价和标准工时，归属日按开工时刻的开班日。"""
        if self.ended_at is not None:
            raise ValidationError("这次开工已经报过工")
        submitted_at = submitted_at or timezone.now()
        ensure_aware(submitted_at, "submitted_at")
        if submitted_at < self.started_at:
            raise ValidationError("报工时间不能早于开工时间")
        self.dispatch.assert_terminal_operator(reported_by)
        if self.dispatch.reports.filter(
            status__in=[
                PieceReport.Status.PENDING,
                PieceReport.Status.APPROVED,
            ]
        ).exists():
            raise ValidationError("该派工已有待审核或已通过的报工")
        latest = self.dispatch.reports.order_by("-submitted_at", "-pk").first()
        if latest is not None and latest.status == PieceReport.Status.REJECTED:
            raise ValidationError("驳回后请修改数量重新提交")
        bounds = shift_for(self.started_by, self.dispatch.work_order)
        ownership = self.ownership_date_snapshot or ownership_date(
            self.started_at, *bounds
        )
        assert_month_open(ownership)
        with transaction.atomic():
            for pause in self.pauses.filter(resumed_at__isnull=True):
                pause.resumed_at = submitted_at
                pause.save(update_fields=["resumed_at"])
            self.ended_at = submitted_at
            self.save(update_fields=["ended_at"])
            for span in self.member_spans.all():
                span.frozen_actual_seconds = span.actual_seconds(end=submitted_at)
                span.save(update_fields=["frozen_actual_seconds"])
            process = Process.objects.select_for_update().get(
                pk=self.dispatch.process_id
            )
            from reporting.allocation import allocated_seconds

            return PieceReport.objects.create(
                dispatch=self.dispatch,
                work_session=self,
                reported_by=reported_by,
                submitted_at=submitted_at,
                ownership_date=ownership,
                completed_qty=completed_qty,
                frozen_unit_price=process.unit_price,
                frozen_standard_minutes=process.standard_minutes,
                original_actual_seconds=self.actual_seconds(),
                allocated_actual_seconds=allocated_seconds(self)
                if self.dispatch.kind == Dispatch.Kind.SINGLE
                else None,
                status=PieceReport.Status.PENDING,
            )

    def join_member(self, *, employee, joined_at, recorded_by):
        if self.dispatch.kind != Dispatch.Kind.GROUP:
            raise ValidationError("只有整组派工可以登记加入")
        if self.ended_at is not None:
            raise ValidationError("已经报工，不能再登记加入")
        if recorded_by.pk != self.dispatch.current_leader_id:
            raise ValidationError("由当前小组长登记加入或离开")
        if not self.dispatch.members.filter(employee=employee).exists():
            raise ValidationError("只能登记派工时列出的组员")
        if self.member_spans.filter(employee=employee, left_at__isnull=True).exists():
            raise ValidationError("该组员已在组内")
        if employee_has_open_work(employee, excluding_session=self):
            raise ValidationError("该组员已有其他未完工任务")
        return GroupMemberSpan.objects.create(
            work_session=self,
            employee=employee,
            joined_at=joined_at,
            recorded_by=recorded_by,
        )


class PauseInterval(models.Model):
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name="开工",
        on_delete=models.PROTECT,
        related_name="pauses",
    )
    reason = models.TextField("暂停原因")
    paused_at = models.DateTimeField("暂停时间")
    resumed_at = models.DateTimeField("恢复时间", null=True, blank=True)

    class Meta:
        verbose_name = "暂停"
        verbose_name_plural = "暂停"
        ordering = ["paused_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="pause_reason_present",
            ),
            models.UniqueConstraint(
                fields=["work_session"],
                condition=models.Q(resumed_at__isnull=True),
                name="one_open_pause_per_session",
            ),
        ]

    def __str__(self):
        return self.reason

    def clean(self):
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "暂停必须填写原因"})
        ensure_aware(self.paused_at, "paused_at")
        ensure_aware(self.resumed_at, "resumed_at")
        session = self.work_session if self.work_session_id else None
        if session is not None and self.paused_at is not None:
            if self._state.adding and session.ended_at is not None:
                raise ValidationError("已经完工，不能暂停")
            if self.paused_at < session.started_at:
                raise ValidationError("暂停不能早于开工")
        if (
            self.resumed_at is not None
            and self.paused_at is not None
            and self.resumed_at < self.paused_at
        ):
            raise ValidationError("恢复时间不能早于暂停时间")
        if session is not None and self.paused_at is not None:
            others = PauseInterval.objects.filter(work_session=session)
            if self.pk:
                others = others.exclude(pk=self.pk)
            for other in others:
                if _open_overlap(
                    self.paused_at,
                    self.resumed_at,
                    other.paused_at,
                    other.resumed_at,
                ):
                    raise ValidationError("暂停时段不能重叠")

    def save(self, *args, **kwargs):
        self.reason = (self.reason or "").strip()
        if self.pk:
            old = PauseInterval.objects.get(pk=self.pk)
            if (
                old.paused_at != self.paused_at
                or old.reason != self.reason
                or old.work_session_id != self.work_session_id
            ):
                raise ValidationError("暂停记录不能改")
            if old.resumed_at is not None and old.resumed_at != self.resumed_at:
                raise ValidationError("已结束的暂停不能改")
            for report in old.work_session.reports.all():
                assert_month_open(report.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.work_session.ended_at is not None:
            raise ValidationError("完工后不能删除暂停")
        for report in self.work_session.reports.all():
            assert_month_open(report.ownership_date)
        super().delete(*args, **kwargs)


class GroupMemberSpan(models.Model):
    """整组组员的一段在组时间。离开或加入按登记时刻截断工时。"""

    work_session = models.ForeignKey(
        WorkSession,
        verbose_name="开工",
        on_delete=models.PROTECT,
        related_name="member_spans",
    )
    employee = models.ForeignKey(
        Employee,
        verbose_name="组员",
        on_delete=models.PROTECT,
        related_name="group_spans",
    )
    joined_at = models.DateTimeField("加入时间")
    left_at = models.DateTimeField("离开时间", null=True, blank=True)
    recorded_by = models.ForeignKey(
        Employee,
        verbose_name="登记人",
        on_delete=models.PROTECT,
        related_name="recorded_group_spans",
        help_text="当前小组长代为登记。",
    )
    frozen_actual_seconds = models.PositiveIntegerField(
        "冻结实际工时（秒）",
        null=True,
        blank=True,
        help_text="报工时写下，已扣除落在本段里的暂停。结算分配用这个数。",
    )

    class Meta:
        verbose_name = "整组在组登记"
        verbose_name_plural = "整组在组登记"
        ordering = ["joined_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["work_session", "employee"],
                condition=models.Q(left_at__isnull=True),
                name="one_open_span_per_member",
            )
        ]

    def __str__(self):
        return f"{self.employee.name} {self.joined_at:%H:%M}"

    def clean(self):
        ensure_aware(self.joined_at, "joined_at")
        ensure_aware(self.left_at, "left_at")
        session = self.work_session if self.work_session_id else None
        if session is None:
            return
        if session.dispatch.kind != Dispatch.Kind.GROUP:
            raise ValidationError("只有整组派工登记加入和离开")
        if self._state.adding and session.ended_at is not None:
            raise ValidationError("已经报工，不能再登记加入")
        if not session.dispatch.members.filter(employee_id=self.employee_id).exists():
            raise ValidationError("只能登记派工时列出的组员")
        if self.joined_at and self.joined_at < session.started_at:
            raise ValidationError("加入时间不能早于开工")
        if (
            self.left_at is not None
            and self.joined_at is not None
            and self.left_at < self.joined_at
        ):
            raise ValidationError("离开时间不能早于加入时间")

    def save(self, *args, **kwargs):
        if self.pk:
            old = GroupMemberSpan.objects.get(pk=self.pk)
            if old.frozen_actual_seconds is not None:
                watched = (
                    "employee_id",
                    "joined_at",
                    "left_at",
                    "recorded_by_id",
                    "frozen_actual_seconds",
                    "work_session_id",
                )
                if any(
                    getattr(old, field) != getattr(self, field) for field in watched
                ):
                    raise ValidationError("报工后已冻结的在组时段不能修改")
            elif (
                old.joined_at != self.joined_at
                or old.employee_id != self.employee_id
                or old.work_session_id != self.work_session_id
                or old.recorded_by_id != self.recorded_by_id
            ):
                raise ValidationError("加入登记不能改")
            elif old.left_at is not None and old.left_at != self.left_at:
                raise ValidationError("离开时间不能改")
            for report in old.work_session.reports.all():
                assert_month_open(report.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)

    def leave(self, *, at, recorded_by):
        if self.left_at is not None:
            raise ValidationError("已经登记离开")
        if self.work_session.ended_at is not None:
            raise ValidationError("已经报工，不能再登记离开")
        dispatch = self.work_session.dispatch
        if recorded_by.pk != dispatch.current_leader_id:
            raise ValidationError("由当前小组长登记加入或离开")
        if dispatch.current_leader_id == self.employee_id:
            raise ValidationError("小组长离开前必须先把小组长身份移交给组内另一人")
        ensure_aware(at, "left_at")
        if at < self.joined_at:
            raise ValidationError("离开时间不能早于加入时间")
        self.left_at = at
        self.save(update_fields=["left_at"])

    def actual_seconds(self, *, end=None):
        end_at = self.left_at or end or self.work_session.ended_at
        if end_at is None:
            raise ValidationError("尚未离开或完工，不能计算工时")
        total = int((end_at - self.joined_at).total_seconds())
        for pause in PauseInterval.objects.filter(work_session_id=self.work_session_id):
            pause_end = pause.resumed_at or end_at
            total -= overlap_seconds(
                self.joined_at,
                end_at,
                pause.paused_at,
                pause_end,
            )
        return max(total, 0)


class PieceReport(models.Model):
    """一条报工。原始数量、单价、标准工时和实际秒数写出后不再覆盖。"""

    class Status(models.TextChoices):
        PENDING = "pending", "待审核"
        WITHDRAWN = "withdrawn", "已撤回"
        REJECTED = "rejected", "已驳回"
        APPROVED = "approved", "已审核通过"

    dispatch = models.ForeignKey(
        Dispatch,
        verbose_name="派工",
        on_delete=models.PROTECT,
        related_name="reports",
    )
    work_session = models.ForeignKey(
        WorkSession,
        verbose_name="开工",
        on_delete=models.PROTECT,
        related_name="reports",
    )
    reported_by = models.ForeignKey(
        Employee,
        verbose_name="报工人",
        on_delete=models.PROTECT,
        related_name="piece_reports",
    )
    previous = models.ForeignKey(
        "self",
        verbose_name="驳回前的报工",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resubmissions",
    )
    submitted_at = models.DateTimeField("报工时间")
    ownership_date = models.DateField(
        "归属日",
        help_text="按开工时刻的开班日。整段夜班归开班日，跨月夜班仍归开班日所在月。",
    )
    completed_qty = models.DecimalField(
        "完工数量",
        max_digits=12,
        decimal_places=2,
        help_text="完工数量就是合格件数，不另存一个合格数。",
    )
    frozen_unit_price = models.DecimalField(
        "冻结单价",
        max_digits=12,
        decimal_places=2,
        help_text="报工提交时从工序抄下。已派未报的任务也要到这一刻才冻结。",
    )
    frozen_standard_minutes = models.DecimalField(
        "冻结标准工时（分钟）",
        max_digits=10,
        decimal_places=2,
    )
    original_actual_seconds = models.PositiveIntegerField(
        "原始实际工时（秒）",
        help_text="开工到报工，扣除暂停。审核要改工时另存修正值。",
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    allocated_actual_seconds = models.PositiveIntegerField(
        "冻结分摊工时（秒）", null=True, blank=True
    )
    withdraw_reason = models.TextField("撤回原因", blank=True, default="")
    withdrawn_at = models.DateTimeField("撤回时间", null=True, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        verbose_name="最近审核通过人",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_reports",
        help_text="班组长。反审核后状态回到待审核，这里仍保留最近一次通过记录。",
    )
    approved_at = models.DateTimeField("最近审核通过时间", null=True, blank=True)

    class Meta:
        verbose_name = "报工"
        verbose_name_plural = "报工"
        permissions = [
            ("review_piecereport", "审核、修正和驳回报工"),
            ("reverse_piecereport", "反审核报工"),
        ]
        indexes = [
            models.Index(
                fields=["status", "ownership_date"],
                name="piece_report_status_day",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(completed_qty__gte=0),
                name="piece_report_qty_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(frozen_unit_price__gte=0),
                name="piece_report_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(frozen_standard_minutes__gte=0),
                name="piece_report_standard_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["pending", "withdrawn", "rejected", "approved"]
                ),
                name="piece_report_status_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="withdrawn") | ~models.Q(withdraw_reason=""),
                name="piece_report_withdraw_reason",
            ),
            models.UniqueConstraint(
                fields=["dispatch"],
                condition=models.Q(status__in=["pending", "approved"]),
                name="one_active_report_per_dispatch",
            ),
        ]

    def __str__(self):
        return f"{self.dispatch} {self.completed_qty}"

    def clean(self):
        ensure_two_places(self.completed_qty, "completed_qty", "完工数量")
        ensure_two_places(self.frozen_unit_price, "frozen_unit_price", "单价")
        ensure_two_places(
            self.frozen_standard_minutes,
            "frozen_standard_minutes",
            "标准工时",
        )
        ensure_aware(self.submitted_at, "submitted_at")
        ensure_aware(self.withdrawn_at, "withdrawn_at")
        ensure_aware(self.approved_at, "approved_at")
        if self.completed_qty is not None and self.completed_qty < 0:
            raise ValidationError({"completed_qty": "完工数量不能为负"})
        if self.status == self.Status.WITHDRAWN:
            if not (self.withdraw_reason or "").strip():
                raise ValidationError({"withdraw_reason": "撤回必须选择或填写原因"})
            if self.withdrawn_at is None:
                raise ValidationError("撤回必须记下时间")
        if self.status == self.Status.APPROVED and not self.approved_by_id:
            raise ValidationError("审核通过必须记下审核人")
        if self.approved_by_id and (
            self._state.adding
            or not PieceReport.objects.filter(
                pk=self.pk, approved_by_id=self.approved_by_id
            ).exists()
        ):
            if self.approved_by.role != Employee.Role.TEAM_LEADER:
                raise ValidationError({"approved_by": "审核由班组长执行"})
        if self.previous_id and self.previous.status != self.Status.REJECTED:
            raise ValidationError("只能接在已驳回的报工后面重新提交")

    def save(self, *args, **kwargs):
        self.withdraw_reason = (self.withdraw_reason or "").strip()
        if self.pk:
            old = PieceReport.objects.get(pk=self.pk)
            assert_month_open(old.ownership_date)
            for field in _REPORT_IMMUTABLE:
                if getattr(old, field) != getattr(self, field):
                    raise ValidationError("报工原始记录不能覆盖")
        else:
            assert_month_open(self.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        assert_month_open(self.ownership_date)
        raise ValidationError("报工记录不能删除")

    def withdraw(self, reason, *, at=None):
        if self.status != self.Status.PENDING:
            raise ValidationError("只能撤回未审核的报工")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("撤回必须选择或填写原因")
        at = at or timezone.now()
        ensure_aware(at, "withdrawn_at")
        self.status = self.Status.WITHDRAWN
        self.withdraw_reason = reason
        self.withdrawn_at = at
        self.save()

    def reject(self, *, reason, rejected_by, at=None):
        require_team_leader(rejected_by, [self.dispatch.team_id])
        if self.status != self.Status.PENDING:
            raise ValidationError("只有待审核的报工可以驳回")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("驳回必须填写原因")
        at = at or timezone.now()
        with transaction.atomic():
            ReportRejection.objects.create(
                piece_report=self,
                reason=reason,
                rejected_by=rejected_by,
                rejected_at=at,
            )
            self.status = self.Status.REJECTED
            self.save(update_fields=["status"])

    def resubmit(self, *, completed_qty, reported_by, submitted_at=None):
        """驳回后只改数量重新提交。单价和标准工时沿用首次冻结值。"""
        if self.status != self.Status.REJECTED:
            raise ValidationError("只有驳回的报工可以修改数量后重新提交")
        if self.resubmissions.exists():
            raise ValidationError("这份驳回已经重新提交过")
        if reported_by.pk != self.reported_by_id:
            raise ValidationError("由原报工人修改数量后重新提交")
        submitted_at = submitted_at or timezone.now()
        return PieceReport.objects.create(
            dispatch=self.dispatch,
            work_session=self.work_session,
            reported_by=reported_by,
            previous=self,
            submitted_at=submitted_at,
            ownership_date=self.ownership_date,
            completed_qty=completed_qty,
            frozen_unit_price=self.frozen_unit_price,
            frozen_standard_minutes=self.frozen_standard_minutes,
            original_actual_seconds=self.original_actual_seconds,
            allocated_actual_seconds=self.allocated_actual_seconds,
            status=self.Status.PENDING,
        )

    def correct(
        self,
        *,
        reason,
        corrected_by,
        corrected_qty=None,
        corrected_actual_seconds=None,
        at=None,
    ):
        """修正值另存，不改原始完工数量和原始实际工时。"""
        require_team_leader(corrected_by, [self.dispatch.team_id])
        if self.status != self.Status.PENDING:
            raise ValidationError("只能在审核待审报工时修正")
        return ReportCorrection.objects.create(
            piece_report=self,
            reason=reason,
            corrected_by=corrected_by,
            corrected_qty=corrected_qty,
            corrected_actual_seconds=corrected_actual_seconds,
            corrected_at=at or timezone.now(),
        )

    def approve(self, *, reviewer, at=None):
        """第一期允许班组长审核自己派的、自己报的任务。"""
        require_team_leader(reviewer, [self.dispatch.team_id])
        if self.status != self.Status.PENDING:
            raise ValidationError("只有待审核的报工可以通过")
        at = at or timezone.now()
        ensure_aware(at, "approved_at")
        self.status = self.Status.APPROVED
        self.approved_by = reviewer
        self.approved_at = at
        self.save()

    def reverse_approval(self, *, actor, at=None):
        """系统管理员在未锁账月份反审核；状态和操作留痕一并提交。"""
        from settlement.models import SettlementPeriod

        require_role(actor, Employee.Role.ADMIN, "反审核仅限启用的系统管理员")
        at = at or timezone.now()
        ensure_aware(at, "reversed_at")
        with transaction.atomic():
            period, _ = SettlementPeriod.objects.get_or_create(
                year=self.ownership_date.year, month=self.ownership_date.month
            )
            SettlementPeriod.objects.select_for_update().get(pk=period.pk)
            report = PieceReport.objects.select_for_update().get(pk=self.pk)
            assert_month_open(report.ownership_date)
            if report.status != self.Status.APPROVED:
                raise ValidationError("只有已审核通过的报工可以反审核")
            report.status = self.Status.PENDING
            report.save(update_fields=["status"])
            ReportApprovalReversal.objects.create(
                piece_report=report,
                reversed_by=actor,
                reversed_at=at,
                approved_by=report.approved_by,
                approved_at=report.approved_at,
            )
        self.refresh_from_db()

    def effective_completed_qty(self):
        latest = (
            self.corrections.exclude(corrected_qty=None)
            .order_by("-corrected_at", "-pk")
            .first()
        )
        if latest is None:
            return self.completed_qty
        return latest.corrected_qty

    def effective_actual_seconds(self):
        latest = (
            self.corrections.exclude(corrected_actual_seconds=None)
            .order_by("-corrected_at", "-pk")
            .first()
        )
        if latest is None:
            return (
                self.allocated_actual_seconds
                if self.allocated_actual_seconds is not None
                else self.original_actual_seconds
            )
        return latest.corrected_actual_seconds


class ReportCorrection(models.Model):
    piece_report = models.ForeignKey(
        PieceReport,
        verbose_name="报工",
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    corrected_qty = models.DecimalField(
        "修正完工数量",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    corrected_actual_seconds = models.PositiveIntegerField(
        "修正实际工时（秒）",
        null=True,
        blank=True,
    )
    reason = models.TextField("修正原因")
    corrected_by = models.ForeignKey(
        Employee,
        verbose_name="修正人",
        on_delete=models.PROTECT,
        related_name="report_corrections",
    )
    corrected_at = models.DateTimeField("修正时间")

    class Meta:
        verbose_name = "审核修正"
        verbose_name_plural = "审核修正"
        ordering = ["corrected_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="report_correction_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(corrected_qty__isnull=False)
                | models.Q(corrected_actual_seconds__isnull=False),
                name="report_correction_has_value",
            ),
        ]

    def __str__(self):
        return self.reason

    def clean(self):
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "修正必须填写原因"})
        require_team_leader(self.corrected_by, [self.piece_report.dispatch.team_id])
        ensure_aware(self.corrected_at, "corrected_at")
        ensure_two_places(self.corrected_qty, "corrected_qty", "修正完工数量")
        if self.corrected_qty is None and self.corrected_actual_seconds is None:
            raise ValidationError("须修正工时或完工数量")
        if self.corrected_qty is not None and self.corrected_qty < 0:
            raise ValidationError({"corrected_qty": "修正完工数量不能为负"})
        if self.corrected_by_id and (
            self.corrected_by.role != Employee.Role.TEAM_LEADER
        ):
            raise ValidationError({"corrected_by": "修正由班组长执行"})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("审核修正不能修改")
        self.reason = (self.reason or "").strip()
        if self.piece_report_id:
            assert_month_open(self.piece_report.ownership_date)
            if self.piece_report.status != PieceReport.Status.PENDING:
                raise ValidationError("只能在待审核时修正")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        assert_month_open(self.piece_report.ownership_date)
        raise ValidationError("审核修正不能删除")


class ReportRejection(models.Model):
    piece_report = models.ForeignKey(
        PieceReport,
        verbose_name="报工",
        on_delete=models.PROTECT,
        related_name="rejections",
    )
    reason = models.TextField("驳回原因")
    rejected_by = models.ForeignKey(
        Employee,
        verbose_name="驳回人",
        on_delete=models.PROTECT,
        related_name="report_rejections",
    )
    rejected_at = models.DateTimeField("驳回时间")

    class Meta:
        verbose_name = "驳回记录"
        verbose_name_plural = "驳回记录"
        ordering = ["rejected_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="report_rejection_reason",
            )
        ]

    def __str__(self):
        return self.reason

    def clean(self):
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "驳回必须填写原因"})
        require_team_leader(self.rejected_by, [self.piece_report.dispatch.team_id])
        ensure_aware(self.rejected_at, "rejected_at")
        if self.rejected_by_id and (self.rejected_by.role != Employee.Role.TEAM_LEADER):
            raise ValidationError({"rejected_by": "驳回由班组长执行"})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("驳回记录不能修改")
        self.reason = (self.reason or "").strip()
        if self.piece_report_id:
            assert_month_open(self.piece_report.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        assert_month_open(self.piece_report.ownership_date)
        raise ValidationError("驳回记录不能删除")


def _open_overlap(start_a, end_a, start_b, end_b):
    """结束时间为空表示尚未恢复，视为一直延续。"""
    if end_a is not None and end_a <= start_b:
        return False
    if end_b is not None and end_b <= start_a:
        return False
    return True
