from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from config.testing import make_center, make_order, make_product, make_single_dispatch
from erp.fake import FakeYifeiSource
from erp.models import SyncLock, SyncRun, WorkOrder
from erp.receipts import (
    RECEIPT_NOT_READY,
    MemoryReceiptWriter,
    get_receipt_model,
    plan_receipts,
)
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
    }
    payload.update(kwargs)
    return WorkOrderRow(**payload)


def receipt_row(**kwargs):
    payload = {
        "source_key": "5801-1-0001",
        "order_no": "5101-1001",
        "quantity": Decimal("10.00"),
        "product_code": "P1",
    }
    payload.update(kwargs)
    return ReceiptRow(**payload)


def sync(source, writer=None, receipt_map=None):
    return run_sync(
        source,
        load_work_order_field_map({}),
        receipt_map or load_receipt_field_map({}),
        stale_seconds=900,
        redact="s3cret-value",
        receipt_writer=writer,
    )


class WorkOrderSyncTests(TestCase):
    def setUp(self):
        self.product = make_product(code="P1")
        self.center = make_center(code="WC1")

    def test_creates_updates_and_is_idempotent(self):
        source_updated_at = timezone.now()
        first = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(
                        source_updated_at=source_updated_at,
                        touch_source_updated_at=True,
                    )
                ]
            )
        )
        order = WorkOrder.objects.get(order_no="5101-1001")
        self.assertEqual(first.created_count, 1)
        self.assertEqual(order.status, WorkOrder.Status.OPEN)
        self.assertEqual(order.remaining_qty, Decimal("60.00"))
        self.assertEqual(order.product, self.product)
        self.assertEqual(order.work_center, self.center)
        self.assertEqual(order.source_updated_at, source_updated_at)

        second = sync(
            FakeYifeiSource(work_orders=[work_row(remaining_qty=Decimal("55.00"))])
        )
        order.refresh_from_db()
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.updated_count, 1)
        self.assertEqual(order.remaining_qty, Decimal("55.00"))
        self.assertEqual(WorkOrder.objects.count(), 1)

        third = sync(
            FakeYifeiSource(work_orders=[work_row(remaining_qty=Decimal("55.00"))])
        )
        self.assertEqual(third.created_count, 0)
        self.assertEqual(third.updated_count, 0)
        self.assertEqual(third.unchanged_count, 1)
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
        sync(FakeYifeiSource(work_orders=[work_row(order_no="A")]))
        closed = WorkOrder.objects.get(order_no="B")
        self.assertEqual(closed.status, WorkOrder.Status.CLOSED)
        open_order = WorkOrder.objects.get(order_no="A")
        self.assertEqual(open_order.status, WorkOrder.Status.OPEN)

        again = sync(FakeYifeiSource(work_orders=[work_row(order_no="A")]))
        self.assertEqual(again.closed_count, 0)
        closed.refresh_from_db()
        self.assertEqual(closed.status, WorkOrder.Status.CLOSED)

    def test_closed_status_in_the_feed_closes_and_does_not_create(self):
        make_order(self.product, self.center, order_no="OLD")
        sync(FakeYifeiSource(work_orders=[work_row(order_no="OLD", status_raw="Y")]))
        order = WorkOrder.objects.get(order_no="OLD")
        self.assertEqual(order.status, WorkOrder.Status.CLOSED)
        self.assertFalse(WorkOrder.objects.filter(order_no="NEW").exists())
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
        order = WorkOrder.objects.get(order_no="5101-1001")
        self.assertEqual(order.status, WorkOrder.Status.OPEN)

    def test_bad_master_data_warns_and_does_not_close_or_crash(self):
        make_order(self.product, self.center, order_no="KEEP", remaining_qty="60.00")
        source = FakeYifeiSource(
            work_orders=[
                work_row(order_no="KEEP", product_code="NO-SUCH"),
                work_row(order_no="NO-CENTER", work_center_code="NO-WC"),
                work_row(order_no="GOOD", remaining_qty=Decimal("10.00")),
                work_row(order_no="", product_code="P1"),
            ]
        )
        run = sync(source)
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(WorkOrder.objects.filter(order_no="GOOD").count(), 1)
        kept = WorkOrder.objects.get(order_no="KEEP")
        self.assertEqual(kept.status, WorkOrder.Status.OPEN)
        self.assertEqual(kept.product, self.product)
        self.assertEqual(kept.remaining_qty, Decimal("60.00"))
        self.assertFalse(WorkOrder.objects.filter(order_no="NO-CENTER").exists())
        text = " ".join(run.warnings)
        self.assertIn("NO-SUCH", text)
        self.assertIn("NO-WC", text)

    def test_negative_remaining_and_unknown_status_do_not_change_the_mirror(self):
        make_order(self.product, self.center, order_no="NEG", remaining_qty="60.00")
        make_order(self.product, self.center, order_no="UNK", remaining_qty="60.00")
        run = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(order_no="NEG", remaining_qty=Decimal("-1.00")),
                    work_row(order_no="UNK", status_raw="Z"),
                ]
            )
        )
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        neg = WorkOrder.objects.get(order_no="NEG")
        unknown = WorkOrder.objects.get(order_no="UNK")
        self.assertEqual(neg.status, WorkOrder.Status.OPEN)
        self.assertEqual(neg.remaining_qty, Decimal("60.00"))
        self.assertEqual(unknown.status, WorkOrder.Status.OPEN)
        self.assertTrue(run.warnings)

    def test_rounds_half_up_and_warns(self):
        run = sync(
            FakeYifeiSource(work_orders=[work_row(remaining_qty=Decimal("1.235"))])
        )
        order = WorkOrder.objects.get()
        self.assertEqual(order.remaining_qty, Decimal("1.24"))
        self.assertTrue(any("四舍五入" in item for item in run.warnings))

    def test_empty_open_list_closes_local_orders_and_warns(self):
        make_order(self.product, self.center, order_no="ONLY")
        run = sync(FakeYifeiSource(work_orders=[]))
        order = WorkOrder.objects.get(order_no="ONLY")
        self.assertEqual(order.status, WorkOrder.Status.CLOSED)
        self.assertTrue(any("未拉到任何未结工单" in item for item in run.warnings))

    def test_duplicate_rows_keep_the_later_quantity(self):
        run = sync(
            FakeYifeiSource(
                work_orders=[
                    work_row(remaining_qty=Decimal("10.00")),
                    work_row(remaining_qty=Decimal("20.00")),
                ]
            )
        )
        self.assertEqual(WorkOrder.objects.get().remaining_qty, Decimal("20.00"))
        self.assertEqual(run.created_count, 1)
        self.assertTrue(any("多次" in item for item in run.warnings))

    def test_failed_fetch_does_not_close_orders_and_hides_the_password(self):
        make_order(self.product, self.center, order_no="STAY")
        run = sync(
            FakeYifeiSource(
                work_order_error=RuntimeError("login failed password=s3cret-value")
            )
        )
        self.assertEqual(run.status, SyncRun.Status.FAILED)
        self.assertNotIn("s3cret-value", run.error)
        self.assertIn("******", run.error)
        self.assertEqual(WorkOrder.objects.get().status, WorkOrder.Status.OPEN)
        self.assertEqual(WorkOrder.objects.get().order_no, "STAY")

    def test_receipt_failure_keeps_the_work_order_write(self):
        run = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipt_error=RuntimeError("receipt password=s3cret-value"),
            )
        )
        self.assertEqual(run.status, SyncRun.Status.FAILED)
        self.assertEqual(run.created_count, 1)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertNotIn("s3cret-value", run.error)

    def test_product_change_on_a_dispatched_order_warns(self):
        bundle = make_single_dispatch(order_no="MO-1")
        other = make_product(code="P-NEW")
        run = sync(
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
        self.assertTrue(any("已有派工" in item for item in run.warnings))

    def test_overlap_skips_and_a_stale_lock_can_be_taken(self):
        SyncLock.objects.filter(name=SyncLock.WORKORDERS).update(
            holder="busy",
            held_until=timezone.now() + timedelta(minutes=5),
        )
        source = FakeYifeiSource(work_orders=[work_row()])
        skipped = sync(source)
        self.assertEqual(skipped.status, SyncRun.Status.SKIPPED)
        self.assertEqual(source.work_order_calls, 0)
        self.assertEqual(WorkOrder.objects.count(), 0)

        SyncLock.objects.filter(name=SyncLock.WORKORDERS).update(
            holder="dead",
            held_until=timezone.now() - timedelta(seconds=5),
        )
        SyncRun.objects.create(
            status=SyncRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(hours=1),
        )
        done = sync(FakeYifeiSource(work_orders=[work_row()]))
        self.assertEqual(done.status, SyncRun.Status.SUCCESS)
        self.assertEqual(
            SyncRun.objects.filter(status=SyncRun.Status.FAILED).count(),
            1,
        )
        self.assertEqual(WorkOrder.objects.count(), 1)


class ReceiptSyncTests(TestCase):
    def setUp(self):
        self.product = make_product(code="P1")
        self.center = make_center(code="WC1")

    def test_receipt_model_is_not_created_on_this_branch(self):
        self.assertIsNone(
            get_receipt_model(),
            "ProductionReceipt 已合并。请写入这个模型，不要另建表，并改掉本测试。",
        )

    def test_without_the_model_work_orders_still_sync_and_receipts_are_not_written(
        self,
    ):
        run = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row()],
            )
        )
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(run.created_count, 1)
        self.assertEqual(run.receipt_created_count, 0)
        self.assertEqual(run.receipt_skipped_count, 1)
        self.assertIn(RECEIPT_NOT_READY, run.warnings)

    def test_fake_receipts_create_update_and_stay_idempotent(self):
        writer = MemoryReceiptWriter()
        source = FakeYifeiSource(
            work_orders=[work_row()],
            receipts=[
                receipt_row(),
                receipt_row(
                    source_key="OTHER", order_no="MISSING", quantity=Decimal("1")
                ),
            ],
        )
        first = sync(source, writer)
        self.assertEqual(first.receipt_created_count, 1)
        self.assertEqual(first.receipt_skipped_count, 1)
        self.assertEqual(writer.rows["5801-1-0001"].quantity, Decimal("10.00"))
        self.assertIn("MISSING", " ".join(first.warnings))

        second = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(quantity=Decimal("12.50"))],
            ),
            writer,
        )
        self.assertEqual(second.receipt_updated_count, 1)
        self.assertEqual(writer.rows["5801-1-0001"].quantity, Decimal("12.50"))

        third = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(quantity=Decimal("12.50"))],
            ),
            writer,
        )
        self.assertEqual(third.receipt_unchanged_count, 1)
        self.assertEqual(third.receipt_created_count, 0)
        self.assertEqual(len(writer.rows), 1)

    def test_missing_receipt_rows_are_kept_until_delete_missing_is_enabled(self):
        writer = MemoryReceiptWriter()
        both = [
            receipt_row(source_key="A"),
            receipt_row(source_key="B", quantity=Decimal("2.00")),
        ]
        sync(FakeYifeiSource(work_orders=[work_row()], receipts=both), writer)
        kept = load_receipt_field_map({})
        self.assertFalse(kept.delete_missing)
        sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(source_key="A")],
            ),
            writer,
            kept,
        )
        self.assertEqual(set(writer.rows), {"A", "B"})

        removing = load_receipt_field_map({"YIFEI_RECEIPT_DELETE_MISSING": "yes"})
        run = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(source_key="A")],
            ),
            writer,
            removing,
        )
        self.assertEqual(run.receipt_removed_count, 1)
        self.assertEqual(set(writer.rows), {"A"})

    def test_unusable_receipt_is_not_deleted_by_a_full_snapshot(self):
        existing = receipt_row(source_key="KEEP", quantity=Decimal("3.00"))
        writer = MemoryReceiptWriter([existing])
        broken = receipt_row(source_key="KEEP", unusable_reason="数量无法解析")
        plan = plan_receipts(
            [broken],
            orders_by_no={"5101-1001": "P1"},
            delete_missing=True,
            existing_keys=writer.existing_keys(),
        )
        writer.apply(plan)
        self.assertEqual(writer.rows["KEEP"].quantity, Decimal("3.00"))
        self.assertEqual(plan.skipped, 1)

    def test_product_mismatch_is_a_warning_and_still_counts(self):
        writer = MemoryReceiptWriter()
        run = sync(
            FakeYifeiSource(
                work_orders=[work_row()],
                receipts=[receipt_row(product_code="OTHER")],
            ),
            writer,
        )
        self.assertEqual(run.receipt_created_count, 1)
        self.assertTrue(any("不一致" in item for item in run.warnings))
        self.assertEqual(writer.rows["5801-1-0001"].product_code, "OTHER")


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
        fetched = FetchResult(rows=[work_row()], warnings=[])
        receipts = FetchResult(rows=[receipt_row()], warnings=[])
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
                return_value=fetched,
            ),
            patch.object(
                SqlServerYifeiSource,
                "fetch_production_receipts",
                return_value=receipts,
            ),
        ):
            call_command("sync_erp_workorders")
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertIn(RECEIPT_NOT_READY, SyncRun.objects.get().warnings)

    def test_dry_run_rolls_back_work_orders(self):
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
                return_value=FetchResult(rows=[], warnings=[]),
            ),
        ):
            call_command("sync_erp_workorders", "--dry-run")
        self.assertEqual(WorkOrder.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

    def test_scheduler_refuses_overlapping_runs(self):
        scheduler = build_scheduler(interval_seconds=300)
        job = scheduler.get_job(JOB_ID)
        self.assertEqual(job.max_instances, 1)
        self.assertTrue(job.coalesce)
        self.assertEqual(int(job.trigger.interval.total_seconds()), 300)

    def test_sync_run_admin_is_read_only(self):
        SyncRun.objects.create(
            status=SyncRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        User.objects.create_superuser("root", "root@example.com", "secret-pass")
        self.client.login(username="root", password="secret-pass")
        self.assertEqual(self.client.get("/admin/erp/syncrun/").status_code, 200)
        self.assertEqual(self.client.get("/admin/erp/syncrun/add/").status_code, 403)
