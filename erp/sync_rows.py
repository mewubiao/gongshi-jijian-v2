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
    source_updated_at: datetime | None = None
    touch_source_updated_at: bool = False
    unusable_reason: str = ""


@dataclass(frozen=True)
class ReceiptRow:
    source_key: str
    order_no: str
    quantity: Decimal
    product_code: str = ""
    received_on: date | None = None
    unusable_reason: str = ""


@dataclass(frozen=True)
class FetchResult:
    rows: list
    warnings: list
