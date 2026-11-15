"""Helpers shared by the course test cases."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse


class CourseClientMixin:
    """Sign-in, URL and upload shortcuts used across the course tests."""

    def sign_in(self, user):
        self.client.force_login(user)

    def url(self, action, *args):
        return reverse(f"course:{action}", args=args)

    def upload(self, name="file.pdf", data=b"data"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")
