"""Shorts: the DoomScroll study-video feed (US-14, URS-9, SRS-8).

Everyone signed in watches; lecturers and the Department can also upload.

Guests never reach these views: GuestAccessMiddleware only lets a guest
session through to the views it lists, and these are not among them.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ShortForm
from .models import Short
from .services import create_short
from .storage import signed_clip_url
from .streaming import ranged_clip_response

# Clips sent per request. The page asks for the next batch a few clips
# before it runs out, so scrolling never waits on the network.
FEED_BATCH = 8


def can_create_shorts(user):
    """Lecturers and the Department may post shorts. Students may not."""
    return (
        user.is_authenticated
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


def visible_shorts():
    # Every signed-in user sees every live short for now. Narrowing the feed
    # to a student's courses (US-14) needs shorts linked to courses first.
    return Short.objects.filter(archived_at__isnull=True).prefetch_related("tags")


def feed_item(short):
    return {
        "id": short.pk,
        "title": short.title,
        "description": short.description,
        "tags": [tag.label for tag in short.tags.all()],
        # A stable address on this site rather than a signed MinIO link, so
        # a clip loaded an hour from now still gets a link that is valid.
        "src": reverse("shorts:clip", args=[short.pk]),
    }


def feed_page(offset):
    rows = list(visible_shorts()[offset:offset + FEED_BATCH + 1])
    has_more = len(rows) > FEED_BATCH
    return {
        "items": [feed_item(short) for short in rows[:FEED_BATCH]],
        # None tells the page it has reached the end, and should start over
        # from the top: SRS-8 says the feed loops and never ends.
        "next": offset + FEED_BATCH if has_more else None,
    }


@login_required(login_url="accounts:login")
@require_GET
def feed(request):
    page = feed_page(0)
    return render(request, "shorts/feed.html", {
        "can_create": can_create_shorts(request.user),
        "feed": page,
        "has_shorts": bool(page["items"]),
    })


@login_required(login_url="accounts:login")
@require_GET
def feed_api(request):
    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        offset = -1
    if offset < 0:
        return JsonResponse({"error": "offset must be a whole number, 0 or more."}, status=400)
    return JsonResponse(feed_page(offset))


@login_required(login_url="accounts:login")
@require_GET
def clip(request, pk):
    """Play one clip: the access check, then the bytes.

    With MinIO this answers with a redirect to a signed link, and the video
    then streams from MinIO directly. Locally it streams the file itself.
    """
    short = get_object_or_404(Short.objects.filter(archived_at__isnull=True), pk=pk)
    if settings.SHORTS_STORAGE == "minio":
        response = redirect(signed_clip_url(short, request))
        # The signed link expires, so browsers must ask again, not reuse it.
        response["Cache-Control"] = "private, no-store"
        return response
    return ranged_clip_response(request, short)


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
