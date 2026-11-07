from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from unfold.sites import UnfoldAdminSite

from masterdata.access import employee_for_user
from masterdata.models import Employee


class BusinessAdminSite(UnfoldAdminSite):
    site_header = "工时计件"
    site_title = "工时计件"
    site_url = "/"
    index_title = "管理后台"
    index_template = "accounts/admin_index.html"

    def has_permission(self, request):
        user = request.user
        if not user.is_authenticated or not user.is_active:
            return False
        if user.is_superuser:
            return True
        employee = employee_for_user(user)
        return employee is not None and employee.role != Employee.Role.WORKER

    def login(self, request, extra_context=None):
        if request.user.is_authenticated:
            if not self.has_permission(request):
                raise PermissionDenied
            return redirect("admin:index")
        target = request.GET.get("next", reverse("admin:index"))
        return redirect(f"{reverse('accounts:login')}?{urlencode({'next': target})}")

    def get_log_entries(self, request):
        # 最近动作的摘要不做对象权限过滤，降权后可能暴露旧角色的数据。
        entries = super().get_log_entries(request)
        return entries if request.user.is_superuser else entries.none()

    def search(self, request, extra_context=None):
        # Unfold 的全局搜索缓存以账号为键，角色或数据范围调整后可能返回旧结果。
        raise Http404
