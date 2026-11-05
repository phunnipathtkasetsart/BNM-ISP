from django.urls import path

from . import views

app_name = "shorts"

urlpatterns = [
    path("", views.feed, name="feed"),
    path("create/", views.create, name="create"),
    path("<int:pk>/clip/", views.clip, name="clip"),
    path("api/feed/", views.feed_api, name="api_feed"),
]
