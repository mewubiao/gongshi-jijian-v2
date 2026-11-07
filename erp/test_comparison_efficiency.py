from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from config.testing import at, make_process, make_single_dispatch, report_single
from erp.comparison import work_order_quantity_comparison


class ComparisonEfficiencyTests(TestCase):
    def test_more_processes_do_not_add_per_process_or_per_report_queries(self):
        bundle = make_single_dispatch()
        report = report_single(bundle, at(2026, 11, 7, 8), at(2026, 11, 7, 9))
        report.correct(
            reason="核对为零", corrected_by=bundle["leader"], corrected_qty=Decimal("0")
        )
        report.approve(reviewer=bundle["leader"])
        with CaptureQueriesContext(connection) as small:
            work_order_quantity_comparison(bundle["order"])
        for index in range(2, 12):
            make_process(bundle["product"], sequence=index * 10)
        with CaptureQueriesContext(connection) as large:
            result = work_order_quantity_comparison(bundle["order"])
        self.assertEqual(len(result["rows"]), 11)
        self.assertEqual(result["rows"][0]["approved_qty"], Decimal("0"))
        self.assertLessEqual(len(large), len(small))
        self.assertEqual(len(large), 3)
