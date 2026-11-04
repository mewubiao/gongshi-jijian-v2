"""同步在内存里传递的一行。还不是数据库模型。"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class WorkOrderRow:
    order_no: str
    product_code: str
    work_center_code: str
    planned_qty: Decimal | None
    completed_qty: Decimal | None
    remaining_qty: Decimal | None
    status_raw: str
    order_type: str = ""
    erp_key: str = ""
    unit: str = ""
    start_date: date | None = None
    due_date: date | None = None
    source_modified_at: datetime | None = None
    touch_source_modified_at: bool = False
    unusable_reason: str = ""


@dataclass(frozen=True)
class ReceiptRow:
    erp_key: str
    receipt_type: str
    receipt_no: str
    line_no: str
    order_key: str
    receipt_qty: Decimal | None
    source_qty_11: Decimal | None
    scrap_qty_12: Decimal | None
    accepted_qty_13: Decimal | None
    product_code: str = ""
    received_on: date | None = None
    source_modified_at: datetime | None = None
    touch_source_modified_at: bool = False
    unusable_reason: str = ""


@dataclass(frozen=True)
class FetchResult:
    rows: list
    warnings: list
