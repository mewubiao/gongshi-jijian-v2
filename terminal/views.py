from django.contrib.auth import logout
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from reporting.models import PauseInterval, PieceReport, WorkSession
from terminal.auth import (
    activate,
    clear_worker_cookie,
    identify,
    set_device_cookie,
    set_worker_cookie,
    terminal_identity,
    worker_required,
)
from terminal.forms import ActivationForm, IdentifyForm
from terminal.models import TerminalDevice
from terminal.selectors import assigned_tasks


@never_cache
@sensitive_post_parameters("code")
@require_http_methods(["GET", "POST"])
def activate_device(request):
    if request.user.is_authenticated:
        logout(request)
    form = ActivationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            device = activate(form.cleaned_data["code"])
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            rotate_token(request)
            response = clear_worker_cookie(redirect("terminal:identify"))
            set_device_cookie(response, device)
            return response
    return render(
        request,
        "terminal/activate.html",
        {"form": form},
        status=400 if request.method == "POST" else 200,
    )


@never_cache
@sensitive_post_parameters("credential")
@require_http_methods(["GET", "POST"])
def identify_employee(request):
    if request.user.is_authenticated:
        logout(request)
    device, employee = terminal_identity(request)
    if device is None:
        return clear_worker_cookie(redirect("terminal:activate"))
    if request.GET.get("locked") == "1" and str(
        device.login_sequence
    ) != request.GET.get("sequence"):
        return render(request, "terminal/switched.html", {"device": device})
    if (
        employee is not None
        and request.method == "GET"
        and request.GET.get("locked") != "1"
    ):
        return redirect("terminal:home")
    form = IdentifyForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            employee = identify(device, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            rotate_token(request)
            response = redirect("terminal:home")
            set_worker_cookie(response, device, employee)
            return response
    return clear_worker_cookie(
        render(
            request,
            "terminal/identify.html",
            {"device": device, "form": form},
            status=400 if request.method == "POST" else 200,
        )
    )


@never_cache
@worker_required
@require_GET
def home(request):
    employee = request.terminal_employee
    query = request.GET.get("q", "").strip()[:100]
    state = "all" if request.GET.get("state") == "all" else "active"
    records = assigned_tasks(employee).annotate(
        has_open_session=Exists(
            WorkSession.objects.filter(
                dispatch_id=OuterRef("pk"), ended_at__isnull=True
            )
        ),
        is_paused=Exists(
            PauseInterval.objects.filter(
                work_session__dispatch_id=OuterRef("pk"),
                work_session__ended_at__isnull=True,
                resumed_at__isnull=True,
            )
        ),
        latest_status=Subquery(
            PieceReport.objects.filter(dispatch_id=OuterRef("pk"))
            .order_by("-submitted_at", "-pk")
            .values("status")[:1]
        ),
    )
    if state == "active":
        records = records.filter(
            Q(has_open_session=True)
            | Q(latest_status__isnull=True)
            | Q(
                latest_status__in=[
                    PieceReport.Status.WITHDRAWN,
                    PieceReport.Status.REJECTED,
                ]
            )
        )
    if query:
        records = records.filter(
            Q(work_order__order_no__icontains=query)
            | Q(work_order__product__code__icontains=query)
            | Q(work_order__product__name__icontains=query)
            | Q(process__name__icontains=query)
        )
    page = Paginator(
        records.order_by("-has_open_session", "-assigned_at", "-pk"), 20
    ).get_page(request.GET.get("page"))
    for task in page:
        task.status_label = (
            PieceReport.Status(task.latest_status).label
            if task.latest_status
            else "待开工"
        )
    return render(
        request,
        "terminal/home.html",
        {
            "employee": employee,
            "device": request.terminal_device,
            "tasks": page,
            "page_obj": page,
            "query": query,
            "state": state,
        },
    )


@never_cache
@require_GET
def status(request):
    device, employee = terminal_identity(request)
    return JsonResponse(
        {
            "active": employee is not None,
            "sequence": device.login_sequence if device else None,
        },
        status=200 if employee else 401,
    )


@never_cache
@require_POST
def activity(request):
    device, employee = terminal_identity(request)
    if employee is None:
        return JsonResponse({"active": False}, status=401)
    response = JsonResponse({"active": True, "sequence": device.login_sequence})
    set_worker_cookie(response, device, employee)
    return response


@never_cache
@require_POST
def signout(request):
    device, _ = terminal_identity(request)
    if device:
        with transaction.atomic():
            current = TerminalDevice.objects.select_for_update().get(pk=device.pk)
            if str(current.login_sequence) != request.POST.get("sequence"):
                return render(request, "terminal/switched.html", {"device": current})
            current.login_sequence += 1
            current.save(update_fields=["login_sequence"])
    response = redirect("terminal:identify")
    return clear_worker_cookie(response)
