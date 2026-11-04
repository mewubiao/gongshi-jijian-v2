"""易飞只读镜像。

同步由 `manage.py sync_erp_workorders` 执行，每 5 分钟一次。
只拉取未结工单和生产入库数量，不把计件数量、工时、金额回写易飞。
工单同步到已关闭后，已派任务仍可报完，但不能再新派工（由派工模型拦截）。

生产入库的落地表不在这里建。那张表由数据模型分支按旧系统的
ProductionReceipt 增加，同步接到那张表上，避免两套模型。
"""

from django.core.exceptions import ValidationError
from django.db import models

from masterdata.models import Product, WorkCenter
from masterdata.validation import ensure_aware, ensure_two_places


class WorkOrder(models.Model):
    """易飞工单镜像。业务操作只读；同步任务通过 ORM 更新。"""

    class Status(models.TextChoices):
        OPEN = "open", "未结"
        CLOSED = "closed", "已关闭"

    order_no = models.CharField("易飞工单号", max_length=64, unique=True)
    product = models.ForeignKey(
        Product,
        verbose_name="产品",
        on_delete=models.PROTECT,
        related_name="work_orders",
    )
    work_center = models.ForeignKey(
        WorkCenter,
        verbose_name="工作中心",
        on_delete=models.PROTECT,
        related_name="work_orders",
    )
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    remaining_qty = models.DecimalField(
        "剩余量",
        max_digits=12,
        decimal_places=2,
        help_text="易飞侧剩余量。单次派工数量不能超过它。",
    )
    synced_at = models.DateTimeField(
        "最近同步时间",
        null=True,
        blank=True,
        help_text="最近一次成功写入这张镜像的时间。",
    )
    source_updated_at = models.DateTimeField(
        "来源更新时间",
        null=True,
        blank=True,
        help_text="易飞来源行上的更新时间。列和格式待按实际库核对；解析不了就留空。",
    )

    class Meta:
        verbose_name = "易飞工单"
        verbose_name_plural = "易飞工单"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(remaining_qty__gte=0),
                name="work_order_remaining_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "closed"]),
                name="work_order_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.order_no}（{self.get_status_display()}）"

    def clean(self):
        ensure_two_places(self.remaining_qty, "remaining_qty", "剩余量")
        ensure_aware(self.synced_at, "synced_at")
        ensure_aware(self.source_updated_at, "source_updated_at")
        if self.remaining_qty is not None and self.remaining_qty < 0:
            raise ValidationError({"remaining_qty": "剩余量不能为负"})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SyncRun(models.Model):
    """一次易飞拉取。只追加运行结果，管理后台不能改。"""

    class Status(models.TextChoices):
        RUNNING = "running", "进行中"
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"
        SKIPPED = "skipped", "已跳过"

    started_at = models.DateTimeField("开始时间")
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    status = models.CharField("状态", max_length=16, choices=Status.choices)
    fetched_count = models.PositiveIntegerField("工单拉取条数", default=0)
    created_count = models.PositiveIntegerField("工单新增条数", default=0)
    updated_count = models.PositiveIntegerField("工单更新条数", default=0)
    unchanged_count = models.PositiveIntegerField("工单未变化条数", default=0)
    closed_count = models.PositiveIntegerField("工单关闭条数", default=0)
    skipped_count = models.PositiveIntegerField("工单跳过条数", default=0)
    receipt_fetched_count = models.PositiveIntegerField("入库拉取条数", default=0)
    receipt_created_count = models.PositiveIntegerField("入库新增条数", default=0)
    receipt_updated_count = models.PositiveIntegerField("入库更新条数", default=0)
    receipt_unchanged_count = models.PositiveIntegerField("入库未变化条数", default=0)
    receipt_removed_count = models.PositiveIntegerField("入库移除条数", default=0)
    receipt_skipped_count = models.PositiveIntegerField("入库跳过条数", default=0)
    warnings = models.JSONField("告警", default=list, blank=True)
    error = models.TextField("错误", blank=True, default="")

    class Meta:
        verbose_name = "易飞同步记录"
        verbose_name_plural = "易飞同步记录"
        ordering = ["-started_at", "-pk"]
        indexes = [
            models.Index(
                fields=["status", "started_at"], name="sync_run_status_started"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["running", "success", "failed", "skipped"]
                ),
                name="sync_run_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.started_at:%Y-%m-%d %H:%M} {self.get_status_display()}"

    def clean(self):
        ensure_aware(self.started_at, "started_at")
        ensure_aware(self.finished_at, "finished_at")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValidationError("结束时间不能早于开始时间")
        if self.warnings is None:
            self.warnings = []
        if not isinstance(self.warnings, list):
            raise ValidationError({"warnings": "告警必须是列表"})

    def save(self, *args, **kwargs):
        if self.warnings is None:
            self.warnings = []
        if not self.error:
            self.error = ""
        self.full_clean()
        super().save(*args, **kwargs)


class SyncLock(models.Model):
    """一行锁，避免定时同步和手工同步叠在一起。不是业务数据。"""

    WORKORDERS = "workorders"

    name = models.CharField(primary_key=True, max_length=32)
    holder = models.CharField(max_length=64, blank=True, default="")
    held_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "易飞同步锁"
        verbose_name_plural = "易飞同步锁"

    def __str__(self):
        return self.name
