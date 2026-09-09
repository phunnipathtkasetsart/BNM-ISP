from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.public_board, name="public_board"),
    path("faq/", views.faq_board, name="faq_board"),
    path("new/", views.announcement_form, name="announcement_create"),
    path("<int:pk>/edit/", views.announcement_form, name="announcement_edit"),
    path("<int:pk>/delete/", views.announcement_delete, name="announcement_delete"),
]
