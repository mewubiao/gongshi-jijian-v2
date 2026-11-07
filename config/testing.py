"""测试里共用的主数据构造。不是业务代码。"""

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from dispatch.models import Dispatch
from erp.models import WorkOrder
from masterdata.models import Employee, Process, Product, Team, WorkCenter
from reporting.models import WorkSession

SHANGHAI = ZoneInfo("Asia/Shanghai")


def make_login_user(employee):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(f"employee-{employee.pk}")
    employee.user = user
    employee.save()
    return user


def at(*parts):
    return datetime(*parts, tzinfo=SHANGHAI)


def make_center(code=None, **times):
    if code is None:
        code = f"WC{WorkCenter.objects.count() + 1}"
    payload = {
        "day_shift_start": time(8, 0),
        "day_shift_end": time(20, 0),
        "night_shift_start": time(20, 0),
        "night_shift_end": time(8, 0),
    }
    payload.update(times)
    return WorkCenter.objects.create(code=code, name=code, **payload)


def make_employee(name, role, **kwargs):
    sequence = Employee.objects.count() + 1
    qr_code = kwargs.pop("qr_code", None) or f"qr-{sequence}-{name}"
    code = kwargs.pop("code", None) or f"E{sequence:04d}"
    return Employee.objects.create(
        name=name,
        role=role,
        qr_code=qr_code,
        code=code,
        **kwargs,
    )


def make_team(center, leader, name="甲班", **times):
    code = times.pop("code", None) or f"T{Team.objects.count() + 1:03d}"
    team = Team.objects.create(
        code=code,
        name=name,
        work_center=center,
        leader=leader,
        **times,
    )
    if leader.team_id is None:
        leader.team = team
        leader.save()
    return team


def make_product(code=None, name=None):
    if code is None:
        code = f"P{Product.objects.count() + 1}"
    return Product.objects.create(code=code, name=name or code)


def make_process(product, **kwargs):
    sequence = kwargs.pop("sequence", 10)
    operation_code = kwargs.pop("operation_code", None) or f"OP{sequence}"
    return Process.objects.create(
        product=product,
        sequence=sequence,
        operation_code=operation_code,
        name=kwargs.pop("name", f"工序{sequence}"),
        unit_price=Decimal(kwargs.pop("unit_price", "1.25")),
        standard_minutes=Decimal(kwargs.pop("standard_minutes", "30.00")),
    )


def make_order(product, center, **kwargs):
    order_no = kwargs.pop("order_no", None)
    if order_no is None:
        order_no = f"MO-{WorkOrder.objects.count() + 1}"
    return WorkOrder.objects.create(
        order_no=order_no,
        product=product,
        work_center=center,
        status=kwargs.pop("status", WorkOrder.Status.OPEN),
        remaining_qty=Decimal(kwargs.pop("remaining_qty", "100.00")),
        synced_at=kwargs.pop("synced_at", None),
    )


def make_single_dispatch(
    *,
    order_no="MO-1",
    quantity="10.00",
    remaining="100.00",
    start=None,
):
    """一名工人、一个班组长、一条已派工。工人的班组继承工作中心班次。"""
    center = make_center()
    leader = make_employee("组长", Employee.Role.TEAM_LEADER)
    team = make_team(center, leader)
    worker = make_employee("工人", Employee.Role.WORKER, team=team)
    product = make_product()
    process = make_process(product)
    order = make_order(
        product,
        center,
        order_no=order_no,
        remaining_qty=remaining,
    )
    dispatch = Dispatch.create_single(
        work_order=order,
        process=process,
        quantity=Decimal(quantity),
        assignee=worker,
        assigned_by=leader,
        assigned_at=start or at(2026, 10, 31, 8, 0),
    )
    return {
        "center": center,
        "leader": leader,
        "team": team,
        "worker": worker,
        "product": product,
        "process": process,
        "order": order,
        "dispatch": dispatch,
    }


def report_single(bundle, start, end, qty="2.00"):
    session = WorkSession.open(
        dispatch=bundle["dispatch"],
        started_by=bundle["worker"],
        started_at=start,
    )
    return session.finish_and_report(
        completed_qty=Decimal(qty),
        reported_by=bundle["worker"],
        submitted_at=end,
    )
