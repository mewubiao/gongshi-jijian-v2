from io import BytesIO

import qrcode
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from qrcode.image.svg import SvgPathImage

from terminal.auth import active_employees
from terminal.forms import DeviceForm
from terminal.models import TerminalDevice


def audit_device(user, device, message, created=False):
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(TerminalDevice),
        object_id=str(device.pk),
        object_repr=str(device),
        action_flag=ADDITION if created else CHANGE,
        change_message=message,
    )


@never_cache
@login_required
@permission_required("terminal.change_terminaldevice", raise_exception=True)
@require_http_methods(["GET", "POST"])
def devices(request):
    form = DeviceForm()
    activation_code = None
    issued = None
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        with transaction.atomic():
            if action == "create":
                form = DeviceForm(request.POST)
                if form.is_valid():
                    issued = form.save()
                    activation_code = issued.issue_activation()
                    audit_device(
                        request.user, issued, "登记终端并生成一次性激活码", True
                    )
                    form = DeviceForm()
                else:
                    status = 400
            elif action in ("authorize", "disable"):
                try:
                    device_id = int(request.POST.get("device_id", ""))
                except (ValueError, TypeError):
                    return HttpResponseBadRequest("请选择有效终端")
                if not 0 < device_id <= 9223372036854775807:
                    return HttpResponseBadRequest("请选择有效终端")
                device = get_object_or_404(
                    TerminalDevice.objects.select_for_update(),
                    pk=device_id,
                )
                if action == "authorize":
                    issued, activation_code = device, device.issue_activation()
                    audit_device(
                        request.user, device, "重新授权终端，撤销之前的浏览器授权"
                    )
                else:
                    device.active = False
                    device.generation += 1
                    device.activation_digest = ""
                    device.activation_expires_at = None
                    device.save()
                    audit_device(request.user, device, "停用终端并撤销会话")
                    messages.success(request, "终端已停用，已有终端会话随即失效。")
                    return redirect("terminal:devices")
            else:
                status = 400
    return render(
        request,
        "terminal/manage.html",
        {
            "form": form,
            "devices": TerminalDevice.objects.order_by("code"),
            "activation_code": activation_code,
            "issued": issued,
        },
        status=status,
    )


@never_cache
@login_required
@permission_required("terminal.view_terminaldevice", raise_exception=True)
@require_GET
def cards(request):
    query = request.GET.get("q", "").strip()[:100]
    employees = active_employees().select_related("team").order_by("code")
    if query:
        employees = employees.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(team__name__icontains=query)
        )
    page = Paginator(employees, 24).get_page(request.GET.get("page"))
    return render(
        request,
        "terminal/cards.html",
        {"employees": page, "page_obj": page, "query": query},
    )


@never_cache
@login_required
@permission_required("terminal.view_terminaldevice", raise_exception=True)
@require_GET
def card_qr(request, pk):
    employee = get_object_or_404(active_employees(), pk=pk)
    output = BytesIO()
    qrcode.make(
        employee.qr_code, image_factory=SvgPathImage, box_size=8, border=4
    ).save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")
