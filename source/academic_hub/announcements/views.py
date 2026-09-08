"""Public information layer (US-02, US-03, US-09).

The board is readable without signing in. Everything reaching the template
comes through `for_guest()`, so the visibility rule lives in one place on the
queryset rather than being re-stated in each view - including in search, where
it would be easiest to leak something by forgetting it.
"""

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.middleware import is_guest_request
from .forms import AnnouncementForm
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

    can_manage = can_manage_announcements(request.user)

    # The board is the public surface, so it shows public items. A signed-in
    # publisher also sees their own drafts and course notices here, otherwise
    # they would have no way back to something they just wrote that guests
    # cannot see. Guests and students are unaffected: this branch needs an
    # authenticated publisher.
    if can_manage:
        mine = Announcement.objects.filter(author=request.user)
        if request.user.is_superuser:
            mine = Announcement.objects.all()
        announcements = (
            (Announcement.objects.for_guest() | mine).distinct()
            .prefetch_related("tags")
        )
    else:
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
        "can_manage": can_manage,
        "is_guest": is_guest_request(request),
    })


# --- staff: create and edit announcements -------------------------------
# The last Iteration 2 task. Without it the boards can only ever show seeded
# content, because nobody has a way to publish.

def can_manage_announcements(user):
    """Lecturers and the Department may publish. Students and guests may not."""
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def _refresh_search_vector(announcement):
    """Rebuild this row's tsvector so the item is findable straight away.

    Done as an UPDATE rather than in save() because SearchVector is database-
    side: it has to run against the stored row, after the write.
    """
    Announcement.objects.filter(pk=announcement.pk).update(
        search_vector=SearchVector("title", weight="A")
        + SearchVector("body", weight="B")
    )


@login_required(login_url="accounts:login")
def announcement_form(request, pk=None):
    """Create a new announcement, or edit one that exists."""
    if not can_manage_announcements(request.user):
        return render(request, "accounts/no_access.html", status=403)

    instance = get_object_or_404(Announcement, pk=pk) if pk else None

    # A lecturer may edit what they wrote; the Department may edit anything.
    if (
        instance is not None
        and not request.user.is_superuser
        and instance.author_id != request.user.pk
    ):
        return render(request, "accounts/no_access.html", status=403)

    if request.method == "POST":
        form = AnnouncementForm(request.POST, instance=instance,
                                author=request.user)
        if form.is_valid():
            announcement = form.save(commit=False)
            if instance is None:
                announcement.author = request.user
                announcement.author_label = (
                    "Department" if request.user.is_superuser else "Lecturer"
                )
            announcement.save()
            form.save_m2m()
            _refresh_search_vector(announcement)
            return redirect("announcements:public_board")
    else:
        form = AnnouncementForm(instance=instance, author=request.user)

    return render(request, "announcements/announcement_form.html", {
        "form": form,
        "instance": instance,
        "is_edit": instance is not None,
    })


@login_required(login_url="accounts:login")
@require_POST
def announcement_delete(request, pk):
    """Withdraw an announcement. POST only - deleting is not a GET."""
    if not can_manage_announcements(request.user):
        return render(request, "accounts/no_access.html", status=403)

    announcement = get_object_or_404(Announcement, pk=pk)
    if not request.user.is_superuser and announcement.author_id != request.user.pk:
        return render(request, "accounts/no_access.html", status=403)

    announcement.delete()
    return redirect("announcements:public_board")
