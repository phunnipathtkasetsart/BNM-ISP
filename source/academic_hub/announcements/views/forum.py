from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from accounts.middleware import is_guest_request
from notifications.services import notify_forum_reply
from ..forms import ForumCategoryForm, ForumQuestionForm, ForumReplyForm
from ..models import ForumQuestion, ForumReply
from .forum_board import _forum_role, _render_faq_board


def _forum_role_label(user):
    return _forum_role(user).capitalize()


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
