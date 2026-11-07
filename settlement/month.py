from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from masterdata.models import Employee
from settlement.checks import assert_ready_to_lock
from settlement.hours import approved_auxiliary_minutes
from settlement.models import SettlementLockEvent, SettlementPeriod
from settlement.piecework import calculate_piecework
from settlement.revisions import settlement_revision


def read_month(year, month):
    """取得同一期间内的计算依据；读取后释放锁，再渲染或写 Excel。"""
    with transaction.atomic():
        period = (
            SettlementPeriod.objects.select_for_update()
            .filter(year=year, month=month)
            .first()
        )
        period = period or SettlementPeriod(year=year, month=month)
        locked = period.status == SettlementPeriod.Status.LOCKED
        entries, blockers, errors, history = [], [], [], []
        event = None
        if period.pk:
            history = list(period.events.select_related("actor").order_by("-pk")[:10])
        if locked:
            if history and history[0].action == SettlementLockEvent.Action.LOCK:
                event = history[0]
            if event and event.has_piecework_snapshot:
                entries = list(event.piecework_entries.all())
            minutes = dict(
                period.auxiliary_hour_summaries.values_list(
                    "employee_id", "total_minutes"
                )
            )
            revision = settlement_revision(period)
        else:
            try:
                assert_ready_to_lock(year, month)
            except ValidationError as error:
                blockers = error.messages
            try:
                entries = calculate_piecework(year, month)
            except ValidationError as error:
                errors = error.messages
            minutes = approved_auxiliary_minutes(year, month)
            revision = (
                settlement_revision(period, entries, minutes)
                if not (blockers or errors)
                else ""
            )
        summary = employee_totals(entries, minutes)
    return {
        "period": period,
        "locked": locked,
        "event": event,
        "entries": entries,
        "minutes": minutes,
        "summary": summary,
        "blockers": blockers,
        "errors": errors,
        "history": history,
        "revision": revision,
        "legacy": locked and not (event and event.has_piecework_snapshot),
        "report_count": len({entry.report_id for entry in entries}),
        "total_amount": Decimal(sum(entry.amount_cents for entry in entries)).scaleb(
            -2
        ),
        "total_minutes": sum(minutes.values(), Decimal("0")),
    }


def employee_totals(entries, minutes):
    totals = {}
    for entry in entries:
        item = totals.setdefault(
            entry.employee_id,
            {
                "code": entry.employee_code,
                "name": entry.employee_name,
                "cents": 0,
            },
        )
        item["cents"] += entry.amount_cents
    for employee in Employee.objects.filter(pk__in=minutes.keys() - totals.keys()):
        totals[employee.pk] = {"code": employee.code, "name": employee.name, "cents": 0}
    for pk, item in totals.items():
        item["amount"] = Decimal(item.pop("cents")).scaleb(-2)
        item["minutes"] = minutes.get(pk, Decimal("0"))
    return sorted(totals.values(), key=lambda item: (item["code"], item["name"]))
