"""Shorts: the DoomScroll study-video feed (US-14, URS-9, SRS-8).

Lecturers and the Department can upload clips. The feed itself (playback,
scrolling) is the next step, so the Shorts page does not list clips yet.

Guests never reach these views: GuestAccessMiddleware only lets a guest
session through to the views it lists, and these are not among them.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ShortForm
from .services import create_short


def can_create_shorts(user):
    """Lecturers and the Department may post shorts. Students may not."""
    return (
        user.is_authenticated
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


@login_required(login_url="accounts:login")
@require_GET
def feed(request):
    return render(request, "shorts/feed.html", {
        "can_create": can_create_shorts(request.user),
    })


@login_required(login_url="accounts:login")
@require_http_methods(["GET", "POST"])
def create(request):
    if not can_create_shorts(request.user):
        messages.error(request, "Only lecturers and the Department can create shorts.")
        return redirect("shorts:feed")

    # "short_" ids: annform.css styles #id_tags as the announcement checkbox list.
    form = ShortForm(request.POST or None, request.FILES or None, auto_id="short_%s")
    if request.method == "POST" and form.is_valid():
        short = create_short(
            author=request.user,
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            tags=form.cleaned_data["tags"],
            clip=form.cleaned_data["clip"],
            duration_seconds=form.clip_duration,
        )
        messages.success(request, f"“{short.title}” was uploaded.")
        return redirect("shorts:feed")

    return render(request, "shorts/create.html", {
        "form": form,
        "max_seconds": settings.SHORTS_MAX_SECONDS,
        "max_bytes": settings.SHORTS_MAX_UPLOAD_BYTES,
    }, status=400 if form.is_bound else 200)
