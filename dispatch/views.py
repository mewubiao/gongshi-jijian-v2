from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from accounts.scopes import scope_queryset
from accounts.workflow import business_actor
from dispatch.forms import DispatchForm
from dispatch.models import Dispatch
from dispatch.selectors import available_orders, search_orders
from dispatch.services import create_dispatch
from erp.models import WorkOrder
from masterdata.access import employee_for_user
from masterdata.models import Employee


@login_required
@permission_required("dispatch.view_dispatch", raise_exception=True)
@require_GET
def dispatch_list(request):
    records = (
        scope_queryset(request.user, Dispatch.objects.all())
        .select_related("work_order__product", "process", "team", "current_leader")
        .prefetch_related("members__employee")
    )
    query = request.GET.get("q", "").strip()[:100]
    if query:
        records = records.filter(
            Q(work_order__order_no__icontains=query)
            | Q(process__name__icontains=query)
            | Q(members__employee__name__icontains=query)
        ).distinct()
    actor = employee_for_user(request.user)
    return render(
        request,
        "dispatch/list.html",
        {
            "employee": actor,
            "query": query,
            "page_obj": Paginator(records.order_by("-assigned_at", "-pk"), 20).get_page(
                request.GET.get("page")
            ),
            "can_create": actor is not None and actor.role == Employee.Role.TEAM_LEADER,
        },
    )


@login_required
@require_GET
def order_list(request):
    actor = business_actor(request, "dispatch.add_dispatch", Employee.Role.TEAM_LEADER)
    query = request.GET.get("q", "").strip()[:100]
    records = search_orders(
        available_orders(actor).filter(
            status=WorkOrder.Status.OPEN, remaining_qty__gt=0
        ),
        query,
    )
    return render(
        request,
        "dispatch/orders.html",
        {
            "employee": actor,
            "query": query,
            "page_obj": Paginator(records.order_by("order_no"), 20).get_page(
                request.GET.get("page")
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def dispatch_create(request, work_order_id):
    actor = business_actor(request, "dispatch.add_dispatch", Employee.Role.TEAM_LEADER)
    order = get_object_or_404(available_orders(actor), pk=work_order_id)
    if request.method == "POST":
        try:
            token = UUID(request.POST.get("submission_key", ""))
        except (ValueError, AttributeError):
            token = None
        existing = (
            scope_queryset(
                request.user,
                Dispatch.objects.filter(submission_key=token, assigned_by=actor),
            ).first()
            if token
            else None
        )
        if existing is not None:
            messages.info(request, "该派工已经提交，未重复创建。")
            return redirect("dispatch:detail", pk=existing.pk)
    form = DispatchForm(
        request.POST if request.method == "POST" else None,
        actor=actor,
        work_order=order,
    )
    if request.method == "POST" and form.is_valid():
        try:
            record, created = create_dispatch(
                actor=actor, work_order_id=order.pk, data=form.cleaned_data
            )
        except ValidationError as error:
            form.add_error(None, error.messages)
            order.refresh_from_db()
        else:
            messages.success(
                request, "派工已创建。" if created else "该派工已经提交，未重复创建。"
            )
            return redirect("dispatch:detail", pk=record.pk)
    return render(
        request,
        "dispatch/create.html",
        {"employee": actor, "order": order, "form": form},
        status=400 if request.method == "POST" else 200,
    )


@login_required
@permission_required("dispatch.view_dispatch", raise_exception=True)
@require_GET
def dispatch_detail(request, pk):
    record = get_object_or_404(
        scope_queryset(request.user, Dispatch.objects.all()).select_related(
            "work_order__product",
            "work_order__work_center",
            "process",
            "team",
            "assigned_by",
            "current_leader",
        ),
        pk=pk,
    )
    return render(
        request,
        "dispatch/detail.html",
        {
            "employee": employee_for_user(request.user),
            "record": record,
            "members": record.members.select_related("employee"),
            "reports": scope_queryset(request.user, record.reports.all()).order_by(
                "-submitted_at", "-pk"
            ),
        },
    )
