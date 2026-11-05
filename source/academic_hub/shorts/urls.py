from django.urls import path

from . import views

app_name = "shorts"

urlpatterns = [
    path("", views.feed, name="feed"),
    path("create/", views.create, name="create"),
]
