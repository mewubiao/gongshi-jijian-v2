"""工单入库数量和每一道工序报工数量的对照。

入库数是工单级的，不按工序拆开。产品工艺路线上的每一道工序都单独给出：
审核通过的报工数量，以及全部未撤回报工的数量。差异 = 报工数量 − 入库数量。
"""

from decimal import Decimal

from django.db.models import Q, Sum

from reporting.models import PieceReport
from reporting.selectors import with_effective_quantity

ZERO = Decimal("0")


def receipt_qty(work_order):
    """当前有效入库行的对账数量合计。"""
    total = work_order.production_receipts.filter(active_source_record=True).aggregate(
        total=Sum("receipt_qty")
    )["total"]
    return total if total is not None else ZERO


def work_order_quantity_comparison(work_order):
    """逐工序对照。没有报工的工序也列出，数量为 0。"""
    received = receipt_qty(work_order)
    totals = (
        with_effective_quantity(
            PieceReport.objects.filter(dispatch__work_order=work_order).exclude(
                status=PieceReport.Status.WITHDRAWN
            )
        )
        .values("dispatch__process_id")
        .annotate(
            approved=Sum(
                "effective_quantity", filter=Q(status=PieceReport.Status.APPROVED)
            ),
            reported=Sum("effective_quantity"),
        )
    )
    by_process = {row["dispatch__process_id"]: row for row in totals}
    rows = []
    processes = work_order.product.processes.order_by("sequence", "pk")
    for process in processes:
        total = by_process.get(process.pk, {})
        approved, reported = (
            total.get("approved") or ZERO,
            total.get("reported") or ZERO,
        )
        rows.append(
            {
                "process": process,
                "sequence": process.sequence,
                "operation_code": process.operation_code,
                "receipt_qty": received,
                "approved_qty": approved,
                "all_reported_qty": reported,
                "approved_difference": approved - received,
                "all_reported_difference": reported - received,
            }
        )
    return {"work_order": work_order, "receipt_qty": received, "rows": rows}
