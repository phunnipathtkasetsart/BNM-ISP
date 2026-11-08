"""Shorts: the DoomScroll study-video feed (US-14, URS-9, SRS-8).

Everyone signed in watches; lecturers and the Department can also upload.

Guests never reach these views: GuestAccessMiddleware only lets a guest
session through to the views it lists, and these are not among them.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ShortForm
from .models import Short, ShortLike
from .services import create_short, update_short
from .storage import signed_clip_url
from .streaming import ranged_clip_response
from .validators import format_duration

# Clips sent per request. The page asks for the next batch a few clips
# before it runs out, so scrolling never waits on the network.
FEED_BATCH = 8
MANAGE_PER_PAGE = 20


def can_create_shorts(user):
    """Lecturers and the Department may post shorts. Students may not."""
    return (
        user.is_authenticated
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


def visible_shorts(user):
    # Every signed-in user sees every live short for now. Narrowing the feed
    # to a student's courses (US-14) needs shorts linked to courses first.
    return (
        Short.objects.filter(archived_at__isnull=True)
        .annotate(
            like_count=Count("likes", distinct=True),
            comment_count=Count("comments", distinct=True),
            liked=Exists(ShortLike.objects.filter(short=OuterRef("pk"), user=user)),
        )
        .prefetch_related("tags")
        # Spelled out: Django drops Meta.ordering from counting (GROUP BY)
        # queries, and without it the feed would come back in any order.
        .order_by("-created_at", "-pk")
    )


def feed_item(short):
    return {
        "id": short.pk,
        "title": short.title,
        "description": short.description,
        "tags": [tag.label for tag in short.tags.all()],
        # A stable address on this site rather than a signed MinIO link, so
        # a clip loaded an hour from now still gets a link that is valid.
        "src": reverse("shorts:clip", args=[short.pk]),
        "liked": short.liked,
        "like_count": short.like_count,
        "comment_count": short.comment_count,
        "like_url": reverse("shorts:api_like", args=[short.pk]),
        "comments_url": reverse("shorts:api_comments", args=[short.pk]),
    }


def feed_page(offset, user):
    rows = list(visible_shorts(user)[offset:offset + FEED_BATCH + 1])
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
    page = feed_page(0, request.user)
    # ?start=<id> opens the feed on that short (from Edit Short's "View"),
    # then carries on through the usual feed. It is taken out of the first
    # batch so it does not show twice in a row; offsets are unchanged, so
    # nothing is skipped, and it comes round again in the loop as normal.
    start = request.GET.get("start", "")
    if start.isdigit():
        first = visible_shorts(request.user).filter(pk=int(start)).first()
        if first is not None:
            page["items"] = [feed_item(first)] + [
                item for item in page["items"] if item["id"] != first.pk]
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
    return JsonResponse(feed_page(offset, request.user))


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

    return render_short_form(request, form, short=None)


def render_short_form(request, form, *, short):
    """Create Short and the edit page share one template; short=None is create."""
    return render(request, "shorts/create.html", {
        "form": form,
        "short": short,
        "length": format_duration(short.duration_seconds) if short else "",
        "max_seconds": settings.SHORTS_MAX_SECONDS,
        "max_bytes": settings.SHORTS_MAX_UPLOAD_BYTES,
    }, status=400 if form.is_bound else 200)


# --- Edit Short: managing uploaded shorts ------------------------------------

def manageable_shorts(user):
    """The shorts this user may edit and delete.

    Lecturers manage the shorts they uploaded. The Department manages every
    short on the site, as it moderates everything else.
    """
    rows = Short.objects.all() if user.is_superuser else Short.objects.filter(author=user)
    return rows.select_related("author")


def staff_only(request):
    """None if the user may manage shorts, otherwise the response to send."""
    if can_create_shorts(request.user):
        return None
    messages.error(request, "Only lecturers and the Department can edit shorts.")
    return redirect("shorts:feed")


@login_required(login_url="accounts:login")
@require_GET
def manage(request):
    if (refused := staff_only(request)) is not None:
        return refused
    rows = (
        manageable_shorts(request.user)
        .annotate(like_count=Count("likes", distinct=True),
                  comment_count=Count("comments", distinct=True))
        .prefetch_related("tags")
        .order_by("-created_at", "-pk")  # counting drops Meta.ordering
    )
    page = Paginator(rows, MANAGE_PER_PAGE).get_page(request.GET.get("page"))
    for short in page:
        short.length = format_duration(short.duration_seconds)
    return render(request, "shorts/manage.html", {
        "page_obj": page,
        "is_department": request.user.is_superuser,
        "can_create": True,
    })


@login_required(login_url="accounts:login")
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    if (refused := staff_only(request)) is not None:
        return refused
    # Someone else's short is a 404, not a 403: it does not say it exists.
    short = get_object_or_404(manageable_shorts(request.user), pk=pk)
    form = ShortForm(
        request.POST or None, request.FILES or None, auto_id="short_%s", editing=True,
        initial={
            "title": short.title,
            "description": short.description,
            "tags": ", ".join(tag.label for tag in short.tags.all()),
        },
    )
    if request.method == "POST" and form.is_valid():
        update_short(
            short,
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            tags=form.cleaned_data["tags"],
            clip=form.cleaned_data["clip"],
            duration_seconds=form.clip_duration,
        )
        messages.success(request, f"“{short.title}” was updated.")
        return redirect("shorts:manage")
    return render_short_form(request, form, short=short)


@login_required(login_url="accounts:login")
@require_http_methods(["GET", "POST"])
def delete(request, pk):
    """GET asks to confirm; POST deletes the short, its clip, likes and comments."""
    if (refused := staff_only(request)) is not None:
        return refused
    short = get_object_or_404(manageable_shorts(request.user), pk=pk)
    if request.method == "POST":
        title = short.title
        short.delete()  # signals.py removes the clip from storage
        messages.success(request, f"“{title}” was deleted.")
        return redirect("shorts:manage")
    return render(request, "shorts/delete.html", {
        "short": short,
        "length": format_duration(short.duration_seconds),
        "like_count": short.likes.count(),
        "comment_count": short.comments.count(),
        "can_create": True,
    })
