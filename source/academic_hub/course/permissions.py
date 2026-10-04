from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.middleware import is_guest_request
from .models import Course, Enrollment


def can_teach(user):
    return user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser)


def visible_courses(user):
    enrolled = Enrollment.objects.filter(student=user).values("course_id")
    return Course.objects.filter(Q(owner=user) | Q(pk__in=enrolled))


def course_access(*, owner=False, lecturer=False, student=False):
    """Apply the same scope guard to HTML and API routes, including direct URLs."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            api = request.path.startswith("/course/api/")
            if not request.user.is_authenticated:
                if api:
                    return JsonResponse({"error": "Authentication required."}, status=401)
                return redirect_to_login(request.get_full_path())
            denied = not request.user.is_active or is_guest_request(request)
            denied |= lecturer and not can_teach(request.user)
            denied |= student and (request.user.is_staff or request.user.is_superuser)
            if denied:
                if api:
                    return JsonResponse({"error": "You do not have access to this action."}, status=403)
                return render(request, "accounts/no_access.html", status=403)
            if "pk" in kwargs:
                courses = visible_courses(request.user)
                if owner:
                    courses = courses.filter(owner=request.user)
                    if not can_teach(request.user):
                        courses = courses.none()
                try:
                    request.course = get_object_or_404(courses.select_related("owner"), pk=kwargs["pk"])
                except Http404:
                    if api:
                        return JsonResponse({"error": "Class not found."}, status=404)
                    messages.warning(request, "This class is no longer available to you. It may have been deleted or your access may have changed. Please contact your lecturer if you need help.")
                    return redirect("course:dashboard")
            return view(request, *args, **kwargs)
        return wrapped
    return decorate
