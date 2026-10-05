import csv
import io
import re

from django import forms
from django.contrib.auth import get_user_model


class CourseForm(forms.Form):
    section = forms.RegexField(
        label="Section (Sec)", regex=r"^[0-9]+$", max_length=20,
        error_messages={"invalid": "Section must contain digits 0–9 only."},
        widget=forms.TextInput(attrs={"placeholder": "e.g. 001", "inputmode": "numeric",
                                      "pattern": "[0-9]+", "data-numeric-section": "",
                                      "title": "Use digits 0–9 only."}),
    )
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


def classify_students(rows):
    """Resolve valid accounts in one query and report every rejected row."""
    candidates = {value for _, value in rows if re.fullmatch(r"[0-9]{10}", value)}
    accounts = {u.pk: u for u in get_user_model().objects.filter(
        pk__in=candidates, is_active=True, is_staff=False, is_superuser=False)}
    students, rejected, seen = [], [], set()
    duplicates = 0
    for line, value in rows:
        if not re.fullmatch(r"[0-9]{10}", value):
            rejected.append({"row": line, "student_id": value,
                             "reason": "Invalid 10-digit IDs: enter one 10-digit student ID."})
        elif value not in accounts:
            rejected.append({"row": line, "student_id": value,
                             "reason": "No active student account for this ID."})
        elif value in seen:
            duplicates += 1
        else:
            seen.add(value)
            students.append(accounts[value])
    return students, rejected, duplicates


class RosterForm(forms.Form):
    student_ids = forms.CharField(label="Student IDs", max_length=12000,
                                 help_text="10-digit IDs separated by commas, spaces or new lines. Up to 500 entries. Valid students are added; rejected IDs are reported.",
                                 widget=forms.Textarea(attrs={"rows": 5, "placeholder": "6610540001\n6610540002"}))

    def clean_student_ids(self):
        ids = re.split(r"[\s,;]+", self.cleaned_data["student_ids"].strip())
        if len(ids) > 500:
            raise forms.ValidationError("Add at most 500 student entries at a time.")
        self.students, self.rejected, self.duplicate_count = classify_students(list(enumerate(ids, 1)))
        return self.students


class CsvRosterForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV file",
        help_text="UTF-8 CSV, up to 1 MB / 500 student rows. Header: student_id or nisit_id. One 10-digit ID per row.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        limit = 1024 * 1024
        if upload.size > limit:
            raise forms.ValidationError("CSV must be no larger than 1 MB.")
        raw = upload.read(limit + 1)
        if len(raw) > limit:
            raise forms.ValidationError("CSV must be no larger than 1 MB.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise forms.ValidationError("Save the file as CSV UTF-8 and upload it again.")
        ids = []
        header_seen = False
        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                if not header_seen:
                    if len(row) != 1 or row[0].strip().lower() not in {"student_id", "nisit_id"}:
                        raise forms.ValidationError("Use one column with the header student_id or nisit_id. Download the template below.")
                    header_seen = True
                    continue
                ids.append((reader.line_num, row[0].strip() if len(row) == 1 else ",".join(row)))
                if len(ids) > 500:
                    raise forms.ValidationError("CSV may contain at most 500 student rows.")
        except csv.Error:
            raise forms.ValidationError("Invalid CSV format. Use the template and upload it again.")
        if not ids:
            raise forms.ValidationError("CSV contains no student IDs.")
        self.students, self.rejected, self.duplicate_count = classify_students(ids)
        return upload
