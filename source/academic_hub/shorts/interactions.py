"""Likes and comments on shorts: the JSON endpoints reels.js calls.

Anyone signed in may like and comment. A comment can be deleted by whoever
wrote it, or by the Department, which moderates the whole site.

These answer in JSON even when something is wrong (401, 400, 404), because
the caller is a script, not a person who could follow a redirect to the
sign-in page.
"""

import json
from functools import wraps

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .models import Short, ShortComment, ShortLike

COMMENT_MAX = 500
COMMENTS_PER_PAGE = 20


def api_login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            return JsonResponse({"error": "Sign in to do that."}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


def error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def read_body(request):
    """A JSON object, or the form fields; None if the JSON is malformed."""
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None
    return request.POST


def live_short(pk):
    return get_object_or_404(Short.objects.filter(archived_at__isnull=True), pk=pk)


def role_label(user):
    if user.is_superuser:
        return "Department"
    if user.is_staff:
        return "Lecturer"
    return "Student"


def can_delete(user, comment):
    return comment.author_id == user.pk or user.is_superuser


def comment_data(comment, user):
    return {
        "id": comment.pk,
        "author": comment.author_label,
        "role": comment.author_role_label,
        "body": comment.body,
        "created_at": comment.created_at.isoformat(),
        "is_mine": comment.author_id == user.pk,
        "delete_url": (
            reverse("shorts:api_comment_delete", args=[comment.short_id, comment.pk])
            if can_delete(user, comment) else None
        ),
    }


@api_login_required
@require_POST
def like(request, pk):
    """Set (not toggle) whether this user likes the short.

    {"liked": true} twice still means one like, so a double tap or a retried
    request can never count twice or undo itself.
    """
    short = live_short(pk)
    data = read_body(request)
    if data is None:
        return error("Send a JSON object.")
    liked = data.get("liked")
    if isinstance(liked, str) and liked in {"true", "false"}:  # a form post
        liked = liked == "true"
    if not isinstance(liked, bool):
        return error('"liked" must be true or false.')

    if liked:
        ShortLike.objects.get_or_create(short=short, user=request.user)
    else:
        ShortLike.objects.filter(short=short, user=request.user).delete()
    return JsonResponse({"liked": liked, "like_count": short.likes.count()})


@api_login_required
@require_http_methods(["GET", "POST"])
def comments(request, pk):
    short = live_short(pk)
    if request.method == "POST":
        return create_comment(request, short)

    # Newest first, 20 at a time. ?before=<id> continues below that comment;
    # ids only grow, so new comments arriving meanwhile never shift a page.
    rows = short.comments.all()
    before = request.GET.get("before")
    if before:
        if not before.isdigit():
            return error('"before" must be a comment id.')
        rows = rows.filter(pk__lt=int(before))
    page = list(rows[:COMMENTS_PER_PAGE + 1])
    more = len(page) > COMMENTS_PER_PAGE
    page = page[:COMMENTS_PER_PAGE]
    return JsonResponse({
        "items": [comment_data(c, request.user) for c in page],
        "next_before": page[-1].pk if more else None,
        "comment_count": short.comments.count(),
    })


def create_comment(request, short):
    data = read_body(request)
    if data is None:
        return error("Send a JSON object.")
    body = data.get("body")
    body = body.strip() if isinstance(body, str) else ""
    if not body:
        return error("Write something first.")
    if len(body) > COMMENT_MAX:
        return error(f"Keep comments under {COMMENT_MAX} characters.")

    comment = ShortComment.objects.create(
        short=short,
        author=request.user,
        author_label=request.user.get_full_name() or request.user.pk,
        author_role_label=role_label(request.user),
        body=body,
    )
    return JsonResponse({
        "comment": comment_data(comment, request.user),
        "comment_count": short.comments.count(),
    }, status=201)


@api_login_required
@require_POST
def delete_comment(request, pk, comment_id):
    short = live_short(pk)
    with transaction.atomic():
        comment = get_object_or_404(
            ShortComment.objects.select_for_update(), pk=comment_id, short=short)
        if not can_delete(request.user, comment):
            return error("You can only delete your own comments.", status=403)
        comment.delete()
    return JsonResponse({"deleted": True, "comment_count": short.comments.count()})
