from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from accounts.middleware import is_guest_request
from ..forms import ForumQuestionFilterForm, ForumReplyForm
from ..models import ForumQuestion, ForumReply, Tag

FORUM_PAGE_SIZE = 20


def _forum_role(user):
    if user.is_superuser:
        return "department"
    if user.is_staff:
        return "lecturer"
    return "student"


def _forum_questions(user, is_guest):
    reply_queryset = ForumReply.objects.select_related("author")
    if not user.is_superuser:
        reply_queryset = reply_queryset.filter(archived_at__isnull=True)
    return (
        ForumQuestion.objects.visible_to(user, is_guest)
        .select_related("author", "category")
        .prefetch_related(
            "departments",
            Prefetch("replies", queryset=reply_queryset, to_attr="visible_replies"),
        )
        .annotate(
            comment_count=Count(
                "replies",
                filter=Q(replies__archived_at__isnull=True)
                if not user.is_superuser
                else Q(),
                distinct=True,
            )
        )
    )


def _filter_questions(questions, form):
    if not form.is_valid():
        return questions.order_by("-created_at", "-pk")

    filters = form.cleaned_data
    query = filters["search"].strip()
    if query:
        questions = questions.filter(Q(title__icontains=query) | Q(body__icontains=query))
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

    ordering = {
        ForumQuestionFilterForm.SortOrder.OLDEST: ("created_at", "pk"),
        ForumQuestionFilterForm.SortOrder.MOST_COMMENTS: ("-comment_count", "-created_at", "-pk"),
        ForumQuestionFilterForm.SortOrder.FEWEST_COMMENTS: ("comment_count", "-created_at", "-pk"),
    }
    return questions.order_by(*ordering.get(filters["sort"], ("-created_at", "-pk")))


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
    filter_data = request.GET.copy()
    filter_data.setdefault("date_direction", ForumQuestionFilterForm.DateDirection.BEFORE)
    filter_form = ForumQuestionFilterForm(filter_data)
    questions = _filter_questions(_forum_questions(request.user, is_guest), filter_form)

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
    reply_has_errors = reply_form is not None and bool(reply_form.errors)

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
            or reply_has_errors
            or reply_edit_form is not None
        ),
        "reply_form_error_question": (
            selected_question.pk
            if selected_question is not None
            and reply_has_errors
            else None
        ),
        "department_unavailable": department_unavailable,
        "can_create_question": not is_guest and request.user.is_active,
        "can_manage_forum_content": can_reply,
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


@login_required(login_url="accounts:login")
def faq_board(request):
    return _render_faq_board(request)


@login_required(login_url="accounts:login")
def forum_question_detail(request, pk):
    is_guest = is_guest_request(request)
    question = get_object_or_404(
        ForumQuestion.objects.visible_to(request.user, is_guest),
        pk=pk,
    )
    return _render_faq_board(
        request,
        selected_question=question,
        detail_page=True,
    )
