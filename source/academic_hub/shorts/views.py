"""Shorts: the DoomScroll study-video feed (US-14, URS-9, SRS-8).

Everyone signed in watches; lecturers and the Department can also upload.

Guests never reach these views: GuestAccessMiddleware only lets a guest
session through to the views it lists, and these are not among them.
"""

import secrets
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import User as Account

from .access import (
    account_of,
    can_create_shorts,
    manageable_shorts,
    signed_in_page,
)
from .forms import ShortForm
from .models import Short, ShortLike
from .recommend import feed_order
from .search import MAX_RESULTS, parse, search
from .services import create_short, update_short
from .storage import signed_clip_url
from .streaming import ranged_clip_response
from .validators import format_duration

# Clips sent per request. The page asks for the next batch a few clips
# before it runs out, so scrolling never waits on the network.
FEED_BATCH = 8
MANAGE_PER_PAGE = 20
# Round seeds are below this, so they stay exact in JavaScript numbers.
MAX_ROUND = 2**31

FeedPage = dict[str, Any]


def _account(request: HttpRequest) -> Account:
    """The signed-in account; signed_in_page has already checked."""
    user = account_of(request)
    if user is None:
        raise PermissionDenied
    return user


def visible_shorts(user: Account) -> "QuerySet[Short]":
    """Live shorts with counts and this user's like, newest first."""
    # Every signed-in user sees every live short for now. Narrowing the
    # feed to a student's courses (US-14) needs shorts linked to courses.
    return (
        Short.objects.filter(archived_at__isnull=True)
        .annotate(
            like_count=Count("likes", distinct=True),
            comment_count=Count("comments", distinct=True),
            liked=Exists(
                ShortLike.objects.filter(short=OuterRef("pk"), user=user)
            ),
        )
        .prefetch_related("tags")
        # Spelled out: Django drops Meta.ordering from counting (GROUP BY)
        # queries, and without it the feed would come back in any order.
        .order_by("-created_at", "-pk")
    )


def feed_item(short: Short) -> dict[str, Any]:
    """One short as reels.js reads it."""
    return {
        "id": short.pk,
        "title": short.title,
        "description": short.description,
        "tags": [tag.label for tag in short.tags.all()],
        # A stable address on this site rather than a signed MinIO link,
        # so a clip loaded an hour from now still gets a valid link.
        "src": reverse("shorts:clip", args=[short.pk]),
        "liked": getattr(short, "liked", False),
        "like_count": getattr(short, "like_count", 0),
        "comment_count": getattr(short, "comment_count", 0),
        "like_url": reverse("shorts:api_like", args=[short.pk]),
        "comments_url": reverse("shorts:api_comments", args=[short.pk]),
    }


def new_round() -> int:
    """A fresh seed: each round of the endless feed is ordered anew."""
    return secrets.randbelow(MAX_ROUND)


def feed_page(
    offset: int,
    user: Account,
    round_seed: int,
    order: list[int] | None = None,
) -> FeedPage:
    """FEED_BATCH shorts from `offset` in this round's order.

    The round seed goes back to the page, which sends it with the next
    batch's request, so one round is paged through in one fixed order.
    A given `order` (a search's results) is used instead of the usual one.
    """
    if order is None:
        order = feed_order(user, round_seed)
    ids = order[offset : offset + FEED_BATCH]
    rows = visible_shorts(user).in_bulk(ids)
    return {
        "items": [feed_item(rows[pk]) for pk in ids if pk in rows],
        # None tells the page it has reached the end, and should start
        # a new round from the top: SRS-8 says the feed never ends.
        "next": (
            offset + FEED_BATCH if len(order) > offset + FEED_BATCH else None
        ),
        "round": round_seed,
    }


def _round_from(request: HttpRequest, offset: int) -> int:
    """The round a later batch belongs to; a new one for offset 0."""
    sent = request.GET.get("round", "")
    if offset and sent.isdigit() and int(sent) < MAX_ROUND:
        return int(sent)
    return new_round()


def _start_with(page: FeedPage, first: Short) -> None:
    """Put `first` at the front of the page, without showing it twice.

    Offsets are unchanged, so nothing is skipped, and it comes round again
    in the loop as normal.
    """
    page["items"] = [feed_item(first)] + [
        item for item in page["items"] if item["id"] != first.pk
    ]


def search_order(request: HttpRequest) -> list[int] | None:
    """The feed's order when it was opened from a search, else None.

    ?q= is the search, ?start=<id> the result that was opened. The feed is
    that search's results only, from the opened one to the end of the
    list, then round from the top again. A search that finds nothing
    (any more) gives the usual feed.
    """
    q = request.GET.get("q", "").strip()
    if not q:
        return None
    ids = [short.pk for short in search(parse(q))]
    start = request.GET.get("start", "")
    if start.isdigit() and int(start) in ids:
        at = ids.index(int(start))
        ids = ids[at:] + ids[:at]
    return ids or None


def search_scope(request: HttpRequest) -> dict[str, str]:
    """The ?q= and ?start= a feed was opened with, to pass on to the API."""
    q = request.GET.get("q", "").strip()
    start = request.GET.get("start", "")
    scope = {"q": q}
    if start.isdigit():
        scope["start"] = start
    return scope


@signed_in_page
@require_GET
def feed(request: HttpRequest) -> HttpResponse:
    """The reels page; ?start=<id> opens it on that short.

    With ?q= as well (a search result was opened) the feed is only that
    search's results; see search_order.
    """
    user = _account(request)
    order = search_order(request)
    page = feed_page(0, user, new_round(), order)
    api = reverse("shorts:api_feed")
    query = ""
    if order is not None:
        query = request.GET.get("q", "").strip()
        api += "?" + urlencode(search_scope(request))
    else:
        start = request.GET.get("start", "")
        if start.isdigit():
            first = visible_shorts(user).filter(pk=int(start)).first()
            if first is not None:
                _start_with(page, first)
    return render(
        request,
        "shorts/feed.html",
        {
            "can_create": can_create_shorts(user),
            "feed": page,
            "has_shorts": bool(page["items"]),
            "feed_api": api,
            "search_query": query,
        },
    )


@signed_in_page
@require_GET
def feed_api(request: HttpRequest) -> JsonResponse:
    """The next batch of the feed, for ?offset=<n>&round=<seed>.

    ?q= and ?start= (passed on by a feed opened from a search) keep the
    batches to that search's results.
    """
    offset = request.GET.get("offset", "0")
    if not offset.isdigit():
        return JsonResponse(
            {"error": "offset must be a whole number, 0 or more."},
            status=400,
        )
    start = int(offset)
    page = feed_page(
        start,
        _account(request),
        _round_from(request, start),
        search_order(request),
    )
    return JsonResponse(page)


def search_item(short: Short, q: str) -> dict[str, Any]:
    """One search result as search.js reads it."""
    return {
        "id": short.pk,
        "title": short.title,
        "tags": [tag.label for tag in short.tags.all()],
        "length": format_duration(short.duration_seconds),
        "src": reverse("shorts:clip", args=[short.pk]),
        # Opens the feed on this short, with only this search's results.
        "url": reverse("shorts:feed")
        + "?"
        + urlencode({"q": q, "start": short.pk}),
    }


@signed_in_page
@require_GET
def search_api(request: HttpRequest) -> JsonResponse:
    """Search results for ?q=: ``#tag`` exactly, or titles, closest first."""
    q = request.GET.get("q", "").strip()
    query = parse(q)
    found = search(query)
    return JsonResponse(
        {
            "kind": query.kind,
            "tags": list(query.tags),
            "items": [search_item(short, q) for short in found[:MAX_RESULTS]],
            "total": len(found),
        }
    )


@signed_in_page
@require_GET
def clip(request: HttpRequest, pk: int) -> HttpResponseBase:
    """Play one clip: the access check, then the bytes.

    With MinIO this answers with a redirect to a signed link, and the video
    then streams from MinIO directly. Locally it streams the file itself.
    """
    short = get_object_or_404(
        Short.objects.filter(archived_at__isnull=True), pk=pk
    )
    if settings.SHORTS_STORAGE != "minio":
        return ranged_clip_response(request, short)
    response = redirect(signed_clip_url(short, request))
    # The signed link expires, so browsers must ask again, not reuse it.
    response["Cache-Control"] = "private, no-store"
    return response


def staff_only(request: HttpRequest, action: str) -> HttpResponse | None:
    """None if the user may post and manage shorts, else where to go."""
    if can_create_shorts(request.user):
        return None
    messages.error(
        request, f"Only lecturers and the Department can {action} shorts."
    )
    return redirect("shorts:feed")


def render_short_form(
    request: HttpRequest, form: ShortForm, *, short: Short | None
) -> HttpResponse:
    """Create Short and the edit page share one template; None is create."""
    return render(
        request,
        "shorts/create.html",
        {
            "form": form,
            "short": short,
            "length": format_duration(short.duration_seconds) if short else "",
            "max_seconds": settings.SHORTS_MAX_SECONDS,
            "max_bytes": settings.SHORTS_MAX_UPLOAD_BYTES,
        },
        status=400 if form.is_bound else 200,
    )


@signed_in_page
@require_http_methods(["GET", "POST"])
def create(request: HttpRequest) -> HttpResponse:
    """Create Short: upload a clip with its title, description and tags."""
    if (refused := staff_only(request, "create")) is not None:
        return refused
    # "short_" ids: annform.css styles #id_tags as the announcement
    # checkbox list.
    form = ShortForm(
        request.POST or None, request.FILES or None, auto_id="short_%s"
    )
    if request.method == "POST" and form.is_valid():
        short = create_short(
            author=_account(request),
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
            tags=form.cleaned_data["tags"],
            clip=form.cleaned_data["clip"],
            duration_seconds=form.clip_duration or 0.0,
        )
        messages.success(request, f"“{short.title}” was uploaded.")
        return redirect("shorts:feed")
    return render_short_form(request, form, short=None)


# --- Edit Short: managing uploaded shorts ----------------------------------


@signed_in_page
@require_GET
def manage(request: HttpRequest) -> HttpResponse:
    """Edit Short: the list of shorts this user may change."""
    if (refused := staff_only(request, "edit")) is not None:
        return refused
    user = _account(request)
    rows = (
        manageable_shorts(user)
        .annotate(
            like_count=Count("likes", distinct=True),
            comment_count=Count("comments", distinct=True),
        )
        .prefetch_related("tags")
        .order_by("-created_at", "-pk")  # counting drops Meta.ordering
    )
    page = Paginator(rows, MANAGE_PER_PAGE).get_page(request.GET.get("page"))
    for short in page:
        setattr(short, "length", format_duration(short.duration_seconds))
    return render(
        request,
        "shorts/manage.html",
        {
            "page_obj": page,
            "is_department": user.is_superuser,
            "can_create": True,
        },
    )


@signed_in_page
@require_http_methods(["GET", "POST"])
def edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Change a short's details, or replace its clip."""
    if (refused := staff_only(request, "edit")) is not None:
        return refused
    # Someone else's short is a 404, not a 403: it does not say it exists.
    short = get_object_or_404(manageable_shorts(_account(request)), pk=pk)
    form = ShortForm(
        request.POST or None,
        request.FILES or None,
        auto_id="short_%s",
        editing=True,
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


@signed_in_page
@require_http_methods(["GET", "POST"])
def delete(request: HttpRequest, pk: int) -> HttpResponse:
    """GET asks to confirm; POST deletes the short, clip, likes, comments."""
    if (refused := staff_only(request, "edit")) is not None:
        return refused
    short = get_object_or_404(manageable_shorts(_account(request)), pk=pk)
    if request.method == "POST":
        title = short.title
        short.delete()  # signals.py removes the clip from storage
        messages.success(request, f"“{title}” was deleted.")
        return redirect("shorts:manage")
    return render(
        request,
        "shorts/delete.html",
        {
            "short": short,
            "length": format_duration(short.duration_seconds),
            "like_count": short.likes.count(),
            "comment_count": short.comments.count(),
            "can_create": True,
        },
    )
