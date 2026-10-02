import re

from django import forms
from django.contrib.auth import get_user_model


class CourseForm(forms.Form):
    section = forms.CharField(label="Section (Sec)", max_length=20,
                              widget=forms.TextInput(attrs={"placeholder": "e.g. 001"}))
    name = forms.CharField(label="Class name", max_length=120,
                          widget=forms.TextInput(attrs={"placeholder": "e.g. Software Engineering", "autofocus": True}))
    field_order = ["name", "section"]


class JoinForm(forms.Form):
    code = forms.CharField(label="Class code", max_length=6, min_length=6,
                          widget=forms.TextInput(attrs={"placeholder": "ABC234", "autocomplete": "off", "autofocus": True}))

    def clean_code(self):
        code = self.cleaned_data["code"].upper()
        if not re.fullmatch(r"[A-Z0-9]{6}", code):
            raise forms.ValidationError("Enter the 6-character code from your lecturer.")
        return code


class RosterForm(forms.Form):
    student_ids = forms.CharField(label="Student IDs", max_length=12000,
                                 help_text="10-digit IDs separated by commas, spaces or new lines. Up to 500 students.",
                                 widget=forms.Textarea(attrs={"rows": 5, "placeholder": "6610540001\n6610540002"}))

    def clean_student_ids(self):
        ids = list(dict.fromkeys(re.split(r"[\s,;]+", self.cleaned_data["student_ids"].strip())))
        if len(ids) > 500:
            raise forms.ValidationError("Add at most 500 students at a time.")
        invalid = [value for value in ids if not re.fullmatch(r"[0-9]{10}", value)]
        if invalid:
            raise forms.ValidationError("Invalid 10-digit IDs: " + ", ".join(invalid[:10]))
        students = get_user_model().objects.filter(pk__in=ids, is_active=True,
                                                   is_staff=False, is_superuser=False)
        found = set(students.values_list("pk", flat=True))
        missing = [value for value in ids if value not in found]
        if missing:
            raise forms.ValidationError("No active student account for: " + ", ".join(missing[:10]))
        return list(students)
