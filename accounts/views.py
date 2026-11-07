from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Count, Q
from django.shortcuts import render
from django.urls import reverse

from accounts.scopes import scope_queryset
from dispatch.models import Dispatch
from masterdata.access import employee_for_user
from masterdata.models import Employee, Process, Product
from reporting.models import PieceReport
from settlement.models import SettlementPeriod
from timekeeping.models import AuxiliaryWorkLog


class EmployeeLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


@login_required
def dashboard(request):
    employee = employee_for_user(request.user)
    can_use_admin = admin.site.has_permission(request)
    modules = []
    role = employee.role if employee else None
    shortcuts = {
        Employee.Role.TEAM_LEADER: [
            ("新建派工", "dispatch:orders"),
            ("待审核报工", "reporting:list"),
            ("待审核辅助工时", "timekeeping:list"),
        ],
        Employee.Role.WORKER: [("进入车间终端", "terminal:identify")],
        Employee.Role.PROCESS_ENGINEER: [
            ("维护工序", "admin:masterdata_process_changelist"),
            ("维护产品", "admin:masterdata_product_changelist"),
        ],
        Employee.Role.SETTLEMENT: [
            ("查询报工", "reporting:list"),
            ("查询辅助工时", "timekeeping:list"),
            ("查看计件月结", "settlement:index"),
        ],
        Employee.Role.ADMIN: [
            ("员工与账号", "admin:masterdata_employee_changelist"),
            ("维护班组", "admin:masterdata_team_changelist"),
            ("终端授权", "terminal:devices"),
            ("查看计件月结", "settlement:index"),
        ],
    }
    business_urls = {
        "dispatch.dispatch": "dispatch:list",
        "reporting.piecereport": "reporting:list",
        "timekeeping.auxiliaryworklog": "timekeeping:list",
        "settlement.settlementperiod": "settlement:index",
    }
    for model in (
        Dispatch,
        PieceReport,
        AuxiliaryWorkLog,
        Product,
        Process,
        SettlementPeriod,
    ):
        opts = model._meta
        if request.user.has_perm(f"{opts.app_label}.view_{opts.model_name}"):
            queryset = scope_queryset(request.user, model.objects.all())
            pending = None
            if role == Employee.Role.TEAM_LEADER and model in (
                PieceReport,
                AuxiliaryWorkLog,
            ):
                pending_status = (
                    PieceReport.Status.PENDING
                    if model is PieceReport
                    else AuxiliaryWorkLog.Status.PENDING_REVIEW
                )
                summary = queryset.aggregate(
                    total=Count("pk"),
                    pending=Count("pk", filter=Q(status=pending_status)),
                )
                count, pending = summary["total"], summary["pending"]
            else:
                count = queryset.count()
            target = business_urls.get(opts.label_lower)
            if target:
                url = reverse(target)
                if target in ("reporting:list", "timekeeping:list"):
                    url += "?status="
            elif can_use_admin:
                url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
            else:
                url = None
            modules.append(
                {
                    "name": opts.verbose_name_plural,
                    "count": count,
                    "pending": pending,
                    "pending_url": reverse(target) if pending is not None else None,
                    "url": url,
                }
            )
    return render(
        request,
        "accounts/dashboard.html",
        {
            "employee": employee,
            "modules": modules,
            "can_use_admin": can_use_admin,
            "shortcuts": [
                {
                    "label": label,
                    "url": reverse(target)
                    + (
                        "?status="
                        if role == Employee.Role.SETTLEMENT
                        and target in ("reporting:list", "timekeeping:list")
                        else ""
                    ),
                }
                for label, target in shortcuts.get(role, ())
            ],
            "teams": employee.led_teams.filter(active=True)
            if employee and employee.role == Employee.Role.TEAM_LEADER
            else (),
        },
    )
