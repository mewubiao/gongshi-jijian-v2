"""月结期间。只做锁账、解锁和留痕，不算工资。

TODO(月结): 按归属月份汇总已审核通过的报工。
金额 = 合格件数 × 报工冻结单价，保留到分。
整组先算整组金额，再按各组员冻结的实际工时占比分配，
尾差给派工时列出的第一位（DispatchMember.sequence = 1）。
第一期不含扣款、补贴。导出 Excel 和页面查询以后再做。
有未审核（待审核）报工时不能锁账；已撤回、已驳回不挡住锁账。
"""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

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

    def lock(self, actor, *, at=None):
        self._require_admin(actor, "锁账由系统管理员执行")
        at = at or timezone.now()
        ensure_aware(at, "locked_at")
        from reporting.models import PieceReport

        with transaction.atomic():
            period = SettlementPeriod.objects.select_for_update().get(pk=self.pk)
            if period.status == self.Status.LOCKED:
                raise ValidationError("该月已经锁账")
            pending = PieceReport.objects.filter(
                status=PieceReport.Status.PENDING,
                ownership_date__year=period.year,
                ownership_date__month=period.month,
            ).exists()
            if pending:
                raise ValidationError("该月还有未审核报工，不能锁账")
            period.status = self.Status.LOCKED
            period.locked_by = actor
            period.locked_at = at
            period.save(allow_status_change=True)
            SettlementLockEvent.objects.create(
                period=period,
                action=SettlementLockEvent.Action.LOCK,
                actor=actor,
                occurred_at=at,
                reason="",
            )
        self.refresh_from_db()

    def unlock(self, actor, reason, *, at=None):
        self._require_admin(actor, "解锁由系统管理员执行")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("解锁必须填写原因")
        at = at or timezone.now()
        ensure_aware(at, "occurred_at")
        with transaction.atomic():
            period = SettlementPeriod.objects.select_for_update().get(pk=self.pk)
            if period.status != self.Status.LOCKED:
                raise ValidationError("该月没有锁账")
            period.status = self.Status.OPEN
            period.locked_by = None
            period.locked_at = None
            period.save(allow_status_change=True)
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
        if actor is None or actor.role != Employee.Role.ADMIN:
            raise ValidationError(message)


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
