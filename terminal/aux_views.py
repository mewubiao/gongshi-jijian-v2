from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from terminal.auth import worker_required
from terminal.operations import auxiliary_action, check_ticket
from timekeeping.models import AuxiliaryWorkCategory, AuxiliaryWorkLog
from timekeeping.selectors import with_effective_minutes


class AuxiliaryForm(forms.Form):
    ticket = forms.CharField(widget=forms.HiddenInput)
    category = forms.IntegerField(
        min_value=1, max_value=9223372036854775807, required=False
    )
    note = forms.CharField(max_length=500, required=False, strip=True)
    reason = forms.CharField(max_length=2000, required=False, strip=True)
    log_id = forms.IntegerField(
        min_value=1, max_value=9223372036854775807, required=False
    )


@never_cache
@worker_required
@require_http_methods(["GET", "POST"])
def auxiliary(request):
    employee, device = request.terminal_employee, request.terminal_device
    form = AuxiliaryForm(request.POST if request.method == "POST" else None)
    response_status = 200
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        action = request.POST.get("action")
        log = (
            get_object_or_404(AuxiliaryWorkLog, pk=data["log_id"], employee=employee)
            if action != "start"
            else None
        )
        try:
            check_ticket(
                data["ticket"], [employee.pk, device.login_sequence], "terminal-aux"
            )
            auxiliary_action(
                device=device,
                employee=employee,
                action=action,
                category_id=data["category"],
                log_id=log.pk if log else None,
                note=data["note"],
                reason=data["reason"],
            )
        except ValidationError as error:
            form.add_error(None, error.messages)
            response_status = 409
        else:
            return redirect("terminal:auxiliary")
    elif request.method == "POST":
        response_status = 400
    logs = (
        with_effective_minutes(AuxiliaryWorkLog.objects.filter(employee=employee))
        .prefetch_related("rejections")
        .order_by("-started_at", "-pk")
    )
    running = (
        logs.select_related("category")
        .filter(status=AuxiliaryWorkLog.Status.RUNNING)
        .first()
    )
    page = Paginator(logs, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "terminal/auxiliary.html",
        {
            "employee": employee,
            "device": device,
            "form": form,
            "ticket": signing.dumps(
                [employee.pk, device.login_sequence], salt="terminal-aux"
            ),
            "categories": AuxiliaryWorkCategory.objects.filter(active=True).order_by(
                "code"
            ),
            "running": running,
            "submit_note": form["note"].value()
            if form.is_bound and request.POST.get("action") == "submit"
            else running.work_note
            if running
            else "",
            "submitted_log_id": request.POST.get("log_id", ""),
            "logs": page,
            "page_obj": page,
        },
        status=response_status,
    )
