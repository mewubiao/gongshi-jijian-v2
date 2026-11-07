from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import Prefetch

from dispatch.models import Dispatch, DispatchMember
from reporting.models import PieceReport
from reporting.selectors import with_effective_quantity
from settlement.models import PieceworkAllocation

MAX_CENTS = (1 << 63) - 1


def _allocations(report, total):
    if report.dispatch.kind == Dispatch.Kind.SINGLE:
        return [(report.reported_by, None, total)]

    members = report.dispatch.members.all()
    spans = report.work_session.member_spans.all()
    weights = defaultdict(int)
    for span in spans:
        if span.frozen_actual_seconds is None:
            raise ValidationError("有在组时段缺少冻结工时")
        weights[span.employee_id] += span.frozen_actual_seconds
    if not weights or not members or members[0].sequence != 1:
        raise ValidationError("缺少整组在组记录或原派工第一位")
    if weights.keys() - {member.employee_id for member in members}:
        raise ValidationError("在组人员与原派工名单不一致")
    seconds = sum(weights.values())
    if total and not seconds:
        raise ValidationError("计件金额大于零，但组员冻结工时合计为零")
    shares = {
        pk: total * value // seconds if seconds else 0 for pk, value in weights.items()
    }
    remainder = total - sum(shares.values())
    if remainder:
        first = members[0].employee_id
        shares[first] = shares.get(first, 0) + remainder
    return [
        (member.employee, weights[member.employee_id], shares[member.employee_id])
        for member in members
        if member.employee_id in shares
    ]


def calculate_piecework(year, month):
    """计算已审核报工的员工金额；正式锁账和页面试算共用。"""
    reports = (
        with_effective_quantity(
            PieceReport.objects.filter(
                status=PieceReport.Status.APPROVED,
                ownership_date__year=year,
                ownership_date__month=month,
            )
        )
        .select_related(
            "reported_by", "dispatch__work_order", "dispatch__process", "work_session"
        )
        .prefetch_related(
            Prefetch(
                "dispatch__members",
                queryset=DispatchMember.objects.select_related("employee").order_by(
                    "sequence"
                ),
            ),
            "work_session__member_spans",
        )
        .order_by("ownership_date", "pk")
    )
    entries = []
    errors = []
    # ponytail: 月度明细在内存中整理；更大体量时再按批生成快照和试算。
    for report in reports.iterator(chunk_size=500):
        total = int(
            (report.effective_quantity * report.frozen_unit_price * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        try:
            if total > MAX_CENTS:
                raise ValidationError("计件金额超出系统记录上限")
            parts = _allocations(report, total)
        except ValidationError as error:
            errors.append(f"报工 #{report.pk}：{error.messages[0]}")
            continue
        entries.extend(
            PieceworkAllocation(
                report_id=report.pk,
                employee_id=employee.pk,
                employee_code=employee.code,
                employee_name=employee.name,
                ownership_date=report.ownership_date,
                order_no=report.dispatch.work_order.order_no,
                process_name=report.dispatch.process.name,
                quantity=report.effective_quantity,
                unit_price=report.frozen_unit_price,
                group_seconds=seconds,
                amount_cents=amount,
            )
            for employee, seconds, amount in parts
        )
    if errors:
        raise ValidationError(
            [f"有 {len(errors)} 条报工无法结算，请核对后再锁账。", *errors[:20]]
        )
    return entries
