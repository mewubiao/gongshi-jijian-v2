from django.urls import path

from reporting import views

app_name = "reporting"
urlpatterns = [
    path("", views.report_list, name="list"),
    path("<int:pk>/", views.report_detail, name="detail"),
]
