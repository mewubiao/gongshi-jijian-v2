from django.contrib import admin
from unfold.admin import ModelAdmin

from terminal.models import TerminalDevice


@admin.register(TerminalDevice)
class TerminalDeviceAdmin(ModelAdmin):
    list_display = ("code", "name", "active")
    fields = ("code", "name", "active", "generation")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
