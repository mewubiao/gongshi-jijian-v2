from django.contrib import admin
from django.urls import reverse

from masterdata.access import employee_for_user
from masterdata.models import Employee

BUSINESS_LINKS = (
    ("派工", "assignment", "dispatch:list", "dispatch.view_dispatch"),
    ("报工记录", "fact_check", "reporting:list", "reporting.view_piecereport"),
    ("辅助工时", "schedule", "timekeeping:list", "timekeeping.view_auxiliaryworklog"),
    ("终端授权", "devices", "terminal:devices", "terminal.view_terminaldevice"),
    ("计件月结", "payments", "settlement:index", "settlement.view_settlementperiod"),
)

CONFIG_GROUPS = (
    (
        "人员与组织",
        (
            ("员工档案", "badge", "masterdata.employee"),
            ("登录账号", "manage_accounts", "auth.user"),
            ("班组", "groups", "masterdata.team"),
            ("工作中心", "factory", "masterdata.workcenter"),
        ),
    ),
    (
        "工艺与类别",
        (
            ("产品", "inventory_2", "masterdata.product"),
            ("工序", "account_tree", "masterdata.process"),
            ("辅助工时类别", "category", "timekeeping.auxiliaryworkcategory"),
        ),
    ),
    (
        "易飞与同步",
        (
            ("生产工单", "receipt_long", "erp.workorder"),
            ("入库记录", "move_to_inbox", "erp.productionreceipt"),
            ("来源映射", "conversion_path", "erp.erpsourcemapping"),
            ("同步日志", "history", "erp.synclog"),
            ("同步状态", "sync", "erp.syncstate"),
        ),
    ),
)


def menu_link(request, title, icon, route):
    path = reverse(route)
    active = (
        request.path == path
        if route in ("dashboard", "admin:index")
        else request.path.startswith(path)
    )
    return {"title": title, "icon": icon, "link": path, "active": active}


def business_navigation(request, permissions):
    is_reviewer = "reporting.review_piecereport" in permissions
    if request.user.is_superuser:
        employee = employee_for_user(request.user)
        is_reviewer = (
            employee is not None and employee.role == Employee.Role.TEAM_LEADER
        )
    items = []
    for title, icon, route, permission in BUSINESS_LINKS:
        if not request.user.is_superuser and permission not in permissions:
            continue
        if route == "reporting:list" and is_reviewer:
            title = "报工审核"
        items.append(menu_link(request, title, icon, route))
    return items


def admin_navigation(request):
    permissions = request.user.get_all_permissions()
    groups = [
        {"items": [menu_link(request, "工作台", "space_dashboard", "dashboard")]},
        {
            "key": "business",
            "title": "生产业务",
            "items": business_navigation(request, permissions),
        },
    ]
    for title, models in CONFIG_GROUPS:
        items = []
        for label, icon, model_label in models:
            app, model = model_label.split(".")
            if request.user.is_superuser or f"{app}.view_{model}" in permissions:
                items.append(
                    menu_link(request, label, icon, f"admin:{app}_{model}_changelist")
                )
        if items:
            groups.append({"title": title, "items": items})
    return groups


def office_navigation(request):
    items = business_navigation(request, request.user.get_all_permissions())
    if admin.site.has_permission(request):
        items.append(menu_link(request, "管理后台", "settings", "admin:index"))
    return items
