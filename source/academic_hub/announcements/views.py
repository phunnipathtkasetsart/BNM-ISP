"""Public information layer (US-02, US-03, US-09).

The board is readable without signing in. Everything reaching the template
comes through `for_guest()`, so the visibility rule lives in one place on the
queryset rather than being re-stated in each view - including in search, where
it would be easiest to leak something by forgetting it.
"""

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F
from django.shortcuts import render

from .models import Announcement, Faq, Tag


def _search(queryset, query):
    """Rank a queryset against the stored tsvector.

    `websearch` is the parser that behaves the way people expect from a search
    box: quoted phrases, OR, and a leading - to exclude. The alternatives
    either choke on punctuation (plainto) or demand operator syntax (raw).

    Ranking uses the weights set when the vector was built, so a title match
    outranks a body match rather than both scoring the same.
    """
    q = SearchQuery(query, search_type="websearch")
    return (
        queryset.filter(search_vector=q)
        .annotate(rank=SearchRank(F("search_vector"), q))
        .order_by("-rank")
    )


def public_board(request):
    """Announcements and FAQs a guest may read, optionally searched/filtered."""
    query = request.GET.get("q", "").strip()
    active_tag = request.GET.get("tag", "").strip()

    announcements = Announcement.objects.for_guest().prefetch_related("tags")
    faqs = Faq.objects.for_guest().prefetch_related("tags")

    if active_tag:
        announcements = announcements.filter(tags__slug=active_tag)
        faqs = faqs.filter(tags__slug=active_tag)

    if query:
        announcements = _search(announcements, query)
        faqs = _search(faqs, query)

    # Chips come from tags that actually appear on announcements a guest can
    # see, so the row can never offer a filter that returns nothing.
    chips = (
        Tag.objects.filter(announcements__in=Announcement.objects.for_guest())
        .distinct()
        .order_by("kind", "label")
    )

    # Suggestions are rendered into a <datalist>, so the browser does the
    # matching with no request and no JavaScript. Fine at this size; if the
    # board ever holds thousands of items this should become a lookup instead.
    suggestions = (
        [t.label for t in chips]
        + list(Announcement.objects.for_guest().values_list("title", flat=True))
        + list(Faq.objects.for_guest().values_list("question", flat=True))
    )

    return render(request, "announcements/public_board.html", {
        "announcements": announcements,
        "faqs": faqs,
        "chips": chips,
        "active_tag": active_tag,
        "query": query,
        "suggestions": suggestions,
        "is_searching": bool(query),
        "result_count": len(announcements) + len(faqs),
    })
