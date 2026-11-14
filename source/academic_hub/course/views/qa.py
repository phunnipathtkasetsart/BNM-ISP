"""Course Q&A JSON API. Rules, parsing and serializers live in ``course.qa``."""
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from ..models import CourseAnswer
from ..permissions import course_access
from ..qa import (
    BODY_MAX,
    TITLE_MAX,
    QAError,
    answer_data,
    answer_response,
    boolean_value,
    can_archive_answer,
    can_archive_question,
    can_edit_answer,
    can_edit_question,
    can_post_question,
    can_restore,
    locked_answer,
    locked_question,
    positive_int_param,
    qa_endpoint,
    qa_payload,
    question_data,
    question_queryset,
    question_response,
    refreshed,
    text_value,
)
from ..services import create_course_answer, create_course_question

STATUS_FILTERS = {"", "answered", "unanswered", "mine", "archived"}
MAX_PAGE_SIZE = 100


def require(allowed, message):
    if not allowed:
        raise QAError(message, status=403)


def filter_questions(request, status_filter):
    questions = question_queryset(request.course).filter(archived_at__isnull=status_filter != "archived")
    if status_filter == "answered":
        questions = questions.filter(answer_count__gt=0)
    elif status_filter == "unanswered":
        questions = questions.filter(answer_count=0)
    elif status_filter == "mine":
        questions = questions.filter(author=request.user)
    search = request.GET.get("q", "").strip()
    if search:
        questions = questions.filter(Q(title__icontains=search) | Q(body__icontains=search))
    return questions.order_by("-is_pinned", "-has_verified_answer", "-created_at", "-pk")


def list_params(request):
    status_filter = request.GET.get("status", "")
    if status_filter not in STATUS_FILTERS:
        raise QAError("Unknown status filter.", field="status")
    if status_filter == "archived" and not request.can_manage_content:
        raise QAError("Only course staff may request archived questions.", field="status")
    page = positive_int_param(request, "page", 1)
    page_size = positive_int_param(request, "page_size", 20)
    if page_size > MAX_PAGE_SIZE:
        raise QAError("Page size cannot exceed 100.", field="page_size")
    return status_filter, page, page_size


@qa_endpoint
@course_access()
@require_http_methods(["GET", "POST"])
def qa_question_list(request, pk):
    if request.method == "POST":
        return qa_question_create_response(request, pk)
    status_filter, page, page_size = list_params(request)
    questions = filter_questions(request, status_filter)
    count = questions.count()
    start = (page - 1) * page_size
    results = questions[start:start + page_size] if start < count else []
    return JsonResponse({
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": [question_data(request, question) for question in results],
    })


@qa_endpoint
@course_access()
@require_http_methods(["GET"])
def qa_question_detail(request, pk, question_id):
    question = get_object_or_404(question_queryset(request.course), pk=question_id)
    if question.archived_at is not None and not request.can_manage_content:
        raise Http404
    answers = CourseAnswer.objects.filter(question=question).select_related("author")
    if not request.can_manage_content:
        answers = answers.filter(archived_at__isnull=True)
    answers = answers.order_by("-is_verified", "created_at", "pk")
    return JsonResponse({
        "question": question_data(request, question, include_body=True),
        "answers": [answer_data(request, question, answer) for answer in answers],
    })


def qa_question_create_response(request, pk):
    require(can_post_question(request), "Only enrolled students who are not course TAs may post questions.")
    data = qa_payload(request)
    title = text_value(data, "title", TITLE_MAX)
    body = text_value(data, "body", BODY_MAX)
    question = create_course_question(course=request.course, author=request.user, title=title, body=body)
    return JsonResponse(
        question_data(request, refreshed(request, question), include_body=True), status=201,
    )


@qa_endpoint
@course_access()
@require_POST
def qa_question_edit(request, pk, question_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        answer_count = CourseAnswer.objects.filter(question=question, archived_at__isnull=True).count()
        require(can_edit_question(request, question, answer_count), "You cannot edit this question.")
        data = qa_payload(request)
        title = text_value(data, "title", TITLE_MAX)
        body = text_value(data, "body", BODY_MAX)
        if title != question.title or body != question.body:
            question.title, question.body = title, body
            question.edited_at = timezone.now()
            question.save(update_fields=["title", "body", "edited_at", "updated_at"])
    return question_response(request, question)


def set_archived(item, user):
    """Archive ``item`` as ``user``, or restore it when ``user`` is None."""
    item.archived_at = timezone.now() if user else None
    item.archived_by = user
    item.save(update_fields=["archived_at", "archived_by", "updated_at"])


@qa_endpoint
@course_access()
@require_POST
def qa_question_archive(request, pk, question_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        require(can_archive_question(request, question), "You cannot archive this question.")
        set_archived(question, request.user)
    return question_response(request, question)


@qa_endpoint
@course_access()
@require_POST
def qa_question_restore(request, pk, question_id):
    with transaction.atomic():
        question = locked_question(request, question_id, archived=True)
        require(can_restore(request, question), "You cannot restore this question.")
        set_archived(question, None)
    return question_response(request, question)


@qa_endpoint
@course_access()
@require_POST
def qa_question_pin(request, pk, question_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        require(request.can_manage_content, "You cannot pin this question.")
        data = qa_payload(request, boolean_fields={"is_pinned"})
        question.is_pinned = boolean_value(request, data, "is_pinned")
        question.pinned_at = timezone.now() if question.is_pinned else None
        question.save(update_fields=["is_pinned", "pinned_at", "updated_at"])
    return question_response(request, question)


@qa_endpoint
@course_access()
@require_POST
def qa_answer_create(request, pk, question_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        require(request.can_manage_content, "Only the course owner, a TA, or Department may answer.")
        body = text_value(qa_payload(request), "body", BODY_MAX)
        answer = create_course_answer(question=question, author=request.user, body=body)
    return answer_response(request, question, answer, status=201)


@qa_endpoint
@course_access()
@require_POST
def qa_answer_edit(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        answer = locked_answer(question, answer_id)
        require(can_edit_answer(request, answer), "You cannot edit this answer.")
        body = text_value(qa_payload(request), "body", BODY_MAX)
        if body != answer.body:
            answer.body = body
            answer.edited_at = timezone.now()
            answer.save(update_fields=["body", "edited_at", "updated_at"])
    answer.refresh_from_db()
    return answer_response(request, question, answer)


@qa_endpoint
@course_access()
@require_POST
def qa_answer_archive(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        answer = locked_answer(question, answer_id)
        require(can_archive_answer(request, answer), "You cannot archive this answer.")
        set_archived(answer, request.user)
    answer.refresh_from_db()
    return answer_response(request, question, answer)


@qa_endpoint
@course_access()
@require_POST
def qa_answer_restore(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        answer = locked_answer(question, answer_id, archived=True)
        require(can_restore(request, answer), "You cannot restore this answer.")
        set_archived(answer, None)
    answer.refresh_from_db()
    return answer_response(request, question, answer)


@qa_endpoint
@course_access()
@require_POST
def qa_answer_verify(request, pk, question_id, answer_id):
    with transaction.atomic():
        question = locked_question(request, question_id)
        answer = locked_answer(question, answer_id)
        require(request.can_manage_content, "You cannot verify this answer.")
        data = qa_payload(request, boolean_fields={"is_verified"})
        is_verified = boolean_value(request, data, "is_verified")
        answer.is_verified = is_verified
        answer.verified_by = request.user if is_verified else None
        answer.verified_at = timezone.now() if is_verified else None
        answer.save(update_fields=["is_verified", "verified_by", "verified_at", "updated_at"])
    answer.refresh_from_db()
    return answer_response(request, question, answer)
