import os
import secrets
import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower, Trim

from .validators import validate_upload_size

def generate_class_code():
    # O/0 and I/1 are omitted so codes are easier to read aloud.
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))


class Course(models.Model):
    name = models.CharField(max_length=120)
    section = models.CharField(max_length=20, blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="owned_courses")
    class_code = models.CharField(max_length=6, unique=True, default=generate_class_code,
                                  editable=False, validators=[RegexValidator(r"^[A-Z0-9]{6}$")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(
            models.F("owner"), Lower(Trim("name")), Lower(Trim("section")),
            name="unique_owner_class_section",
        )]

    def __str__(self):
        return self.name


class Enrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="course_enrollments")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["course", "student"],
                                               name="unique_course_student")]
        ordering = ["student__first_name", "student__last_name", "student_id"]

# --- Iteration 4: course content -------------------------------------------

def course_upload_path(instance, filename):
    """Store under a random name so file paths cannot be guessed or collide.

    The name the uploader chose is kept in ``original_name`` and used for the
    download, so nothing the user typed ever becomes part of a path on disk.
    """
    extension = os.path.splitext(filename)[1].lower()[:10]
    return f"course_files/{instance.course_id}/{uuid.uuid4().hex}{extension}"


class StoredFile(models.Model):
    """Shared fields for an uploaded file; subclasses must provide ``course``."""
    file = models.FileField(upload_to=course_upload_path, max_length=255,
                            validators=[validate_upload_size])
    original_name = models.CharField(max_length=255, blank=True, editable=False)
    size = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # A fresh upload still carries the browser's file name here; after
        # super().save() it has been replaced by the random storage path.
        if self.file and not self.original_name:
            self.original_name = os.path.basename(self.file.name)[:255]
        if self.file and not self.size:
            self.size = self.file.size
        super().save(*args, **kwargs)


class CourseTA(models.Model):
    """An enrolled student a lecturer has made a teaching assistant.

    "Enrolled" is enforced by the service that creates the link (task 4.5),
    not here, so removing a student from the roster must also remove this row.
    """
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="ta_links")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="course_ta_links")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["assigned_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["course", "user"], name="unique_course_ta")]


class Topic(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="topics")
    title = models.CharField(max_length=120)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [models.UniqueConstraint(
            models.F("course"), Lower(Trim("title")), name="unique_course_topic_title",
        )]

    def __str__(self):
        return self.title


class Material(StoredFile):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="materials")
    # Deleting a topic keeps its materials; they fall back to "No topic".
    topic = models.ForeignKey(Topic, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="materials")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.title


class CoursePost(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="posts")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True, default="")
    deadline = models.DateTimeField(null=True, blank=True, help_text="Optional deadline for this announcement") # New field
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.title


class PostAttachment(StoredFile):
    post = models.ForeignKey(CoursePost, on_delete=models.CASCADE, related_name="attachments")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "pk"]

    @property
    def course_id(self):
        return self.post.course_id

    @property
    def course(self):
        return self.post.course
