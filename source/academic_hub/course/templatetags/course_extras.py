import os
import re

from django import template
from django.utils.html import urlize
from django.utils.safestring import mark_safe

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


_LINK_TAG = re.compile(r"<a\b([^>]*)>")
_REL_ATTRIBUTE = re.compile(r'\srel="([^"]*)"')


@register.filter
def urlize_new_tab(value):
    linked_text = str(urlize(value, autoescape=True))

    def add_new_tab_attributes(match):
        attributes = match.group(1)
        rel_match = _REL_ATTRIBUTE.search(attributes)
        if rel_match:
            rel_values = rel_match.group(1).split()
            for rel_value in ("noopener", "noreferrer"):
                if rel_value not in rel_values:
                    rel_values.append(rel_value)
            attributes = _REL_ATTRIBUTE.sub(
                f' rel="{" ".join(rel_values)}"', attributes, count=1
            )
        else:
            attributes += ' rel="noopener noreferrer"'
        return f'<a target="_blank"{attributes}>'

    return mark_safe(_LINK_TAG.sub(add_new_tab_attributes, linked_text))
