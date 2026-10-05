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
        # Who may tag what, decided by kind rather than a list of slugs,
        # so a new scholarship or programme tag reaches the right role
        # without touching this file. Syllabus is TOPIC and is offered to
        # nobody here: it belongs to a course, which is a later iteration.
        if author is not None and author.is_superuser:
            allowed = [
                Tag.Kind.DEPARTMENT,
                Tag.Kind.SCHOLARSHIP,
                Tag.Kind.LAB,
                Tag.Kind.PROGRAMME,
            ]
        else:
            allowed = [Tag.Kind.LAB, Tag.Kind.PROGRAMME]
        self.fields["tags"].queryset = Tag.objects.filter(kind__in=allowed)
        # Tag.__str__ is "#slug"; the board chips show the label. Match them.
        self.fields["tags"].label_from_instance = lambda tag: f"#{tag.label}"

        # Only the Department speaks for the department. A lecturer posting to
        # "everyone, including guests" would put course-level notices on the
        # public board, which is exactly what the guest filter exists to stop.
        if author is not None and not author.is_superuser:
            # Narrowing the queryset is what rejects a tag a lecturer may
            # not use, so the default "N is not one of the available
            # choices" is what they would read. Say why instead.
            self.fields["tags"].error_messages["invalid_choice"] = (
                "Lecturers can tag Lab, SKE and CPE only."
            )
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

    # A lecturer may tag Lab, SKE and CPE. The Department may also tag
    # Department and Scholarship. Nobody tags Syllabus here; that belongs to
    # a course and arrives with the course engine.
    LECTURER_KINDS = {Tag.Kind.LAB, Tag.Kind.PROGRAMME}

    def clean_tags(self):
        """Re-check the tags a lecturer is allowed to use.

        The queryset above hides the options, but a checkbox list is posted as
        plain IDs and anyone can add one. This is the check that holds.
        """
        tags = self.cleaned_data.get("tags")
        if tags is None:
            return tags
        if self.author is not None and not self.author.is_superuser:
            refused = [t for t in tags if t.kind not in self.LECTURER_KINDS]
            if refused:
                raise forms.ValidationError(
                    "Lecturers can tag Lab, SKE and CPE only."
                )
        return tags

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
