"""实际工时：完工减开工，再扣掉暂停重叠。不足一秒的零头舍去。"""


def overlap_seconds(start_a, end_a, start_b, end_b) -> int:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if end <= start:
        return 0
    return int((end - start).total_seconds())
