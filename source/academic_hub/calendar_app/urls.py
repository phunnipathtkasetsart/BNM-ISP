from django.urls import path

from . import views

app_name = "calendar"

urlpatterns = [
    path("", views.month_view, name="month"),
    path("notes/new/", views.note_save, name="note_create"),
    path("notes/<int:pk>/edit/", views.note_save, name="note_edit"),
    path("notes/<int:pk>/delete/", views.note_delete, name="note_delete"),
]
