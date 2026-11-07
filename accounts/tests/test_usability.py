from decimal import Decimal

from django.test import TestCase
from django.utils.encoding import iri_to_uri

from config.testing import (
    at,
    make_employee,
    make_login_user,
    make_single_dispatch,
    report_single,
)
from masterdata.models import Employee
from reporting.review_common import review_revision
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class RoleUsabilityTests(TestCase):
    def setUp(self):
        self.bundle = make_single_dispatch()
        self.report = report_single(self.bundle, at(2026, 11, 7, 8), at(2026, 11, 7, 9))
        self.leader = make_login_user(self.bundle["leader"])
        self.client.force_login(self.leader)
        category = AuxiliaryWorkCategory.objects.create(code="CLEAN", name="清理")
        self.log = AuxiliaryWorkLog.start(
            employee=self.bundle["worker"],
            team=self.bundle["team"],
            category=category,
            started_at=at(2026, 11, 7, 10),
        )
        self.log.submit(ended_at=at(2026, 11, 7, 11))

    def test_reviewer_default_pending_filter_survives_page_and_search(self):
        self.report.approve(reviewer=self.bundle["leader"])
        self.log.approve(reviewer=self.bundle["leader"])
        for url in ("/reports/?page=2", "/auxiliary/?q=清理&page=2"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_finance_defaults_to_all_states_and_sees_current_effective_values(self):
        self.report.correct(
            reason="核对数量",
            corrected_by=self.bundle["leader"],
            corrected_qty=Decimal("0"),
        )
        self.report.approve(reviewer=self.bundle["leader"])
        self.log.correct(
            minutes=Decimal("0"), reason="核对分钟", corrected_by=self.bundle["leader"]
        )
        self.log.approve(reviewer=self.bundle["leader"])
        self.client.force_login(
            make_login_user(make_employee("财务", Employee.Role.SETTLEMENT))
        )
        reports = self.client.get("/reports/")
        self.assertEqual(reports.context["page_obj"].paginator.count, 1)
        self.assertContains(reports, "当前有效数量")
        self.assertEqual(
            reports.context["page_obj"][0].effective_quantity, Decimal("0")
        )
        self.assertNotContains(reports, "报工记录与审核")
        auxiliary = self.client.get("/auxiliary/")
        self.assertContains(auxiliary, "当前有效分钟")
        self.assertEqual(auxiliary.context["page_obj"][0].review_minutes, Decimal("0"))

    def test_review_can_return_to_filtered_queue_without_external_redirect(self):
        for record, url, back in (
            (
                self.report,
                f"/reports/{self.report.pk}/",
                "/reports/?status=pending&month=2026-11&q=MO&page=2",
            ),
            (
                self.log,
                f"/auxiliary/{self.log.pk}/",
                "/auxiliary/?status=PENDING_REVIEW&q=清理&page=2",
            ),
        ):
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    {
                        "action": "approve",
                        "revision": review_revision(record),
                        "after": "queue",
                        "return_to": back,
                    },
                )
                self.assertEqual(response.url, iri_to_uri(back))
                self.assertContains(
                    self.client.get(url, {"return_to": "https://example.com/"}),
                    'href="' + url.rsplit("/", 2)[0] + '/"',
                )

    def test_dashboard_provides_role_specific_shortcuts(self):
        for role, label, target in (
            (Employee.Role.TEAM_LEADER, "新建派工", "/dispatch/new/"),
            (Employee.Role.WORKER, "进入车间终端", "/terminal/"),
            (Employee.Role.PROCESS_ENGINEER, "维护工序", "/admin/masterdata/process/"),
            (Employee.Role.SETTLEMENT, "查询报工", "/reports/?status="),
            (Employee.Role.ADMIN, "员工与账号", "/admin/masterdata/employee/"),
        ):
            with self.subTest(role=role):
                user = make_login_user(make_employee(str(role), role))
                self.client.force_login(user)
                response = self.client.get("/")
                self.assertContains(response, label)
                self.assertContains(response, f'href="{target}"')

    def test_one_permission_check_reads_current_employee_once(self):
        with self.assertNumQueries(1):
            self.assertTrue(self.leader.has_perm("reporting.review_piecereport"))
        Employee.objects.filter(pk=self.bundle["leader"].pk).update(
            role=Employee.Role.WORKER
        )
        self.assertFalse(self.leader.has_perm("reporting.review_piecereport"))

    def test_engineer_stale_rate_form_does_not_overwrite_new_price(self):
        engineer = make_employee("工艺", Employee.Role.PROCESS_ENGINEER)
        self.client.force_login(make_login_user(engineer))
        process = self.bundle["process"]
        url = f"/admin/masterdata/process/{process.pk}/change/"
        revision = (
            self.client.get(url).context["adminform"].form["rate_revision"].value()
        )
        process.unit_price = Decimal("2")
        process.save(changed_by=engineer)
        response = self.client.post(
            url,
            {
                "product": process.product_id,
                "operation_code": process.operation_code,
                "sequence": process.sequence,
                "name": process.name,
                "active": "on",
                "unit_price": "3",
                "standard_minutes": process.standard_minutes,
                "rate_revision": revision,
                "_save": "保存",
                "rate_history-TOTAL_FORMS": "0",
                "rate_history-INITIAL_FORMS": "0",
                "rate_history-MIN_NUM_FORMS": "0",
                "rate_history-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertContains(response, "工价已被更新")
        process.refresh_from_db()
        self.assertEqual(process.unit_price, Decimal("2"))
        self.assertEqual(process.rate_history.count(), 1)
        self.assertEqual(self.report.frozen_unit_price, Decimal("1.25"))
        self.assertNotContains(
            self.client.get(url), f"/admin/masterdata/employee/{engineer.pk}/change/"
        )
