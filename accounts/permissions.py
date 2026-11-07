"""员工角色是唯一授权来源，Django 用户组和零散权限不叠加。"""

from masterdata.models import Employee


def model_permissions(actions, *models):
    return {
        f"{app}.{action}_{model}"
        for label in models
        for app, model in [label.split(".")]
        for action in actions.split()
    }


BUSINESS_VIEWS = model_permissions(
    "view",
    "dispatch.dispatch",
    "reporting.worksession",
    "reporting.piecereport",
    "timekeeping.auxiliaryworklog",
)
AUDIT_VIEWS = model_permissions(
    "view",
    "dispatch.dispatchmember",
    "dispatch.leadertransfer",
    "reporting.pauseinterval",
    "reporting.groupmemberspan",
    "reporting.reportcorrection",
    "reporting.reportrejection",
    "reporting.reportapprovalreversal",
    "timekeeping.auxiliaryworkcorrection",
    "timekeeping.auxiliaryworkrejection",
)
ROLE_PERMISSIONS = {
    Employee.Role.WORKER: BUSINESS_VIEWS,
    Employee.Role.TEAM_LEADER: BUSINESS_VIEWS
    | AUDIT_VIEWS
    | {
        "dispatch.add_dispatch",
        "reporting.review_piecereport",
        "timekeeping.review_auxiliaryworklog",
    },
    Employee.Role.SETTLEMENT: AUDIT_VIEWS
    | {"settlement.view_settlementlockevent"}
    | model_permissions(
        "view",
        "reporting.piecereport",
        "timekeeping.auxiliaryworklog",
        "settlement.settlementperiod",
    ),
    Employee.Role.PROCESS_ENGINEER: model_permissions(
        "view add change",
        "masterdata.product",
        "masterdata.process",
    )
    | {"masterdata.view_processratehistory"},
    Employee.Role.ADMIN: BUSINESS_VIEWS
    | AUDIT_VIEWS
    | {"settlement.view_settlementlockevent"}
    | model_permissions(
        "view add change",
        "masterdata.employee",
        "masterdata.workcenter",
        "masterdata.team",
        "masterdata.product",
        "timekeeping.auxiliaryworkcategory",
        "erp.erpsourcemapping",
        "auth.user",
        "terminal.terminaldevice",
    )
    | model_permissions(
        "view",
        "masterdata.process",
        "masterdata.processratehistory",
        "erp.workorder",
        "erp.productionreceipt",
        "erp.synclog",
        "erp.syncstate",
    )
    | model_permissions("view add", "settlement.settlementperiod")
    | {
        "reporting.reverse_piecereport",
        "settlement.lock_settlementperiod",
        "settlement.unlock_settlementperiod",
    },
}
