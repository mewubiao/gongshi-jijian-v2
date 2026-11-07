from django.urls import path

from dispatch import views

app_name = "dispatch"
urlpatterns = [
    path("", views.dispatch_list, name="list"),
    path("new/", views.order_list, name="orders"),
    path("new/<int:work_order_id>/", views.dispatch_create, name="create"),
    path("<int:pk>/", views.dispatch_detail, name="detail"),
]
