from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.public_board, name="public_board"),
    path("faq/", views.faq_board, name="faq_board"),
    path(
        "faq/questions/new/",
        views.forum_question_create,
        name="forum_question_create",
    ),
    path(
        "faq/questions/<int:pk>/edit/",
        views.forum_question_edit,
        name="forum_question_edit",
    ),
    path(
        "faq/questions/<int:pk>/archive/",
        views.forum_question_archive,
        name="forum_question_archive",
    ),
    path(
        "faq/questions/<int:pk>/restore/",
        views.forum_question_restore,
        name="forum_question_restore",
    ),
    path(
        "faq/questions/<int:pk>/replies/new/",
        views.forum_reply_create,
        name="forum_reply_create",
    ),
    path(
        "faq/replies/<int:pk>/edit/",
        views.forum_reply_edit,
        name="forum_reply_edit",
    ),
    path(
        "faq/replies/<int:pk>/archive/",
        views.forum_reply_archive,
        name="forum_reply_archive",
    ),
    path(
        "faq/replies/<int:pk>/restore/",
        views.forum_reply_restore,
        name="forum_reply_restore",
    ),
    path("new/", views.announcement_form, name="announcement_create"),
    path("<int:pk>/edit/", views.announcement_form, name="announcement_edit"),
    path("<int:pk>/delete/", views.announcement_delete, name="announcement_delete"),
]
