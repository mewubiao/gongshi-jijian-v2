"""报工归属日。整段夜班归属开班日，按 Asia/Shanghai 的钟点判断。"""

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

SHANGHAI = ZoneInfo("Asia/Shanghai")


def in_shift(clock: time, start: time, end: time) -> bool:
    """左闭右开。跨午夜的班次，凌晨仍算前一天开的那一班。"""
    if start == end:
        return False
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def ownership_date(started_at, day_start, day_end, night_start, night_end):
    """按开工时刻归属。夜班跨日时，过了午夜仍归开班日。

    班次之间的空隙文档没有约定。落在空隙里时，用 Asia/Shanghai 的日历日，
    避免归属日为空；这不是一条新的班次制度。
    """
    if started_at is None:
        raise ValidationError("缺少开工时间")
    if timezone.is_naive(started_at):
        raise ValidationError("开工时间必须带时区")
    local = started_at.astimezone(SHANGHAI)
    clock = local.time()
    if in_shift(clock, night_start, night_end):
        if night_start > night_end and clock < night_end:
            return local.date() - timedelta(days=1)
        return local.date()
    if in_shift(clock, day_start, day_end):
        return local.date()
    return local.date()


def classify_shift(started_at, day_start, day_end, night_start, night_end):
    """返回归属日，以及白班 / 夜班名称。空隙没有班次名。"""
    owned = ownership_date(started_at, day_start, day_end, night_start, night_end)
    clock = started_at.astimezone(SHANGHAI).time()
    if in_shift(clock, night_start, night_end):
        return owned, "夜班"
    if in_shift(clock, day_start, day_end):
        return owned, "白班"
    return owned, ""
