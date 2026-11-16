from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, Q
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_date
from accounts.middleware import is_guest_request
from ..models import Announcement, Faq, ForumQuestion, Tag
from .announcements import can_manage_announcements


def _search(queryset, query):
    """Rank against the stored tsvector.

    `websearch` handles quoted phrases and OR the way people expect. Ranking
    uses the stored weights, so a title match outranks a body match.
    """
    q = SearchQuery(query, search_type="websearch")
    return (
        queryset.filter(search_vector=q)
        .annotate(rank=SearchRank(F("search_vector"), q))
        .order_by("-rank")
    )


def reader_has_programme(user):
    """True when this reader belongs to a programme that has its own tag.

    The Department is excluded: it manages every programme, so it keeps
    the general filter. Matched case-insensitively, because
    userDepartment holds both "SKE" and "ske".
    """
    if not user.is_authenticated or user.is_superuser:
        return False
    return Tag.objects.filter(
        kind=Tag.Kind.PROGRAMME, slug__iexact=(user.department or "").strip()
    ).exists()


def _parse_filter_date(value):
    try:
        return parse_date(value)
    except ValueError:
        return None


def public_board(request):
    """The board. Signed-in users and guests only; anonymous goes to sign in."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("accounts:login"))

    query = request.GET.get("q", "").strip()
    active_tag = request.GET.get("tag", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    can_manage = can_manage_announcements(request.user)
    is_guest = is_guest_request(request)

    visible_announcements = Announcement.objects.visible_to(request.user, is_guest)
    announcements = visible_announcements.filter(archived_at__isnull=True)
    if can_manage and not request.user.is_superuser:
        announcements = (
            announcements | Announcement.objects.filter(
                author=request.user, archived_at__isnull=True
            )
        ).distinct()
    announcements = announcements.prefetch_related("tags")

    archived_announcements = Announcement.objects.none()
    if can_manage:
        archived_qs = Announcement.objects.filter(archived_at__isnull=False).prefetch_related("tags")
        if not request.user.is_superuser:
            archived_qs = archived_qs.filter(author=request.user)
        archived_announcements = archived_qs.order_by("-archived_at")

    faqs = Faq.objects.visible_to(request.user, is_guest).prefetch_related("tags")
    pinned_forum_questions = (
        ForumQuestion.objects.visible_to(request.user, is_guest)
        .filter(
            is_pinned=True,
            is_published=True,
            archived_at__isnull=True,
        )
        .select_related("category")
        .prefetch_related("departments")
        .order_by("-created_at", "-pk")
    )

    if active_tag:
        announcements = announcements.filter(tags__slug=active_tag)
        faqs = faqs.filter(tags__slug=active_tag)
        pinned_forum_questions = pinned_forum_questions.filter(
            departments__slug=active_tag
        ).distinct()

    parsed_from = _parse_filter_date(date_from)
    parsed_to = _parse_filter_date(date_to)
    if parsed_from:
        announcements = announcements.filter(published_at__date__gte=parsed_from)
        pinned_forum_questions = pinned_forum_questions.filter(
            created_at__date__gte=parsed_from
        )
    if parsed_to:
        announcements = announcements.filter(published_at__date__lte=parsed_to)
        pinned_forum_questions = pinned_forum_questions.filter(
            created_at__date__lte=parsed_to
        )

    if query:
        announcements = _search(announcements, query)
        faqs = _search(faqs, query)
        pinned_forum_questions = pinned_forum_questions.filter(
            Q(title__icontains=query) | Q(body__icontains=query)
        )
    else:
        announcements = announcements.pinned_first()

    chips = (
        Tag.objects.filter(announcements__in=visible_announcements)
        .distinct()
        .order_by("kind", "label")
    )
    if not is_guest and reader_has_programme(request.user):
        chips = chips.exclude(kind=Tag.Kind.DEPARTMENT)

    suggestions = (
        [t.label for t in chips]
        + list(announcements.values_list("title", flat=True))
        + list(faqs.values_list("question", flat=True))
        + list(pinned_forum_questions.values_list("title", flat=True))
    )

    return render(request, "announcements/public_board.html", {
        "announcements": announcements,
        "archived_announcements": archived_announcements,
        "faqs": faqs,
        "pinned_forum_questions": pinned_forum_questions,
        "chips": chips,
        "active_tag": active_tag,
        "date_from": date_from,
        "date_to": date_to,
        "date_filtered": bool(parsed_from or parsed_to),
        "query": query,
        "suggestions": suggestions,
        "is_searching": bool(query),
        "result_count": (
            len(announcements) + len(faqs) + len(pinned_forum_questions)
        ),
        "can_manage": can_manage,
        "is_guest": is_guest,
        "guest_expires_at": (
            request.session.get_expiry_date().isoformat() if is_guest else ""
        ),
    })
