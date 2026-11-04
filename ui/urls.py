from django.urls import path

from ui import views

urlpatterns = [
    path("", views.home, name="home"),
    path("terminal/", views.terminal, name="terminal"),
    path("ui/", views.gallery, name="ui-gallery"),
    path("ui/modal/", views.gallery_modal, name="ui-modal"),
]
