from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("oauth/google/", views.google_login, name="google_login"),
    path("oauth/google/callback/", views.google_callback, name="google_callback"),
    path("oauth/google/register/", views.google_register, name="google_register"),
    path("register/", views.register_view, name="register"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("logout/", views.logout_view, name="logout"),
    path("forgot-password/", views.forgot_password_view, name="forgot_password"),
    path("faq/", views.faq_view, name="faq"),
    path("roles/", views.roles_view, name="roles"),
]
