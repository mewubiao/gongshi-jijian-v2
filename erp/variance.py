"""工单生产入库数量和报工数量的差异。

报工口径文档没有单写。这里暂定：同一工单上，待审核和已审核通过的报工，
取有效完工数量（有审核修正则用修正值）求和。已撤回、已驳回不计入。
已驳回若已重报，只算新的那条待审核或已通过记录，避免一笔数量算两次。
整组计件只有小组长代报的一条，不按组员重复加。

差额 = 入库数量合计 − 报工数量。入库合计来自已落地的入库行。
模型还没合并时，入库数量留空，不把“没有模型”显示成 0。
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Prefetch

from erp.models import WorkOrder
from reporting.models import PieceReport, ReportCorrection

_ZERO = Decimal("0.00")
_COUNTED_STATUSES = (PieceReport.Status.PENDING, PieceReport.Status.APPROVED)


@dataclass(frozen=True)
class QuantityVariance:
    order_no: str
    receipt_qty: Decimal | None
    reported_qty: Decimal
    difference: Decimal | None

    @property
    def receipt_known(self):
        return self.receipt_qty is not None


def reported_qty_by_order(order_nos=None):
    corrections = ReportCorrection.objects.exclude(corrected_qty=None).order_by(
        "-corrected_at", "-pk"
    )
    reports = PieceReport.objects.filter(status__in=_COUNTED_STATUSES).select_related(
        "dispatch__work_order"
    )
    if order_nos is not None:
        reports = reports.filter(dispatch__work_order__order_no__in=list(order_nos))
    reports = reports.prefetch_related(
        Prefetch("corrections", queryset=corrections, to_attr="qty_corrections")
    )
    totals = {}
    for report in reports:
        order_no = report.dispatch.work_order.order_no
        if report.qty_corrections:
            quantity = report.qty_corrections[0].corrected_qty
        else:
            quantity = report.completed_qty
        totals[order_no] = totals.get(order_no, _ZERO) + quantity
    return totals


def build_variances(receipt_totals):
    """receipt_totals 为 None 表示入库还没落地；空字典表示已经落地但没有任何入库。"""

    reported = reported_qty_by_order()
    order_nos = set(WorkOrder.objects.values_list("order_no", flat=True))
    order_nos.update(reported)
    if receipt_totals is not None:
        order_nos.update(receipt_totals)
    rows = []
    for order_no in sorted(order_nos):
        reported_qty = reported.get(order_no, _ZERO)
        if receipt_totals is None:
            receipt_qty = None
            difference = None
        else:
            receipt_qty = receipt_totals.get(order_no, _ZERO)
            difference = receipt_qty - reported_qty
        rows.append(
            QuantityVariance(
                order_no=order_no,
                receipt_qty=receipt_qty,
                reported_qty=reported_qty,
                difference=difference,
            )
        )
    return rows


def current_receipt_totals():
    """入库模型合并并接上同步之后，这里改为按工单合计。合并前返回 None。"""

    from erp.receipts import get_receipt_model

    if get_receipt_model() is None:
        return None
    raise RuntimeError(
        "ProductionReceipt 已在本分支，但还没有按工单合计入库数量。"
        "请接到该模型上，不要另建表。"
    )
