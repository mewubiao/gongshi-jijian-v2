from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from reporting.duration import overlap_seconds
from reporting.forms import QueueFilterForm
from reporting.models import PieceReport
from reporting.selectors import with_effective_quantity
from terminal.auth import worker_required
from terminal.forms import TaskForm
from terminal.operations import (
    personal_report_action,
    report_ticket,
    task_action,
    task_ticket,
)
from terminal.selectors import assigned_tasks, personal_reports


@never_cache
@worker_required
@require_http_methods(["GET", "POST"])
def task_detail(request, pk):
    employee, device = request.terminal_employee, request.terminal_device
    dispatch = get_object_or_404(assigned_tasks(employee), pk=pk)
    form = TaskForm(request.POST if request.method == "POST" else None)
    response_status = 200
    if request.method == "POST":
        if dispatch.current_leader_id != employee.pk:
            raise PermissionDenied
        if form.is_valid():
            try:
                report = task_action(
                    device=device,
                    employee=employee,
                    dispatch_id=dispatch.pk,
                    action=request.POST.get("action"),
                    data=form.cleaned_data,
                    present_ids=request.POST.getlist("present_members"),
                )
            except ValidationError as error:
                form.add_error(None, error.messages)
                response_status = 409
            else:
                return (
                    redirect("terminal:report", pk=report.pk)
                    if report
                    and personal_reports(employee).filter(pk=report.pk).exists()
                    else redirect("terminal:task", pk=dispatch.pk)
                )
        else:
            response_status = 400
        dispatch.refresh_from_db()
    session = dispatch.sessions.filter(ended_at__isnull=True).first()
    last = dispatch.reports.order_by("-submitted_at", "-pk").first()
    pause = session.pauses.filter(resumed_at__isnull=True).first() if session else None
    elapsed = 0
    if session:
        now = timezone.now()
        elapsed = max(
            0,
            int((now - session.started_at).total_seconds())
            - sum(
                overlap_seconds(
                    session.started_at, now, value.paused_at, value.resumed_at or now
                )
                for value in session.pauses.all()
            ),
        )
    roster = list(dispatch.members.select_related("employee").order_by("sequence"))
    present_ids = (
        set(
            session.member_spans.filter(left_at__isnull=True).values_list(
                "employee_id", flat=True
            )
        )
        if session
        else set()
    )
    for member in roster:
        member.present = member.employee_id in present_ids
        member.checked = request.method != "POST" or str(
            member.employee_id
        ) in request.POST.getlist("present_members")
    return render(
        request,
        "terminal/task.html",
        {
            "employee": employee,
            "device": device,
            "dispatch": dispatch,
            "session": session,
            "pause": pause,
            "elapsed": elapsed,
            "form": form,
            "ticket": task_ticket(dispatch, employee),
            "roster": roster,
            "last_report": last,
            "last_report_visible": last is not None
            and personal_reports(employee).filter(pk=last.pk).exists(),
            "can_operate": dispatch.current_leader_id == employee.pk,
            "can_open": session is None
            and (last is None or last.status == "withdrawn"),
        },
        status=response_status,
    )


@never_cache
@worker_required
@require_GET
def reports(request):
    filters = QueueFilterForm(request.GET, statuses=PieceReport.Status.choices)
    filters.fields["q"].label = "工单／工序"
    records = with_effective_quantity(personal_reports(request.terminal_employee))
    valid = filters.is_valid()
    if valid:
        data = filters.cleaned_data
        if data["status"]:
            records = records.filter(status=data["status"])
        if data["month"]:
            records = records.filter(
                ownership_date__year=data["month"].year,
                ownership_date__month=data["month"].month,
            )
        if data["q"]:
            records = records.filter(
                Q(dispatch__work_order__order_no__icontains=data["q"])
                | Q(dispatch__process__name__icontains=data["q"])
            )
    else:
        records = records.none()
    return render(
        request,
        "terminal/reports.html",
        {
            "employee": request.terminal_employee,
            "device": request.terminal_device,
            "filters": filters,
            "page_obj": Paginator(
                records.order_by("-submitted_at", "-pk"),
                20,
            ).get_page(request.GET.get("page")),
        },
        status=200 if valid else 400,
    )


@never_cache
@worker_required
@require_http_methods(["GET", "POST"])
def report_detail(request, pk):
    employee, device = request.terminal_employee, request.terminal_device
    report = get_object_or_404(personal_reports(employee), pk=pk)
    form = TaskForm(request.POST if request.method == "POST" else None)
    response_status = 200
    if request.method == "POST":
        if report.reported_by_id != employee.pk:
            raise PermissionDenied
        if form.is_valid():
            try:
                record = personal_report_action(
                    device=device,
                    employee=employee,
                    report_id=report.pk,
                    action=request.POST.get("action"),
                    data=form.cleaned_data,
                )
            except ValidationError as error:
                form.add_error(None, error.messages)
                response_status = 409
            else:
                return redirect("terminal:report", pk=record.pk)
        else:
            response_status = 400
        report.refresh_from_db()
    return render(
        request,
        "terminal/report.html",
        {
            "employee": employee,
            "device": device,
            "report": report,
            "form": form,
            "ticket": report_ticket(report, employee),
            "quantity": report.effective_completed_qty(),
            "seconds": report.effective_actual_seconds(),
            "own_group_seconds": report.work_session.member_spans.filter(
                employee=employee
            ).aggregate(total=Sum("frozen_actual_seconds"))["total"],
            "rejections": report.rejections.order_by("-rejected_at", "-pk"),
            "corrections": report.corrections.order_by("-corrected_at", "-pk"),
            "resubmission": report.resubmissions.order_by(
                "-submitted_at", "-pk"
            ).first(),
        },
        status=response_status,
    )
