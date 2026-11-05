from django.urls import path

from . import interactions, views

app_name = "shorts"

urlpatterns = [
    path("", views.feed, name="feed"),
    path("create/", views.create, name="create"),
    path("<int:pk>/clip/", views.clip, name="clip"),
    path("api/feed/", views.feed_api, name="api_feed"),
    path("api/<int:pk>/like/", interactions.like, name="api_like"),
    path("api/<int:pk>/comments/", interactions.comments, name="api_comments"),
    path("api/<int:pk>/comments/<int:comment_id>/delete/", interactions.delete_comment,
         name="api_comment_delete"),
]
