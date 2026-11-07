from django.contrib import admin
from django.urls import include, path

from accounts.views import dashboard
from config.views import health

admin.site.site_header = "工时计件"
admin.site.site_title = "工时计件"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("accounts/", include("accounts.urls")),
    path("dispatch/", include("dispatch.urls")),
    path("reports/", include("reporting.urls")),
    path("auxiliary/", include("timekeeping.urls")),
    path("settlement/", include("settlement.urls")),
    path("terminal/", include("terminal.urls")),
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
]
