"""反审核的追加留痕，保留当时被撤销的审核信息。"""

from django.core.exceptions import ValidationError
from django.db import models

from masterdata.access import require_role
from masterdata.models import Employee
from masterdata.validation import ensure_aware
from reporting.locking import assert_month_open


class ReportApprovalReversal(models.Model):
    piece_report = models.ForeignKey(
        "reporting.PieceReport",
        on_delete=models.PROTECT,
        related_name="approval_reversals",
        verbose_name="报工",
    )
    reversed_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="report_approval_reversals",
        verbose_name="反审核人",
    )
    reversed_at = models.DateTimeField("反审核时间")
    approved_by = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="reversed_report_approvals",
        verbose_name="原审核人",
    )
    approved_at = models.DateTimeField("原审核时间", null=True, blank=True)

    class Meta:
        verbose_name = "反审核记录"
        verbose_name_plural = "反审核记录"
        ordering = ["reversed_at", "pk"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("反审核记录不能修改")
        require_role(self.reversed_by, Employee.Role.ADMIN, "反审核仅限系统管理员")
        assert_month_open(self.piece_report.ownership_date)
        ensure_aware(self.reversed_at, "reversed_at")
        ensure_aware(self.approved_at, "approved_at")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("反审核记录不能删除")
