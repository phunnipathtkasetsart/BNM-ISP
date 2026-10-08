from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.middleware import is_guest_request
from .models import Course, CourseTA, Enrollment

def can_teach(user):
    return user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser)


def visible_courses(user):
    """Owned, enrolled, or ALL courses for Superusers/Department admins."""
    if not user.is_authenticated:
        return Course.objects.none()
    
    # Global access: Department Admins / Superusers can see all courses
    if user.is_active and user.is_superuser:
        return Course.objects.all()

    enrolled = Enrollment.objects.filter(student=user).values("course_id")
    q = Q(owner=user) | Q(pk__in=enrolled)
    return Course.objects.filter(q).distinct()


def course_access(
    *,
    owner=False,
    lecturer=False,
    student=False,
    manage_content=False,
    manage_roster=False,
):
    """Scope guard for Task 4.2 handling HTML and API endpoints."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            api = request.path.startswith("/course/api/")
            user = request.user

            if not user.is_authenticated:
                if api:
                    return JsonResponse({"error": "Authentication required."}, status=401)
                return redirect_to_login(request.get_full_path())

            denied = not user.is_active or is_guest_request(request)
            denied |= lecturer and not can_teach(user)
            denied |= student and (user.is_staff or user.is_superuser)

            if denied:
                if api:
                    return JsonResponse({"error": "You do not have access to this action."}, status=403)
                return render(request, "accounts/no_access.html", status=403)

            if "pk" in kwargs:
                courses = visible_courses(user)

                try:
                    course = get_object_or_404(courses.select_related("owner"), pk=kwargs["pk"])
                except Http404:
                    if api:
                        return JsonResponse({"error": "Class not found."}, status=404)
                    messages.warning(
                        request,
                        "This class is no longer available to you. It may have been deleted or your access may have changed.",
                    )
                    return redirect("course:dashboard")

                # Role flags evaluation
                is_owner = course.owner_id == user.pk and can_teach(user)
                is_ta = not is_owner and CourseTA.objects.filter(course=course, user=user).exists()
                
                # Global admin override: any active superuser gets override access regardless of department
                is_dept_override = bool(user.is_superuser and user.is_active)

                can_manage_roster = is_owner or is_dept_override
                can_manage_content = is_owner or is_ta or is_dept_override

                if (owner or manage_roster) and not can_manage_roster:
                    denied = True
                elif manage_content and not can_manage_content:
                    denied = True

                if denied:
                    if api:
                        return JsonResponse({"error": "You do not have permission for this action."}, status=403)
                    return render(request, "accounts/no_access.html", status=403)

                # Attach context for views & templates
                request.course = course
                request.is_owner = is_owner
                request.is_ta = is_ta
                request.is_dept_override = is_dept_override
                request.can_manage_content = can_manage_content
                request.can_manage_roster = can_manage_roster

            return view(request, *args, **kwargs)

        return wrapped

    return decorate