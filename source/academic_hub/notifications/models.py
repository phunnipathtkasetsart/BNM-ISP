from django.conf import settings
from django.db import models


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="notifications",
    )
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=500, blank=True, default="")
    url = models.CharField(max_length=500)
    event_key = models.CharField(max_length=220)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "event_key"],
                name="unique_notification_recipient_event",
            ),
        ]
        indexes = [
            models.Index(fields=["recipient", "read_at", "-created_at"],
                         name="notif_recipient_read_date"),
        ]

    def __str__(self):
        return f"{self.title} ({self.recipient_id})"
