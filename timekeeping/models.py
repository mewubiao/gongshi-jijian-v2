"""辅助工时。归属日、审核和锁账与计件报工同一套。

已审核通过的记录按员工、归属月份计入月结，只汇总工时。
unit_price / amount 可空，留给以后计价，一期不参与计算。
固定休息不自动扣；fixed_break_overlap_seconds 默认 0。
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from masterdata.models import Employee, Team
from masterdata.shifts import effective_shift
from masterdata.validation import ensure_aware, ensure_two_places
from reporting.locking import assert_month_open
from reporting.ownership import classify_shift

FOUR = Decimal("0.0001")


def _minutes(seconds):
    return (Decimal(seconds) / Decimal(60)).quantize(FOUR)


class AuxiliaryWorkCategory(models.Model):
    code = models.CharField("辅助作业类别编码", max_length=32, unique=True)
    name = models.CharField("辅助作业类别名称", max_length=100)
    active = models.BooleanField("启用", default=True, db_index=True)
    note_required = models.BooleanField("必须填写说明", default=False)

    class Meta:
        verbose_name = "辅助作业类别"
        verbose_name_plural = "辅助作业类别"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class AuxiliaryWorkLog(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "进行中"
        PENDING_REVIEW = "PENDING_REVIEW", "待审核"
        WITHDRAWN = "WITHDRAWN", "已撤回"
        REJECTED = "REJECTED", "已驳回"
        APPROVED = "APPROVED", "已审核"

    employee = models.ForeignKey(
        Employee,
        verbose_name="员工",
        on_delete=models.PROTECT,
        related_name="auxiliary_work_logs",
    )
    team = models.ForeignKey(
        Team,
        verbose_name="班组",
        on_delete=models.PROTECT,
        related_name="auxiliary_work_logs",
    )
    category = models.ForeignKey(
        AuxiliaryWorkCategory,
        verbose_name="辅助作业类别",
        on_delete=models.PROTECT,
        related_name="work_logs",
    )
    category_name_snapshot = models.CharField("类别名称快照", max_length=100)
    ownership_date = models.DateField(
        "归属日",
        db_index=True,
        help_text="与计件报工相同：按开工时刻的开班日，夜班跨日仍归开班日。",
    )
    shift_name_snapshot = models.CharField(
        "班次名称快照",
        max_length=50,
        blank=True,
        default="",
        help_text="白班或夜班。班次空隙时为空。",
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
        db_index=True,
    )
    started_at = models.DateTimeField("开始时间", db_index=True)
    ended_at = models.DateTimeField("结束时间", null=True, blank=True)
    work_note = models.CharField("作业说明", max_length=500, blank=True, default="")
    raw_seconds = models.PositiveBigIntegerField("原始秒数", default=0)
    fixed_break_overlap_seconds = models.PositiveBigIntegerField(
        "固定休息重叠秒数",
        default=0,
        help_text="一期不扣固定休息，保持 0。",
    )
    effective_minutes = models.DecimalField(
        "有效人工分钟",
        max_digits=22,
        decimal_places=4,
        default=0,
    )
    original_effective_minutes = models.DecimalField(
        "原始有效人工分钟",
        max_digits=22,
        decimal_places=4,
        null=True,
        blank=True,
    )
    source = models.CharField("来源", max_length=30, default="TERMINAL")
    withdraw_reason = models.TextField("撤回原因", blank=True, default="")
    withdrawn_at = models.DateTimeField("撤回时间", null=True, blank=True)
    approved_by = models.ForeignKey(
        Employee,
        verbose_name="审核人",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_auxiliary_work_logs",
    )
    approved_at = models.DateTimeField("审核时间", null=True, blank=True)
    unit_price = models.DecimalField(
        "单价",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="预留。一期月结只汇总工时，不读取这个单价。",
    )
    amount = models.DecimalField(
        "金额",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="预留。一期不计算金额。",
    )
    created_at = models.DateTimeField("创建时间", default=timezone.now)

    class Meta:
        verbose_name = "辅助作业记录"
        verbose_name_plural = "辅助作业记录"
        ordering = ["-started_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(status="RUNNING"),
                name="uniq_running_auxiliary_per_employee",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="WITHDRAWN") | ~models.Q(withdraw_reason=""),
                name="aux_withdraw_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["team", "status"], name="aux_team_status_idx"),
            models.Index(fields=["employee", "status"], name="aux_employee_status_idx"),
            models.Index(
                fields=["status", "ownership_date"],
                name="aux_status_day_idx",
            ),
        ]

    def __str__(self):
        return f"{self.employee.code} {self.category_name_snapshot}"

    def clean(self):
        ensure_aware(self.started_at, "started_at")
        ensure_aware(self.ended_at, "ended_at")
        ensure_aware(self.withdrawn_at, "withdrawn_at")
        ensure_aware(self.approved_at, "approved_at")
        ensure_two_places(self.unit_price, "unit_price", "单价")
        ensure_two_places(self.amount, "amount", "金额")
        if self.category_id and not self.category.active and self._state.adding:
            raise ValidationError("辅助作业类别未启用")
        if self.ended_at and self.started_at and self.ended_at < self.started_at:
            raise ValidationError("结束不能早于开始")
        if (
            self.status == self.Status.WITHDRAWN
            and not (self.withdraw_reason or "").strip()
        ):
            raise ValidationError({"withdraw_reason": "撤回必须填写原因"})
        if self.status == self.Status.APPROVED and not self.approved_by_id:
            raise ValidationError("审核通过必须记下审核人")
        if self.approved_by_id and (self.approved_by.role != Employee.Role.TEAM_LEADER):
            raise ValidationError({"approved_by": "审核由班组长执行"})

    def save(self, *args, **kwargs):
        self.withdraw_reason = (self.withdraw_reason or "").strip()
        if self.pk:
            old = AuxiliaryWorkLog.objects.get(pk=self.pk)
            assert_month_open(old.ownership_date)
            if old.ownership_date != self.ownership_date or (
                old.started_at != self.started_at
            ):
                raise ValidationError("开工时间和归属日不能改")
            if old.status != self.Status.RUNNING and (
                old.original_effective_minutes != self.original_effective_minutes
                or old.raw_seconds != self.raw_seconds
                or old.employee_id != self.employee_id
                or old.category_id != self.category_id
            ):
                raise ValidationError("辅助工时原始记录不能覆盖")
        else:
            assert_month_open(self.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)

    def effective_minutes_for_review(self):
        latest = self.corrections.order_by("-corrected_at", "-pk").first()
        if latest is None:
            return self.original_effective_minutes or self.effective_minutes
        return latest.corrected_effective_minutes

    @classmethod
    def start(
        cls,
        *,
        employee,
        team,
        category,
        started_at=None,
        work_note="",
        source="TERMINAL",
    ):
        started_at = started_at or timezone.now()
        ensure_aware(started_at, "started_at")
        if not employee.active:
            raise ValidationError("员工未启用")
        owned, shift_name = classify_shift(started_at, *effective_shift(team=team))
        return cls.objects.create(
            employee=employee,
            team=team,
            category=category,
            category_name_snapshot=category.name,
            ownership_date=owned,
            shift_name_snapshot=shift_name,
            started_at=started_at,
            work_note=work_note or "",
            source=source,
            created_at=timezone.now(),
        )

    def submit(self, *, ended_at=None, work_note=None):
        if self.status != self.Status.RUNNING:
            raise ValidationError("只有进行中的辅助工时可以提交")
        ended_at = ended_at or timezone.now()
        ensure_aware(ended_at, "ended_at")
        if work_note is not None:
            self.work_note = work_note.strip()
        if self.category.note_required and not (self.work_note or "").strip():
            raise ValidationError("这个类别必须填写说明")
        if ended_at < self.started_at:
            raise ValidationError("结束不能早于开始")
        raw = int((ended_at - self.started_at).total_seconds())
        self.ended_at = ended_at
        self.raw_seconds = raw
        overlap = self.fixed_break_overlap_seconds or 0
        minutes = _minutes(max(raw - overlap, 0))
        self.effective_minutes = minutes
        self.original_effective_minutes = minutes
        self.status = self.Status.PENDING_REVIEW
        self.save()

    def withdraw(self, reason, *, at=None):
        if self.status != self.Status.PENDING_REVIEW:
            raise ValidationError("只能撤回未审核的辅助工时")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("撤回必须填写原因")
        self.status = self.Status.WITHDRAWN
        self.withdraw_reason = reason
        self.withdrawn_at = at or timezone.now()
        self.save()

    def approve(self, *, reviewer, at=None):
        if reviewer.role != Employee.Role.TEAM_LEADER:
            raise ValidationError("审核由班组长执行")
        if self.status != self.Status.PENDING_REVIEW:
            raise ValidationError("只有待审核的辅助工时可以通过")
        self.status = self.Status.APPROVED
        self.approved_by = reviewer
        self.approved_at = at or timezone.now()
        self.save()

    def reject(self, *, reason, rejected_by, at=None):
        if rejected_by.role != Employee.Role.TEAM_LEADER:
            raise ValidationError("驳回由班组长执行")
        if self.status != self.Status.PENDING_REVIEW:
            raise ValidationError("只有待审核的辅助工时可以驳回")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("驳回必须填写原因")
        AuxiliaryWorkRejection.objects.create(
            work_log=self,
            reason=reason,
            rejected_by=rejected_by,
            rejected_at=at or timezone.now(),
        )
        self.status = self.Status.REJECTED
        self.save(update_fields=["status"])

    def correct(self, *, minutes, reason, corrected_by, at=None):
        if corrected_by.role != Employee.Role.TEAM_LEADER:
            raise ValidationError("修正由班组长执行")
        if self.status != self.Status.PENDING_REVIEW:
            raise ValidationError("只能在待审核时修正")
        return AuxiliaryWorkCorrection.objects.create(
            work_log=self,
            corrected_effective_minutes=minutes,
            reason=reason,
            corrected_by=corrected_by,
            corrected_at=at or timezone.now(),
        )


class AuxiliaryWorkCorrection(models.Model):
    work_log = models.ForeignKey(
        AuxiliaryWorkLog,
        verbose_name="辅助工时",
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    corrected_effective_minutes = models.DecimalField(
        "修正有效分钟",
        max_digits=22,
        decimal_places=4,
    )
    reason = models.TextField("修正原因")
    corrected_by = models.ForeignKey(
        Employee,
        verbose_name="修正人",
        on_delete=models.PROTECT,
        related_name="auxiliary_corrections",
    )
    corrected_at = models.DateTimeField("修正时间")

    class Meta:
        verbose_name = "辅助工时修正"
        verbose_name_plural = "辅助工时修正"
        ordering = ["corrected_at", "pk"]

    def clean(self):
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "修正必须填写原因"})
        ensure_aware(self.corrected_at, "corrected_at")
        if self.corrected_effective_minutes is not None and (
            self.corrected_effective_minutes < 0
        ):
            raise ValidationError({"corrected_effective_minutes": "分钟不能为负"})
        if self.corrected_by_id and (
            self.corrected_by.role != Employee.Role.TEAM_LEADER
        ):
            raise ValidationError({"corrected_by": "修正由班组长执行"})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("辅助工时修正不能修改")
        self.reason = (self.reason or "").strip()
        if self.work_log_id:
            assert_month_open(self.work_log.ownership_date)
            if self.work_log.status != AuxiliaryWorkLog.Status.PENDING_REVIEW:
                raise ValidationError("只能在待审核时修正")
        self.full_clean()
        super().save(*args, **kwargs)


class AuxiliaryWorkRejection(models.Model):
    work_log = models.ForeignKey(
        AuxiliaryWorkLog,
        verbose_name="辅助工时",
        on_delete=models.PROTECT,
        related_name="rejections",
    )
    reason = models.TextField("驳回原因")
    rejected_by = models.ForeignKey(
        Employee,
        verbose_name="驳回人",
        on_delete=models.PROTECT,
        related_name="auxiliary_rejections",
    )
    rejected_at = models.DateTimeField("驳回时间")

    class Meta:
        verbose_name = "辅助工时驳回"
        verbose_name_plural = "辅助工时驳回"
        ordering = ["rejected_at", "pk"]

    def clean(self):
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "驳回必须填写原因"})
        ensure_aware(self.rejected_at, "rejected_at")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("驳回记录不能修改")
        self.reason = (self.reason or "").strip()
        if self.work_log_id:
            assert_month_open(self.work_log.ownership_date)
        self.full_clean()
        super().save(*args, **kwargs)
