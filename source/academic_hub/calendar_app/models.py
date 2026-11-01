"""Personal notes on calendar days.

A note belongs to one user and one date. Only its author ever sees it: the
calendar loads notes with DayNote.objects.filter(user=request.user), and the
edit/delete views look a note up the same way, so another user's note id
simply 404s.

`notify` records that the author wants a reminder on that day. Nothing sends
reminders yet; the flag is stored so a reminder job can pick it up later.
"""

from django.conf import settings
from django.db import models


class DayNote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="day_notes")
    date = models.DateField()
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="", max_length=2000)
    notify = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "created_at", "pk"]
        indexes = [models.Index(fields=["user", "date"], name="daynote_user_date")]

    def __str__(self):
        return f"{self.date}: {self.title}"
