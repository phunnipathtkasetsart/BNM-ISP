from django.conf import settings
from django.core.exceptions import ValidationError


def max_upload_bytes():
    return getattr(settings, "MAX_UPLOAD_BYTES", 50 * 1024 * 1024)


def validate_upload_size(file):
    """Reject files over the configured limit (50 MB by default).

    Shared by materials and post attachments so the rule lives in one place.
    The migration references this function by path - do not rename it.
    """
    limit = max_upload_bytes()
    if file.size > limit:
        raise ValidationError(
            "File is too large. The maximum size is %(mb)d MB.",
            code="file_too_large", params={"mb": limit // (1024 * 1024)},
        )
