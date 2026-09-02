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

KU_EMAIL_ATTRS = {
    "placeholder": "KU Email (@ku.th)",
    "inputmode": "email",
    "autocomplete": "email",
    "autocapitalize": "off",
    "spellcheck": "false",
    "pattern": r"[^@\s]+@ku\.th",
    "title": "Your KU address, ending in @ku.th.",
}


def normalise_ku_email(email):
    """Lower-case it and confirm it is a @ku.th address."""
    email = (email or "").strip().lower()
    ku_email_validator(email)
    return email


class LoginForm(forms.Form):
    nisit_id = forms.CharField(
        label="Nisit ID",
        min_length=10,
        max_length=10,
        validators=[nisit_id_validator],
        widget=forms.TextInput(attrs={**NISIT_ATTRS, "autofocus": True}),
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
            "first_name": forms.TextInput(attrs={"placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Last Name"}),
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


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        label="KU Email",
        widget=forms.EmailInput(attrs={**KU_EMAIL_ATTRS, "autofocus": True}),
    )

    def clean_email(self):
        return normalise_ku_email(self.cleaned_data.get("email"))
