from django.db.models import Q

from erp.models import WorkOrder


def available_orders(actor):
    return (
        WorkOrder.objects.filter(
            work_center__teams__leader=actor,
            work_center__teams__active=True,
            work_center__active=True,
            product__active=True,
        )
        .select_related("product", "work_center")
        .distinct()
    )


def search_orders(queryset, query):
    return (
        queryset.filter(
            Q(order_no__icontains=query)
            | Q(product__code__icontains=query)
            | Q(product__name__icontains=query)
        )
        if query
        else queryset
    )
