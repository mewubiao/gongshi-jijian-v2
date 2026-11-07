from django.urls import path

from settlement import views

app_name = "settlement"

urlpatterns = [
    path("", views.index, name="index"),
    path("<int:year>/<int:month>/", views.detail, name="detail"),
    path("<int:year>/<int:month>/export/", views.export, name="export"),
]
