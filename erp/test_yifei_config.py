from django.test import SimpleTestCase

from erp.yifei_config import (
    VERIFICATION_NOTE,
    YifeiConfigError,
    load_connection_config,
    load_receipt_field_map,
    load_schedule_config,
    load_work_order_field_map,
)


class YifeiConfigTests(SimpleTestCase):
    def test_defaults_are_marked_unverified(self):
        self.assertEqual(VERIFICATION_NOTE, "待按实际库核对")
        work_orders = load_work_order_field_map({})
        receipts = load_receipt_field_map({})
        self.assertEqual(work_orders.table, "MOCTA")
        self.assertEqual(work_orders.order_type_column, "TA001")
        self.assertEqual(work_orders.work_center_column, "TA021")
        self.assertEqual(work_orders.open_status_values, ("1", "2", "3"))
        self.assertEqual(work_orders.closed_status_values, ("Y", "y"))
        self.assertEqual(receipts.table, "MOCTG")
        self.assertEqual(receipts.quantity_column, "TG013")
        self.assertEqual(receipts.source_qty_11_column, "TG011")
        self.assertEqual(receipts.scrap_qty_12_column, "TG012")
        self.assertEqual(receipts.accepted_qty_13_column, "TG013")
        self.assertFalse(receipts.delete_missing)

    def test_environment_overrides_table_and_rejects_unsafe_names(self):
        mapped = load_work_order_field_map({"YIFEI_TABLE": "CUSTOM_MO"})
        self.assertEqual(mapped.table, "CUSTOM_MO")
        with self.assertRaises(YifeiConfigError):
            load_work_order_field_map({"YIFEI_TABLE": "MOCTA;DROP"})
        with self.assertRaises(YifeiConfigError):
            load_receipt_field_map({"YIFEI_RECEIPT_COL_QTY": "TG011 OR 1=1"})

    def test_connection_string_is_read_only_and_braces_the_password(self):
        config = load_connection_config(
            {
                "YIFEI_SQLSERVER_HOST": "10.0.0.8",
                "YIFEI_SQLSERVER_DATABASE": "DEMO",
                "YIFEI_SQLSERVER_USER": "reader",
                "YIFEI_SQLSERVER_PASSWORD": "p;a}ss",
            }
        )
        text = config.odbc_connection_string()
        self.assertIn("PWD={p;a}}ss}", text)
        self.assertIn("ApplicationIntent=ReadOnly", text)
        self.assertIn("Encrypt={yes}", text)
        self.assertIn("TrustServerCertificate={no}", text)
        self.assertNotIn("p;a}ss;", text)

    def test_missing_connection_names_the_variables_not_a_password(self):
        with self.assertRaises(YifeiConfigError) as raised:
            load_connection_config({})
        self.assertIn("YIFEI_SQLSERVER_PASSWORD", str(raised.exception))
        self.assertNotIn("PWD=", str(raised.exception))

    def test_stale_lock_must_outlast_the_interval(self):
        with self.assertRaises(YifeiConfigError):
            load_schedule_config(
                {
                    "YIFEI_SYNC_INTERVAL_SECONDS": "300",
                    "YIFEI_SYNC_STALE_SECONDS": "300",
                }
            )
        schedule = load_schedule_config({})
        self.assertEqual(schedule.interval_seconds, 300)
        self.assertEqual(schedule.stale_seconds, 900)
