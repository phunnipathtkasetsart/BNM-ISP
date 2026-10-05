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
    path("<int:pk>/members/import-csv/", views.import_csv, name="import_csv"),
    path("<int:pk>/members/<str:student_id>/remove/", views.remove_member, name="remove_member"),

    # Task 4.3: Course Post & Attachment Routes
    path("<int:pk>/posts/create/", views.create_post, name="create_post"),
    path("<int:pk>/posts/<int:post_id>/delete/", views.delete_post, name="delete_post"),
    path("<int:pk>/attachments/<int:attachment_id>/download/", views.download_attachment, name="download_attachment"),

    # API routes
    path("api/", views.dashboard, name="api_list"),
    path("api/create/", views.create, name="api_create"),
    path("api/join/", views.join, name="api_join"),
    path("api/<int:pk>/", views.detail, name="api_detail"),
    path("api/<int:pk>/edit/", views.edit, name="api_edit"),
    path("api/<int:pk>/delete/", views.delete, name="api_delete"),
    path("api/<int:pk>/members/", views.members, name="api_members"),
    path("api/<int:pk>/members/add/", views.import_members, name="api_import_members"),
    path("api/<int:pk>/members/import-csv/", views.import_csv, name="api_import_csv"),
    path("api/<int:pk>/members/<str:student_id>/remove/", views.remove_member, name="api_remove_member"),
    path("api/<int:pk>/posts/create/", views.create_post, name="api_create_post"),
    path("api/<int:pk>/posts/<int:post_id>/delete/", views.delete_post, name="api_delete_post"),
]