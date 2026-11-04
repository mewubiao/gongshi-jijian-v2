from datetime import datetime
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from erp.queries import (
    ReadOnlyViolation,
    assert_single_select,
    build_receipt_query,
    build_work_order_query,
    parse_receipt_records,
    parse_work_order_records,
)
from erp.yifei_config import load_receipt_field_map, load_work_order_field_map


class QueryTests(SimpleTestCase):
    def test_work_order_sql_is_a_single_select_with_bound_statuses(self):
        sql, params = build_work_order_query(load_work_order_field_map({}))
        self.assertTrue(sql.startswith("SELECT "))
        self.assertNotIn(";", sql)
        self.assertIn("FROM [MOCTA]", sql)
        self.assertIn("[TA001] AS order_type", sql)
        self.assertIn("[TA021] AS work_center_code", sql)
        self.assertIn("[TA011] IN (?, ?, ?)", sql)
        self.assertEqual(list(params), ["1", "2", "3"])
        assert_single_select(sql)

    def test_receipt_sql_uses_the_unverified_receipt_table(self):
        sql, params = build_receipt_query(
            load_receipt_field_map({"YIFEI_RECEIPT_EXTRA_FILTERS": "TG022=Y"})
        )
        self.assertTrue(sql.startswith("SELECT "))
        self.assertIn("FROM [MOCTG]", sql)
        self.assertIn("[TG014] AS order_type", sql)
        self.assertIn("[TG015] AS order_no", sql)
        self.assertIn("[TG011] AS quantity", sql)
        self.assertIn("[TG022] = ?", sql)
        self.assertEqual(params, ["Y"])
        self.assertNotIn("INSERT", sql.upper())
        self.assertNotIn("UPDATE", sql.upper())
        self.assertNotIn("DELETE", sql.upper())

    def test_only_a_single_select_is_allowed(self):
        with self.assertRaises(ReadOnlyViolation):
            assert_single_select("DELETE FROM MOCTA")
        with self.assertRaises(ReadOnlyViolation):
            assert_single_select("SELECT 1; DELETE FROM MOCTA")

    def test_parser_strips_char_padding_and_reads_an_unverified_timestamp(self):
        rows, warnings = parse_work_order_records(
            [
                {
                    "order_type": "5101",
                    "order_no": " 1001 ",
                    "product_code": "P1",
                    "planned_qty": "100.00",
                    "completed_qty": "40",
                    "work_center_code": "WC1",
                    "status_raw": "1 ",
                    "source_updated_at": "20261104153000",
                }
            ],
            load_work_order_field_map({}),
        )
        self.assertEqual(warnings, [])
        self.assertEqual(rows[0].order_no, "5101-1001")
        self.assertEqual(rows[0].status_raw, "1")
        self.assertEqual(rows[0].planned_qty, Decimal("100.00"))
        self.assertFalse(timezone.is_naive(rows[0].source_updated_at))
        local = timezone.localtime(rows[0].source_updated_at)
        self.assertEqual(local, datetime(2026, 11, 4, 15, 30, tzinfo=local.tzinfo))

    def test_receipt_parser_builds_a_source_key(self):
        rows, warnings = parse_receipt_records(
            [
                {
                    "order_type": "5101",
                    "order_no": "1001",
                    "receipt_type": "5801",
                    "receipt_no": "1",
                    "line_no": "0001",
                    "product_code": "P1",
                    "quantity": "2.50",
                }
            ],
            load_receipt_field_map({}),
        )
        self.assertEqual(warnings, [])
        self.assertEqual(rows[0].source_key, "5801-1-0001")
        self.assertEqual(rows[0].order_no, "5101-1001")
        self.assertEqual(rows[0].quantity, Decimal("2.50"))
        self.assertEqual(rows[0].unusable_reason, "")

    def test_unparseable_receipt_quantity_does_not_raise(self):
        rows, _warnings = parse_receipt_records(
            [
                {
                    "order_no": "1001",
                    "receipt_no": "1",
                    "line_no": "1",
                    "quantity": "abc",
                }
            ],
            load_receipt_field_map({"YIFEI_RECEIPT_COL_ORDER_TYPE": ""}),
        )
        self.assertIn("无法解析", rows[0].unusable_reason)
        self.assertEqual(rows[0].source_key, "1-1")
