from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from accounts.scopes import scope_queryset


class ScopedAdminMixin:
    def get_queryset(self, request):
        return scope_queryset(request.user, super().get_queryset(request))


class AccountAdmin(UserAdmin, ModelAdmin):
    """业务管理员可维护普通账号，技术超级用户仅由超级用户管理。"""

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not request.user.is_superuser:
            queryset = queryset.filter(is_superuser=False)
        return queryset

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser or obj is None:
            return super().get_fieldsets(request, obj)
        return (
            (None, {"fields": ("username", "password")}),
            ("个人信息", {"fields": ("first_name", "last_name", "email")}),
            ("账号状态", {"fields": ("is_active",)}),
        )

    def get_list_display(self, request):
        if request.user.is_superuser:
            return super().get_list_display(request)
        return ("username", "first_name", "last_name", "is_active")

    def get_list_filter(self, request):
        return (
            super().get_list_filter(request)
            if request.user.is_superuser
            else ("is_active",)
        )


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), AccountAdmin)


class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    pass


admin.site.unregister(Group)
admin.site.register(Group, GroupAdmin)
