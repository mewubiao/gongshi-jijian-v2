from django.urls import path

from timekeeping import views

app_name = "timekeeping"
urlpatterns = [
    path("", views.auxiliary_list, name="list"),
    path("<int:pk>/", views.auxiliary_detail, name="detail"),
]
