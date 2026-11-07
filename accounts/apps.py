from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "登录与权限"


class BusinessAdminConfig(AdminConfig):
    default_site = "accounts.admin_site.BusinessAdminSite"
