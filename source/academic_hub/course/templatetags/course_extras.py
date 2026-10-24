import os

from django import template

register = template.Library()

_KINDS = {
    "pdf": {"pdf"},
    "zip": {"zip", "rar", "7z", "gz", "tar"},
    "doc": {"doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "odt"},
    "img": {"png", "jpg", "jpeg", "gif", "webp", "svg"},
}


def _ext(name):
    return os.path.splitext(name or "")[1].lstrip(".").lower()


@register.filter
def file_ext(name):
    """Short upper-case label for the file badge, e.g. PDF, DOCX."""
    return _ext(name)[:4].upper() or "FILE"


@register.filter
def file_kind(name):
    """Badge colour group: pdf, zip, doc, img or txt."""
    ext = _ext(name)
    for kind, exts in _KINDS.items():
        if ext in exts:
            return kind
    return "txt"
