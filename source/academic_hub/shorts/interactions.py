"""Likes and comments on shorts: the JSON endpoints reels.js calls.

Anyone signed in may like and comment. A comment can be deleted by whoever
wrote it, or by the Department, which moderates the whole site.

Replies are one level deep, as on Reels: a reply hangs from a top-level
comment, and replying to a reply joins that same thread.

These answer in JSON even when something is wrong (401, 400, 404), because
the caller is a script, not a person who could follow a redirect to the
sign-in page.
"""

import json
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from django.db import transaction
from django.db.models import Count, QuerySet
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from accounts.models import User as Account

from .access import account_of
from .models import Short, ShortComment, ShortLike

COMMENT_MAX = 500
COMMENTS_PER_PAGE = 20

JsonView = Callable[..., JsonResponse]


class ApiError(Exception):
    """A request the API refuses; becomes {"error": message} JSON."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def json_api(view: JsonView) -> JsonView:
    """Require a signed-in user, and answer ApiError as JSON."""

    @wraps(view)
    def wrapped(
        request: HttpRequest, *args: Any, **kwargs: Any
    ) -> JsonResponse:
        user = account_of(request)
        if user is None or not user.is_active:
            return JsonResponse({"error": "Sign in to do that."}, status=401)
        try:
            return view(request, *args, **kwargs)
        except ApiError as error:
            return JsonResponse({"error": error.message}, status=error.status)

    return wrapped


def read_body(request: HttpRequest) -> Mapping[str, Any]:
    """A JSON object, or the form fields of a regular POST."""
    if request.content_type != "application/json":
        return request.POST
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError) as error:
        raise ApiError("Send a JSON object.") from error
    if not isinstance(data, dict):
        raise ApiError("Send a JSON object.")
    return data


def signed_in(request: HttpRequest) -> Account:
    """The signed-in account (json_api has already turned others away)."""
    user = account_of(request)
    if user is None:
        raise ApiError("Sign in to do that.", status=401)
    return user


def live_short(pk: int) -> Short:
    """The short, unless it does not exist or is archived (404)."""
    return get_object_or_404(
        Short.objects.filter(archived_at__isnull=True), pk=pk
    )


def role_label(user: Account) -> str:
    """Department, Lecturer or Student."""
    if user.is_superuser:
        return "Department"
    if user.is_staff:
        return "Lecturer"
    return "Student"


def can_delete(user: Account, comment: ShortComment) -> bool:
    """The author and the Department may delete a comment."""
    return comment.author_id == user.pk or user.is_superuser


def comment_data(comment: ShortComment, user: Account) -> dict[str, Any]:
    """One comment as reels.js reads it."""
    data: dict[str, Any] = {
        "id": comment.pk,
        "parent_id": comment.parent_id,
        "author": comment.author_label,
        "role": comment.author_role_label,
        "body": comment.body,
        "created_at": comment.created_at.isoformat(),
        "is_mine": comment.author_id == user.pk,
        "delete_url": (
            reverse(
                "shorts:api_comment_delete",
                args=[comment.short_id, comment.pk],
            )
            if can_delete(user, comment)
            else None
        ),
    }
    if comment.parent_id is None:
        data["reply_count"] = getattr(comment, "reply_count", 0)
        data["replies_url"] = reverse(
            "shorts:api_comment_replies", args=[comment.short_id, comment.pk]
        )
    return data


def _cursor(request: HttpRequest, name: str) -> int | None:
    """The comment id in ?before= or ?after=, if one was sent."""
    value = request.GET.get(name)
    if not value:
        return None
    if not value.isdigit():
        raise ApiError(f'"{name}" must be a comment id.')
    return int(value)


def _page(
    rows: "QuerySet[ShortComment]",
) -> tuple[list[ShortComment], int | None]:
    """The next COMMENTS_PER_PAGE rows and the cursor for the page after.

    One extra row is fetched to learn whether there is more, so no count
    query is needed. The cursor is the last row's id, or None at the end.
    """
    rows_plus_one = list(rows[: COMMENTS_PER_PAGE + 1])
    page = rows_plus_one[:COMMENTS_PER_PAGE]
    more = len(rows_plus_one) > COMMENTS_PER_PAGE
    return page, (page[-1].pk if more else None)


def _parse_liked(data: Mapping[str, Any]) -> bool:
    liked = data.get("liked")
    if liked in ("true", "false"):  # a form post sends text
        return str(liked) == "true"
    if not isinstance(liked, bool):
        raise ApiError('"liked" must be true or false.')
    return liked


@json_api
@require_POST
def like(request: HttpRequest, pk: int) -> JsonResponse:
    """Set (not toggle) whether this user likes the short.

    {"liked": true} twice still means one like, so a double tap or a retried
    request can never count twice or undo itself.
    """
    user = signed_in(request)
    short = live_short(pk)
    liked = _parse_liked(read_body(request))
    if liked:
        ShortLike.objects.get_or_create(short=short, user=user)
    else:
        ShortLike.objects.filter(short=short, user=user).delete()
    return JsonResponse({"liked": liked, "like_count": short.likes.count()})


@json_api
@require_http_methods(["GET", "POST"])
def comments(request: HttpRequest, pk: int) -> JsonResponse:
    """GET: top-level comments, newest first. POST: add one."""
    short = live_short(pk)
    if request.method == "POST":
        return create_comment(request, short)
    # 20 at a time; ?before=<id> continues below that comment. Ids only
    # grow, so new comments arriving meanwhile never shift a page. Replies
    # come separately, from comment_replies.
    rows = (
        short.comments.filter(parent__isnull=True)
        .annotate(reply_count=Count("replies"))
        .order_by("-created_at", "-pk")  # counting drops Meta.ordering
    )
    before = _cursor(request, "before")
    if before is not None:
        rows = rows.filter(pk__lt=before)
    page, next_before = _page(rows)
    return JsonResponse(
        {
            "items": [comment_data(c, signed_in(request)) for c in page],
            "next_before": next_before,
            "comment_count": short.comments.count(),
        }
    )


def _comment_body(data: Mapping[str, Any]) -> str:
    body = data.get("body")
    body = body.strip() if isinstance(body, str) else ""
    if not body:
        raise ApiError("Write something first.")
    if len(body) > COMMENT_MAX:
        raise ApiError(f"Keep comments under {COMMENT_MAX} characters.")
    return body


def _reply_parent(
    data: Mapping[str, Any], short: Short
) -> ShortComment | None:
    """The top-level comment a reply joins, or None for a new comment."""
    reply_to = data.get("reply_to")
    if reply_to in (None, ""):
        return None
    if isinstance(reply_to, str) and reply_to.isdigit():  # a form post
        reply_to = int(reply_to)
    if not isinstance(reply_to, int) or isinstance(reply_to, bool):
        raise ApiError('"reply_to" must be a comment id.')
    parent = ShortComment.objects.filter(pk=reply_to, short=short).first()
    if parent is None:
        raise ApiError("That comment has been deleted.", status=404)
    # Replying to a reply joins its thread: one level, as on Reels.
    return parent.parent if parent.parent_id is not None else parent


def create_comment(request: HttpRequest, short: Short) -> JsonResponse:
    """Add a comment or reply from the POSTed body and reply_to."""
    data = read_body(request)
    body = _comment_body(data)
    parent = _reply_parent(data, short)
    user = signed_in(request)
    comment = ShortComment.objects.create(
        short=short,
        parent=parent,
        author=user,
        author_label=user.get_full_name() or user.pk,
        author_role_label=role_label(user),
        body=body,
    )
    response = {
        "comment": comment_data(comment, user),
        "comment_count": short.comments.count(),
    }
    if parent is not None:
        response["reply_count"] = parent.replies.count()
    return JsonResponse(response, status=201)


@json_api
@require_GET
def comment_replies(
    request: HttpRequest, pk: int, comment_id: int
) -> JsonResponse:
    """One thread's replies, oldest first, 20 at a time (?after=<id>)."""
    short = live_short(pk)
    parent = get_object_or_404(
        ShortComment, pk=comment_id, short=short, parent__isnull=True
    )
    rows = parent.replies.order_by("created_at", "pk")
    after = _cursor(request, "after")
    if after is not None:
        rows = rows.filter(pk__gt=after)
    page, next_after = _page(rows)
    return JsonResponse(
        {
            "items": [comment_data(c, signed_in(request)) for c in page],
            "next_after": next_after,
            "reply_count": parent.replies.count(),
        }
    )


@json_api
@require_POST
def delete_comment(
    request: HttpRequest, pk: int, comment_id: int
) -> JsonResponse:
    """Delete a comment; a top-level one takes its replies with it."""
    short = live_short(pk)
    with transaction.atomic():
        comment = get_object_or_404(
            ShortComment.objects.select_for_update(),
            pk=comment_id,
            short=short,
        )
        if not can_delete(signed_in(request), comment):
            raise ApiError("You can only delete your own comments.", 403)
        parent = comment.parent
        comment.delete()
    response = {"deleted": True, "comment_count": short.comments.count()}
    if parent is not None:
        response["reply_count"] = parent.replies.count()
    return JsonResponse(response)
