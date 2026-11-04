"""易飞工单和入库的只读镜像，以及同步日志。

TODO(易飞同步): 每 5 分钟从易飞 9.012 拉取未结工单写入本表。
不把计件数量、工时、金额回写易飞。同步到已关闭后，已派任务仍可报完，
但不能再新派工（由派工模型拦截）。
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from masterdata.models import Product, WorkCenter
from masterdata.validation import ensure_aware, ensure_two_places


class WorkOrder(models.Model):
    """易飞工单镜像。业务操作只读；同步任务后续通过 ORM 更新。"""

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
        help_text="后续同步任务写入。对应旧字段 last_synced_at，名字不改。",
    )
    erp_key = models.CharField(
        "易飞工单键",
        max_length=180,
        null=True,
        blank=True,
        unique=True,
    )
    erp_order_type = models.CharField("工单单别", max_length=40, blank=True, default="")
    planned_qty = models.DecimalField(
        "计划数量",
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    unit = models.CharField("单位", max_length=40, blank=True, default="")
    erp_status = models.CharField(
        "易飞工单状态",
        max_length=20,
        blank=True,
        default="",
        db_index=True,
    )
    erp_start_date = models.DateField("易飞计划开工日期", null=True, blank=True)
    erp_due_date = models.DateField("易飞计划完工日期", null=True, blank=True)
    source_modified_at = models.DateTimeField("易飞修改时间", null=True, blank=True)

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
        ensure_aware(self.source_modified_at, "source_modified_at")
        if self.remaining_qty is not None and self.remaining_qty < 0:
            raise ValidationError({"remaining_qty": "剩余量不能为负"})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProductionReceipt(models.Model):
    """易飞生产入库行。只读拉取，不回写。"""

    erp_key = models.CharField("易飞入库行键", max_length=220, unique=True)
    erp_receipt_type = models.CharField("入库单别", max_length=40)
    erp_receipt_no = models.CharField("入库单号", max_length=80)
    erp_line_no = models.CharField("入库行号", max_length=40)
    receipt_date = models.DateField("入库日期", null=True, blank=True, db_index=True)
    product = models.ForeignKey(
        Product,
        verbose_name="产品",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="production_receipts",
    )
    work_order = models.ForeignKey(
        WorkOrder,
        verbose_name="工单",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="production_receipts",
    )
    erp_work_order_key = models.CharField("易飞工单键", max_length=180, db_index=True)
    receipt_qty = models.DecimalField(
        "对账入库数量",
        max_digits=18,
        decimal_places=4,
        help_text="旧注释为 TG013，字段待核对。",
    )
    source_qty_11 = models.DecimalField(
        "TG011原始数量",
        max_digits=18,
        decimal_places=4,
        help_text="字段名沿用旧系统，待核对。",
    )
    scrap_qty_12 = models.DecimalField(
        "TG012原始数量",
        max_digits=18,
        decimal_places=4,
        help_text="字段名沿用旧系统，待核对。",
    )
    accepted_qty_13 = models.DecimalField(
        "TG013原始数量",
        max_digits=18,
        decimal_places=4,
        help_text="字段名沿用旧系统，待核对。",
    )
    source_modified_at = models.DateTimeField("易飞修改时间", null=True, blank=True)
    last_synced_at = models.DateTimeField("最后同步时间", null=True, blank=True)
    active_source_record = models.BooleanField(
        "源端当前有效",
        default=True,
        db_index=True,
    )

    class Meta:
        verbose_name = "生产入库镜像"
        verbose_name_plural = "生产入库镜像"
        ordering = [
            "-receipt_date",
            "erp_receipt_type",
            "erp_receipt_no",
            "erp_line_no",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["erp_receipt_type", "erp_receipt_no", "erp_line_no"],
                name="uniq_yifei_receipt_line",
            ),
            models.CheckConstraint(
                condition=models.Q(receipt_qty__gte=0),
                name="receipt_qty_non_negative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["work_order", "active_source_record"],
                name="receipt_wo_active_idx",
            ),
            models.Index(
                fields=["erp_work_order_key", "active_source_record"],
                name="receipt_erp_wo_active_idx",
            ),
        ]

    def __str__(self):
        return f"{self.erp_receipt_type}-{self.erp_receipt_no}-{self.erp_line_no}"

    def clean(self):
        ensure_aware(self.source_modified_at, "source_modified_at")
        ensure_aware(self.last_synced_at, "last_synced_at")
        if self.receipt_qty is not None and self.receipt_qty < 0:
            raise ValidationError({"receipt_qty": "入库数量不能为负"})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ErpSourceMapping(models.Model):
    """易飞表和字段怎么对到本地。默认行都未核对。"""

    code = models.CharField("编码", max_length=64, unique=True)
    erp_table = models.CharField("易飞表", max_length=64, blank=True, default="")
    erp_column = models.CharField("易飞字段", max_length=64, blank=True, default="")
    local_model = models.CharField("本地模型", max_length=64)
    local_field = models.CharField("本地字段", max_length=64, blank=True, default="")
    note = models.CharField("说明", max_length=200, blank=True, default="")
    verified = models.BooleanField(
        "已核对",
        default=False,
        help_text="默认否。表名和字段名待核对，核对后再打开。",
    )

    class Meta:
        verbose_name = "易飞字段映射"
        verbose_name_plural = "易飞字段映射"
        ordering = ["code"]

    def __str__(self):
        return self.code


class SyncLog(models.Model):
    class SyncType(models.TextChoices):
        ITEM = "ITEM", "品号"
        WORK_ORDER = "WORK_ORDER", "工单"
        RECEIPT = "RECEIPT", "生产入库"

    class Trigger(models.TextChoices):
        MANUAL = "MANUAL", "手工"
        SCHEDULED = "SCHEDULED", "自动"

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "执行中"
        SUCCEEDED = "SUCCEEDED", "成功"
        FAILED = "FAILED", "失败"

    sync_type = models.CharField(
        "同步类型",
        max_length=20,
        choices=SyncType.choices,
        db_index=True,
    )
    trigger = models.CharField("触发方式", max_length=20, choices=Trigger.choices)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
        db_index=True,
    )
    started_at = models.DateTimeField("开始时间", db_index=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    inserted = models.PositiveIntegerField("新增", default=0)
    updated = models.PositiveIntegerField("更新", default=0)
    skipped = models.PositiveIntegerField("跳过", default=0)
    invalidated = models.PositiveIntegerField("失效", default=0)
    error_summary = models.TextField("错误摘要", blank=True, default="")
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="触发人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workshop_sync_logs",
    )

    class Meta:
        verbose_name = "易飞同步日志"
        verbose_name_plural = "易飞同步日志"
        ordering = ["-started_at", "-pk"]
        indexes = [
            models.Index(
                fields=["sync_type", "status", "-started_at"],
                name="sync_log_type_status_idx",
            )
        ]

    def __str__(self):
        return f"{self.get_sync_type_display()} {self.get_status_display()}"

    def clean(self):
        ensure_aware(self.started_at, "started_at")
        ensure_aware(self.finished_at, "finished_at")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SyncState(models.Model):
    sync_type = models.CharField(
        "同步类型",
        max_length=20,
        choices=SyncLog.SyncType.choices,
        unique=True,
    )
    cursor_at = models.DateTimeField("增量游标", null=True, blank=True)
    last_started_at = models.DateTimeField("最近开始时间", null=True, blank=True)
    last_succeeded_at = models.DateTimeField("最近成功结束时间", null=True, blank=True)

    class Meta:
        verbose_name = "易飞同步状态"
        verbose_name_plural = "易飞同步状态"

    def __str__(self):
        return self.get_sync_type_display()

    def clean(self):
        ensure_aware(self.cursor_at, "cursor_at")
        ensure_aware(self.last_started_at, "last_started_at")
        ensure_aware(self.last_succeeded_at, "last_succeeded_at")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
