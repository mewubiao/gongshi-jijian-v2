from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import (
    at,
    make_center,
    make_employee,
    make_order,
    make_process,
    make_product,
    make_team,
)
from dispatch.models import Dispatch
from erp.models import WorkOrder
from masterdata.models import Employee


class DispatchRuleTests(TestCase):
    def setUp(self):
        self.center = make_center()
        self.leader = make_employee("组长", Employee.Role.TEAM_LEADER)
        self.team = make_team(self.center, self.leader)
        self.worker = make_employee("工人", Employee.Role.WORKER, team=self.team)
        self.product = make_product()
        self.process = make_process(self.product)
        self.other_process = make_process(
            make_product(code="P2"),
            sequence=10,
            name="别的产品工序",
        )
        self.order = make_order(self.product, self.center, remaining_qty="10.00")

    def test_quantity_cannot_exceed_remaining_and_closed_orders_reject_new_work(self):
        Dispatch.create_single(
            work_order=self.order,
            process=self.process,
            quantity=Decimal("10.00"),
            assignee=self.worker,
            assigned_by=self.leader,
            assigned_at=at(2026, 10, 1, 8, 0),
        )
        with self.assertRaises(ValidationError) as caught:
            Dispatch.create_single(
                work_order=self.order,
                process=self.process,
                quantity=Decimal("10.01"),
                assignee=self.worker,
                assigned_by=self.leader,
                assigned_at=at(2026, 10, 1, 9, 0),
            )
        self.assertIn("剩余量", str(caught.exception))

        self.order.status = WorkOrder.Status.CLOSED
        self.order.save()
        with self.assertRaises(ValidationError) as caught:
            Dispatch.create_single(
                work_order=self.order,
                process=self.process,
                quantity=Decimal("1.00"),
                assignee=self.worker,
                assigned_by=self.leader,
                assigned_at=at(2026, 10, 1, 10, 0),
            )
        self.assertIn("已关闭", str(caught.exception))

    def test_process_must_belong_to_the_order_product_and_assigner_is_team_leader(self):
        with self.assertRaises(ValidationError):
            Dispatch.create_single(
                work_order=self.order,
                process=self.other_process,
                quantity=Decimal("1.00"),
                assignee=self.worker,
                assigned_by=self.leader,
                assigned_at=at(2026, 10, 1, 8, 0),
            )
        with self.assertRaises(ValidationError):
            Dispatch.create_single(
                work_order=self.order,
                process=self.process,
                quantity=Decimal("1.00"),
                assignee=self.worker,
                assigned_by=self.worker,
                assigned_at=at(2026, 10, 1, 8, 0),
            )

    def test_group_order_keeps_the_first_member_when_leadership_moves(self):
        second = make_employee("乙", Employee.Role.WORKER, team=self.team)
        third = make_employee("丙", Employee.Role.WORKER, team=self.team)
        outsider = make_employee("丁", Employee.Role.WORKER, team=self.team)
        dispatch = Dispatch.create_group(
            work_order=self.order,
            process=self.process,
            quantity=Decimal("5.00"),
            members=[self.worker, second, third],
            assigned_by=self.leader,
            assigned_at=at(2026, 10, 2, 8, 0),
        )
        self.assertEqual(dispatch.current_leader, self.worker)
        self.assertEqual(dispatch.first_listed_member().sequence, 1)
        self.assertEqual(dispatch.first_listed_member().employee, self.worker)

        when = at(2026, 10, 2, 9, 0)
        dispatch.transfer_leader(to_employee=second, transferred_at=when)
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.current_leader, second)
        self.assertEqual(dispatch.first_listed_member().employee, self.worker)
        transfer = dispatch.leader_transfers.get()
        self.assertEqual(transfer.from_employee, self.worker)
        self.assertEqual(transfer.to_employee, second)
        self.assertEqual(transfer.transferred_at, when)

        with self.assertRaises(ValidationError):
            dispatch.transfer_leader(
                to_employee=outsider,
                transferred_at=at(2026, 10, 2, 9, 30),
            )

        single = Dispatch.create_single(
            work_order=self.order,
            process=self.process,
            quantity=Decimal("1.00"),
            assignee=outsider,
            assigned_by=self.leader,
            assigned_at=at(2026, 10, 2, 8, 0),
        )
        with self.assertRaises(ValidationError):
            single.transfer_leader(
                to_employee=second,
                transferred_at=at(2026, 10, 2, 10, 0),
            )

    def test_group_needs_two_distinct_members(self):
        with self.assertRaises(ValidationError):
            Dispatch.create_group(
                work_order=self.order,
                process=self.process,
                quantity=Decimal("1.00"),
                members=[self.worker],
                assigned_by=self.leader,
                assigned_at=at(2026, 10, 2, 8, 0),
            )
        with self.assertRaises(ValidationError):
            Dispatch.create_group(
                work_order=self.order,
                process=self.process,
                quantity=Decimal("1.00"),
                members=[self.worker, self.worker],
                assigned_by=self.leader,
                assigned_at=at(2026, 10, 2, 8, 0),
            )
