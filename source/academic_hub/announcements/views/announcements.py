from django.contrib.auth.decorators import login_required
from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from notifications.services import notify_announcement
from ..forms import AnnouncementForm
from ..models import Announcement


@login_required(login_url="accounts:login")
@require_POST
def announcement_archive(request, pk):
    """Archive an announcement (Phase 1 soft delete)."""
    if not can_manage_announcements(request.user):
        return render(request, "accounts/no_access.html", status=403)

    announcement = get_object_or_404(Announcement, pk=pk)
    if not can_manage_announcements(request.user, announcement):
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
    if not can_manage_announcements(request.user, announcement):
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
    if not can_manage_announcements(request.user, announcement):
        return render(request, "accounts/no_access.html", status=403)

    announcement.delete()
    return redirect("announcements:public_board")


def can_manage_announcements(user, announcement=None):
    """Lecturers manage their own announcements; the Department manages all."""
    return user.is_authenticated and (
        user.is_superuser
        or (
            user.is_staff
            and (announcement is None or announcement.author_id == user.pk)
        )
    )


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

    if not can_manage_announcements(request.user, instance):
        return render(request, "accounts/no_access.html", status=403)

    form = AnnouncementForm(
        request.POST if request.method == "POST" else None,
        instance=instance,
        author=request.user,
    )
    if request.method == "POST" and form.is_valid():
        announcement = form.save(commit=False)
        if instance is None:
            announcement.author = request.user
            announcement.author_label = (
                "Department" if request.user.is_superuser else "Lecturer"
            )
        with transaction.atomic():
            announcement.save()
            form.save_m2m()
            _refresh_search_vector(announcement)
        if announcement.is_published:
            notify_announcement(announcement)
        return redirect("announcements:public_board")

    return render(request, "announcements/announcement_form.html", {
        "form": form,
        "instance": instance,
        "is_edit": instance is not None,
    })
