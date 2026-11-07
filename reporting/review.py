from django.core.exceptions import ValidationError

from masterdata.access import require_role, require_team_leader
from masterdata.models import Employee
from reporting.locking import open_month_transaction
from reporting.models import PieceReport
from reporting.review_common import check_revision


def review_piece(
    *,
    report_id,
    actor,
    action,
    revision,
    reason="",
    corrected_qty=None,
    corrected_actual_seconds=None,
    confirm_excess=False,
):
    report = PieceReport.objects.get(pk=report_id)
    with open_month_transaction(report.ownership_date):
        report = PieceReport.objects.select_for_update().get(pk=report_id)
        check_revision(report, revision)
        if action == "reverse":
            require_role(actor, Employee.Role.ADMIN, "反审核仅限系统管理员")
            report.reverse_approval(actor=actor)
            return report
        require_team_leader(actor, [report.dispatch.team_id])
        if report.status != PieceReport.Status.PENDING:
            raise ValidationError("该记录已处理，请刷新页面")
        if action == "reject":
            report.reject(reason=reason, rejected_by=actor)
        elif action == "approve":
            quantity = (
                corrected_qty
                if corrected_qty is not None
                else report.effective_completed_qty()
            )
            if quantity > report.dispatch.quantity and not confirm_excess:
                raise ValidationError("有效完工数量超出派工数量，须勾选核对确认后通过")
            if corrected_qty is not None or corrected_actual_seconds is not None:
                report.correct(
                    reason=reason,
                    corrected_by=actor,
                    corrected_qty=corrected_qty,
                    corrected_actual_seconds=corrected_actual_seconds,
                )
            report.approve(reviewer=actor)
        else:
            raise ValidationError("审核操作无效")
        return report
