from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from django.urls import reverse

from masterdata.access import employee_for_user


def business_actor(request, permission, role):
    actor = employee_for_user(request.user)
    if actor is None or actor.role != role or not request.user.has_perm(permission):
        raise PermissionDenied
    return actor


def queue_return_url(request, route):
    """只接受对应列表的筛选地址，不能跳到站外或其他页面。"""
    fallback = reverse(route)
    try:
        target = urlsplit(
            request.POST.get("return_to") or request.GET.get("return_to", "")
        )
    except ValueError:
        return fallback
    if target.scheme or target.netloc or target.path != fallback:
        return fallback
    query = QueryDict(target.query).urlencode()
    return fallback + ("?" + query if query else "")
