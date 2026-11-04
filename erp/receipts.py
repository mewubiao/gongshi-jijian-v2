"""把生产入库行写入 ProductionReceipt。

缺失行默认不删除。打开全量快照后，把这次没出现的有效行标成源端失效，
不物理删除。数量按模型保留四位小数。
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from erp.models import ProductionReceipt, WorkOrder
from masterdata.models import Product

FOUR = Decimal("0.0001")


def four_places(value):
    quantized = value.quantize(FOUR, rounding=ROUND_HALF_UP)
    return quantized, quantized != value


@dataclass
class ReceiptStats:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    warnings: list = field(default_factory=list)


@dataclass(frozen=True)
class ReceiptPlan:
    desired: dict
    remove_keys: tuple
    skipped: int
    warnings: list


def plan_receipts(lines, *, known_order_keys, delete_missing, existing_keys):
    """known_order_keys 含工单号和 erp_key。未知工单仍保留行，外键留空。"""

    warnings = []
    skipped = 0
    desired = {}
    protected = set()
    seen = set()
    for line in lines:
        if line.unusable_reason or not line.erp_key:
            skipped += 1
            warnings.append(line.unusable_reason or "入库行缺少来源键，已跳过")
            if line.erp_key:
                protected.add(line.erp_key)
            continue
        if line.erp_key in seen:
            warnings.append(f"入库 {line.erp_key} 在本次结果里出现多次，以后一条为准")
        seen.add(line.erp_key)
        if line.order_key not in known_order_keys:
            warnings.append(
                f"入库 {line.erp_key} 的工单 {line.order_key} 在本地不存在，"
                "仍写入入库行，工单外键留空"
            )
        if line.receipt_qty is not None and line.receipt_qty < 0:
            skipped += 1
            protected.add(line.erp_key)
            warnings.append(f"入库 {line.erp_key} 的对账数量为负，已跳过")
            continue
        desired[line.erp_key] = line
        protected.discard(line.erp_key)
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


class ProductionReceiptWriter:
    """按易飞入库行键写入 ProductionReceipt。"""

    def existing_keys(self):
        return set(
            ProductionReceipt.objects.filter(active_source_record=True).values_list(
                "erp_key", flat=True
            )
        )

    def apply(self, plan, *, now, products_by_code, orders_by_key):
        created = updated = unchanged = removed = 0
        warnings = list(plan.warnings)
        for erp_key in plan.remove_keys:
            changed = ProductionReceipt.objects.filter(
                erp_key=erp_key,
                active_source_record=True,
            ).update(active_source_record=False, last_synced_at=now)
            removed += changed
        for line in plan.desired.values():
            outcome, note = _upsert_receipt(
                line,
                now=now,
                product=products_by_code.get(line.product_code),
                work_order=orders_by_key.get(line.order_key),
            )
            if note:
                warnings.append(note)
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1
        return created, updated, unchanged, removed, warnings


def _quantize_qty(line, field_name, label):
    value = getattr(line, field_name)
    rounded, changed = four_places(value)
    note = ""
    if changed:
        note = f"入库 {line.erp_key} 的{label} {value} 已四舍五入为 {rounded}"
    return rounded, note


def _upsert_receipt(line, *, now, product, work_order):
    notes = []
    quantities = {}
    for field_name, label in (
        ("receipt_qty", "对账入库数量"),
        ("source_qty_11", "TG011 数量"),
        ("scrap_qty_12", "TG012 数量"),
        ("accepted_qty_13", "TG013 数量"),
    ):
        rounded, note = _quantize_qty(line, field_name, label)
        quantities[field_name] = rounded
        if note:
            notes.append(note)
    if line.product_code and product is None:
        notes.append(
            f"入库 {line.erp_key} 的品号 {line.product_code} "
            "在本系统不存在，产品外键留空"
        )
    if (
        work_order is not None
        and product is not None
        and work_order.product_id != product.id
    ):
        notes.append(
            f"入库 {line.erp_key} 的品号 {line.product_code} 与工单 "
            f"{work_order.order_no} 的产品不一致，仍按入库行记入。"
            "品号是否应一致待按实际库核对"
        )
    payload = {
        "erp_receipt_type": line.receipt_type,
        "erp_receipt_no": line.receipt_no,
        "erp_line_no": line.line_no,
        "receipt_date": line.received_on,
        "product": product,
        "work_order": work_order,
        "erp_work_order_key": line.order_key,
        "receipt_qty": quantities["receipt_qty"],
        "source_qty_11": quantities["source_qty_11"],
        "scrap_qty_12": quantities["scrap_qty_12"],
        "accepted_qty_13": quantities["accepted_qty_13"],
        "last_synced_at": now,
        "active_source_record": True,
    }
    if line.touch_source_modified_at:
        payload["source_modified_at"] = line.source_modified_at
    existing = ProductionReceipt.objects.filter(erp_key=line.erp_key).first()
    if existing is None:
        ProductionReceipt.objects.create(erp_key=line.erp_key, **payload)
        return "created", "；".join(notes)
    changed = False
    for name, value in payload.items():
        if name == "last_synced_at":
            continue
        if getattr(existing, name) != value:
            setattr(existing, name, value)
            changed = True
    if line.touch_source_modified_at and "source_modified_at" in payload:
        if existing.source_modified_at != payload["source_modified_at"]:
            existing.source_modified_at = payload["source_modified_at"]
            changed = True
    existing.last_synced_at = now
    existing.save()
    if changed:
        return "updated", "；".join(notes)
    return "unchanged", "；".join(notes)


def orders_by_key():
    found = {}
    for order in WorkOrder.objects.all():
        found[order.order_no] = order
        if order.erp_key:
            found[order.erp_key] = order
    return found


def products_by_code(codes):
    found = {}
    codes = [code for code in codes if code]
    for start in range(0, len(codes), 500):
        chunk = codes[start : start + 500]
        for item in Product.objects.filter(code__in=chunk):
            found[item.code] = item
    return found
