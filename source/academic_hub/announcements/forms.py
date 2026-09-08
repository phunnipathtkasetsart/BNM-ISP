"""Staff-facing announcement form.

Who may address whom is enforced here rather than in the template, because a
select element is trivial to edit in a browser and the server is the only
boundary that actually holds.
"""

from django import forms
from django.utils import timezone

from .models import Announcement, Audience, Tag


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = [
            "title", "body", "audience", "tags",
            "is_urgent", "deadline", "is_published",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "What is this about?",
                "autocomplete": "off",
            }),
            "body": forms.Textarea(attrs={
                "rows": 7,
                "placeholder": "The announcement itself. Plain text.",
            }),
            "tags": forms.CheckboxSelectMultiple,
            # datetime-local so a phone offers a picker rather than a text box.
            "deadline": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        labels = {
            "is_urgent": "Pin to the top as urgent",
            "is_published": "Visible now",
            "deadline": "Deadline (optional)",
        }
        help_texts = {
            "is_urgent": "Use sparingly. Everything urgent means nothing is.",
            "tags": "What the announcement is about. Course tags limit it to that course.",
        }

    def __init__(self, *args, author=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.author = author
        self.fields["deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["tags"].queryset = Tag.objects.all()

        # Only the Department speaks for the department. A lecturer posting to
        # "everyone, including guests" would put course-level notices on the
        # public board, which is exactly what the guest filter exists to stop.
        if author is not None and not author.is_superuser:
            self.fields["audience"].choices = [
                (value, label) for value, label in Audience.choices
                if value != Audience.PUBLIC
            ]
            self.fields["audience"].initial = Audience.STUDENTS
            self.fields["audience"].help_text = (
                "Only the Department can publish to guests."
            )

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")
        if deadline and deadline < timezone.now():
            raise forms.ValidationError("That deadline has already passed.")
        return deadline

    def clean_audience(self):
        """Re-check the restriction; the browser is not a boundary."""
        audience = self.cleaned_data.get("audience")
        if (
            audience == Audience.PUBLIC
            and self.author is not None
            and not self.author.is_superuser
        ):
            raise forms.ValidationError(
                "Only the Department can publish an announcement to guests."
            )
        return audience
