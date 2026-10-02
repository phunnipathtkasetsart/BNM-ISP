from django.urls import path
from . import views

app_name = "course"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("create/", views.create, name="create"),
    path("join/", views.join, name="join"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("<int:pk>/members/", views.members_page, name="members_page"),
    path("<int:pk>/members/add/", views.import_members, name="import_members"),
    path("<int:pk>/members/<str:student_id>/remove/", views.remove_member, name="remove_member"),
    path("api/", views.dashboard, name="api_list"),
    path("api/create/", views.create, name="api_create"),
    path("api/join/", views.join, name="api_join"),
    path("api/<int:pk>/", views.detail, name="api_detail"),
    path("api/<int:pk>/edit/", views.edit, name="api_edit"),
    path("api/<int:pk>/delete/", views.delete, name="api_delete"),
    path("api/<int:pk>/members/", views.members, name="api_members"),
    path("api/<int:pk>/members/add/", views.import_members, name="api_import_members"),
    path("api/<int:pk>/members/<str:student_id>/remove/", views.remove_member, name="api_remove_member"),
]
