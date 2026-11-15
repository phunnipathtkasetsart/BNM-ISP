"""Course Q&A support: input parsing, permission rules and JSON serializers.

The permission rules live here once and are used both to build the ``can_*``
flags sent to the browser and to authorize the matching write endpoint, so the
two can never disagree.
"""
import json
from functools import wraps

from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404

from .models import CourseAnswer, CourseQuestion
from .permissions import is_student_user

TITLE_MAX = 200
BODY_MAX = 5000


class QAError(Exception):
    """A request problem reported to the client as a JSON error."""

    def __init__(self, message, *, status=400, field=None):
        super().__init__(message)
        self.message, self.status, self.field = message, status, field


def qa_error(message, *, status=400, field=None):
    if field:
        return JsonResponse({"errors": {field: [{"message": message, "code": "invalid"}]}}, status=status)
    return JsonResponse({"error": message}, status=status)


def qa_endpoint(view):
    """Turn QAError and Http404 raised by a Q&A view into JSON responses."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except QAError as error:
            return qa_error(error.message, status=error.status, field=error.field)
        except Http404:
            return JsonResponse({"error": "Not found."}, status=404)
    return wrapped


# --- input -------------------------------------------------------------------

def qa_payload(request, *, boolean_fields=()):
    if request.content_type != "application/json":
        return request.POST
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise QAError("Invalid JSON body.", field="__all__")
    if not isinstance(data, dict):
        raise QAError("Provide a JSON object.", field="__all__")
    for field, value in data.items():
        if not (isinstance(value, str) or (field in boolean_fields and type(value) is bool)):
            raise QAError("Provide a string value.", field=field)
    return data


def text_value(data, field, max_length):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise QAError("This field is required.", field=field)
    value = value.strip()
    if len(value) > max_length:
        raise QAError(f"Ensure this value has at most {max_length} characters.", field=field)
    return value


def boolean_value(request, data, field):
    value = data.get(field)
    if request.content_type == "application/json":
        if type(value) is not bool:
            raise QAError("Provide a JSON boolean.", field=field)
        return value
    if value not in {"true", "false"}:
        raise QAError('Use "true" or "false".', field=field)
    return value == "true"


def positive_int_param(request, name, default):
    raw = request.GET.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value < 1:
        raise QAError("Provide a positive integer.", field=name)
    return value


# --- lookups -------------------------------------------------------------------

def question_queryset(course):
    active_verified_answers = CourseAnswer.objects.filter(
        question_id=OuterRef("pk"), archived_at__isnull=True, is_verified=True,
    )
    return (
        CourseQuestion.objects.filter(course=course)
        .select_related("author", "course")
        .annotate(
            answer_count=Count("answers", filter=Q(answers__archived_at__isnull=True), distinct=True),
            has_verified_answer=Exists(active_verified_answers),
        )
    )


def locked_question(request, question_id, *, archived=False):
    """Lock a question for writing; 404 unless its archived state matches ``archived``."""
    questions = CourseQuestion.objects.filter(course=request.course).select_for_update()
    question = get_object_or_404(questions, pk=question_id)
    if (question.archived_at is not None) != archived:
        raise Http404
    return question


def locked_answer(question, answer_id, *, archived=False):
    """Lock an answer of ``question``; 404 unless its archived state matches ``archived``."""
    answers = CourseAnswer.objects.select_for_update().filter(question=question)
    answer = get_object_or_404(answers, pk=answer_id)
    if (answer.archived_at is not None) != archived:
        raise Http404
    return answer


def refreshed(request, question):
    return question_queryset(request.course).get(pk=question.pk)


# --- permission rules ------------------------------------------------------------

def is_student_author(request, item):
    return item.author_id == request.user.pk and is_student_user(request.user)


def is_course_staff(request):
    return request.is_dept_override or request.is_owner or request.is_ta


def can_restore(request, item):
    """Department restores anything; owner/TA restore what they archived."""
    return request.is_dept_override or (
        (request.is_owner or request.is_ta) and item.archived_by_id == request.user.pk
    )


def can_edit_question(request, question, answer_count):
    return request.is_dept_override or (is_student_author(request, question) and answer_count == 0)


def can_archive_question(request, question):
    return is_course_staff(request) or is_student_author(request, question)


def can_edit_answer(request, answer):
    return request.is_dept_override or (
        request.can_manage_content and answer.author_id == request.user.pk
    )


def can_archive_answer(request, answer):
    return is_course_staff(request) or (
        request.can_manage_content and answer.author_id == request.user.pk
    )


def can_post_question(request):
    course, user = request.course, request.user
    return (
        is_student_user(user)
        and not course.ta_links.filter(user=user).exists()
        and course.enrollments.filter(student=user).exists()
    )


# --- serializers -----------------------------------------------------------------

def _staff_fields(request, item):
    if not request.can_manage_content:
        return {}
    return {
        "archived_at": item.archived_at,
        "can_restore": item.archived_at is not None and can_restore(request, item),
    }


def question_data(request, question, *, include_body=False):
    active = question.archived_at is None
    manage = request.can_manage_content
    data = {
        "id": question.pk,
        "title": question.title,
        "author_name": question.author_label,
        "author_role_label": question.author_role_label,
        "created_at": question.created_at,
        "updated_at": question.updated_at,
        "edited_at": question.edited_at,
        "is_pinned": question.is_pinned,
        "pinned_at": question.pinned_at,
        "answer_count": question.answer_count,
        "has_verified_answer": question.has_verified_answer,
        "is_mine": question.author_id == request.user.pk,
        "can_edit": active and can_edit_question(request, question, question.answer_count),
        "can_archive": active and can_archive_question(request, question),
        "can_answer": active and manage,
        "can_pin": active and manage,
        "can_verify": active and manage,
    }
    if include_body:
        data["body"] = question.body
    else:
        data["body_excerpt"] = question.body[:240]
    data.update(_staff_fields(request, question))
    return data


def answer_data(request, question, answer):
    active = question.archived_at is None and answer.archived_at is None
    return {
        "id": answer.pk,
        "body": answer.body,
        "author_name": answer.author_label,
        "author_role_label": answer.author_role_label,
        "created_at": answer.created_at,
        "updated_at": answer.updated_at,
        "edited_at": answer.edited_at,
        "is_verified": answer.is_verified,
        "verified_at": answer.verified_at,
        "is_mine": answer.author_id == request.user.pk,
        "can_edit": active and can_edit_answer(request, answer),
        "can_archive": active and can_archive_answer(request, answer),
        "can_verify": active and request.can_manage_content,
        **_staff_fields(request, answer),
    }


def question_response(request, question, *, status=200):
    return JsonResponse(
        {"question": question_data(request, refreshed(request, question), include_body=True)},
        status=status,
    )


def answer_response(request, question, answer, *, status=200):
    question = refreshed(request, question)
    return JsonResponse({
        "answer": answer_data(request, question, answer),
        "question": question_data(request, question, include_body=True),
    }, status=status)
