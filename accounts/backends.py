from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission
from django.db.models import Q

from accounts.permissions import ROLE_PERMISSIONS
from masterdata.access import employee_for_user


class EmployeeBackend(ModelBackend):
    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user) and (
            user.is_superuser or employee_for_user(user) is not None
        )

    def get_user_permissions(self, user_obj, obj=None):
        if user_obj.is_anonymous or not user_obj.is_active:
            return set()
        if user_obj.is_superuser:
            return {
                f"{app}.{name}"
                for app, name in Permission.objects.values_list(
                    "content_type__app_label", "codename"
                )
            }
        employee = employee_for_user(user_obj)
        return set(ROLE_PERMISSIONS.get(employee.role, ())) if employee else set()

    get_all_permissions = get_user_permissions

    def get_group_permissions(self, user_obj, obj=None):
        return set()

    def has_perm(self, user_obj, perm, obj=None):
        if perm not in self.get_all_permissions(user_obj):
            return False
        if obj is None:
            return True
        opts = obj._meta
        if not perm.startswith(f"{opts.app_label}.") or not perm.endswith(
            f"_{opts.model_name}"
        ):
            return False
        from accounts.scopes import scope_queryset

        return scope_queryset(
            user_obj, obj.__class__._default_manager.filter(pk=obj.pk)
        ).exists()

    def with_perm(self, perm, is_active=True, include_superusers=True, obj=None):
        if isinstance(perm, Permission):
            perm = f"{perm.content_type.app_label}.{perm.codename}"
        roles = [role for role, perms in ROLE_PERMISSIONS.items() if perm in perms]
        users = get_user_model()._default_manager.all()
        if obj is not None:
            return users.none()
        allowed = Q(employee__role__in=roles, employee__active=True)
        if include_superusers:
            allowed |= Q(is_superuser=True)
        users = users.filter(allowed)
        return users.filter(is_active=is_active) if is_active is not None else users

    async def aauthenticate(self, request, username=None, password=None, **kwargs):
        return await sync_to_async(self.authenticate)(
            request, username=username, password=password, **kwargs
        )

    async def aget_user(self, user_id):
        return await sync_to_async(self.get_user)(user_id)

    async def aget_user_permissions(self, user_obj, obj=None):
        return await sync_to_async(self.get_user_permissions)(user_obj, obj)

    aget_all_permissions = aget_user_permissions

    async def aget_group_permissions(self, user_obj, obj=None):
        return set()

    async def ahas_perm(self, user_obj, perm, obj=None):
        return await sync_to_async(self.has_perm)(user_obj, perm, obj)
