"""The day-note form."""

from typing import TYPE_CHECKING

from django import forms

from .models import DayNote

# ModelForm is generic only in the type stubs; at runtime it can't be
# subscripted, so the typed base class exists for mypy alone.
if TYPE_CHECKING:
    DayNoteFormBase = forms.ModelForm[DayNote]
else:
    DayNoteFormBase = forms.ModelForm


class DayNoteForm(DayNoteFormBase):
    """Date, title, description and reminder flag of one note.

    The model's CharField and TextField become form fields that trim
    surrounding spaces themselves, so a title of only spaces is "required"
    and neither field needs cleaning of its own.
    """

    class Meta:
        model = DayNote
        fields = ["date", "title", "description", "notify"]
