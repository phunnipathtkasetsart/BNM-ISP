from django import forms

from .models import DayNote


class DayNoteForm(forms.ModelForm):
    class Meta:
        model = DayNote
        fields = ["date", "title", "description", "notify"]

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Give the note a title.")
        return title

    def clean_description(self):
        return self.cleaned_data.get("description", "").strip()
