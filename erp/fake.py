"""测试和试跑用的假数据源。不连接 SQL Server。"""

from erp.sync_rows import FetchResult


class FakeYifeiSource:
    def __init__(
        self,
        work_orders=None,
        receipts=None,
        work_order_warnings=None,
        receipt_warnings=None,
        work_order_error=None,
        receipt_error=None,
    ):
        self.work_orders = list(work_orders or [])
        self.receipts = list(receipts or [])
        self.work_order_warnings = list(work_order_warnings or [])
        self.receipt_warnings = list(receipt_warnings or [])
        self.work_order_error = work_order_error
        self.receipt_error = receipt_error
        self.work_order_calls = 0
        self.receipt_calls = 0

    def fetch_open_work_orders(self):
        self.work_order_calls += 1
        if self.work_order_error is not None:
            raise self.work_order_error
        return FetchResult(
            rows=list(self.work_orders),
            warnings=list(self.work_order_warnings),
        )

    def fetch_production_receipts(self):
        self.receipt_calls += 1
        if self.receipt_error is not None:
            raise self.receipt_error
        return FetchResult(
            rows=list(self.receipts),
            warnings=list(self.receipt_warnings),
        )
