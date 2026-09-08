from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.public_board, name="public_board"),
]
