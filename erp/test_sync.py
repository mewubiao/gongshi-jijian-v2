from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from config.testing import (
    make_center,
    make_order,
    make_process,
    make_product,
    make_single_dispatch,
)
from erp.comparison import work_order_quantity_comparison
from erp.fake import FakeYifeiSource
from erp.models import ProductionReceipt, SyncLock, SyncLog, WorkOrder
from erp.receipts import plan_receipts
from erp.scheduler import JOB_ID, build_scheduler
from erp.source import SqlServerYifeiSource
from erp.sync import run_sync
from erp.sync_rows import FetchResult, ReceiptRow, WorkOrderRow
from erp.yifei_config import load_receipt_field_map, load_work_order_field_map


def work_row(**kwargs):
    payload = {
        "order_no": "5101-1001",
        "product_code": "P1",
        "work_center_code": "WC1",
        "planned_qty": Decimal("100.00"),
        "completed_qty": Decimal("40.00"),
        "remaining_qty": Decimal("60.00"),
        "status_raw": "1",
        "order_type": "5101",
    }
    payload.update(kwargs)
    return WorkOrderRow(**payload)


def receipt_row(**kwargs):
    payload = {
        "erp_key": "5801-1-0001",
        "receipt_type": "5801",
        "receipt_no": "1",
        "line_no": "0001",
        "order_key": "5101-1001",
        "receipt_qty": Decimal("10.00"),
        "source_qty_11": Decimal("10.00"),
        "scrap_qty_12": Decimal("0"),
        "accepted_qty_13": Decimal("10.00"),
        "product_code": "P1",
    }
    payload.update(kwargs)
    return ReceiptRow(**payload)


def sync(source, receipt_map=None):
    return run_sync(
        source,
        load_work_order_field_map({}),
        receipt_map or load_receipt_field_map({}),
        stale_seconds=900,
        redact="s3cret-value",
    )


class WorkOrderSyncTests(TestCase):
    def setUp(self):
        self.product = make_product(code="P1")
        self.center = make_center(code="WC1")

    def test_creates_updates_and_is_idempotent(self):
        source_modified_at = timezone.now()
        work_log, _receipt_log = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(
                        source_modified_at=source_modified_at,
                        touch_source_modified_at=True,
                    )
                ]
            )
        )
        order = WorkOrder.objects.get(order_no="5101-1001")
        self.assertEqual(work_log.status, SyncLog.Status.SUCCEEDED)
        self.assertEqual(work_log.inserted, 1)
        self.assertEqual(order.status, WorkOrder.Status.OPEN)
        self.assertEqual(order.remaining_qty, Decimal("60.00"))
        self.assertEqual(order.planned_qty, Decimal("100.0000"))
        self.assertEqual(order.erp_order_type, "5101")
        self.assertEqual(order.erp_key, "5101-1001")
        self.assertEqual(order.erp_status, "1")
        self.assertEqual(order.product, self.product)
        self.assertEqual(order.work_center, self.center)
        self.assertEqual(order.source_modified_at, source_modified_at)

        work_log, _receipt_log = sync(
            FakeYifeiSource(work_orders=[work_row(remaining_qty=Decimal("55.00"))])
        )
        order.refresh_from_db()
        self.assertEqual(work_log.inserted, 0)
        self.assertEqual(work_log.updated, 1)
        self.assertEqual(order.remaining_qty, Decimal("55.00"))
        self.assertEqual(WorkOrder.objects.count(), 1)

        work_log, _receipt_log = sync(
            FakeYifeiSource(work_orders=[work_row(remaining_qty=Decimal("55.00"))])
        )
        self.assertEqual(work_log.inserted, 0)
        self.assertEqual(work_log.updated, 0)
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_missing_open_order_is_closed_and_stays_closed(self):
        sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(order_no="A"),
                    work_row(order_no="B"),
                ]
            )
        )
        work_log, _receipt_log = sync(
            FakeYifeiSource(work_orders=[work_row(order_no="A")])
        )
        self.assertEqual(work_log.invalidated, 1)
        closed = WorkOrder.objects.get(order_no="B")
        self.assertEqual(closed.status, WorkOrder.Status.CLOSED)
        open_order = WorkOrder.objects.get(order_no="A")
        self.assertEqual(open_order.status, WorkOrder.Status.OPEN)

        work_log, _receipt_log = sync(
            FakeYifeiSource(work_orders=[work_row(order_no="A")])
        )
        self.assertEqual(work_log.invalidated, 0)
        closed.refresh_from_db()
        self.assertEqual(closed.status, WorkOrder.Status.CLOSED)

    def test_closed_status_in_the_feed_closes_and_does_not_create(self):
        make_order(self.product, self.center, order_no="OLD")
        sync(FakeYifeiSource(work_orders=[work_row(order_no="OLD", status_raw="Y")]))
        old = WorkOrder.objects.get(order_no="OLD")
        self.assertEqual(old.status, WorkOrder.Status.CLOSED)
        sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(order_no="OLD", status_raw="y"),
                    work_row(order_no="NEW", status_raw="Y"),
                ]
            )
        )
        self.assertFalse(WorkOrder.objects.filter(order_no="NEW").exists())

    def test_reopen_when_the_order_is_open_again(self):
        make_order(
            self.product,
            self.center,
            order_no="5101-1001",
            status=WorkOrder.Status.CLOSED,
        )
        sync(FakeYifeiSource(work_orders=[work_row()]))
        self.assertEqual(
            WorkOrder.objects.get(order_no="5101-1001").status,
            WorkOrder.Status.OPEN,
        )

    def test_bad_master_data_warns_and_does_not_close_or_crash(self):
        make_order(self.product, self.center, order_no="KEEP", remaining_qty="60.00")
        work_log, _receipt_log = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(order_no="KEEP", product_code="NO-SUCH"),
                    work_row(order_no="NO-CENTER", work_center_code="NO-WC"),
                    work_row(order_no="GOOD", remaining_qty=Decimal("10.00")),
                    work_row(order_no="", product_code="P1"),
                ]
            )
        )
        self.assertEqual(work_log.status, SyncLog.Status.SUCCEEDED)
        self.assertEqual(WorkOrder.objects.filter(order_no="GOOD").count(), 1)
        kept = WorkOrder.objects.get(order_no="KEEP")
        self.assertEqual(kept.status, WorkOrder.Status.OPEN)
        self.assertEqual(kept.product, self.product)
        self.assertEqual(kept.remaining_qty, Decimal("60.00"))
        self.assertFalse(WorkOrder.objects.filter(order_no="NO-CENTER").exists())
        self.assertIn("NO-SUCH", work_log.error_summary)
        self.assertIn("NO-WC", work_log.error_summary)

    def test_negative_remaining_and_unknown_status_do_not_change_the_mirror(self):
        make_order(self.product, self.center, order_no="NEG", remaining_qty="60.00")
        make_order(self.product, self.center, order_no="UNK", remaining_qty="60.00")
        work_log, _receipt_log = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(order_no="NEG", remaining_qty=Decimal("-1.00")),
                    work_row(order_no="UNK", status_raw="Z"),
                ]
            )
        )
        self.assertEqual(work_log.status, SyncLog.Status.SUCCEEDED)
        neg = WorkOrder.objects.get(order_no="NEG")
        unknown = WorkOrder.objects.get(order_no="UNK")
        self.assertEqual(neg.status, WorkOrder.Status.OPEN)
        self.assertEqual(neg.remaining_qty, Decimal("60.00"))
        self.assertEqual(unknown.status, WorkOrder.Status.OPEN)
        self.assertIn("为负", work_log.error_summary)
        self.assertIn("未配置", work_log.error_summary)

    def test_rounds_half_up_and_warns(self):
        work_log, _receipt_log = sync(
            FakeYifeiSource(work_orders=[work_row(remaining_qty=Decimal("1.235"))])
        )
        self.assertEqual(WorkOrder.objects.get().remaining_qty, Decimal("1.24"))
        self.assertIn("四舍五入", work_log.error_summary)

    def test_empty_open_list_closes_local_orders_and_warns(self):
        make_order(self.product, self.center, order_no="ONLY")
        work_log, _receipt_log = sync(FakeYifeiSource(work_orders=[]))
        only = WorkOrder.objects.get(order_no="ONLY")
        self.assertEqual(only.status, WorkOrder.Status.CLOSED)
        self.assertIn("未拉到任何未结工单", work_log.error_summary)

    def test_duplicate_rows_keep_the_later_quantity(self):
        work_log, _receipt_log = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(remaining_qty=Decimal("10.00")),
                    work_row(remaining_qty=Decimal("20.00")),
                ]
            )
        )
        self.assertEqual(WorkOrder.objects.get().remaining_qty, Decimal("20.00"))
        self.assertEqual(work_log.inserted, 1)
        self.assertIn("多次", work_log.error_summary)

    def test_failed_fetch_does_not_close_orders_and_hides_the_password(self):
        make_order(self.product, self.center, order_no="STAY")
        work_log, receipt_log = sync(
            FakeYifeiSource(
                work_order_error=RuntimeError("login failed password=s3cret-value")
            )
        )
        self.assertEqual(work_log.status, SyncLog.Status.FAILED)
        self.assertIsNone(receipt_log)
        self.assertNotIn("s3cret-value", work_log.error_summary)
        self.assertIn("******", work_log.error_summary)
        self.assertEqual(WorkOrder.objects.get().status, WorkOrder.Status.OPEN)

    def test_receipt_failure_keeps_the_work_order_write(self):
        work_log, receipt_log = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipt_error=RuntimeError("receipt password=s3cret-value"),
            )
        )
        self.assertEqual(work_log.status, SyncLog.Status.SUCCEEDED)
        self.assertEqual(work_log.inserted, 1)
        self.assertEqual(receipt_log.status, SyncLog.Status.FAILED)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertNotIn("s3cret-value", receipt_log.error_summary)

    def test_product_change_on_a_dispatched_order_warns(self):
        bundle = make_single_dispatch(order_no="MO-1")
        other = make_product(code="P-NEW")
        work_log, _receipt_log = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(
                        order_no="MO-1",
                        product_code=other.code,
                        work_center_code=bundle["center"].code,
                        remaining_qty=Decimal("100.00"),
                    )
                ]
            )
        )
        bundle["order"].refresh_from_db()
        self.assertEqual(bundle["order"].product, other)
        self.assertIn("已有派工", work_log.error_summary)

    def test_overlap_skips_and_a_stale_lock_can_be_taken(self):
        SyncLock.objects.filter(name=SyncLock.WORKORDERS).update(
            holder="busy",
            held_until=timezone.now() + timedelta(minutes=5),
        )
        source = FakeYifeiSource(work_orders=[work_row()])
        work_log, receipt_log = sync(source)
        self.assertEqual(work_log.status, SyncLog.Status.FAILED)
        self.assertIn("已有同步正在执行", work_log.error_summary)
        self.assertIsNone(receipt_log)
        self.assertEqual(source.work_order_calls, 0)
        self.assertEqual(WorkOrder.objects.count(), 0)

        SyncLock.objects.filter(name=SyncLock.WORKORDERS).update(
            holder="dead",
            held_until=timezone.now() - timedelta(seconds=5),
        )
        SyncLog.objects.create(
            sync_type=SyncLog.SyncType.WORK_ORDER,
            trigger=SyncLog.Trigger.MANUAL,
            status=SyncLog.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=1),
        )
        work_log, _receipt_log = sync(FakeYifeiSource(work_orders=[work_row()]))
        self.assertEqual(work_log.status, SyncLog.Status.SUCCEEDED)
        self.assertEqual(
            SyncLog.objects.filter(status=SyncLog.Status.FAILED).count(),
            2,
        )
        self.assertEqual(WorkOrder.objects.count(), 1)


class ReceiptSyncTests(TestCase):
    def setUp(self):
        self.product = make_product(code="P1")
        self.center = make_center(code="WC1")

    def test_receipts_land_on_production_receipt_and_are_idempotent(self):
        _work_log, receipt_log = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[
                    receipt_row(),
                    receipt_row(
                        erp_key="OTHER",
                        receipt_type="5801",
                        receipt_no="9",
                        line_no="1",
                        order_key="MISSING",
                    ),
                ],
            )
        )
        self.assertEqual(receipt_log.status, SyncLog.Status.SUCCEEDED)
        self.assertEqual(receipt_log.inserted, 2)
        stored = ProductionReceipt.objects.get(erp_key="5801-1-0001")
        self.assertEqual(stored.receipt_qty, Decimal("10.0000"))
        self.assertEqual(stored.source_qty_11, Decimal("10.0000"))
        self.assertEqual(stored.accepted_qty_13, Decimal("10.0000"))
        self.assertEqual(stored.work_order.order_no, "5101-1001")
        self.assertEqual(stored.product, self.product)
        missing = ProductionReceipt.objects.get(erp_key="OTHER")
        self.assertIsNone(missing.work_order)
        self.assertIn("MISSING", receipt_log.error_summary)

        _work_log, receipt_log = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[
                    receipt_row(
                        receipt_qty=Decimal("12.50"),
                        accepted_qty_13=Decimal("12.50"),
                    )
                ],
            )
        )
        self.assertEqual(receipt_log.updated, 1)
        stored.refresh_from_db()
        self.assertEqual(stored.receipt_qty, Decimal("12.5000"))

        _work_log, receipt_log = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[
                    receipt_row(
                        receipt_qty=Decimal("12.50"),
                        accepted_qty_13=Decimal("12.50"),
                    )
                ],
            )
        )
        self.assertEqual(receipt_log.inserted, 0)
        self.assertEqual(receipt_log.updated, 0)
        self.assertEqual(ProductionReceipt.objects.count(), 2)

    def test_missing_receipt_rows_stay_until_invalidate_is_enabled(self):
        both = [
            receipt_row(erp_key="A", receipt_no="1", line_no="1"),
            receipt_row(
                erp_key="B",
                receipt_no="1",
                line_no="2",
                receipt_qty=Decimal("2"),
            ),
        ]
        sync(FakeYifeiSource(work_orders=[work_row()], receipts=both))
        kept = load_receipt_field_map({})
        self.assertFalse(kept.delete_missing)
        sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(erp_key="A", receipt_no="1", line_no="1")],
            ),
            kept,
        )
        self.assertTrue(ProductionReceipt.objects.get(erp_key="B").active_source_record)
        removing = load_receipt_field_map({"YIFEI_RECEIPT_DELETE_MISSING": "yes"})
        _work_log, receipt_log = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(erp_key="A", receipt_no="1", line_no="1")],
            ),
            removing,
        )
        self.assertEqual(receipt_log.invalidated, 1)
        self.assertFalse(
            ProductionReceipt.objects.get(erp_key="B").active_source_record
        )
        self.assertTrue(ProductionReceipt.objects.get(erp_key="A").active_source_record)

    def test_unusable_receipt_is_not_invalidated_by_a_full_snapshot(self):
        stored = ProductionReceipt.objects.create(
            erp_key="KEEP",
            erp_receipt_type="5801",
            erp_receipt_no="1",
            erp_line_no="1",
            erp_work_order_key="5101-1001",
            receipt_qty=Decimal("3.0000"),
            source_qty_11=Decimal("3.0000"),
            scrap_qty_12=Decimal("0.0000"),
            accepted_qty_13=Decimal("3.0000"),
        )
        broken = receipt_row(erp_key="KEEP", unusable_reason="数量无法解析")
        plan = plan_receipts(
            [broken],
            known_order_keys={"5101-1001"},
            delete_missing=True,
            existing_keys={"KEEP"},
        )
        self.assertEqual(plan.remove_keys, ())
        self.assertEqual(plan.skipped, 1)
        stored.refresh_from_db()
        self.assertTrue(stored.active_source_record)

    def test_product_mismatch_warns_and_still_saves(self):
        other = make_product(code="OTHER")
        _work_log, receipt_log = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(product_code="OTHER")],
            )
        )
        stored = ProductionReceipt.objects.get()
        self.assertEqual(stored.product, other)
        self.assertEqual(receipt_log.inserted, 1)
        self.assertIn("不一致", receipt_log.error_summary)

    def test_synced_receipt_feeds_the_process_comparison(self):
        make_process(self.product)
        sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[
                    receipt_row(
                        receipt_qty=Decimal("3"),
                        accepted_qty_13=Decimal("3"),
                    )
                ],
            )
        )
        result = work_order_quantity_comparison(WorkOrder.objects.get())
        self.assertEqual(result["receipt_qty"], Decimal("3.0000"))
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["operation_code"], "OP10")
        self.assertEqual(row["approved_qty"], Decimal("0"))
        self.assertEqual(row["all_reported_qty"], Decimal("0"))
        self.assertEqual(row["approved_difference"], Decimal("0") - Decimal("3.0000"))
        output = StringIO()
        call_command("work_order_variances", stdout=output)
        text = output.getvalue()
        self.assertIn("5101-1001", text)
        self.assertIn("OP10", text)


class CommandAndSchedulerTests(TestCase):
    def test_command_requires_connection_settings(self):
        with (
            patch.dict("os.environ", {"YIFEI_SQLSERVER_HOST": ""}, clear=False),
            self.assertRaises(CommandError),
        ):
            call_command("sync_erp_workorders")

    def test_command_syncs_through_the_fake_fetch(self):
        make_product(code="P1")
        make_center(code="WC1")
        env = {
            "YIFEI_SQLSERVER_HOST": "erp.internal",
            "YIFEI_SQLSERVER_DATABASE": "YIFEI",
            "YIFEI_SQLSERVER_USER": "reader",
            "YIFEI_SQLSERVER_PASSWORD": "s3cret-value",
        }
        with (
            patch.dict("os.environ", env),
            patch.object(
                SqlServerYifeiSource,
                "fetch_open_work_orders",
                return_value=FetchResult(rows=[work_row()], warnings=[]),
            ),
            patch.object(
                SqlServerYifeiSource,
                "fetch_production_receipts",
                return_value=FetchResult(rows=[receipt_row()], warnings=[]),
            ),
        ):
            call_command("sync_erp_workorders")
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(ProductionReceipt.objects.count(), 1)
        self.assertTrue(
            SyncLog.objects.filter(
                sync_type=SyncLog.SyncType.RECEIPT,
                status=SyncLog.Status.SUCCEEDED,
            ).exists()
        )

    def test_dry_run_rolls_back_work_orders_and_receipts(self):
        make_product(code="P1")
        make_center(code="WC1")
        env = {
            "YIFEI_SQLSERVER_HOST": "erp.internal",
            "YIFEI_SQLSERVER_DATABASE": "YIFEI",
            "YIFEI_SQLSERVER_USER": "reader",
            "YIFEI_SQLSERVER_PASSWORD": "s3cret-value",
        }
        with (
            patch.dict("os.environ", env),
            patch.object(
                SqlServerYifeiSource,
                "fetch_open_work_orders",
                return_value=FetchResult(rows=[work_row()], warnings=[]),
            ),
            patch.object(
                SqlServerYifeiSource,
                "fetch_production_receipts",
                return_value=FetchResult(rows=[receipt_row()], warnings=[]),
            ),
        ):
            call_command("sync_erp_workorders", "--dry-run")
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(ProductionReceipt.objects.count(), 0)
        self.assertEqual(SyncLog.objects.count(), 0)

    def test_scheduler_refuses_overlapping_runs(self):
        scheduler = build_scheduler(interval_seconds=300)
        job = scheduler.get_job(JOB_ID)
        self.assertEqual(job.max_instances, 1)
        self.assertTrue(job.coalesce)
        self.assertEqual(int(job.trigger.interval.total_seconds()), 300)

    def test_sync_log_admin_is_read_only(self):
        SyncLog.objects.create(
            sync_type=SyncLog.SyncType.WORK_ORDER,
            trigger=SyncLog.Trigger.MANUAL,
            status=SyncLog.Status.SUCCEEDED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        User.objects.create_superuser("root", "root@example.com", "secret-pass")
        self.client.login(username="root", password="secret-pass")
        self.assertEqual(self.client.get("/admin/erp/synclog/").status_code, 200)
        self.assertEqual(self.client.get("/admin/erp/synclog/add/").status_code, 403)
