"""按工单打印生产入库数量和报工数量的差额。"""

from django.core.management.base import BaseCommand, CommandError

from erp.variance import build_variances, current_receipt_totals


class Command(BaseCommand):
    help = "对照每张工单的生产入库数量和报工数量。差额 = 入库 − 报工。"

    def handle(self, *args, **options):
        try:
            totals = current_receipt_totals()
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        if totals is None:
            self.stdout.write(
                "生产入库模型还没合并，入库数量和差额先空着。"
                "报工数量只计待审核和已审核通过的有效完工数量。"
            )
        self.stdout.write("工单\t入库数量\t报工数量\t差额")
        for row in build_variances(totals):
            receipt = _qty(row.receipt_qty)
            difference = _qty(row.difference)
            self.stdout.write(
                f"{row.order_no}\t{receipt}\t{_qty(row.reported_qty)}\t{difference}"
            )


def _qty(value):
    if value is None:
        return "—"
    return f"{value:.2f}"
