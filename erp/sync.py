"""把易飞未结工单和生产入库同步到本地。只读易飞，幂等，未知主数据只告警。"""

import logging
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from erp.models import SyncLock, SyncLog, SyncState, WorkOrder
from erp.receipts import (
    ProductionReceiptWriter,
    ReceiptStats,
    four_places,
    orders_by_key,
    plan_receipts,
    products_by_code,
)
from masterdata.models import Product, WorkCenter

logger = logging.getLogger("erp.sync")

_TWO = Decimal("0.01")
_WARNING_LIMIT = 100
_IN_CHUNK = 500


class LockLost(RuntimeError):
    """同步超过锁定时间，结果放弃，避免和下一轮叠写。"""


def cap_warnings(warnings):
    if len(warnings) <= _WARNING_LIMIT:
        return list(warnings)
    hidden = len(warnings) - _WARNING_LIMIT
    return [*warnings[:_WARNING_LIMIT], f"另有 {hidden} 条告警未写入"]


def public_error(exc, redact):
    parts = [str(exc)]
    cause = exc.__cause__
    if cause is not None:
        parts.append(str(cause))
    text = "：".join(part for part in parts if part)
    if redact:
        text = text.replace(redact, "******")
    return text[:2000]


def _claim_lock(now, stale_seconds):
    SyncLock.objects.get_or_create(
        name=SyncLock.WORKORDERS,
        defaults={"holder": "", "held_until": None},
    )
    token = uuid.uuid4().hex
    until = now + timedelta(seconds=stale_seconds)
    claimed = (
        SyncLock.objects.filter(name=SyncLock.WORKORDERS)
        .filter(Q(held_until__isnull=True) | Q(held_until__lt=now))
        .update(holder=token, held_until=until)
    )
    if claimed != 1:
        return None
    SyncLog.objects.filter(status=SyncLog.Status.RUNNING).update(
        status=SyncLog.Status.FAILED,
        finished_at=now,
        error_summary="超过锁定时间仍未结束，已由新的同步接管",
    )
    return token


def _release_lock(token):
    if not token:
        return
    SyncLock.objects.filter(name=SyncLock.WORKORDERS, holder=token).update(
        holder="",
        held_until=None,
    )


def _holding(token):
    if not token:
        return True
    return SyncLock.objects.filter(
        name=SyncLock.WORKORDERS,
        holder=token,
        held_until__gt=timezone.now(),
    ).exists()


def _load_by_code(model, codes):
    found = {}
    codes = [code for code in codes if code]
    for start in range(0, len(codes), _IN_CHUNK):
        chunk = codes[start : start + _IN_CHUNK]
        for item in model.objects.filter(code__in=chunk):
            found[item.code] = item
    return found


def _two_places(value):
    quantized = value.quantize(_TWO, rounding=ROUND_HALF_UP)
    return quantized, quantized != value


def _planned_qty(row, warnings):
    if row.planned_qty is None:
        return None
    rounded, changed = four_places(row.planned_qty)
    if changed:
        warnings.append(
            f"工单 {row.order_no} 的计划数量 {row.planned_qty} 已四舍五入为 {rounded}"
        )
    return rounded


def _classify(status_raw, open_values, closed_values):
    text = "" if status_raw is None else str(status_raw).strip()
    if text in open_values:
        return "open"
    if text in closed_values:
        return "closed"
    return "unknown"


def _remaining(row):
    notes = []
    if row.remaining_qty is not None:
        value = row.remaining_qty
        label = "剩余量"
    elif row.planned_qty is not None:
        completed = row.completed_qty if row.completed_qty is not None else Decimal("0")
        value = row.planned_qty - completed
        label = "计划数量减已完成量"
        if row.completed_qty is None:
            notes.append(f"工单 {row.order_no} 没有已完成量，按 0 计算剩余量")
    else:
        return None, [f"工单 {row.order_no} 没有剩余量，也没有计划数量，已跳过"]
    rounded, changed = _two_places(value)
    if changed:
        notes.append(f"工单 {row.order_no} 的{label} {value} 已四舍五入为 {rounded}")
    if rounded < 0:
        return None, [*notes, f"工单 {row.order_no} 的剩余量 {rounded} 为负，已跳过"]
    return rounded, notes


def apply_work_orders(fetched, field_map, *, now, token=None):
    warnings = list(fetched.warnings)
    created = updated = unchanged = skipped = 0
    prepared = {}
    protected = set()
    seen = set()
    open_values = tuple(field_map.open_status_values)
    closed_values = tuple(field_map.closed_status_values)

    for row in fetched.rows:
        order_no = (row.order_no or "").strip()
        if row.unusable_reason or not order_no:
            skipped += 1
            warnings.append(row.unusable_reason or "来源行缺少工单号，已跳过")
            if order_no:
                protected.add(order_no)
                prepared.pop(order_no, None)
            continue
        if order_no in seen:
            warnings.append(f"工单 {order_no} 在本次结果里出现多次，以后一条为准")
        seen.add(order_no)
        kind = _classify(row.status_raw, open_values, closed_values)
        if kind == "unknown":
            skipped += 1
            protected.add(order_no)
            prepared.pop(order_no, None)
            warnings.append(
                f"工单 {order_no} 的状态值 {row.status_raw or '（空）'} "
                "未配置为未结或已关闭，已跳过且不改本地镜像"
            )
            continue
        if kind == "closed":
            protected.discard(order_no)
            prepared.pop(order_no, None)
            continue
        product_code = (row.product_code or "").strip()
        center_code = (row.work_center_code or "").strip()
        remaining, notes = _remaining(row)
        warnings.extend(notes)
        if remaining is None or not product_code or not center_code:
            skipped += 1
            protected.add(order_no)
            prepared.pop(order_no, None)
            if not product_code:
                warnings.append(f"工单 {order_no} 缺少产品编码，已跳过")
            if not center_code:
                warnings.append(f"工单 {order_no} 缺少工作中心编码，已跳过")
            continue
        protected.discard(order_no)
        prepared[order_no] = (row, product_code, center_code, remaining)

    products = _load_by_code(Product, {item[1] for item in prepared.values()})
    centers = _load_by_code(WorkCenter, {item[2] for item in prepared.values()})
    ready = {}
    for order_no, (row, product_code, center_code, remaining) in prepared.items():
        missing = []
        product = products.get(product_code)
        center = centers.get(center_code)
        if product is None:
            missing.append(f"产品 {product_code}")
        if center is None:
            missing.append(f"工作中心 {center_code}")
        if missing:
            skipped += 1
            protected.add(order_no)
            warnings.append(
                f"工单 {order_no} 的{'、'.join(missing)}在本系统不存在，已跳过"
            )
            continue
        ready[order_no] = (row, product, center, remaining)

    with transaction.atomic():
        if not _holding(token):
            raise LockLost("同步锁已过期，本次不写入工单")
        for order_no, (row, product, center, remaining) in ready.items():
            outcome = _upsert_work_order(
                order_no,
                product,
                center,
                remaining,
                row,
                now,
                warnings,
            )
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1
        keep = set(ready) | protected
        closed = _close_missing(keep, now)
        if not _holding(token):
            raise LockLost("同步锁已过期，本次不写入工单")
    if not fetched.rows and closed:
        warnings.append(
            f"本次未拉到任何未结工单，已将本地 {closed} 张未结工单标为已关闭。"
            "若开放状态码或过滤条件尚未核对，请先停掉定时任务。"
        )
    return {
        "fetched": len(fetched.rows),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "closed": closed,
        "skipped": skipped,
        "warnings": warnings,
    }


def _assign_mirror_fields(order, row, planned, warnings):
    order.erp_order_type = (row.order_type or "")[:40]
    order.erp_key = (row.erp_key or order.order_no)[:180]
    order.planned_qty = planned
    order.unit = (row.unit or "")[:40]
    order.erp_status = (row.status_raw or "")[:20]
    order.erp_start_date = row.start_date
    order.erp_due_date = row.due_date
    if row.touch_source_modified_at:
        order.source_modified_at = row.source_modified_at
    if len(row.status_raw or "") > 20:
        warnings.append(f"工单 {order.order_no} 的易飞状态超过 20 字，已截断")


def _mirror_changed(existing, row, planned):
    erp_key = (row.erp_key or existing.order_no)[:180]
    checks = [
        existing.erp_order_type != (row.order_type or "")[:40],
        existing.erp_key != erp_key,
        existing.planned_qty != planned,
        existing.unit != (row.unit or "")[:40],
        existing.erp_status != (row.status_raw or "")[:20],
        existing.erp_start_date != row.start_date,
        existing.erp_due_date != row.due_date,
    ]
    if row.touch_source_modified_at:
        checks.append(existing.source_modified_at != row.source_modified_at)
    return any(checks)


def _upsert_work_order(order_no, product, center, remaining, row, now, warnings):
    planned = _planned_qty(row, warnings)
    existing = (
        WorkOrder.objects.filter(order_no=order_no).select_related("product").first()
    )
    if existing is None:
        order = WorkOrder(
            order_no=order_no,
            product=product,
            work_center=center,
            status=WorkOrder.Status.OPEN,
            remaining_qty=remaining,
            synced_at=now,
        )
        _assign_mirror_fields(order, row, planned, warnings)
        order.save()
        return "created"
    changed = False
    if existing.product_id != product.id:
        if existing.dispatches.exists():
            warnings.append(
                f"工单 {order_no} 的产品从 {existing.product.code} "
                f"改为 {product.code}，已有派工仍指向原工序"
            )
        existing.product = product
        changed = True
    if existing.work_center_id != center.id:
        existing.work_center = center
        changed = True
    if existing.remaining_qty != remaining:
        existing.remaining_qty = remaining
        changed = True
    if existing.status != WorkOrder.Status.OPEN:
        existing.status = WorkOrder.Status.OPEN
        changed = True
    if _mirror_changed(existing, row, planned):
        _assign_mirror_fields(existing, row, planned, warnings)
        changed = True
    existing.synced_at = now
    existing.save()
    if changed:
        return "updated"
    return "unchanged"


def _close_missing(keep, now):
    closed = 0
    for order in WorkOrder.objects.filter(status=WorkOrder.Status.OPEN):
        if order.order_no in keep:
            continue
        order.status = WorkOrder.Status.CLOSED
        order.synced_at = now
        order.save()
        closed += 1
    return closed


def apply_receipts(fetched, *, delete_missing, now, token=None):
    writer = ProductionReceiptWriter()
    known = set(orders_by_key())
    plan = plan_receipts(
        fetched.rows,
        known_order_keys=known,
        delete_missing=delete_missing,
        existing_keys=writer.existing_keys(),
    )
    if not _holding(token):
        raise LockLost("同步锁已过期，本次不写入入库")
    created, updated, unchanged, removed, warnings = writer.apply(
        plan,
        now=now,
        products_by_code=products_by_code(
            {line.product_code for line in plan.desired.values()}
        ),
        orders_by_key=orders_by_key(),
    )
    return ReceiptStats(
        fetched=len(fetched.rows),
        created=created,
        updated=updated,
        unchanged=unchanged,
        removed=removed,
        skipped=plan.skipped,
        warnings=[*fetched.warnings, *warnings],
    )


def _summary(warnings, error=""):
    parts = []
    if error:
        parts.append(error)
    parts.extend(cap_warnings(warnings))
    return "\n".join(parts)[:4000]


def _write_log(
    sync_type,
    trigger,
    now,
    *,
    status,
    inserted,
    updated,
    skipped,
    invalidated,
    warnings,
    error="",
):
    finished = timezone.now()
    log = SyncLog.objects.create(
        sync_type=sync_type,
        trigger=trigger,
        status=status,
        started_at=now,
        finished_at=finished,
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        invalidated=invalidated,
        error_summary=_summary(warnings, error),
    )
    state, _created = SyncState.objects.get_or_create(sync_type=sync_type)
    state.last_started_at = now
    if status == SyncLog.Status.SUCCEEDED:
        state.last_succeeded_at = finished
    state.save()
    return log


def run_sync(
    source,
    field_map,
    receipt_map,
    *,
    now=None,
    stale_seconds=900,
    redact="",
    trigger=SyncLog.Trigger.MANUAL,
):
    now = now or timezone.now()
    token = _claim_lock(now, stale_seconds)
    if token is None:
        skipped = _write_log(
            SyncLog.SyncType.WORK_ORDER,
            trigger,
            now,
            status=SyncLog.Status.FAILED,
            inserted=0,
            updated=0,
            skipped=0,
            invalidated=0,
            warnings=[],
            error="已有同步正在执行",
        )
        return skipped, None
    try:
        work_fetched = source.fetch_open_work_orders()
        work_stats = apply_work_orders(work_fetched, field_map, now=now, token=token)
        work_log = _write_log(
            SyncLog.SyncType.WORK_ORDER,
            trigger,
            now,
            status=SyncLog.Status.SUCCEEDED,
            inserted=work_stats["created"],
            updated=work_stats["updated"],
            skipped=work_stats["skipped"],
            invalidated=work_stats["closed"],
            warnings=work_stats["warnings"],
        )
        try:
            receipt_fetched = source.fetch_production_receipts()
            receipt_stats = apply_receipts(
                receipt_fetched,
                delete_missing=receipt_map.delete_missing,
                now=now,
                token=token,
            )
        except Exception as exc:
            message = public_error(exc, redact)
            logger.error("易飞入库同步失败：%s", message)
            receipt_log = _write_log(
                SyncLog.SyncType.RECEIPT,
                trigger,
                now,
                status=SyncLog.Status.FAILED,
                inserted=0,
                updated=0,
                skipped=0,
                invalidated=0,
                warnings=[],
                error=message,
            )
            return work_log, receipt_log
        receipt_log = _write_log(
            SyncLog.SyncType.RECEIPT,
            trigger,
            now,
            status=SyncLog.Status.SUCCEEDED,
            inserted=receipt_stats.created,
            updated=receipt_stats.updated,
            skipped=receipt_stats.skipped,
            invalidated=receipt_stats.removed,
            warnings=receipt_stats.warnings,
        )
        return work_log, receipt_log
    except Exception as exc:
        message = public_error(exc, redact)
        logger.error("易飞工单同步失败：%s", message)
        failed = _write_log(
            SyncLog.SyncType.WORK_ORDER,
            trigger,
            now,
            status=SyncLog.Status.FAILED,
            inserted=0,
            updated=0,
            skipped=0,
            invalidated=0,
            warnings=[],
            error=message,
        )
        return failed, None
    finally:
        _release_lock(token)


def preview_sync(source, field_map, receipt_map, *, stale_seconds=900):
    """拉取并在事务里试算，然后回滚。不写同步日志。"""

    now = timezone.now()
    token = _claim_lock(now, stale_seconds)
    if token is None:
        raise LockLost("已有同步正在执行")
    try:
        work_fetched = source.fetch_open_work_orders()
        receipt_fetched = source.fetch_production_receipts()
        with transaction.atomic():
            work_stats = apply_work_orders(
                work_fetched,
                field_map,
                now=now,
                token=token,
            )
            receipt_stats = apply_receipts(
                receipt_fetched,
                delete_missing=receipt_map.delete_missing,
                now=now,
                token=token,
            )
            transaction.set_rollback(True)
        return work_stats, receipt_stats
    finally:
        _release_lock(token)


def load_runtime():
    import os

    from erp.mappings import overlay_receipt_env
    from erp.source import SqlServerYifeiSource
    from erp.yifei_config import (
        load_connection_config,
        load_receipt_field_map,
        load_schedule_config,
        load_work_order_field_map,
    )

    connection = load_connection_config()
    env = overlay_receipt_env(os.environ)
    work_map = load_work_order_field_map(env)
    receipt_map = load_receipt_field_map(env)
    schedule = load_schedule_config(env)
    source = SqlServerYifeiSource(connection, work_map, receipt_map)
    return source, work_map, receipt_map, schedule, connection


def run_from_environment(*, trigger=SyncLog.Trigger.MANUAL):
    source, work_map, receipt_map, schedule, connection = load_runtime()
    return run_sync(
        source,
        work_map,
        receipt_map,
        stale_seconds=schedule.stale_seconds,
        redact=connection.password,
        trigger=trigger,
    )
