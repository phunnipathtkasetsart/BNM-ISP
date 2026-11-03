from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.middleware import is_guest_request
from .models import Notification
from .services import refresh_time_sensitive_notifications


def _denied(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)
    if not request.user.is_active or is_guest_request(request):
        return JsonResponse({"error": "You do not have access to notifications."}, status=403)
    return None


@require_GET
def notification_list(request):
    denied = _denied(request)
    if denied:
        return denied

    refresh_time_sensitive_notifications(request.user)
    notifications = Notification.objects.filter(recipient=request.user)
    return JsonResponse({
        "unread_count": notifications.filter(read_at__isnull=True).count(),
        "notifications": [
            {
                "id": item.pk,
                "kind": item.kind,
                "title": item.title,
                "message": item.message,
                "url": item.url,
                "created_at": item.created_at.isoformat(),
                "is_read": item.read_at is not None,
            }
            for item in notifications[:10]
        ],
    })


@require_POST
def notification_read(request, pk):
    denied = _denied(request)
    if denied:
        return denied
    updated = Notification.objects.filter(
        pk=pk, recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    if not updated and not Notification.objects.filter(
        pk=pk, recipient=request.user
    ).exists():
        return JsonResponse({"error": "Notification not found."}, status=404)
    return JsonResponse({"read": True})


@require_POST
def notification_read_all(request):
    denied = _denied(request)
    if denied:
        return denied
    Notification.objects.filter(
        recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    return JsonResponse({"read": True})
