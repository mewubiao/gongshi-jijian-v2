from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from config.testing import make_center, make_order, make_product
from erp.models import WorkOrder


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
