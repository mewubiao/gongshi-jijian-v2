import os
import secrets
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from dispatch.models import Dispatch
from erp.models import WorkOrder
from masterdata.models import Employee, Process, Product, Team, WorkCenter
from reporting.models import WorkSession
from terminal.models import TerminalDevice
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog


class Command(BaseCommand):
    help = "只在独立的 .mvp-demo.sqlite3 中初始化演示数据，不接触业务库"

    def handle(self, *args, **options):
        if Path(connection.settings_dict["NAME"]).name != ".mvp-demo.sqlite3":
            raise CommandError("请通过 scripts/start_mvp_demo.sh 使用独立演示库")
        if Employee.objects.exists() or get_user_model().objects.exists():
            self.stdout.write("演示库已有数据，保留现有账号和任务。")
            return
        password = "Demo-" + secrets.token_urlsafe(16)
        with transaction.atomic():
            people = {}
            for code, name, role, username in [
                ("A001", "演示管理员", Employee.Role.ADMIN, "demo-admin"),
                ("L001", "张组长", Employee.Role.TEAM_LEADER, "demo-leader"),
                ("G001", "演示工艺", Employee.Role.PROCESS_ENGINEER, "demo-engineer"),
                ("F001", "演示财务", Employee.Role.SETTLEMENT, "demo-finance"),
                ("W001", "李师傅", Employee.Role.WORKER, None),
                ("W002", "王师傅", Employee.Role.WORKER, None),
                ("W003", "陈师傅", Employee.Role.WORKER, None),
            ]:
                user = None
                if username:
                    factory = (
                        get_user_model().objects.create_superuser
                        if role == Employee.Role.ADMIN
                        else get_user_model().objects.create_user
                    )
                    user = factory(username, password=password)
                people[code] = Employee.objects.create(
                    code=code,
                    name=name,
                    role=role,
                    qr_code="MVP-" + secrets.token_urlsafe(18),
                    badge_nfc="0000" + code[1:]
                    if role == Employee.Role.WORKER
                    else None,
                    user=user,
                )
            center = WorkCenter.objects.create(
                code="MVP-WC",
                name="演示冲压车间",
                day_shift_start=time(8),
                day_shift_end=time(20),
                night_shift_start=time(20),
                night_shift_end=time(8),
            )
            team = Team.objects.create(
                code="MVP-T", name="冲压一班", work_center=center, leader=people["L001"]
            )
            for code in ("L001", "W001", "W002", "W003"):
                people[code].team = team
                people[code].save()
            product = Product.objects.create(
                code="MVP-P01", name="精密支架", specification="MVP 演示件", unit="件"
            )
            cut = Process.objects.create(
                product=product,
                operation_code="CUT",
                sequence=10,
                name="下料",
                unit_price=Decimal("0.80"),
                standard_minutes=Decimal("10"),
            )
            press = Process.objects.create(
                product=product,
                operation_code="PRESS",
                sequence=20,
                name="冲压",
                unit_price=Decimal("1.25"),
                standard_minutes=Decimal("30"),
            )
            order = WorkOrder.objects.create(
                order_no="MO-MVP-001",
                product=product,
                work_center=center,
                remaining_qty=Decimal("200"),
                unit="件",
                synced_at=timezone.now(),
            )
            for process in (cut, press):
                Dispatch.create_single(
                    work_order=order,
                    process=process,
                    quantity=Decimal("100"),
                    assignee=people["W001"],
                    assigned_by=people["L001"],
                )
            Dispatch.create_group(
                work_order=order,
                process=press,
                quantity=Decimal("100"),
                members=[people["W002"], people["W003"]],
                assigned_by=people["L001"],
            )
            review_dispatch = Dispatch.create_single(
                work_order=order,
                process=press,
                quantity=Decimal("10"),
                assignee=people["W001"],
                assigned_by=people["L001"],
            )
            now = timezone.now()
            WorkSession.open(
                dispatch=review_dispatch,
                started_by=people["W001"],
                started_at=now - timedelta(minutes=90),
            ).finish_and_report(
                completed_qty=Decimal("12"),
                reported_by=people["W001"],
                submitted_at=now - timedelta(minutes=30),
            )
            AuxiliaryWorkCategory.objects.create(code="CLEAN", name="清理现场")
            category = AuxiliaryWorkCategory.objects.create(
                code="MAINT", name="设备维护", note_required=True
            )
            log = AuxiliaryWorkLog.start(
                employee=people["W003"],
                team=team,
                category=category,
                started_at=now - timedelta(hours=2),
                work_note="协助检修冲床",
            )
            log.submit(ended_at=now - timedelta(hours=1))
            device = TerminalDevice.objects.create(code="MVP-K01", name="演示车间终端")
            activation = device.issue_activation()
        output = Path(settings.BASE_DIR) / ".mvp-demo-credentials.txt"
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(
                "仅用于本机独立演示库，不是真实业务账号。\n"
                "管理入口：http://127.0.0.1:8011/\n"
                "终端入口：http://127.0.0.1:8011/terminal/\n"
                "账号：demo-admin / demo-leader / demo-engineer / demo-finance\n"
                "共同演示密码："
                + password
                + "\n终端一次性激活码（15 分钟有效；过期可由管理员重新授权）："
                + activation
                + "\n"
            )
            for code in ("W001", "W002", "W003"):
                worker = people[code]
                file.write(
                    f"{worker.name}（{worker.code}） NFC：{worker.badge_nfc}  "
                    f"二维码内容：{worker.qr_code}\n"
                )
        self.stdout.write(f"已初始化独立演示数据，账号与工牌标识见 {output}")
