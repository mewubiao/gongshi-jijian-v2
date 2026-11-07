from django.core.exceptions import ValidationError

from masterdata.access import require_team_leader
from reporting.locking import open_month_transaction
from reporting.review_common import check_revision
from timekeeping.models import AuxiliaryWorkLog


def review_auxiliary(*, log_id, actor, action, revision, reason="", minutes=None):
    log = AuxiliaryWorkLog.objects.get(pk=log_id)
    with open_month_transaction(log.ownership_date):
        log = AuxiliaryWorkLog.objects.select_for_update().get(pk=log_id)
        require_team_leader(actor, [log.team_id])
        check_revision(log, revision)
        if log.status != AuxiliaryWorkLog.Status.PENDING_REVIEW:
            raise ValidationError("该记录已处理，请刷新页面")
        if action == "reject":
            log.reject(reason=reason, rejected_by=actor)
        elif action == "approve":
            if minutes is not None:
                log.correct(minutes=minutes, reason=reason, corrected_by=actor)
            log.approve(reviewer=actor)
        else:
            raise ValidationError("审核操作无效")
        return log
