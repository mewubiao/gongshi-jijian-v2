"""按工单、按工序打印入库数量和报工数量。直接调用已有的对照函数。"""

from django.core.management.base import BaseCommand

from erp.comparison import work_order_quantity_comparison
from erp.models import WorkOrder


class Command(BaseCommand):
    help = "对照每张工单的入库数量和每一道工序的报工数量。差异 = 报工数量 − 入库数量。"

    def handle(self, *args, **options):
        self.stdout.write(
            "工单\t工序\t入库数量\t审核通过\t未撤回报工\t审核差异\t未撤回差异"
        )
        orders = WorkOrder.objects.select_related("product").order_by("order_no")
        for order in orders:
            result = work_order_quantity_comparison(order)
            for row in result["rows"]:
                self.stdout.write(
                    "\t".join(
                        [
                            order.order_no,
                            row["operation_code"],
                            _qty(row["receipt_qty"]),
                            _qty(row["approved_qty"]),
                            _qty(row["all_reported_qty"]),
                            _qty(row["approved_difference"]),
                            _qty(row["all_reported_difference"]),
                        ]
                    )
                )


def _qty(value):
    return f"{value:.2f}"
