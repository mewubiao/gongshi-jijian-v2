"""生产入库的同步计划。

落地表由数据模型分支的 ProductionReceipt 提供。模型还没合并过来时，
不在这里另建一张表。测试用内存写入器覆盖新增、更新、跳过和幂等。
"""

from dataclasses import dataclass, field
from decimal import Decimal

RECEIPT_MODEL_NAME = "ProductionReceipt"
RECEIPT_NOT_READY = (
    "生产入库模型 ProductionReceipt 还没合并进本分支，本次没有写入入库数量。"
    "该模型由数据模型分支提供，同步不另建表。"
)


def get_receipt_model():
    from erp import models

    return getattr(models, RECEIPT_MODEL_NAME, None)


@dataclass
class ReceiptStats:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    warnings: list = field(default_factory=list)
    ready: bool = True


@dataclass(frozen=True)
class ReceiptPlan:
    desired: dict
    remove_keys: tuple
    skipped: int
    warnings: list


def plan_receipts(lines, *, orders_by_no, delete_missing, existing_keys):
    """orders_by_no: 工单号 -> 产品编码。未知工单跳过，且不参与删除。"""

    warnings = []
    skipped = 0
    desired = {}
    protected = set()
    seen = set()
    for line in lines:
        if line.unusable_reason or not line.source_key:
            skipped += 1
            warnings.append(line.unusable_reason or "入库行缺少来源键，已跳过")
            if line.source_key:
                protected.add(line.source_key)
            continue
        if line.source_key in seen:
            warnings.append(
                f"入库 {line.source_key} 在本次结果里出现多次，以后一条为准"
            )
        seen.add(line.source_key)
        product_code = orders_by_no.get(line.order_no)
        if product_code is None:
            skipped += 1
            protected.add(line.source_key)
            warnings.append(
                f"入库 {line.source_key} 的工单 {line.order_no} 在本地不存在，已跳过"
            )
            continue
        if line.product_code and line.product_code != product_code:
            warnings.append(
                f"入库 {line.source_key} 的品号 {line.product_code} 与工单 "
                f"{line.order_no} 的产品 {product_code} 不一致，仍按工单记入。"
                "品号是否应一致待按实际库核对"
            )
        desired[line.source_key] = line
        protected.discard(line.source_key)
    remove_keys = ()
    if delete_missing:
        remove_keys = tuple(
            key for key in existing_keys if key not in desired and key not in protected
        )
    return ReceiptPlan(
        desired=desired,
        remove_keys=remove_keys,
        skipped=skipped,
        warnings=warnings,
    )


class MemoryReceiptWriter:
    """测试里的入库落地。按来源键保存，第二次写入同一批数据应无变化。"""

    def __init__(self, rows=None):
        self.rows = {row.source_key: row for row in (rows or [])}

    def existing_keys(self):
        return set(self.rows)

    def apply(self, plan):
        created = updated = unchanged = removed = 0
        for key in plan.remove_keys:
            if key in self.rows:
                del self.rows[key]
                removed += 1
        for key, line in plan.desired.items():
            current = self.rows.get(key)
            if current is None:
                created += 1
            elif _same_receipt(current, line):
                unchanged += 1
            else:
                updated += 1
            self.rows[key] = line
        return created, updated, unchanged, removed


def _same_receipt(left, right):
    return (
        left.order_no == right.order_no
        and left.quantity == right.quantity
        and left.product_code == right.product_code
        and left.received_on == right.received_on
    )


def totals_by_order(lines):
    totals = {}
    for line in lines:
        if line.unusable_reason:
            continue
        totals[line.order_no] = totals.get(line.order_no, Decimal("0")) + line.quantity
    return totals


class DjangoReceiptWriter:
    """模型合并之后，把计划写到 ProductionReceipt。合并前不要调用。"""

    def existing_keys(self):
        model = get_receipt_model()
        if model is None:
            raise RuntimeError(RECEIPT_NOT_READY)
        raise RuntimeError(
            "ProductionReceipt 已在本分支，但入库字段还没接到这个模型上。"
            "请按模型字段实现写入，不要另建一张入库表。"
        )

    def apply(self, plan):
        self.existing_keys()
        return 0, 0, 0, 0
