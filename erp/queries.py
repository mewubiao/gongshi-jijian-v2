"""只生成 SELECT，并把来源行解析成同步用的结构。

列名来自配置，先校验再拼进 SQL。状态值和附加过滤用参数，不拼进语句。
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from erp.yifei_config import quote_identifier

_TWO_PLACES = Decimal("0.01")


class ReadOnlyViolation(RuntimeError):
    """试图对易飞执行 SELECT 以外的语句。"""


def assert_single_select(sql):
    text = sql.strip()
    upper = text.upper()
    if not upper.startswith("SELECT"):
        raise ReadOnlyViolation("易飞连接只允许 SELECT")
    if ";" in text or "--" in text or "/*" in text:
        raise ReadOnlyViolation("易飞查询只能有一条 SELECT，且不能带注释")


def _select_list(items):
    return ", ".join(items)


def _table_sql(schema, table):
    quoted = quote_identifier(table)
    if schema:
        return f"{quote_identifier(schema)}.{quoted}"
    return quoted


def _where(field_map):
    clauses = []
    params = []
    if getattr(field_map, "status_column", "") and getattr(
        field_map, "open_status_values", ()
    ):
        placeholders = ", ".join("?" for _ in field_map.open_status_values)
        clauses.append(
            f"{quote_identifier(field_map.status_column)} IN ({placeholders})"
        )
        params.extend(field_map.open_status_values)
    for column, value in field_map.extra_filters:
        clauses.append(f"{quote_identifier(column)} = ?")
        params.append(value)
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def build_work_order_query(field_map):
    columns = []
    if field_map.order_type_column:
        columns.append(f"{quote_identifier(field_map.order_type_column)} AS order_type")
    columns.append(f"{quote_identifier(field_map.order_no_column)} AS order_no")
    columns.append(f"{quote_identifier(field_map.product_column)} AS product_code")
    if field_map.planned_qty_column:
        columns.append(
            f"{quote_identifier(field_map.planned_qty_column)} AS planned_qty"
        )
    if field_map.completed_qty_column:
        columns.append(
            f"{quote_identifier(field_map.completed_qty_column)} AS completed_qty"
        )
    if field_map.remaining_qty_column:
        columns.append(
            f"{quote_identifier(field_map.remaining_qty_column)} AS remaining_qty"
        )
    columns.append(
        f"{quote_identifier(field_map.work_center_column)} AS work_center_code"
    )
    columns.append(f"{quote_identifier(field_map.status_column)} AS status_raw")
    if field_map.updated_at_column:
        columns.append(
            f"{quote_identifier(field_map.updated_at_column)} AS source_updated_at"
        )
    where, params = _where(field_map)
    table = _table_sql(field_map.schema, field_map.table)
    sql = f"SELECT {_select_list(columns)} FROM {table}{where}"
    assert_single_select(sql)
    return sql, params


def build_receipt_query(field_map):
    columns = []
    if field_map.order_type_column:
        columns.append(f"{quote_identifier(field_map.order_type_column)} AS order_type")
    columns.append(f"{quote_identifier(field_map.order_no_column)} AS order_no")
    if field_map.receipt_type_column:
        columns.append(
            f"{quote_identifier(field_map.receipt_type_column)} AS receipt_type"
        )
    if field_map.receipt_no_column:
        columns.append(f"{quote_identifier(field_map.receipt_no_column)} AS receipt_no")
    if field_map.line_column:
        columns.append(f"{quote_identifier(field_map.line_column)} AS line_no")
    if field_map.source_key_column:
        columns.append(f"{quote_identifier(field_map.source_key_column)} AS source_key")
    if field_map.product_column:
        columns.append(f"{quote_identifier(field_map.product_column)} AS product_code")
    columns.append(f"{quote_identifier(field_map.quantity_column)} AS quantity")
    if field_map.received_on_column:
        columns.append(
            f"{quote_identifier(field_map.received_on_column)} AS received_on"
        )
    where, params = _where(field_map)
    table = _table_sql(field_map.schema, field_map.table)
    sql = f"SELECT {_select_list(columns)} FROM {table}{where}"
    assert_single_select(sql)
    return sql, params


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _decimal(value):
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text == "":
            return None
        return Decimal(text)
    return Decimal(str(value))


def parse_decimal(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        return _decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("数量无法解析") from exc


def parse_source_datetime(value):
    """把易飞常见的日期时间收成带时区的 datetime。

    无时区的值按项目时区理解（Asia/Shanghai），不当成 UTC。
    具体列和格式待按实际库核对。
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    if isinstance(value, date):
        return timezone.make_aware(
            datetime.combine(value, datetime.min.time()),
            timezone.get_current_timezone(),
        )
    text = str(value).strip()
    if "." in text:
        text = text.split(".", 1)[0]
    for fmt in (
        "%Y%m%d%H%M%S",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    raise ValueError(f"更新时间无法解析：{text}")


def parse_source_date(value):
    parsed = parse_source_datetime(value)
    if parsed is None:
        return None
    return timezone.localtime(parsed).date()


def _join_order_no(order_type, order_no, separator):
    if order_type and order_no:
        return f"{order_type}{separator}{order_no}"
    return order_no or order_type


def parse_work_order_records(records, field_map):
    from erp.sync_rows import WorkOrderRow

    rows = []
    warnings = []
    for index, record in enumerate(records, start=1):
        issues = []
        notes = []
        order_no = _join_order_no(
            _text(record.get("order_type")) if field_map.order_type_column else "",
            _text(record.get("order_no")),
            field_map.order_no_separator,
        )
        label = order_no or f"第 {index} 行"
        if not order_no:
            issues.append(f"{label}缺少工单号")

        def quantity(alias, title, record=record, label=label, issues=issues):
            if alias not in record:
                return None
            raw = record.get(alias)
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                return None
            try:
                return parse_decimal(raw)
            except ValueError:
                issues.append(f"工单 {label} 的{title}无法解析")
                return None

        updated_at = None
        if "source_updated_at" in record and record.get("source_updated_at") not in (
            None,
            "",
        ):
            try:
                updated_at = parse_source_datetime(record.get("source_updated_at"))
            except ValueError:
                notes.append(f"工单 {label} 的更新时间无法解析，已忽略")
        rows.append(
            WorkOrderRow(
                order_no=order_no,
                product_code=_text(record.get("product_code")),
                work_center_code=_text(record.get("work_center_code")),
                planned_qty=quantity("planned_qty", "计划数量"),
                completed_qty=quantity("completed_qty", "已完成量"),
                remaining_qty=quantity("remaining_qty", "剩余量"),
                status_raw=_text(record.get("status_raw")),
                source_updated_at=updated_at,
                touch_source_updated_at="source_updated_at" in record
                and not any("更新时间" in note for note in notes),
                unusable_reason="；".join(issues),
            )
        )
        warnings.extend(notes)
    return rows, warnings


def parse_receipt_records(records, field_map):
    from erp.sync_rows import ReceiptRow

    rows = []
    warnings = []
    for index, record in enumerate(records, start=1):
        issues = []
        order_no = _join_order_no(
            _text(record.get("order_type")) if field_map.order_type_column else "",
            _text(record.get("order_no")),
            field_map.order_no_separator,
        )
        source_key = _receipt_source_key(record, field_map)
        label = source_key or order_no or f"第 {index} 行"
        if not order_no:
            issues.append(f"入库 {label} 缺少工单号")
        if not source_key:
            issues.append(f"入库 {label} 缺少来源键")
        quantity = None
        raw_qty = record.get("quantity")
        if raw_qty is None or (isinstance(raw_qty, str) and raw_qty.strip() == ""):
            issues.append(f"入库 {label} 缺少数量")
        else:
            try:
                quantity = parse_decimal(raw_qty)
            except ValueError:
                issues.append(f"入库 {label} 的数量无法解析")
        received_on = None
        if "received_on" in record and record.get("received_on") not in (None, ""):
            try:
                received_on = parse_source_date(record.get("received_on"))
            except ValueError:
                warnings.append(f"入库 {label} 的日期无法解析，已忽略")
        rows.append(
            ReceiptRow(
                source_key=source_key,
                order_no=order_no,
                quantity=quantity if quantity is not None else Decimal("0"),
                product_code=_text(record.get("product_code")),
                received_on=received_on,
                unusable_reason="；".join(issues),
            )
        )
    return rows, warnings


def _receipt_source_key(record, field_map):
    explicit = _text(record.get("source_key")) if field_map.source_key_column else ""
    if explicit:
        return explicit
    receipt_type = (
        _text(record.get("receipt_type")) if field_map.receipt_type_column else ""
    )
    receipt_no = _text(record.get("receipt_no")) if field_map.receipt_no_column else ""
    line_no = _text(record.get("line_no")) if field_map.line_column else ""
    parts = [part for part in (receipt_type, receipt_no, line_no) if part]
    if receipt_no and line_no:
        return field_map.key_separator.join(parts)
    return ""
