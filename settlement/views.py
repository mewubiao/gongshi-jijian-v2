from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from accounts.workflow import business_actor
from masterdata.access import employee_for_user
from masterdata.models import Employee
from settlement.forms import LockForm, MonthForm, UnlockForm
from settlement.models import SettlementPeriod
from settlement.month import read_month
from settlement.workbook import settlement_workbook


@never_cache
@login_required
@permission_required("settlement.view_settlementperiod", raise_exception=True)
@require_GET
def index(request):
    today = timezone.localdate()
    form = MonthForm(
        request.GET if "month" in request.GET else None,
        initial={"month": today.replace(day=1)},
    )

    if form.is_bound and form.is_valid():
        month = form.cleaned_data["month"]
        return redirect("settlement:detail", year=month.year, month=month.month)
    return render(
        request,
        "settlement/index.html",
        {
            "employee": employee_for_user(request.user),
            "form": form,
            "page_obj": Paginator(
                SettlementPeriod.objects.select_related("locked_by").order_by(
                    "-year", "-month"
                ),
                20,
            ).get_page(request.GET.get("page")),
        },
        status=400 if form.is_bound else 200,
    )


def _validate_month(year, month):
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise Http404("无效的归属月份")


@never_cache
@login_required
@permission_required("settlement.view_settlementperiod", raise_exception=True)
@require_http_methods(["GET", "POST"])
def detail(request, year, month):
    _validate_month(year, month)
    employee = employee_for_user(request.user)
    form, action, status = None, "", 200
    if request.method == "POST":
        action = request.POST.get("action")
        if action not in ("lock", "unlock"):
            return HttpResponse("月结操作无效", status=400)
        actor = business_actor(
            request, f"settlement.{action}_settlementperiod", Employee.Role.ADMIN
        )
        form = (LockForm if action == "lock" else UnlockForm)(request.POST)
        if not form.is_valid():
            status = 400
        else:
            try:
                with transaction.atomic():
                    period, _ = SettlementPeriod.objects.get_or_create(
                        year=year, month=month
                    )
                    revision = form.cleaned_data["revision"]
                    if action == "lock":
                        period.lock(actor, expected_revision=revision)
                    else:
                        period.unlock(
                            actor,
                            form.cleaned_data["reason"],
                            expected_revision=revision,
                        )
            except ValidationError as error:
                form.add_error(None, error.messages)
                status = 409
            else:
                messages.success(
                    request,
                    "已锁账，计件结算快照已保存。"
                    if action == "lock"
                    else "已解锁，原计件快照和操作历史已保留。",
                )
                return redirect("settlement:detail", year=year, month=month)

    data = read_month(year, month)
    is_admin = employee is not None and employee.role == Employee.Role.ADMIN
    form_class = UnlockForm if data["locked"] else LockForm
    # 失效表单保留提示，不接受旧版本的再次提交。
    current_form = form_class(initial={"revision": data["revision"]})
    query = request.GET.get("q", "").strip()[:100]
    match = query.casefold()
    return render(
        request,
        "settlement/detail.html",
        {
            **data,
            "employee": employee,
            "query": query,
            "action_form": current_form,
            "form_errors": form.errors if form is not None else None,
            "can_lock": is_admin and not data["locked"] and bool(data["revision"]),
            "can_unlock": is_admin and data["locked"],
            "summary": [
                item
                for item in data["summary"]
                if match in f"{item['code']} {item['name']}".casefold()
            ],
            "page_obj": Paginator(
                [
                    item
                    for item in data["entries"]
                    if match in f"{item.employee_code} {item.employee_name}".casefold()
                ],
                25,
            ).get_page(request.GET.get("page")),
        },
        status=status,
    )


@never_cache
@login_required
@permission_required("settlement.view_settlementperiod", raise_exception=True)
@require_GET
def export(request, year, month):
    _validate_month(year, month)
    data = read_month(year, month)
    if not data["locked"] or data["legacy"]:
        return HttpResponse("仅已锁账且有计件快照的月份可导出", status=409)
    return FileResponse(
        settlement_workbook(data),
        as_attachment=True,
        filename=f"piecework-{year}-{month:02d}-v{data['event'].pk}.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
