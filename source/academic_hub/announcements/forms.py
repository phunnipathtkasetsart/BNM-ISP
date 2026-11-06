"""Staff-facing announcement form.

Who may address whom is enforced here rather than in the template, because a
select element is trivial to edit in a browser and the server is the only
boundary that actually holds.
"""

from django import forms
from django.db import models
from django.utils import timezone

from .models import (
    Announcement,
    Audience,
    ForumCategory,
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
    visibility = forms.ChoiceField(label="Audience")

    class Meta:
        model = ForumQuestion
        fields = [
            "title",
            "body",
            "visibility",
            "category",
            "is_pinned",
            "is_published",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"autocomplete": "off"}),
            "body": forms.Textarea(attrs={"rows": 5}),
        }
        labels = {
            "title": "Question",
            "body": "Details",
            "visibility": "Audience",
            "category": "Category",
            "is_pinned": "Pin to Most FAQ",
            "is_published": "Published",
        }
        help_texts = {
            "category": (
                "Choose a category for this question. Categories are available "
                "to everyone as a forum filter."
            ),
            "is_pinned": (
                "Pinned questions appear in the homepage Most FAQ box."
            ),
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
        self.fields["category"].queryset = ForumCategory.objects.order_by("name")
        self.fields["title"].max_length = 200
        self.fields["body"].max_length = 5000
        self.fields["body"].strip = True

        if self.role != "department":
            self.fields.pop("is_published")
            self.fields.pop("is_pinned")

        if self.role == "student":
            self.fields["visibility"].choices = [
                (ForumQuestion.Visibility.EVERYONE, "Everyone"),
                (ForumQuestion.Visibility.DEPARTMENT, "My department only"),
            ]
        else:
            self.fields["visibility"].choices = [
                (ForumQuestion.Visibility.EVERYONE, "Everyone"),
                ("ske", "SKE"),
                ("cpe", "CPE"),
            ]
            self.fields["visibility"].initial = ForumQuestion.Visibility.EVERYONE
        if (
            instance is not None
            and instance.visibility == ForumQuestion.Visibility.DEPARTMENT
            and self.role in {"department", "lecturer"}
        ):
            tag = instance.departments.first()
            self.fields["visibility"].initial = tag.slug if tag else "ske"
        elif instance is not None:
            self.fields["visibility"].initial = instance.visibility
        if (
            instance is not None
            and instance.visibility == ForumQuestion.Visibility.DEPARTMENT
            and self.role == "student"
        ):
            self.fields["visibility"].initial = ForumQuestion.Visibility.DEPARTMENT

        self._student_department = None
        self._audience_programme = None

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

        if self.role in {"department", "lecturer"}:
            if visibility in {"ske", "cpe"}:
                self._audience_programme = Tag.objects.filter(
                    kind=Tag.Kind.PROGRAMME,
                    slug__iexact=visibility,
                ).first()
                if self._audience_programme is None:
                    self.add_error(
                        "visibility",
                        f"The {visibility.upper()} audience tag is not configured.",
                    )
                cleaned_data["visibility"] = ForumQuestion.Visibility.DEPARTMENT
            else:
                cleaned_data["visibility"] = ForumQuestion.Visibility.EVERYONE

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

        if visibility == ForumQuestion.Visibility.DEPARTMENT and self.role == "student":
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

        return cleaned_data

    def selected_departments(self):
        visibility = self.cleaned_data.get(
            "visibility",
            self.instance.visibility if self.instance.pk else None,
        )
        if visibility == ForumQuestion.Visibility.EVERYONE:
            return []
        if self.role in {"department", "lecturer"}:
            return [self._audience_programme] if self._audience_programme else []
        if self.role == "student":
            if (
                visibility == ForumQuestion.Visibility.DEPARTMENT
                and self._student_department is not None
            ):
                return [self._student_department]
            return []
        return []


class ForumCategoryForm(forms.ModelForm):
    class Meta:
        model = ForumCategory
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={"autocomplete": "off", "maxlength": 80}
            ),
        }
        labels = {"name": "Category name"}


class ForumQuestionFilterForm(forms.Form):
    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        SKE = "ske", "SKE"
        CPE = "cpe", "CPE"

    class AuthorRole(models.TextChoices):
        DEPARTMENT = "department", "Department"
        LECTURER = "lecturer", "Lecturer"
        STUDENT = "student", "Student"

    class SortOrder(models.TextChoices):
        NEWEST = "newest", "Newest first"
        OLDEST = "oldest", "Oldest first"
        MOST_COMMENTS = "comments_desc", "Most comments"
        FEWEST_COMMENTS = "comments_asc", "Fewest comments"

    class DateDirection(models.TextChoices):
        BEFORE = "before", "Before"
        AFTER = "after", "After"

    search = forms.CharField(
        required=False,
        max_length=200,
        label="Search",
        widget=forms.TextInput(
            attrs={"type": "search", "autocomplete": "off"}
        ),
    )
    audience = forms.ChoiceField(
        required=False,
        choices=[("", "All audiences")] + list(Audience.choices),
    )
    author = forms.ChoiceField(
        required=False,
        choices=[("", "All authors")] + list(AuthorRole.choices),
    )
    category = forms.ModelChoiceField(
        required=False,
        queryset=ForumCategory.objects.order_by("name"),
        empty_label="All categories",
    )
    date_direction = forms.ChoiceField(
        required=False,
        choices=DateDirection.choices,
        label="Date direction",
    )
    date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Posted date",
    )
    sort = forms.ChoiceField(
        required=False,
        choices=SortOrder.choices,
        initial=SortOrder.NEWEST,
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("date") and not cleaned_data.get("date_direction"):
            self.add_error(
                "date_direction",
                "Choose whether to show questions before or after this date.",
            )
        return cleaned_data


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
