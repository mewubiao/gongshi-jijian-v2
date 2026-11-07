from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def _sheet(book, title, columns, rows):
    sheet = book.create_sheet(title)
    sheet.freeze_panes = "A2"
    for i, (_, width, _) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    heading = []
    for label, _, _ in columns:
        cell = WriteOnlyCell(sheet, value=label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="214D77")
        heading.append(cell)
    sheet.append(heading)
    for row in rows:
        cells = []
        for value, (_, _, number_format) in zip(row, columns, strict=True):
            if (
                isinstance(value, Decimal)
                and len(value.normalize().as_tuple().digits) > 15
            ):
                value = format(value, "f")
            if isinstance(value, str):
                cell = WriteOnlyCell(sheet, value=ILLEGAL_CHARACTERS_RE.sub("", value))
                cell.data_type = "s"  # 员工、工单等字符串不能被 Excel 当作公式。
            else:
                cell = WriteOnlyCell(sheet, value=value)
                if number_format:
                    cell.number_format = number_format
            cells.append(cell)
        sheet.append(cells)


def settlement_workbook(data):
    period = data["period"]
    month = f"{period.year}-{period.month:02d}"
    book = Workbook(write_only=True)
    book.properties.title = f"{month} 计件月结（锁账 #{data['event'].pk}）"
    _sheet(
        book,
        "员工汇总",
        [
            ("归属月份", 14, None),
            ("工号", 16, None),
            ("员工", 20, None),
            ("计件金额（元）", 22, "0.00"),
            ("辅助工时（分钟）", 22, "0.0000"),
        ],
        [
            (month, item["code"], item["name"], item["amount"], item["minutes"])
            for item in data["summary"]
        ]
        + [(month, "", "合计", data["total_amount"], data["total_minutes"])],
    )
    _sheet(
        book,
        "计件明细",
        [
            ("报工编号", 14, None),
            ("归属日", 14, "yyyy-mm-dd"),
            ("工单", 22, None),
            ("工序", 22, None),
            ("工号", 16, None),
            ("员工", 20, None),
            ("报工有效量（整组为总量）", 30, "0.00"),
            ("冻结单价（元）", 20, "0.00"),
            ("整组在组秒数", 18, None),
            ("个人计件金额（元）", 24, "0.00"),
        ],
        (
            (
                entry.report_id,
                entry.ownership_date,
                entry.order_no,
                entry.process_name,
                entry.employee_code,
                entry.employee_name,
                entry.quantity,
                entry.unit_price,
                entry.group_seconds,
                entry.amount,
            )
            for entry in data["entries"]
        ),
    )
    result = BytesIO()
    book.save(result)
    result.seek(0)
    return result
