"""Public information layer (US-02, US-03, US-09).

Every read goes through `visible_to()` so the board and search cannot
disagree about what a reader may see.
"""

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import transaction
from django.db.models import F, Prefetch
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.middleware import is_guest_request
from .forms import AnnouncementForm, ForumQuestionForm, ForumReplyForm
from .models import (
    Announcement,
    Faq,
    ForumQuestion,
    ForumReply,
    Tag,
)


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

    # One rule for both models and for search below, so no read path can
    # disagree with another about what this reader may see.
    announcements = (
        Announcement.objects.visible_to(request.user, is_guest)
        .prefetch_related("tags")
    )
    # A lecturer also keeps sight of their own drafts, which visible_to()
    # filters out for everyone but the department.
    if can_manage and not request.user.is_superuser:
        announcements = (
            announcements | Announcement.objects.filter(author=request.user)
        ).distinct().prefetch_related("tags")

    faqs = Faq.objects.visible_to(request.user, is_guest).prefetch_related("tags")

    if active_tag:
        announcements = announcements.filter(tags__slug=active_tag)
        faqs = faqs.filter(tags__slug=active_tag)

    # `__date` so a single day includes items posted during it. A malformed
    # value parses to None and narrows nothing.
    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_from:
        announcements = announcements.filter(published_at__date__gte=parsed_from)
    if parsed_to:
        announcements = announcements.filter(published_at__date__lte=parsed_to)

    if query:
        announcements = _search(announcements, query)
        faqs = _search(faqs, query)

    # Chips come from tags that actually appear on announcements this reader
    # can see, so the row can never offer a filter that returns nothing.
    chips = (
        Tag.objects.filter(announcements__in=Announcement.objects.visible_to(
            request.user, is_guest))
        .distinct()
        .order_by("kind", "label")
    )
    # SKE and CPE are departments themselves, so the generic #Department
    # chip is noise once a reader has a programme of their own. The posts
    # stay on the board - registration and scholarship notices still reach
    # students - only the filter button goes. A guest keeps it, having no
    # programme, and so does the Department, which manages the board.
    if not is_guest and reader_has_programme(request.user):
        chips = chips.exclude(kind=Tag.Kind.DEPARTMENT)

    # Rendered into a <datalist>. Fine at this size; needs a lookup if the
    # board ever holds thousands of items.
    suggestions = (
        [t.label for t in chips]
        + list(announcements.values_list("title", flat=True))
        + list(faqs.values_list("question", flat=True))
    )

    return render(request, "announcements/public_board.html", {
        "announcements": announcements,
        "faqs": faqs,
        "chips": chips,
        "active_tag": active_tag,
        "date_from": date_from,
        "date_to": date_to,
        "date_filtered": bool(parsed_from or parsed_to),
        "query": query,
        "suggestions": suggestions,
        "is_searching": bool(query),
        "result_count": len(announcements) + len(faqs),
        "can_manage": can_manage,
        "is_guest": is_guest,
        # ISO timestamp for the countdown in the bar. A guest should be able
        # to see how long is left rather than be dropped without warning.
        "guest_expires_at": (
            request.session.get_expiry_date().isoformat() if is_guest else ""
        ),
    })


# --- staff: create and edit announcements -------------------------------
# The last Iteration 2 task. Without it the boards can only ever show seeded
# content, because nobody has a way to publish.

def can_manage_announcements(user):
    """Lecturers and the Department may publish. Students and guests may not."""
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def _refresh_search_vector(announcement):
    """Rebuild this row's tsvector. An UPDATE, because SearchVector runs
    database-side against the stored row."""
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

    # Lab tags are Department-only, and the form cannot offer one it is not
    # allowed to keep. Editing here would silently drop the tag, so a lecturer
    # is stopped instead.
    if (
        instance is not None
        and not request.user.is_superuser
        and instance.tags.filter(kind=Tag.Kind.LAB).exists()
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


FORUM_PAGE_SIZE = 20


def _forum_role(user):
    if user.is_superuser:
        return "department"
    if user.is_staff:
        return "lecturer"
    return "student"


def _forum_role_label(user):
    return _forum_role(user).capitalize()


def _render_faq_board(
    request,
    *,
    selected_question=None,
    question_form=None,
    question_edit=False,
    reply_form=None,
    reply_edit_form=None,
    editing_reply=None,
):
    is_guest = is_guest_request(request)
    role = _forum_role(request.user)
    reply_queryset = ForumReply.objects.select_related("author")
    if role != "department":
        reply_queryset = reply_queryset.filter(archived_at__isnull=True)
    questions = (
        ForumQuestion.objects.visible_to(request.user, is_guest)
        .select_related("author")
        .prefetch_related(
            "departments",
            Prefetch("replies", queryset=reply_queryset, to_attr="visible_replies"),
        )
    )

    if selected_question is None and "q" in request.GET:
        try:
            question_id = int(request.GET["q"])
        except (TypeError, ValueError):
            raise Http404
        selected_question = get_object_or_404(questions, pk=question_id)

    page_obj = Paginator(questions, FORUM_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    can_reply = (
        role in {"lecturer", "department"}
        and not is_guest
        and request.user.is_active
    )
    if (
        can_reply
        and reply_edit_form is None
        and reply_form is None
    ):
        reply_form = ForumReplyForm()

    department_unavailable = False
    if role == "student":
        department_unavailable = not Tag.objects.filter(
            kind=Tag.Kind.PROGRAMME,
            slug__iexact=(request.user.department or "").strip(),
        ).exists()

    return render(request, "announcements/faq_board.html", {
        "is_guest": is_guest,
        "role": role,
        "threads": page_obj.object_list,
        "page_obj": page_obj,
        "selected_question": selected_question,
        "question_form": question_form,
        "question_edit": question_edit,
        "reply_form": reply_form,
        "reply_edit_form": reply_edit_form,
        "editing_reply": editing_reply,
        "comments_open": (
            bool(request.GET.get("comments"))
            or bool(reply_form and reply_form.errors)
            or reply_edit_form is not None
        ),
        "reply_form_error_question": (
            selected_question.pk
            if selected_question is not None
            and reply_form is not None
            and reply_form.errors
            else None
        ),
        "department_unavailable": department_unavailable,
        "can_create_question": not is_guest and request.user.is_active,
        "can_reply": can_reply,
        "guest_expires_at": (
            request.session.get_expiry_date().isoformat() if is_guest else ""
        ),
    })


def _forum_author_label(user):
    return user.get_full_name().strip() or "Unknown author"


def _forum_forbidden(request):
    return render(request, "accounts/no_access.html", status=403)


def _forum_mutation_denied(request):
    return is_guest_request(request) or not request.user.is_active


def _save_forum_question(form, request, instance=None):
    question = form.save(commit=False)
    if instance is None:
        question.author = request.user
        question.author_label = _forum_author_label(request.user)
        question.author_role = _forum_role_label(request.user)
    else:
        question.updated_by = request.user
        question.updated_by_label = _forum_author_label(request.user)
        question.updated_by_is_author = question.author_id == request.user.pk
    if not request.user.is_superuser:
        question.is_published = True
    with transaction.atomic():
        question.save()
        question.departments.set(form.selected_departments())
    return question


@login_required(login_url="accounts:login")
def faq_board(request):
    return _render_faq_board(request)


@login_required(login_url="accounts:login")
@require_http_methods(["GET", "POST"])
def forum_question_create(request):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)

    form = ForumQuestionForm(
        request.POST if request.method == "POST" else None,
        author=request.user,
    )
    if request.method == "POST" and form.is_valid():
        question = _save_forum_question(form, request)
        return redirect(
            f"{reverse('announcements:faq_board')}?q={question.pk}"
        )

    return _render_faq_board(request, question_form=form)


@login_required(login_url="accounts:login")
@require_http_methods(["GET", "POST"])
def forum_question_edit(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)

    role = _forum_role(request.user)
    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user),
        pk=pk,
    )
    if role == "student" and question.author_id != request.user.pk:
        return _forum_forbidden(request)

    form = ForumQuestionForm(
        request.POST if request.method == "POST" else None,
        author=request.user,
        instance=question,
    )
    if request.method == "POST" and form.is_valid():
        question = _save_forum_question(form, request, instance=question)
        return redirect(
            f"{reverse('announcements:faq_board')}?q={question.pk}"
        )

    return _render_faq_board(
        request,
        selected_question=question,
        question_form=form,
        question_edit=True,
    )


@login_required(login_url="accounts:login")
@require_POST
def forum_question_archive(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)

    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user),
        pk=pk,
    )
    if (
        not request.user.is_superuser
        and (
            request.user.is_staff
            or question.author_id != request.user.pk
        )
    ):
        return _forum_forbidden(request)

    question.archived_at = timezone.now()
    question.save(update_fields=["archived_at", "updated_at"])
    return redirect("announcements:faq_board")


@login_required(login_url="accounts:login")
@require_POST
def forum_question_restore(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)
    if not request.user.is_superuser:
        return _forum_forbidden(request)

    question = get_object_or_404(ForumQuestion, pk=pk)
    question.archived_at = None
    question.save(update_fields=["archived_at", "updated_at"])
    return redirect(
        f"{reverse('announcements:faq_board')}?q={question.pk}"
    )


@login_required(login_url="accounts:login")
@require_POST
def forum_reply_create(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)
    if not (request.user.is_staff or request.user.is_superuser):
        return _forum_forbidden(request)

    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user),
        pk=pk,
    )
    form = ForumReplyForm(request.POST)
    if form.is_valid():
        reply = form.save(commit=False)
        reply.question = question
        reply.author = request.user
        reply.author_label = _forum_author_label(request.user)
        reply.author_role = _forum_role_label(request.user)
        reply.save()
        return redirect(
            f"{reverse('announcements:faq_board')}?q={question.pk}&comments=1#forum-comments-{question.pk}"
        )

    return _render_faq_board(
        request,
        selected_question=question,
        reply_form=form,
    )


@login_required(login_url="accounts:login")
@require_http_methods(["GET", "POST"])
def forum_reply_edit(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)

    reply = get_object_or_404(
        ForumReply.objects.select_related("question", "author"),
        pk=pk,
    )
    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user),
        pk=reply.question_id,
    )
    if not request.user.is_superuser and (
        not request.user.is_staff or reply.author_id != request.user.pk
    ):
        return _forum_forbidden(request)

    form = ForumReplyForm(
        request.POST if request.method == "POST" else None,
        instance=reply,
    )
    if request.method == "POST" and form.is_valid():
        reply = form.save(commit=False)
        reply.has_been_edited = True
        reply.save()
        return redirect(
            f"{reverse('announcements:faq_board')}?q={question.pk}&comments=1#forum-comments-{question.pk}"
        )

    return _render_faq_board(
        request,
        selected_question=question,
        reply_edit_form=form,
        editing_reply=reply,
    )


@login_required(login_url="accounts:login")
@require_POST
def forum_reply_archive(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)
    if not (request.user.is_staff or request.user.is_superuser):
        return _forum_forbidden(request)

    reply = get_object_or_404(
        ForumReply.objects.select_related("question"),
        pk=pk,
    )
    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user),
        pk=reply.question_id,
    )
    if not request.user.is_superuser and reply.author_id != request.user.pk:
        return _forum_forbidden(request)

    reply.archived_at = timezone.now()
    reply.save(update_fields=["archived_at", "updated_at"])
    return redirect(
        f"{reverse('announcements:faq_board')}?q={question.pk}&comments=1#forum-comments-{question.pk}"
    )


@login_required(login_url="accounts:login")
@require_POST
def forum_reply_restore(request, pk):
    if _forum_mutation_denied(request):
        return _forum_forbidden(request)
    if not request.user.is_superuser:
        return _forum_forbidden(request)

    reply = get_object_or_404(
        ForumReply.objects.select_related("question"),
        pk=pk,
    )
    reply.archived_at = None
    reply.save(update_fields=["archived_at", "updated_at"])
    return redirect(
        f"{reverse('announcements:faq_board')}?q={reply.question_id}&comments=1#forum-comments-{reply.question_id}"
    )
