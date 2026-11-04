from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from config.testing import (
    at,
    make_center,
    make_employee,
    make_order,
    make_process,
    make_product,
    make_team,
    report_single,
)
from dispatch.models import Dispatch
from erp.comparison import work_order_quantity_comparison
from erp.models import ErpSourceMapping, ProductionReceipt, WorkOrder
from masterdata.models import Employee


class WorkOrderTests(TestCase):
    def test_status_includes_closed_and_remaining_cannot_be_negative(self):
        product = make_product()
        center = make_center()
        order = make_order(
            product,
            center,
            status=WorkOrder.Status.CLOSED,
            synced_at=timezone.now(),
        )
        self.assertEqual(order.status, WorkOrder.Status.CLOSED)
        self.assertEqual(order.get_status_display(), "已关闭")

        order.remaining_qty = Decimal("-1.00")
        with self.assertRaises(ValidationError):
            order.save()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkOrder.objects.bulk_create(
                    [
                        WorkOrder(
                            order_no="NEG",
                            product=product,
                            work_center=center,
                            status=WorkOrder.Status.OPEN,
                            remaining_qty=Decimal("-1.00"),
                        )
                    ]
                )

    def test_admin_can_view_the_mirror_but_not_edit_it(self):
        product = make_product(code="P-ADMIN")
        center = make_center(code="WC-ADMIN")
        order = make_order(product, center, order_no="MO-ADMIN")
        User.objects.create_superuser("root", "root@example.com", "secret-pass")
        self.client.login(username="root", password="secret-pass")

        listing = self.client.get("/admin/erp/workorder/")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, order.order_no)

        add = self.client.get("/admin/erp/workorder/add/")
        self.assertEqual(add.status_code, 403)

        detail = self.client.get(f"/admin/erp/workorder/{order.pk}/change/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, order.order_no)


class QuantityComparisonTests(TestCase):
    def test_each_process_shows_approved_and_all_reported_quantities(self):
        center = make_center(code="WC-CMP")
        leader = make_employee("组长", Employee.Role.TEAM_LEADER)
        team = make_team(center, leader, code="T-CMP")
        worker = make_employee("工人", Employee.Role.WORKER, team=team)
        product = make_product(code="P-CMP")
        first = make_process(product, sequence=10, operation_code="OP10", name="冲压")
        second = make_process(product, sequence=20, operation_code="OP20", name="包装")
        make_process(product, sequence=30, operation_code="OP30", name="未报工")
        order = make_order(product, center, order_no="MO-CMP", remaining_qty="100")
        ProductionReceipt.objects.create(
            erp_key="R1",
            erp_receipt_type="58",
            erp_receipt_no="001",
            erp_line_no="1",
            product=product,
            work_order=order,
            erp_work_order_key="MO-CMP",
            receipt_qty=Decimal("3.0000"),
            source_qty_11=Decimal("3.0000"),
            scrap_qty_12=Decimal("0.0000"),
            accepted_qty_13=Decimal("3.0000"),
        )
        ProductionReceipt.objects.create(
            erp_key="R2",
            erp_receipt_type="58",
            erp_receipt_no="001",
            erp_line_no="2",
            product=product,
            work_order=order,
            erp_work_order_key="MO-CMP",
            receipt_qty=Decimal("5.0000"),
            source_qty_11=Decimal("5.0000"),
            scrap_qty_12=Decimal("0.0000"),
            accepted_qty_13=Decimal("5.0000"),
        )
        ProductionReceipt.objects.create(
            erp_key="R-OLD",
            erp_receipt_type="58",
            erp_receipt_no="002",
            erp_line_no="1",
            work_order=order,
            erp_work_order_key="MO-CMP",
            receipt_qty=Decimal("100.0000"),
            source_qty_11=Decimal("100.0000"),
            scrap_qty_12=Decimal("0.0000"),
            accepted_qty_13=Decimal("100.0000"),
            active_source_record=False,
        )

        def finish(process, qty, start_hour):
            dispatch = Dispatch.create_single(
                work_order=order,
                process=process,
                quantity=Decimal("20"),
                assignee=worker,
                assigned_by=leader,
                assigned_at=at(2026, 10, 6, start_hour, 0),
            )
            return report_single(
                {
                    "dispatch": dispatch,
                    "worker": worker,
                },
                at(2026, 10, 6, start_hour, 0),
                at(2026, 10, 6, start_hour, 30),
                qty=qty,
            )

        approved = finish(first, "5.00", 8)
        approved.correct(
            reason="点数",
            corrected_by=leader,
            corrected_qty=Decimal("7.00"),
            at=at(2026, 10, 6, 8, 50),
        )
        approved.approve(reviewer=leader, at=at(2026, 10, 6, 9, 0))
        finish(first, "1.00", 10)
        withdrawn = finish(first, "9.00", 11)
        withdrawn.withdraw("报错", at=at(2026, 10, 6, 12, 0))
        second_report = finish(second, "4.00", 13)
        second_report.approve(reviewer=leader, at=at(2026, 10, 6, 14, 0))

        result = work_order_quantity_comparison(order)
        self.assertEqual(result["receipt_qty"], Decimal("8.0000"))
        rows = {row["operation_code"]: row for row in result["rows"]}
        self.assertEqual(
            [row["operation_code"] for row in result["rows"]],
            ["OP10", "OP20", "OP30"],
        )
        self.assertEqual(rows["OP10"]["receipt_qty"], Decimal("8.0000"))
        self.assertEqual(rows["OP10"]["approved_qty"], Decimal("7.00"))
        self.assertEqual(rows["OP10"]["all_reported_qty"], Decimal("8.00"))
        self.assertEqual(
            rows["OP10"]["approved_difference"], Decimal("7.00") - Decimal("8.0000")
        )
        self.assertEqual(rows["OP20"]["approved_qty"], Decimal("4.00"))
        self.assertEqual(rows["OP20"]["all_reported_qty"], Decimal("4.00"))
        self.assertEqual(rows["OP30"]["approved_qty"], Decimal("0"))
        self.assertEqual(rows["OP30"]["all_reported_qty"], Decimal("0"))
        self.assertEqual(rows["OP30"]["receipt_qty"], Decimal("8.0000"))

    def test_receipt_mapping_stays_unverified(self):
        mapping = ErpSourceMapping.objects.get(code="receipt.qty")
        self.assertFalse(mapping.verified)
        self.assertIn("待核对", mapping.note)
        self.assertFalse(ErpSourceMapping.objects.filter(verified=True).exists())
