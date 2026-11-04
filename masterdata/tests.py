from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from config.testing import at, make_center, make_employee, make_process, make_product
from masterdata.admin import ProcessAdminForm
from masterdata.models import Employee, ProcessRateHistory, Team


class EmployeeTests(TestCase):
    def test_blank_nfc_is_allowed_more_than_once_and_qr_is_unique(self):
        first = make_employee("甲", Employee.Role.WORKER, badge_nfc="")
        second = make_employee("乙", Employee.Role.WORKER, badge_nfc=None)
        self.assertIsNone(first.badge_nfc)
        self.assertIsNone(second.badge_nfc)

        make_employee("丙", Employee.Role.WORKER, badge_nfc="NFC-1")
        with self.assertRaises(ValidationError):
            make_employee("丁", Employee.Role.WORKER, badge_nfc="NFC-1")
        with self.assertRaises(ValidationError):
            make_employee("戊", Employee.Role.WORKER, qr_code=first.qr_code)

    def test_qr_code_is_required(self):
        with self.assertRaises(ValidationError):
            Employee.objects.create(
                name="无码",
                role=Employee.Role.WORKER,
                qr_code="  ",
            )


class ShiftTests(TestCase):
    def test_work_center_rejects_overlapping_shifts(self):
        with self.assertRaises(ValidationError) as caught:
            make_center(
                day_shift_start=time(8, 0),
                day_shift_end=time(21, 0),
                night_shift_start=time(20, 0),
                night_shift_end=time(8, 0),
            )
        self.assertIn("不能重叠", str(caught.exception))

    def test_team_override_is_all_or_nothing(self):
        center = make_center(code="WC-TEAM")
        leader = make_employee("组长", Employee.Role.TEAM_LEADER)
        with self.assertRaises(ValidationError):
            Team.objects.create(
                name="只填了白班",
                work_center=center,
                leader=leader,
                day_shift_start=time(7, 0),
                day_shift_end=time(19, 0),
            )
        team = Team.objects.create(
            name="整段覆盖",
            work_center=center,
            leader=leader,
            day_shift_start=time(7, 0),
            day_shift_end=time(19, 0),
            night_shift_start=time(19, 0),
            night_shift_end=time(7, 0),
        )
        self.assertTrue(team.has_shift_override)


class ProcessRateHistoryTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.process = make_process(self.product)
        self.engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        self.worker = make_employee("工人", Employee.Role.WORKER)

    def test_rate_change_records_who_when_and_old_new_values(self):
        changed_at = at(2026, 10, 1, 8, 0)
        self.process.unit_price = Decimal("2.00")
        self.process.standard_minutes = Decimal("45.00")
        self.process.save(changed_by=self.engineer, changed_at=changed_at)

        history = ProcessRateHistory.objects.get()
        self.assertEqual(history.changed_by, self.engineer)
        self.assertEqual(history.changed_at, changed_at)
        self.assertEqual(history.old_unit_price, Decimal("1.25"))
        self.assertEqual(history.new_unit_price, Decimal("2.00"))
        self.assertEqual(history.old_standard_minutes, Decimal("30.00"))
        self.assertEqual(history.new_standard_minutes, Decimal("45.00"))

        self.process.name = "改个名字"
        self.process.save()
        self.assertEqual(ProcessRateHistory.objects.count(), 1)

    def test_rate_change_requires_a_process_engineer(self):
        self.process.unit_price = Decimal("3.00")
        with self.assertRaises(ValidationError):
            self.process.save()
        self.process.refresh_from_db()
        self.assertEqual(self.process.unit_price, Decimal("1.25"))

        self.process.unit_price = Decimal("3.00")
        with self.assertRaises(ValidationError):
            self.process.save(changed_by=self.worker)
        self.process.refresh_from_db()
        self.assertEqual(self.process.unit_price, Decimal("1.25"))

    def test_money_and_standard_time_keep_two_decimal_places(self):
        self.process.unit_price = Decimal("1.234")
        with self.assertRaises(ValidationError):
            self.process.save(changed_by=self.engineer)

    def test_admin_form_asks_for_the_editor_when_rates_change(self):
        form = ProcessAdminForm(
            data={
                "product": self.process.product_id,
                "sequence": self.process.sequence,
                "name": self.process.name,
                "unit_price": "9.50",
                "standard_minutes": "30.00",
                "changed_by": "",
            },
            instance=self.process,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("修改人", form.errors.as_text())
        self.process.refresh_from_db()

        form = ProcessAdminForm(
            data={
                "product": self.process.product_id,
                "sequence": self.process.sequence,
                "name": "只改名称",
                "unit_price": "1.25",
                "standard_minutes": "30.00",
                "changed_by": "",
            },
            instance=self.process,
        )
        self.assertTrue(form.is_valid(), form.errors)
