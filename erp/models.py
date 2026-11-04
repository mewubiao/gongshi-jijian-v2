"""易飞工单的只读镜像。

TODO(易飞同步): 每 5 分钟从易飞 9.012 拉取未结工单写入本表。
不把计件数量、工时、金额回写易飞。同步到已关闭后，已派任务仍可报完，
但不能再新派工（由派工模型拦截）。
"""

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
        help_text="由后续易飞同步任务写入。",
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
        if self.remaining_qty is not None and self.remaining_qty < 0:
            raise ValidationError({"remaining_qty": "剩余量不能为负"})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
