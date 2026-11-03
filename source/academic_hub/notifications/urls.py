from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("api/", views.notification_list, name="list"),
    path("api/read-all/", views.notification_read_all, name="read_all"),
    path("api/<int:pk>/read/", views.notification_read, name="read"),
]
