from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from config.testing import (
    at,
    make_employee,
    make_process,
    make_single_dispatch,
    report_single,
)
from dispatch.models import Dispatch
from erp.variance import build_variances, current_receipt_totals
from masterdata.models import Employee


class VarianceTests(TestCase):
    def test_difference_is_receipt_minus_effective_reported_qty(self):
        bundle = make_single_dispatch(order_no="MO-VAR", quantity="30.00")
        corrected = report_single(
            bundle,
            at(2026, 10, 31, 9, 0),
            at(2026, 10, 31, 10, 0),
            qty="2.00",
        )
        corrected.correct(
            reason="件数点多了",
            corrected_by=bundle["leader"],
            corrected_qty=Decimal("3.00"),
            at=at(2026, 10, 31, 10, 30),
        )
        worker = make_employee("乙", Employee.Role.WORKER, team=bundle["team"])
        process = make_process(bundle["product"], sequence=20)
        second = Dispatch.create_single(
            work_order=bundle["order"],
            process=process,
            quantity=Decimal("5.00"),
            assignee=worker,
            assigned_by=bundle["leader"],
            assigned_at=at(2026, 10, 31, 8, 0),
        )
        report_single(
            {**bundle, "worker": worker, "dispatch": second},
            at(2026, 10, 31, 11, 0),
            at(2026, 10, 31, 12, 0),
            qty="1.00",
        ).approve(reviewer=bundle["leader"], at=at(2026, 10, 31, 12, 30))

        withdrawn_bundle = make_single_dispatch(order_no="MO-OUT", quantity="10.00")
        report_single(
            withdrawn_bundle,
            at(2026, 10, 31, 9, 0),
            at(2026, 10, 31, 10, 0),
            qty="9.00",
        ).withdraw("选错任务", at=at(2026, 10, 31, 10, 30))

        rejected_bundle = make_single_dispatch(order_no="MO-RE", quantity="10.00")
        rejected = report_single(
            rejected_bundle,
            at(2026, 10, 31, 9, 0),
            at(2026, 10, 31, 10, 0),
            qty="2.00",
        )
        rejected.reject(
            reason="请改数量",
            rejected_by=rejected_bundle["leader"],
            at=at(2026, 10, 31, 11, 0),
        )
        rejected.resubmit(
            completed_qty=Decimal("4.00"),
            reported_by=rejected_bundle["worker"],
            submitted_at=at(2026, 10, 31, 12, 0),
        )

        rows = {
            row.order_no: row
            for row in build_variances(
                {
                    "MO-VAR": Decimal("10.00"),
                    "MO-OUT": Decimal("1.00"),
                    "MO-RE": Decimal("4.00"),
                }
            )
        }
        self.assertEqual(rows["MO-VAR"].reported_qty, Decimal("4.00"))
        self.assertEqual(rows["MO-VAR"].difference, Decimal("6.00"))
        self.assertEqual(rows["MO-OUT"].reported_qty, Decimal("0.00"))
        self.assertEqual(rows["MO-OUT"].difference, Decimal("1.00"))
        self.assertEqual(rows["MO-RE"].reported_qty, Decimal("4.00"))
        self.assertEqual(rows["MO-RE"].difference, Decimal("0.00"))

        unknown = {row.order_no: row for row in build_variances(None)}
        self.assertIsNone(unknown["MO-VAR"].receipt_qty)
        self.assertIsNone(unknown["MO-VAR"].difference)
        self.assertEqual(unknown["MO-VAR"].reported_qty, Decimal("4.00"))

    def test_command_leaves_receipt_blank_until_the_model_is_merged(self):
        self.assertIsNone(current_receipt_totals())
        output = StringIO()
        call_command("work_order_variances", stdout=output)
        self.assertIn("还没合并", output.getvalue())
