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
from reporting.forms import QueueFilterForm
from reporting.review_common import RejectForm, review_revision
from settlement.models import SettlementPeriod
from timekeeping.forms import AuxiliaryApprovalForm
from timekeeping.models import AuxiliaryWorkLog
from timekeeping.review import review_auxiliary
from timekeeping.selectors import with_effective_minutes


@login_required
@permission_required("timekeeping.view_auxiliaryworklog", raise_exception=True)
@require_GET
def auxiliary_list(request):
    employee = employee_for_user(request.user)
    is_reviewer = employee is not None and employee.role == Employee.Role.TEAM_LEADER
    filters = QueueFilterForm(
        request.GET,
        statuses=AuxiliaryWorkLog.Status.choices,
        default_status=AuxiliaryWorkLog.Status.PENDING_REVIEW if is_reviewer else "",
    )
    filters.fields["q"].label = "员工／作业类别"
    records = with_effective_minutes(
        scope_queryset(request.user, AuxiliaryWorkLog.objects.all())
    ).select_related("employee", "team")
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
                Q(employee__name__icontains=data["q"])
                | Q(employee__code__icontains=data["q"])
                | Q(category_name_snapshot__icontains=data["q"])
            )
    else:
        records = records.none()
    return render(
        request,
        "timekeeping/list.html",
        {
            "employee": employee,
            "is_reviewer": is_reviewer,
            "return_to": request.get_full_path(),
            "filters": filters,
            "page_obj": Paginator(records.order_by("-started_at", "-pk"), 20).get_page(
                request.GET.get("page")
            ),
        },
        status=200 if valid else 400,
    )


@login_required
@permission_required("timekeeping.view_auxiliaryworklog", raise_exception=True)
@require_http_methods(["GET", "POST"])
def auxiliary_detail(request, pk):
    return_to = queue_return_url(request, "timekeeping:list")
    log = get_object_or_404(
        scope_queryset(request.user, AuxiliaryWorkLog.objects.all()).select_related(
            "employee", "team", "approved_by"
        ),
        pk=pk,
    )
    employee = employee_for_user(request.user)
    revision = review_revision(log)
    approve_form = AuxiliaryApprovalForm(
        initial={"revision": revision}, auto_id="approve_%s"
    )
    reject_form = RejectForm(initial={"revision": revision}, auto_id="reject_%s")
    status = 200
    if request.method == "POST":
        actor = business_actor(
            request, "timekeeping.review_auxiliaryworklog", Employee.Role.TEAM_LEADER
        )
        action = request.POST.get("action")
        if action not in ("approve", "reject"):
            return render(request, "reporting/invalid_action.html", status=400)
        form = (
            AuxiliaryApprovalForm(request.POST, auto_id="approve_%s")
            if action == "approve"
            else RejectForm(request.POST, auto_id="reject_%s")
        )
        if action == "approve":
            approve_form = form
        else:
            reject_form = form
        if form.is_valid():
            data = form.cleaned_data
            try:
                review_auxiliary(
                    log_id=log.pk,
                    actor=actor,
                    action=action,
                    revision=data["revision"],
                    reason=data.get("reason", ""),
                    minutes=data.get("minutes"),
                )
            except ValidationError as error:
                form.add_error(None, error.messages)
                status = 409
                log.refresh_from_db()
            else:
                messages.success(
                    request,
                    "辅助工时已审核通过。"
                    if action == "approve"
                    else "辅助工时已驳回。",
                )
                if request.POST.get("after") == "queue":
                    return redirect(return_to)
                return redirect("timekeeping:detail", pk=log.pk)
        else:
            status = 400
    locked = SettlementPeriod.objects.filter(
        year=log.ownership_date.year,
        month=log.ownership_date.month,
        status=SettlementPeriod.Status.LOCKED,
    ).exists()
    return render(
        request,
        "timekeeping/detail.html",
        {
            "employee": employee,
            "return_to": return_to,
            "log": log,
            "effective_minutes": log.effective_minutes_for_review(),
            "approve_form": approve_form,
            "reject_form": reject_form,
            "locked": locked,
            "can_review": not locked
            and log.status == AuxiliaryWorkLog.Status.PENDING_REVIEW
            and employee is not None
            and employee.role == Employee.Role.TEAM_LEADER,
            "corrections": log.corrections.select_related("corrected_by").order_by(
                "-corrected_at", "-pk"
            ),
            "rejections": log.rejections.select_related("rejected_by").order_by(
                "-rejected_at", "-pk"
            ),
        },
        status=status,
    )
