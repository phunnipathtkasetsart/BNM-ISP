"""Staff-facing announcement form.

Who may address whom is enforced here rather than in the template, because a
select element is trivial to edit in a browser and the server is the only
boundary that actually holds.
"""

from django import forms
from django.utils import timezone

from .models import (
    Announcement,
    Audience,
    ForumQuestion,
    ForumReply,
    Tag,
)


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


class ForumQuestionForm(forms.ModelForm):
    class Meta:
        model = ForumQuestion
        fields = ["title", "body", "visibility", "departments", "is_published"]
        widgets = {
            "title": forms.TextInput(attrs={"autocomplete": "off"}),
            "body": forms.Textarea(attrs={"rows": 5}),
            "departments": forms.CheckboxSelectMultiple,
        }
        labels = {
            "title": "Question",
            "body": "Details",
            "visibility": "Who can see this question?",
            "departments": "Department tags",
            "is_published": "Published",
        }
        help_texts = {
            "is_published": (
                "When checked, this question is visible to users allowed by "
                "its audience and department. Uncheck it to hide the question "
                "from students, lecturers, and guests; the Department can "
                "still view and manage it."
            ),
        }

    def __init__(self, *args, author=None, instance=None, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        self.author = author
        self.role = self._role_for(author)
        self.fields["departments"].queryset = Tag.objects.filter(
            kind=Tag.Kind.PROGRAMME
        ).order_by("label")
        self.fields["departments"].label_from_instance = lambda tag: tag.label
        self.fields["title"].max_length = 200
        self.fields["body"].max_length = 5000
        self.fields["body"].strip = True

        if self.role != "department":
            self.fields.pop("is_published")

        if self.role == "student":
            self.fields.pop("departments")
            self.fields["visibility"].choices = [
                (ForumQuestion.Visibility.EVERYONE, "Everyone"),
                (ForumQuestion.Visibility.DEPARTMENT, "My department only"),
            ]
        elif self.role == "lecturer":
            self.fields["visibility"].choices = [
                (ForumQuestion.Visibility.EVERYONE, "Everyone"),
                (ForumQuestion.Visibility.DEPARTMENT, "Department only"),
            ]
            if instance is not None:
                self.fields.pop("title")
                self.fields.pop("body")
                self.fields.pop("visibility")
                self.fields.pop("is_published", None)
                self.fields["departments"].initial = instance.departments.all()
                self.fields["departments"].required = (
                    instance.visibility == ForumQuestion.Visibility.DEPARTMENT
                )

        self._student_department = None

    @staticmethod
    def _role_for(author):
        if author is not None and author.is_superuser:
            return "department"
        if author is not None and author.is_staff:
            return "lecturer"
        return "student"

    def clean(self):
        cleaned_data = super().clean()
        visibility = cleaned_data.get(
            "visibility",
            self.instance.visibility if self.instance.pk else None,
        )
        departments = cleaned_data.get("departments")

        if self.role == "student" and "departments" in self.data:
            allowed_department = Tag.objects.filter(
                kind=Tag.Kind.PROGRAMME,
                slug__iexact=(getattr(self.author, "department", "") or "").strip(),
            ).first()
            allowed_id = str(allowed_department.pk) if allowed_department else None
            getlist = getattr(self.data, "getlist", None)
            submitted_ids = (
                getlist("departments")
                if getlist is not None
                else self.data.get("departments", [])
            )
            if isinstance(submitted_ids, str):
                submitted_ids = [submitted_ids]
            elif not isinstance(submitted_ids, (list, tuple)):
                submitted_ids = [submitted_ids]
            if any(submitted_id != allowed_id for submitted_id in submitted_ids):
                self.add_error(
                    None,
                    "Students may only use their own department tag.",
                )

        if (
            visibility == ForumQuestion.Visibility.DEPARTMENT
            and self.role == "student"
        ):
            department = (getattr(self.author, "department", "") or "").strip()
            self._student_department = Tag.objects.filter(
                kind=Tag.Kind.PROGRAMME,
                slug__iexact=department,
            ).first()
            if self._student_department is None:
                self.add_error(
                    "visibility",
                    "Your department is not configured for FAQ questions. "
                    "Please contact the Department.",
                )
        elif (
            visibility == ForumQuestion.Visibility.DEPARTMENT
            and not departments
        ):
            self.add_error(
                "departments",
                "Choose at least one department for a department-only question.",
            )

        if self.role == "lecturer" and self.instance.pk:
            if visibility != self.instance.visibility:
                self.add_error(
                    None,
                    "Lecturers may change department tags but cannot change "
                    "a question's visibility.",
                )

        return cleaned_data

    def selected_departments(self):
        visibility = self.cleaned_data.get(
            "visibility",
            self.instance.visibility if self.instance.pk else None,
        )
        if self.role == "student":
            if (
                visibility == ForumQuestion.Visibility.DEPARTMENT
                and self._student_department is not None
            ):
                return [self._student_department]
            return []
        return list(self.cleaned_data.get("departments", []))


class ForumReplyForm(forms.ModelForm):
    class Meta:
        model = ForumReply
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3})}
        labels = {"body": "Your reply"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].max_length = 5000
        self.fields["body"].strip = True
