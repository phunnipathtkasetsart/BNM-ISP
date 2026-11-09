"""Public information layer (US-02, US-03, US-09).

Every read goes through `visible_to()` so the board and search cannot
disagree about what a reader may see.
"""

from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.middleware import is_guest_request
from notifications.services import notify_announcement, notify_forum_reply
from .forms import (
    AnnouncementForm,
    ForumCategoryForm,
    ForumQuestionFilterForm,
    ForumQuestionForm,
    ForumReplyForm,
)
from .models import (
    Announcement,
    Faq,
    ForumCategory,
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

# Update to handle archive 
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

    # 1. Active announcements MUST exclude archived items for ALL users
    announcements = (
        Announcement.objects.visible_to(request.user, is_guest)
        .filter(archived_at__isnull=True)
        .prefetch_related("tags")
    )
    if can_manage and not request.user.is_superuser:
        announcements = (
            announcements | Announcement.objects.filter(author=request.user, archived_at__isnull=True)
        ).distinct().prefetch_related("tags")

    # 2. Archived announcements queryset (Only for staff / department admins)
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

    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
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
        Tag.objects.filter(announcements__in=Announcement.objects.visible_to(
            request.user, is_guest))
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

# Archive features 
@login_required(login_url="accounts:login")
@require_POST
def announcement_archive(request, pk):
    """Archive an announcement (Phase 1 soft delete)."""
    if not can_manage_announcements(request.user):
        return render(request, "accounts/no_access.html", status=403)

    announcement = get_object_or_404(Announcement, pk=pk)
    if not request.user.is_superuser and announcement.author_id != request.user.pk:
        return render(request, "accounts/no_access.html", status=403)

    announcement.archived_at = timezone.now()
    announcement.archived_by = request.user
    announcement.save(update_fields=["archived_at", "archived_by", "updated_at"])
    return redirect("announcements:public_board")


@login_required(login_url="accounts:login")
@require_POST
def announcement_restore(request, pk):
    """Restore an archived announcement back to active status."""
    if not can_manage_announcements(request.user):
        return render(request, "accounts/no_access.html", status=403)

    announcement = get_object_or_404(Announcement, pk=pk)
    if not request.user.is_superuser and announcement.author_id != request.user.pk:
        return render(request, "accounts/no_access.html", status=403)

    announcement.archived_at = None
    announcement.archived_by = None
    announcement.save(update_fields=["archived_at", "archived_by", "updated_at"])
    return redirect("announcements:public_board")


@login_required(login_url="accounts:login")
@require_POST
def announcement_delete(request, pk):
    """Permanently delete an announcement (Phase 2 hard delete)."""
    if not can_manage_announcements(request.user):
        return render(request, "accounts/no_access.html", status=403)

    announcement = get_object_or_404(Announcement, pk=pk)
    if not request.user.is_superuser and announcement.author_id != request.user.pk:
        return render(request, "accounts/no_access.html", status=403)

    announcement.delete()
    return redirect("announcements:public_board")

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
            if announcement.is_published:
                notify_announcement(announcement)
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
    detail_page=False,
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
        .select_related("author", "category")
        .prefetch_related(
            "departments",
            Prefetch("replies", queryset=reply_queryset, to_attr="visible_replies"),
        )
    )
    questions = questions.annotate(
        comment_count=Count(
            "replies",
            filter=Q(replies__archived_at__isnull=True)
            if role != "department"
            else Q(),
            distinct=True,
        )
    )

    filter_data = request.GET.copy()
    filter_data.setdefault(
        "date_direction",
        ForumQuestionFilterForm.DateDirection.BEFORE,
    )
    filter_form = ForumQuestionFilterForm(filter_data)
    if filter_form.is_valid():
        filters = filter_form.cleaned_data
        query = filters["search"].strip()
        if query:
            questions = questions.filter(
                Q(title__icontains=query) | Q(body__icontains=query)
            )
        if filters["audience"] in {
            ForumQuestionFilterForm.Audience.SKE,
            ForumQuestionFilterForm.Audience.CPE,
        }:
            questions = questions.filter(
                visibility=ForumQuestion.Visibility.DEPARTMENT,
                departments__kind=Tag.Kind.PROGRAMME,
                departments__slug__iexact=filters["audience"],
            ).distinct()
        if filters["author"]:
            questions = questions.filter(author_role__iexact=filters["author"])
        if filters["category"]:
            questions = questions.filter(category=filters["category"])
        selected_date = filters.get("date")
        date_direction = filters.get("date_direction")
        if selected_date and date_direction == "before":
            questions = questions.filter(created_at__date__lt=selected_date)
        elif selected_date and date_direction == "after":
            questions = questions.filter(created_at__date__gt=selected_date)

        sort_order = filters["sort"]
    else:
        sort_order = ForumQuestionFilterForm.SortOrder.NEWEST

    if sort_order == ForumQuestionFilterForm.SortOrder.OLDEST:
        questions = questions.order_by("created_at", "pk")
    elif sort_order == ForumQuestionFilterForm.SortOrder.MOST_COMMENTS:
        questions = questions.order_by("-comment_count", "-created_at", "-pk")
    elif sort_order == ForumQuestionFilterForm.SortOrder.FEWEST_COMMENTS:
        questions = questions.order_by("comment_count", "-created_at", "-pk")
    else:
        questions = questions.order_by("-created_at", "-pk")

    if selected_question is not None:
        selected_question = get_object_or_404(questions, pk=selected_question.pk)
    elif "q" in request.GET:
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
    active_filter_params = request.GET.copy()
    active_filter_params.pop("page", None)

    template = (
        "announcements/forum_question_detail.html"
        if detail_page
        else "announcements/faq_board.html"
    )
    context = {
        "is_guest": is_guest,
        "role": role,
        "threads": page_obj.object_list,
        "page_obj": page_obj,
        "filter_form": filter_form,
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
        "can_manage_forum_content": (
            not is_guest
            and request.user.is_active
            and role in {"department", "lecturer"}
        ),
        "can_reply": can_reply,
        "active_filters": active_filter_params.urlencode(),
        "guest_expires_at": (
            request.session.get_expiry_date().isoformat() if is_guest else ""
        ),
    }
    if (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        and not detail_page
        and question_form is None
    ):
        return render(
            request,
            "announcements/_forum_question_results.html",
            context,
        )
    return render(request, template, context)


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
def forum_category_create(request):
    if _forum_mutation_denied(request) or _forum_role(request.user) not in {
        "department",
        "lecturer",
    }:
        return _forum_forbidden(request)

    return_to = request.POST.get("next") or request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(
        return_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return_to = reverse("announcements:forum_question_create")

    form = ForumCategoryForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        category.created_by = request.user
        category.save()
        return redirect(return_to)
    return render(
        request,
        "announcements/forum_category_form.html",
        {
            "form": form,
            "is_guest": is_guest_request(request),
            "return_to": return_to,
        },
    )


@login_required(login_url="accounts:login")
def forum_question_detail(request, pk):
    is_guest = is_guest_request(request)
    role = _forum_role(request.user)
    reply_queryset = ForumReply.objects.select_related("author")
    if role != "department":
        reply_queryset = reply_queryset.filter(archived_at__isnull=True)
    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user, is_guest)
        .select_related("author")
        .prefetch_related(
            "departments",
            Prefetch("replies", queryset=reply_queryset, to_attr="visible_replies"),
        ),
        pk=pk,
    )
    return _render_faq_board(
        request,
        selected_question=question,
        detail_page=True,
    )


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
        return redirect("announcements:forum_question_detail", pk=question.pk)

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
        return redirect("announcements:forum_question_detail", pk=question.pk)

    return _render_faq_board(
        request,
        selected_question=question,
        detail_page=True,
        question_form=form,
        question_edit=True,
    )


@login_required(login_url="accounts:login")
@require_POST
def forum_question_pin(request, pk):
    if _forum_mutation_denied(request) or not request.user.is_superuser:
        return _forum_forbidden(request)

    question = get_object_or_404(ForumQuestion, pk=pk)
    question.is_pinned = not question.is_pinned
    question.save(update_fields=["is_pinned"])
    return redirect("announcements:forum_question_detail", pk=question.pk)


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
    if request.user.is_superuser:
        return redirect("announcements:forum_question_detail", pk=question.pk)
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
    return redirect("announcements:forum_question_detail", pk=question.pk)


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
        notify_forum_reply(reply)
        return redirect("announcements:forum_question_detail", pk=question.pk)

    return _render_faq_board(
        request,
        selected_question=question,
        detail_page=True,
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
        return redirect("announcements:forum_question_detail", pk=question.pk)

    return _render_faq_board(
        request,
        selected_question=question,
        detail_page=True,
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
    return redirect("announcements:forum_question_detail", pk=question.pk)


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
        "announcements:forum_question_detail",
        pk=reply.question_id,
    )
