from django.contrib import admin
from django.urls import path

from config.views import health

admin.site.site_header = "工时计件"
admin.site.site_title = "工时计件"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
]
