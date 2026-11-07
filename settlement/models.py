"""月结期间、锁账留痕、计件金额快照和辅助工时汇总。"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from masterdata.access import require_role
from masterdata.models import Employee
from masterdata.validation import ensure_aware


class SettlementPeriod(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "未锁"
        LOCKED = "locked", "已锁账"

    year = models.PositiveSmallIntegerField("年")
    month = models.PositiveSmallIntegerField("月")
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    locked_by = models.ForeignKey(
        Employee,
        verbose_name="最近锁账人",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="locked_periods",
    )
    locked_at = models.DateTimeField("最近锁账时间", null=True, blank=True)

    class Meta:
        verbose_name = "月结期间"
        verbose_name_plural = "月结期间"
        permissions = [
            ("lock_settlementperiod", "月结锁账"),
            ("unlock_settlementperiod", "月结解锁"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "month"],
                name="settlement_period_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(month__gte=1, month__lte=12),
                name="settlement_month_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "locked"]),
                name="settlement_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.year}-{self.month:02d}（{self.get_status_display()}）"

    def clean(self):
        if self.month is not None and not 1 <= self.month <= 12:
            raise ValidationError({"month": "月份必须在 1 到 12 之间"})
        ensure_aware(self.locked_at, "locked_at")

    def save(self, *args, allow_status_change=False, **kwargs):
        if self.pk:
            old = SettlementPeriod.objects.get(pk=self.pk)
            if old.year != self.year or old.month != self.month:
                raise ValidationError("月结期间的年月不能修改")
            changed = (
                old.status != self.status
                or old.locked_by_id != self.locked_by_id
                or old.locked_at != self.locked_at
            )
            if changed and not allow_status_change:
                raise ValidationError("锁账状态请通过锁账或解锁方法变更")
        self.full_clean()
        super().save(*args, **kwargs)

    def lock(self, actor, *, at=None, expected_revision=None):
        self._require_admin(actor, "锁账由系统管理员执行")
        at = at or timezone.now()
        ensure_aware(at, "locked_at")
        from settlement.checks import assert_ready_to_lock
        from settlement.hours import approved_auxiliary_minutes
        from settlement.piecework import calculate_piecework
        from settlement.revisions import check_revision

        with transaction.atomic():
            period = SettlementPeriod.objects.select_for_update().get(pk=self.pk)
            if period.status == self.Status.LOCKED:
                raise ValidationError("该月已经锁账")
            assert_ready_to_lock(period.year, period.month)
            entries = calculate_piecework(period.year, period.month)
            minutes = approved_auxiliary_minutes(period.year, period.month)
            check_revision(period, expected_revision, entries, minutes)
            period.status = self.Status.LOCKED
            period.locked_by = actor
            period.locked_at = at
            period.save(allow_status_change=True)
            period.write_auxiliary_hour_summaries(minutes)
            event = SettlementLockEvent.objects.create(
                period=period,
                action=SettlementLockEvent.Action.LOCK,
                actor=actor,
                occurred_at=at,
                reason="",
                has_piecework_snapshot=True,
            )
            for entry in entries:
                entry.lock_event = event
            PieceworkAllocation.objects.bulk_create(entries, batch_size=500)
        self.refresh_from_db()

    def unlock(self, actor, reason, *, at=None, expected_revision=None):
        self._require_admin(actor, "解锁由系统管理员执行")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("解锁必须填写原因")
        at = at or timezone.now()
        ensure_aware(at, "occurred_at")
        from settlement.revisions import check_revision

        with transaction.atomic():
            period = SettlementPeriod.objects.select_for_update().get(pk=self.pk)
            if period.status != self.Status.LOCKED:
                raise ValidationError("该月没有锁账")
            check_revision(period, expected_revision)
            period.status = self.Status.OPEN
            period.locked_by = None
            period.locked_at = None
            period.save(allow_status_change=True)
            period.auxiliary_hour_summaries.all().delete()
            SettlementLockEvent.objects.create(
                period=period,
                action=SettlementLockEvent.Action.UNLOCK,
                actor=actor,
                occurred_at=at,
                reason=reason,
            )
        self.refresh_from_db()

    @staticmethod
    def _require_admin(actor, message):
        require_role(actor, Employee.Role.ADMIN, message)

    def write_auxiliary_hour_summaries(self, totals):
        """按员工重写本月已审核辅助工时。不写单价和金额。"""
        self.auxiliary_hour_summaries.all().delete()
        AuxiliaryHourSummary.objects.bulk_create(
            [
                AuxiliaryHourSummary(
                    period=self,
                    employee_id=employee_id,
                    total_minutes=minutes,
                )
                for employee_id, minutes in totals.items()
            ],
            batch_size=500,
        )


class SettlementLockEvent(models.Model):
    """锁账和解锁的追加记录。解锁必须有原因、人和时间。"""

    class Action(models.TextChoices):
        LOCK = "lock", "锁账"
        UNLOCK = "unlock", "解锁"

    period = models.ForeignKey(
        SettlementPeriod,
        verbose_name="月结期间",
        on_delete=models.PROTECT,
        related_name="events",
    )
    action = models.CharField("动作", max_length=16, choices=Action.choices)
    actor = models.ForeignKey(
        Employee,
        verbose_name="操作人",
        on_delete=models.PROTECT,
        related_name="settlement_lock_events",
    )
    occurred_at = models.DateTimeField("操作时间")
    reason = models.TextField("原因", blank=True, default="")
    has_piecework_snapshot = models.BooleanField("已生成计件快照", default=False)

    class Meta:
        verbose_name = "锁账留痕"
        verbose_name_plural = "锁账留痕"
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=["lock", "unlock"]),
                name="settlement_event_action_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(action="unlock") | ~models.Q(reason=""),
                name="settlement_unlock_reason_required",
            ),
        ]

    def __str__(self):
        return f"{self.period} {self.get_action_display()}"

    def clean(self):
        self.reason = (self.reason or "").strip()
        ensure_aware(self.occurred_at, "occurred_at")
        if self.action == self.Action.UNLOCK and not self.reason:
            raise ValidationError({"reason": "解锁必须填写原因"})
        if self.actor_id and self.actor.role != Employee.Role.ADMIN:
            raise ValidationError({"actor": "锁账和解锁由系统管理员执行"})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("锁账留痕不能修改")
        self.reason = (self.reason or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("锁账留痕不能删除")


class AuxiliaryHourSummary(models.Model):
    """某员工在该归属月已审核通过的辅助工时合计。只记分钟。"""

    period = models.ForeignKey(
        SettlementPeriod,
        verbose_name="月结期间",
        on_delete=models.PROTECT,
        related_name="auxiliary_hour_summaries",
    )
    employee = models.ForeignKey(
        Employee,
        verbose_name="员工",
        on_delete=models.PROTECT,
        related_name="auxiliary_hour_summaries",
    )
    total_minutes = models.DecimalField(
        "合计分钟",
        max_digits=22,
        decimal_places=4,
    )
    unit_price = models.DecimalField(
        "单价",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="预留。锁账汇总不填写，不参与计算。",
    )
    amount = models.DecimalField(
        "金额",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="预留。锁账汇总不填写，不参与计算。",
    )

    class Meta:
        verbose_name = "辅助工时月结"
        verbose_name_plural = "辅助工时月结"
        constraints = [
            models.UniqueConstraint(
                fields=["period", "employee"],
                name="aux_hour_summary_employee",
            )
        ]

    def __str__(self):
        return f"{self.period} {self.employee} {self.total_minutes}"


class PieceworkAllocation(models.Model):
    """一次锁账下，一份报工分给一名员工的金额和计算依据。"""

    lock_event = models.ForeignKey(
        SettlementLockEvent, on_delete=models.PROTECT, related_name="piecework_entries"
    )
    report = models.ForeignKey("reporting.PieceReport", on_delete=models.PROTECT)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    employee_code = models.CharField(max_length=32)
    employee_name = models.CharField(max_length=100)
    ownership_date = models.DateField()
    order_no = models.CharField(max_length=64)
    process_name = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    group_seconds = models.PositiveBigIntegerField(null=True)
    amount_cents = models.PositiveBigIntegerField()

    class Meta:
        verbose_name = "计件结算明细"
        verbose_name_plural = "计件结算明细"
        ordering = ("ownership_date", "report_id", "employee_code", "employee_id")
        constraints = [
            models.UniqueConstraint(
                fields=["lock_event", "report", "employee"],
                name="unique_piecework_share",
            ),
        ]

    @property
    def amount(self):
        return Decimal(self.amount_cents).scaleb(-2)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("计件结算快照不能修改")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("计件结算快照不能删除")
