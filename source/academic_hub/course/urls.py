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

    # Task 4.4: materials
    path("<int:pk>/materials/upload/", views.upload_materials, name="upload_materials"),
    path("<int:pk>/materials/<int:material_id>/edit/", views.edit_material, name="edit_material"),
    path("<int:pk>/materials/<int:material_id>/delete/", views.delete_material, name="delete_material"),
    path("<int:pk>/materials/<int:material_id>/download/", views.download_material, name="download_material"),


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

    path("api/<int:pk>/materials/upload/", views.upload_materials, name="api_upload_materials"),
    path("api/<int:pk>/materials/<int:material_id>/edit/", views.edit_material, name="api_edit_material"),
    path("api/<int:pk>/materials/<int:material_id>/delete/", views.delete_material, name="api_delete_material"),

    # Task 5.2: course Q&A JSON API
    path("api/<int:pk>/questions/", views.qa_question_list, name="api_qa_questions"),
    path("api/<int:pk>/questions/<int:question_id>/", views.qa_question_detail, name="api_qa_question_detail"),
    path("api/<int:pk>/questions/<int:question_id>/edit/", views.qa_question_edit, name="api_qa_question_edit"),
    path("api/<int:pk>/questions/<int:question_id>/archive/", views.qa_question_archive, name="api_qa_question_archive"),
    path("api/<int:pk>/questions/<int:question_id>/restore/", views.qa_question_restore, name="api_qa_question_restore"),
    path("api/<int:pk>/questions/<int:question_id>/pin/", views.qa_question_pin, name="api_qa_question_pin"),
    path("api/<int:pk>/questions/<int:question_id>/answers/", views.qa_answer_create, name="api_qa_answer_create"),
    path("api/<int:pk>/questions/<int:question_id>/answers/<int:answer_id>/edit/", views.qa_answer_edit, name="api_qa_answer_edit"),
    path("api/<int:pk>/questions/<int:question_id>/answers/<int:answer_id>/archive/", views.qa_answer_archive, name="api_qa_answer_archive"),
    path("api/<int:pk>/questions/<int:question_id>/answers/<int:answer_id>/restore/", views.qa_answer_restore, name="api_qa_answer_restore"),
    path("api/<int:pk>/questions/<int:question_id>/answers/<int:answer_id>/verify/", views.qa_answer_verify, name="api_qa_answer_verify"),

    path("<int:pk>/posts/<int:post_id>/edit/", views.edit_post, name="edit_post"),

        # Task 4.5: teaching assistants
    path("<int:pk>/tas/<str:student_id>/assign/", views.ta_assign, name="ta_assign"),
    path("<int:pk>/tas/<str:student_id>/remove/", views.ta_remove, name="ta_remove"),
    path("api/<int:pk>/tas/<str:student_id>/assign/", views.ta_assign, name="api_ta_assign"),
    path("api/<int:pk>/tas/<str:student_id>/remove/", views.ta_remove, name="api_ta_remove"),

    path("<int:pk>/tas/assign/", views.ta_assign, name="ta_assign_form"),

]