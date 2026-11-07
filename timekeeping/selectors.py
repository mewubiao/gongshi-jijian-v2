from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Coalesce

from timekeeping.models import AuxiliaryWorkCorrection


def with_effective_minutes(queryset):
    latest = AuxiliaryWorkCorrection.objects.filter(
        work_log_id=OuterRef("pk")
    ).order_by("-corrected_at", "-pk")
    return queryset.annotate(
        review_minutes=Coalesce(
            Subquery(latest.values("corrected_effective_minutes")[:1]),
            F("original_effective_minutes"),
            F("effective_minutes"),
        )
    )
