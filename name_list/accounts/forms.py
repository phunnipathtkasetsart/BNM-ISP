import re

from django import forms

from .models import User, ku_email_validator, nisit_id_validator

# Shared widget attributes. The browser-level constraints (maxlength, pattern,
# inputmode) are a convenience — they stop bad input being typed and bring up
# the right keyboard on a phone. The real enforcement is the validators, which
# run on the server whatever the browser does.
NISIT_ATTRS = {
    "placeholder": "Nisit ID",
    "inputmode": "numeric",
    "autocomplete": "off",
    "maxlength": "10",
    "minlength": "10",
    "pattern": "[0-9]{10}",
    "title": "Exactly 10 digits, numbers only.",
}

# Signing in is a lookup, not a definition, and not every account is a student:
# staff IDs look like "A0001" and "L0001". So the sign-in field accepts any
# text up to the column width and leaves judgement to authentication — a wrong
# ID simply fails to match. The strict digits-only rule stays on registration,
# where a new student ID is actually being created.
LOGIN_ID_ATTRS = {
    "placeholder": "Nisit ID",
    "inputmode": "text",
    "autocapitalize": "characters",   # staff IDs are upper-case
    "autocomplete": "username",
    "spellcheck": "false",
    "maxlength": "10",                # matches the userID column
}

KU_EMAIL_ATTRS = {
    "placeholder": "KU Email (@ku.th)",
    "inputmode": "email",
    "autocomplete": "email",
    "autocapitalize": "off",
    "spellcheck": "false",
    "pattern": r"[^@\s]+@ku\.th",
    "title": "Your KU address, ending in @ku.th.",
}


NAME_ATTRS = {
    "autocapitalize": "words",
    "autocomplete": "off",
    "spellcheck": "false",
    "maxlength": "150",
    "title": "Letters only — no numbers or symbols.",
}

# Names are letters, and the joiners real names actually use: a space, a hyphen
# ("Anne-Marie"), an apostrophe ("O'Brien"). No digits and no symbols. [^\W\d_]
# is "a word character that is not a digit or underscore", which under Python's
# default str matching means any Unicode letter — so Thai names pass as readily
# as Latin ones. A joiner must sit between two letters, never lead or trail.
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")


def validate_person_name(value, label):
    """Return the cleaned name, or raise with a message naming the field."""
    value = (value or "").strip()
    if not value:
        raise forms.ValidationError(f"Please enter your {label}.")
    if any(ch.isdigit() for ch in value):
        raise forms.ValidationError(f"{label.capitalize()} cannot contain numbers.")
    if not NAME_RE.match(value):
        raise forms.ValidationError(
            f"{label.capitalize()} cannot contain symbols like @ or _."
        )
    return value


def normalise_ku_email(email):
    """Lower-case it and confirm it is a @ku.th address."""
    email = (email or "").strip().lower()
    ku_email_validator(email)
    return email


class LoginForm(forms.Form):
    nisit_id = forms.CharField(
        label="Nisit ID",
        max_length=10,
        widget=forms.TextInput(attrs={**LOGIN_ID_ATTRS, "autofocus": True}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
    )


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
        help_text="At least 8 characters.",
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "nisit_id", "department", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={**NAME_ATTRS, "placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={**NAME_ATTRS, "placeholder": "Last Name"}),
            "nisit_id": forms.TextInput(
                attrs={
                    **NISIT_ATTRS,
                    "placeholder": "Nisit Number (Student ID) (10 digits)",
                }
            ),
            "department": forms.RadioSelect,
            "email": forms.EmailInput(attrs=KU_EMAIL_ATTRS),
        }
        labels = {"email": "KU Email"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # drop Django's blank "---------" option from the radio group
        self.fields["department"].choices = User.Department.choices

    def clean_first_name(self):
        return validate_person_name(self.cleaned_data.get("first_name"), "first name")

    def clean_last_name(self):
        return validate_person_name(self.cleaned_data.get("last_name"), "last name")

    def clean_email(self):
        return normalise_ku_email(self.cleaned_data.get("email"))

    def clean_password1(self):
        password = self.cleaned_data.get("password1", "")
        if len(password) < 8:
            raise forms.ValidationError("Password must be at least 8 characters.")
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class GoogleAccountForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["nisit_id", "department"]
        widgets = {
            "nisit_id": forms.TextInput(attrs=NISIT_ATTRS),
            "department": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].choices = User.Department.choices


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        label="KU Email",
        widget=forms.EmailInput(attrs={**KU_EMAIL_ATTRS, "autofocus": True}),
    )

    def clean_email(self):
        return normalise_ku_email(self.cleaned_data.get("email"))
