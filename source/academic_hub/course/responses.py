"""Request parsing and dual HTML/JSON responses shared by every course view.

Most course endpoints serve both the browser (HTML, flash message, redirect)
and the JSON API under ``/course/api/``. These helpers hold that branching in
one place so each view states only *what* happened, not how to say it twice.
"""
import json
import os
from typing import Any, Optional

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

API_PREFIX = "/course/api/"
FORBIDDEN_MESSAGE = "You do not have permission for this action."


def is_api(request) -> bool:
    """Select the response format; authorization is handled by course_access."""
    return request.path.startswith(API_PREFIX)


def payload(request):
    """Return the submitted data from a JSON body or a regular form post."""
    if request.content_type != "application/json":
        return request.POST
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValidationError("Invalid JSON body.")
    # Require string values to preserve identifiers such as section "001".
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        raise ValidationError("Provide an object with string values.")
    # Uploaded files are supplied separately in request.FILES.
    return data


def bind_form(request, form_class):
    """Bind a form to the payload, turning malformed JSON into a form error."""
    try:
        return form_class(payload(request))
    except ValidationError as error:
        form = form_class({})
        form.is_valid()
        form.add_error(None, error)
        return form


def detail_url(course_pk: int) -> str:
    return reverse("course:detail", args=[course_pk])


def respond(request, data: dict, message: str, redirect_to: str,
            *, status: int = 200) -> HttpResponse:
    """Success: JSON ``data`` for the API, flash ``message`` and redirect for HTML."""
    if is_api(request):
        return JsonResponse(data, status=status)
    messages.success(request, message)
    return redirect(redirect_to)


def fail(request, message: str, redirect_to: str, *, status: int = 400,
         html_message: Optional[str] = None) -> HttpResponse:
    """Failure: ``{"error": message}`` for the API, flash error and redirect for HTML."""
    if is_api(request):
        return JsonResponse({"error": message}, status=status)
    messages.error(request, html_message or message)
    return redirect(redirect_to)


def forbidden(request) -> HttpResponse:
    if is_api(request):
        return JsonResponse({"error": FORBIDDEN_MESSAGE}, status=403)
    return render(request, "accounts/no_access.html", status=403)


def form_errors_json(form) -> JsonResponse:
    return JsonResponse({"errors": form.errors.get_json_data()}, status=400)


def form_error(request, form, title: str, *, course=None,
               template: str = "course/form.html") -> HttpResponse:
    if is_api(request):
        return form_errors_json(form)
    return render(request, template, {"form": form, "title": title, "course": course}, status=400)


def _download_name(item: Any) -> str:
    stored = item.file.name if item.file and item.file.name else ""
    return (
        getattr(item, "display_name", "").strip()
        or getattr(item, "original_name", "").strip()
        or getattr(item, "title", "").strip()
        or os.path.basename(stored)
        or "attachment"
    )


def file_download(item) -> FileResponse:
    """Stream an attachment or material file with its original filename."""
    try:
        return FileResponse(item.file.open("rb"), as_attachment=True, filename=_download_name(item))
    except (FileNotFoundError, ValueError, AttributeError):
        raise Http404("Attachment file unavailable")
