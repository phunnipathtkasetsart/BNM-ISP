"""Shorts: the DoomScroll study-video feed (US-14, URS-9, SRS-8).

This first slice adds the tab and the staff entry point only. Video
storage, the upload form and the scrolling feed come in later steps.

Guests never reach these views: GuestAccessMiddleware only lets a guest
session through to the views it lists, and these are not among them.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET


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
@require_GET
def create(request):
    if not can_create_shorts(request.user):
        messages.error(request, "Only lecturers and the Department can create shorts.")
        return redirect("shorts:feed")
    return render(request, "shorts/create.html")
