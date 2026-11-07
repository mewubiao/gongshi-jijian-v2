"""按员工活动时段等分并行工时，整秒尾差给派工顺序靠前的任务。"""

from django.db.models import Q


def allocated_seconds(target):
    from reporting.models import WorkSession

    end = target.ended_at
    anchor = target.started_at
    while True:
        sessions = list(
            WorkSession.objects.filter(
                started_by_id=target.started_by_id,
                dispatch__kind="single",
                started_at__lt=end,
            )
            .filter(Q(ended_at__gte=anchor) | Q(ended_at__isnull=True))
            .prefetch_related("pauses")
        )
        earlier = min([anchor, *(session.started_at for session in sessions)])
        if earlier == anchor:
            break
        anchor = earlier
    if target.pk not in {session.pk for session in sessions}:
        sessions.append(target)
    boundaries = {anchor, end}
    pauses = {}
    for session in sessions:
        boundaries.update(
            (max(anchor, session.started_at), min(end, session.ended_at or end))
        )
        pauses[session.pk] = [
            (max(anchor, pause.paused_at), min(end, pause.resumed_at or end))
            for pause in session.pauses.all()
            if pause.paused_at < end
            and (pause.resumed_at is None or pause.resumed_at > anchor)
        ]
        for start, stop in pauses[session.pk]:
            boundaries.update((start, stop))
    points = sorted(boundaries)
    total = carry = 0
    # ponytail: 同一员工的重叠任务边界扫描；极密集并行时改为增量事件扫描。
    for start, stop in zip(points, points[1:], strict=False):
        active = [
            session
            for session in sessions
            if session.started_at <= start
            and (session.ended_at is None or session.ended_at >= stop)
            and not any(
                left < stop and right > start for left, right in pauses[session.pk]
            )
        ]
        if not active:
            continue
        delta = stop - start
        carry += (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        seconds, carry = divmod(carry, 1000000)
        share, remainder = divmod(seconds, len(active))
        for index, session in enumerate(
            sorted(active, key=lambda item: (item.dispatch_id, item.pk))
        ):
            if session.pk == target.pk:
                total += share + (index < remainder)
    return total
