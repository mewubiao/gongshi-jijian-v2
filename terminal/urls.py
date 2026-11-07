from django.urls import path

from terminal import aux_views, device_views, views, work_views

app_name = "terminal"
urlpatterns = [
    path("", views.identify_employee, name="identify"),
    path("activate/", views.activate_device, name="activate"),
    path("work/", views.home, name="home"),
    path("status/", views.status, name="status"),
    path("activity/", views.activity, name="activity"),
    path("logout/", views.signout, name="logout"),
    path("tasks/<int:pk>/", work_views.task_detail, name="task"),
    path("reports/", work_views.reports, name="reports"),
    path("reports/<int:pk>/", work_views.report_detail, name="report"),
    path("auxiliary/", aux_views.auxiliary, name="auxiliary"),
    path("manage/", device_views.devices, name="devices"),
    path("manage/cards/", device_views.cards, name="cards"),
    path("manage/cards/<int:pk>/qr/", device_views.card_qr, name="card_qr"),
]
