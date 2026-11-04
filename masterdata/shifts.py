"""工作中心与班组的白班 / 夜班钟点。

钟点按 Asia/Shanghai 理解，结束时刻不包含在本班内。夜班可以跨午夜。
"""

from datetime import time

from django.core.exceptions import ValidationError


def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def clock_ranges(start: time, end: time) -> list[tuple[int, int]]:
    start_s = _seconds(start)
    end_s = _seconds(end)
    if start_s == end_s:
        raise ValidationError("班次起止时间不能相同")
    day = 24 * 3600
    if start_s < end_s:
        return [(start_s, end_s)]
    return [(start_s, day), (0, end_s)]


def validate_shift_pair(day_start, day_end, night_start, night_end):
    day_ranges = clock_ranges(day_start, day_end)
    night_ranges = clock_ranges(night_start, night_end)
    for left in day_ranges:
        for right in night_ranges:
            if left[0] < right[1] and right[0] < left[1]:
                raise ValidationError("白班与夜班的时间不能重叠")


def effective_shift(team=None, work_center=None):
    """班组填了完整覆盖时用班组，否则用班组所属工作中心。

    员工没有班组时，调用方可以只传工单上的工作中心。
    """
    if team is not None and team.has_shift_override:
        return (
            team.day_shift_start,
            team.day_shift_end,
            team.night_shift_start,
            team.night_shift_end,
        )
    center = team.work_center if team is not None else work_center
    if center is None:
        raise ValidationError("无法确定班次：缺少班组或工作中心")
    return (
        center.day_shift_start,
        center.day_shift_end,
        center.night_shift_start,
        center.night_shift_end,
    )
