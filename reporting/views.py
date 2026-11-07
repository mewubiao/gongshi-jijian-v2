from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from accounts.scopes import scope_queryset
from accounts.workflow import business_actor, queue_return_url
from masterdata.access import employee_for_user
from masterdata.models import Employee
from reporting.forms import PieceApprovalForm, QueueFilterForm
from reporting.models import PieceReport, WorkSession
from reporting.review import review_piece
from reporting.review_common import RejectForm, ReverseForm, review_revision
from reporting.selectors import with_effective_quantity
from settlement.models import SettlementPeriod


@login_required
@permission_required("reporting.view_piecereport", raise_exception=True)
@require_GET
def report_list(request):
    employee = employee_for_user(request.user)
    is_reviewer = employee is not None and employee.role == Employee.Role.TEAM_LEADER
    filters = QueueFilterForm(
        request.GET,
        statuses=PieceReport.Status.choices,
        default_status=PieceReport.Status.PENDING if is_reviewer else "",
    )
    records = with_effective_quantity(
        scope_queryset(request.user, PieceReport.objects.all())
    ).select_related(
        "dispatch__work_order", "dispatch__process", "dispatch__team", "reported_by"
    )
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
                | Q(reported_by__name__icontains=data["q"])
                | Q(reported_by__code__icontains=data["q"])
            )
    else:
        records = records.none()
    return render(
        request,
        "reporting/list.html",
        {
            "employee": employee,
            "is_reviewer": is_reviewer,
            "return_to": request.get_full_path(),
            "filters": filters,
            "page_obj": Paginator(
                records.order_by("-submitted_at", "-pk"), 20
            ).get_page(request.GET.get("page")),
        },
        status=200 if valid else 400,
    )


@login_required
@permission_required("reporting.view_piecereport", raise_exception=True)
@require_http_methods(["GET", "POST"])
def report_detail(request, pk):
    return_to = queue_return_url(request, "reporting:list")
    report = get_object_or_404(
        scope_queryset(request.user, PieceReport.objects.all()).select_related(
            "dispatch__work_order__product",
            "dispatch__process",
            "dispatch__team",
            "reported_by",
            "approved_by",
            "work_session",
        ),
        pk=pk,
    )
    employee = employee_for_user(request.user)
    revision = review_revision(report)
    approve_form = PieceApprovalForm(
        initial={"revision": revision}, auto_id="approve_%s"
    )
    reject_form = RejectForm(initial={"revision": revision}, auto_id="reject_%s")
    reverse_form = ReverseForm(initial={"revision": revision}, auto_id="reverse_%s")
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        permission, role = (
            ("reporting.reverse_piecereport", Employee.Role.ADMIN)
            if action == "reverse"
            else ("reporting.review_piecereport", Employee.Role.TEAM_LEADER)
        )
        actor = business_actor(request, permission, role)
        forms_by_action = {
            "approve": PieceApprovalForm,
            "reject": RejectForm,
            "reverse": ReverseForm,
        }
        form_class = forms_by_action.get(action)
        if form_class is None:
            return render(request, "reporting/invalid_action.html", status=400)
        form = form_class(request.POST, auto_id=f"{action}_%s")
        if action == "approve":
            approve_form = form
        elif action == "reject":
            reject_form = form
        else:
            reverse_form = form
        if form.is_valid():
            data = form.cleaned_data
            try:
                review_piece(
                    report_id=report.pk,
                    actor=actor,
                    action=action,
                    revision=data["revision"],
                    reason=data.get("reason", ""),
                    corrected_qty=data.get("corrected_qty"),
                    corrected_actual_seconds=data.get("actual_seconds"),
                    confirm_excess=data.get("confirm_excess", False),
                )
            except ValidationError as error:
                form.add_error(None, error.messages)
                status = 409
                report.refresh_from_db()
            else:
                messages.success(
                    request,
                    {
                        "approve": "报工已审核通过。",
                        "reject": "报工已驳回。",
                        "reverse": "已反审核，报工返回待审核。",
                    }[action],
                )
                if request.POST.get("after") == "queue":
                    return redirect(return_to)
                return redirect("reporting:detail", pk=report.pk)
        else:
            status = 400
    locked = SettlementPeriod.objects.filter(
        year=report.ownership_date.year,
        month=report.ownership_date.month,
        status=SettlementPeriod.Status.LOCKED,
    ).exists()
    session = report.work_session
    parallel = (
        report.dispatch.kind == "single"
        and session.ended_at is not None
        and WorkSession.objects.filter(
            started_by=session.started_by, started_at__lt=session.ended_at
        )
        .filter(Q(ended_at__gt=session.started_at) | Q(ended_at__isnull=True))
        .exclude(pk=session.pk)
        .exists()
    )
    return render(
        request,
        "reporting/detail.html",
        {
            "employee": employee,
            "return_to": return_to,
            "report": report,
            "effective_qty": report.effective_completed_qty(),
            "effective_seconds": report.effective_actual_seconds(),
            "approve_form": approve_form,
            "reject_form": reject_form,
            "reverse_form": reverse_form,
            "can_review": not locked
            and report.status == PieceReport.Status.PENDING
            and employee is not None
            and employee.role == Employee.Role.TEAM_LEADER,
            "can_reverse": not locked
            and report.status == PieceReport.Status.APPROVED
            and employee is not None
            and employee.role == Employee.Role.ADMIN,
            "locked": locked,
            "parallel": parallel and report.allocated_actual_seconds is None,
            "corrections": report.corrections.select_related("corrected_by").order_by(
                "-corrected_at", "-pk"
            ),
            "rejections": report.rejections.select_related("rejected_by").order_by(
                "-rejected_at", "-pk"
            ),
            "reversals": report.approval_reversals.select_related(
                "reversed_by", "approved_by"
            ).order_by("-reversed_at", "-pk"),
        },
        status=status,
    )
