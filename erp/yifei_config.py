"""易飞连接和字段映射。全部来自环境变量，仓库里不放密码。

默认表名、列名是易飞 9.x 里常见的写法，待按实际库核对，不是已验证的客户库结构。
"""

import os
import re
from dataclasses import dataclass

VERIFICATION_NOTE = "待按实际库核对"

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class YifeiConfigError(Exception):
    """连接或映射还不能用。这种错误发生在同步开始之前。"""


def quote_identifier(name):
    if not name or _IDENTIFIER.fullmatch(name) is None:
        raise YifeiConfigError(f"非法的表名或字段名：{name}")
    return f"[{name}]"


def _environment(env):
    return os.environ if env is None else env


def _pick(env, name, default):
    if name not in env:
        return default
    return env[name].strip()


def _yes_no(env, name, default):
    raw = _pick(env, name, default).lower()
    if raw in {"yes", "true", "1"}:
        return "yes"
    if raw in {"no", "false", "0"}:
        return "no"
    raise YifeiConfigError(f"{name} 只能是 yes 或 no")


def _positive_int(env, name, default):
    raw = _pick(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise YifeiConfigError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise YifeiConfigError(f"{name} 必须大于 0")
    return value


def _statuses(env, name, default, *, allow_empty):
    raw = _pick(env, name, default)
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not values and not allow_empty:
        raise YifeiConfigError(f"{name} 至少要有一个状态值")
    return values


def _optional_identifier(value):
    if not value:
        return ""
    quote_identifier(value)
    return value


def _required_identifier(label, value):
    if not value:
        raise YifeiConfigError(f"必须配置{label}")
    quote_identifier(value)
    return value


def _separator(env, name, default):
    value = _pick(env, name, default)
    if len(value) > 4 or any(character.isspace() for character in value):
        raise YifeiConfigError(f"{name} 最多 4 个字符，且不能含空白")
    return value


def _extra_filters(env, name):
    raw = _pick(env, name, "")
    if not raw:
        return ()
    filters = []
    for part in raw.split(","):
        if "=" not in part:
            raise YifeiConfigError(f"{name} 须写成 列名=值，多项用英文逗号分隔")
        column, value = part.split("=", 1)
        column = column.strip()
        value = value.strip()
        if not column or value == "":
            raise YifeiConfigError(f"{name} 的列名和值都不能为空")
        quote_identifier(column)
        filters.append((column, value))
    return tuple(filters)


@dataclass(frozen=True)
class ConnectionConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    driver: str
    encrypt: str
    trust_server_certificate: str
    login_timeout: int
    query_timeout: int

    def odbc_connection_string(self):
        """ODBC 连接串。调用方不要把它写进日志或同步记录。"""

        def brace(value):
            return "{" + str(value).replace("}", "}}") + "}"

        return ";".join(
            [
                f"DRIVER={brace(self.driver)}",
                f"SERVER={brace(f'{self.host},{self.port}')}",
                f"DATABASE={brace(self.database)}",
                f"UID={brace(self.user)}",
                f"PWD={brace(self.password)}",
                f"Encrypt={brace(self.encrypt)}",
                f"TrustServerCertificate={brace(self.trust_server_certificate)}",
                "ApplicationIntent=ReadOnly",
            ]
        )


@dataclass(frozen=True)
class WorkOrderFieldMap:
    """工单单头映射。默认值待按实际库核对。"""

    table: str
    schema: str
    order_type_column: str
    order_no_column: str
    order_no_separator: str
    product_column: str
    planned_qty_column: str
    completed_qty_column: str
    remaining_qty_column: str
    work_center_column: str
    status_column: str
    updated_at_column: str
    erp_key_column: str
    unit_column: str
    start_date_column: str
    due_date_column: str
    open_status_values: tuple
    closed_status_values: tuple
    extra_filters: tuple


@dataclass(frozen=True)
class ReceiptFieldMap:
    """生产入库映射。默认值待按实际库核对。"""

    table: str
    schema: str
    order_type_column: str
    order_no_column: str
    order_no_separator: str
    receipt_type_column: str
    receipt_no_column: str
    line_column: str
    source_key_column: str
    key_separator: str
    product_column: str
    work_order_key_column: str
    quantity_column: str
    source_qty_11_column: str
    scrap_qty_12_column: str
    accepted_qty_13_column: str
    received_on_column: str
    extra_filters: tuple
    delete_missing: bool


@dataclass(frozen=True)
class ScheduleConfig:
    interval_seconds: int
    stale_seconds: int


def load_connection_config(env=None):
    env = _environment(env)
    missing = []
    host = env.get("YIFEI_SQLSERVER_HOST", "")
    database = env.get("YIFEI_SQLSERVER_DATABASE", "")
    user = env.get("YIFEI_SQLSERVER_USER", "")
    password = env.get("YIFEI_SQLSERVER_PASSWORD")
    if not str(host).strip():
        missing.append("YIFEI_SQLSERVER_HOST")
    if not str(database).strip():
        missing.append("YIFEI_SQLSERVER_DATABASE")
    if not str(user).strip():
        missing.append("YIFEI_SQLSERVER_USER")
    if not isinstance(password, str) or password == "":
        missing.append("YIFEI_SQLSERVER_PASSWORD")
    if missing:
        raise YifeiConfigError("缺少易飞连接环境变量：" + "、".join(missing))
    port = _positive_int(env, "YIFEI_SQLSERVER_PORT", 1433)
    if port > 65535:
        raise YifeiConfigError("YIFEI_SQLSERVER_PORT 不能大于 65535")
    return ConnectionConfig(
        host=host.strip(),
        port=port,
        database=database.strip(),
        user=user.strip(),
        password=password,
        driver=_pick(env, "YIFEI_SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server"),
        encrypt=_yes_no(env, "YIFEI_SQLSERVER_ENCRYPT", "yes"),
        trust_server_certificate=_yes_no(
            env, "YIFEI_SQLSERVER_TRUST_SERVER_CERTIFICATE", "no"
        ),
        login_timeout=_positive_int(env, "YIFEI_SQLSERVER_LOGIN_TIMEOUT", 5),
        query_timeout=_positive_int(env, "YIFEI_SQLSERVER_QUERY_TIMEOUT", 60),
    )


def load_work_order_field_map(env=None):
    """默认对照易飞工单单头 MOCTA。这些列名待按实际库核对。"""

    env = _environment(env)
    field_map = WorkOrderFieldMap(
        table=_required_identifier("工单表名", _pick(env, "YIFEI_TABLE", "MOCTA")),
        schema=_optional_identifier(_pick(env, "YIFEI_SCHEMA", "")),
        order_type_column=_optional_identifier(
            _pick(env, "YIFEI_COL_ORDER_TYPE", "TA001")
        ),
        order_no_column=_required_identifier(
            "工单号列", _pick(env, "YIFEI_COL_ORDER_NO", "TA002")
        ),
        order_no_separator=_separator(env, "YIFEI_ORDER_NO_SEPARATOR", "-"),
        product_column=_required_identifier(
            "产品列", _pick(env, "YIFEI_COL_PRODUCT", "TA006")
        ),
        planned_qty_column=_optional_identifier(
            _pick(env, "YIFEI_COL_PLANNED_QTY", "TA015")
        ),
        completed_qty_column=_optional_identifier(
            _pick(env, "YIFEI_COL_COMPLETED_QTY", "TA017")
        ),
        remaining_qty_column=_optional_identifier(
            _pick(env, "YIFEI_COL_REMAINING_QTY", "")
        ),
        work_center_column=_required_identifier(
            "工作中心列", _pick(env, "YIFEI_COL_WORK_CENTER", "TA021")
        ),
        status_column=_required_identifier(
            "状态列", _pick(env, "YIFEI_COL_STATUS", "TA011")
        ),
        updated_at_column=_optional_identifier(
            _pick(env, "YIFEI_COL_UPDATED_AT", "MODI_DATE")
        ),
        erp_key_column=_optional_identifier(_pick(env, "YIFEI_COL_ERP_KEY", "")),
        unit_column=_optional_identifier(_pick(env, "YIFEI_COL_UNIT", "")),
        start_date_column=_optional_identifier(_pick(env, "YIFEI_COL_START_DATE", "")),
        due_date_column=_optional_identifier(_pick(env, "YIFEI_COL_DUE_DATE", "")),
        open_status_values=_statuses(
            env, "YIFEI_OPEN_STATUSES", "1,2,3", allow_empty=False
        ),
        closed_status_values=_statuses(
            env, "YIFEI_CLOSED_STATUSES", "Y,y", allow_empty=True
        ),
        extra_filters=_extra_filters(env, "YIFEI_EXTRA_FILTERS"),
    )
    if not field_map.remaining_qty_column and not field_map.planned_qty_column:
        raise YifeiConfigError("必须配置剩余量列，或计划数量列以便计算剩余量")
    return field_map


def load_receipt_field_map(env=None):
    """默认对照易飞生产入库单身 MOCTG。这些列名待按实际库核对。"""

    env = _environment(env)
    field_map = ReceiptFieldMap(
        table=_required_identifier(
            "入库表名", _pick(env, "YIFEI_RECEIPT_TABLE", "MOCTG")
        ),
        schema=_optional_identifier(_pick(env, "YIFEI_RECEIPT_SCHEMA", "")),
        order_type_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_ORDER_TYPE", "TG014")
        ),
        order_no_column=_required_identifier(
            "入库上的工单号列", _pick(env, "YIFEI_RECEIPT_COL_ORDER_NO", "TG015")
        ),
        order_no_separator=_separator(env, "YIFEI_ORDER_NO_SEPARATOR", "-"),
        receipt_type_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_TYPE", "TG001")
        ),
        receipt_no_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_NO", "TG002")
        ),
        line_column=_optional_identifier(_pick(env, "YIFEI_RECEIPT_COL_LINE", "TG003")),
        source_key_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_SOURCE_KEY", "")
        ),
        key_separator=_separator(env, "YIFEI_RECEIPT_KEY_SEPARATOR", "-"),
        product_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_PRODUCT", "TG004")
        ),
        work_order_key_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_WORK_ORDER_KEY", "")
        ),
        quantity_column=_required_identifier(
            "对账入库数量列", _pick(env, "YIFEI_RECEIPT_COL_QTY", "TG013")
        ),
        source_qty_11_column=_required_identifier(
            "TG011 数量列", _pick(env, "YIFEI_RECEIPT_COL_QTY_11", "TG011")
        ),
        scrap_qty_12_column=_required_identifier(
            "TG012 数量列", _pick(env, "YIFEI_RECEIPT_COL_QTY_12", "TG012")
        ),
        accepted_qty_13_column=_required_identifier(
            "TG013 数量列", _pick(env, "YIFEI_RECEIPT_COL_QTY_13", "TG013")
        ),
        received_on_column=_optional_identifier(
            _pick(env, "YIFEI_RECEIPT_COL_DATE", "")
        ),
        extra_filters=_extra_filters(env, "YIFEI_RECEIPT_EXTRA_FILTERS"),
        delete_missing=_yes_no(env, "YIFEI_RECEIPT_DELETE_MISSING", "no") == "yes",
    )
    has_line_key = field_map.receipt_no_column and field_map.line_column
    if not field_map.source_key_column and not has_line_key:
        raise YifeiConfigError("入库必须配置来源键列，或同时配置入库单号列和序号列")
    return field_map


def load_schedule_config(env=None):
    env = _environment(env)
    interval = _positive_int(env, "YIFEI_SYNC_INTERVAL_SECONDS", 300)
    stale = _positive_int(env, "YIFEI_SYNC_STALE_SECONDS", 900)
    if stale <= interval:
        raise YifeiConfigError(
            "YIFEI_SYNC_STALE_SECONDS 必须大于同步间隔，避免锁被提前抢走"
        )
    return ScheduleConfig(interval_seconds=interval, stale_seconds=stale)
