from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Coalesce

from reporting.models import ReportCorrection


def with_effective_quantity(queryset):
    """列表也显示审核后的有效数量，避免逐条查询修正记录。"""
    latest = ReportCorrection.objects.filter(
        piece_report_id=OuterRef("pk"), corrected_qty__isnull=False
    ).order_by("-corrected_at", "-pk")
    return queryset.annotate(
        effective_quantity=Coalesce(
            Subquery(latest.values("corrected_qty")[:1]), F("completed_qty")
        )
    )
